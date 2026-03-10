from __future__ import annotations

import json
import csv
import math
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.event_bus import EventBus
from app.core.events import OrchestratorStateEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.orchestrator.mayak_controller import (
    MayakStubController,
    is_stub_mode,
    read_mayak_mode,
    resolve_mayak_controller,
)
from app.orchestrator.session_gps_tx import SessionGpsTxRunner
from app.orchestrator.session_runtime import SessionRuntime, SessionStatus
from app.orchestrator.session_trajectory_ticker import SessionTrajectoryTicker
from app.orchestrator.session_video_recorder import SessionVideoRecorder
from app.orchestrator.states import OrchestratorPhase, OrchestratorState
from app.profiles.loader import load_profile
from app.services.gps_sdr_sim.engine import prepare_nmea_input
from app.services.base import ServiceStatus
from app.services.service_manager import ServiceManager


def deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base (in-place) and return base.

    Rules:
    - dict + dict -> recursive merge
    - any other value (including lists) -> replace entirely
    """
    if not isinstance(base, dict):
        raise TypeError("base must be dict")
    if not isinstance(overrides, dict):
        raise TypeError("overrides must be dict")

    for key, override_value in overrides.items():
        if key in base:
            base_value = base[key]
            if isinstance(base_value, dict) and isinstance(override_value, dict):
                deep_merge(base_value, override_value)
            else:
                base[key] = override_value
        else:
            base[key] = override_value
    return base


def count_leaf_values(d: object) -> int:
    """Count leaf (non-dict) values in a nested dict structure.

    - dict -> sum of children leaves
    - anything else (including lists) -> 1
    """
    if isinstance(d, dict):
        total = 0
        for v in d.values():
            total += count_leaf_values(v)
        return total
    return 1


class Orchestrator:
    """
    v4:
      - service roles: daemon/job
      - RUNNING reflects job run-cycle, not daemon lifetime
      - stop(): stops only jobs; shutdown path may stop all elsewhere (app.main)
    """

    def __init__(self, bus: EventBus, service_manager: ServiceManager) -> None:
        self._bus = bus
        self._sm = service_manager

        self._lock = threading.Lock()
        self._state: OrchestratorState = OrchestratorState.IDLE
        self._phase: OrchestratorPhase = OrchestratorPhase.PREPARING

        # service_name -> last ServiceStatus (source of truth: ServiceStatusEvent)
        self._service_status: dict[str, ServiceStatus] = {}

        # current run-cycle jobs set
        self._run_jobs: set[str] = set()

        # wake-up for stop waiter
        self._status_changed = threading.Event()

        # stop waiter thread control
        self._stop_wait_thread: Optional[threading.Thread] = None
        self._stop_wait_cancel = threading.Event()

        # stop timeout (loaded from profile on start; default applied by orchestrator)
        self._stop_timeout_sec: int = 10
        self._scenario_seq: int = 0
        self._test_session_seq: int = 0
        self._active_scenario_id: Optional[str] = None
        self._prepared_scenario: Optional[dict[str, Any]] = None
        self._active_test_session: Optional[SessionRuntime] = None
        self._gps_tx_runner = SessionGpsTxRunner(bus)
        self._trajectory_ticker = SessionTrajectoryTicker(bus)
        self._video_recorder = SessionVideoRecorder(bus, self._sm.get_services)
        self._mayak_mode: str = "real"
        self._mayak_stub = MayakStubController()

        self._bus.subscribe(ServiceStatusEvent, self._on_service_status_event)

        # initial state event for UI
        self._publish_state(self._state)

    @property
    def state(self) -> OrchestratorState:
        with self._lock:
            return self._state

    @property
    def phase(self) -> OrchestratorPhase:
        with self._lock:
            return self._phase

    def start(self, profile_name: str, overrides: dict | None = None) -> None:
        with self._lock:
            if self._state in (OrchestratorState.PRECHECK, OrchestratorState.RUNNING, OrchestratorState.STOPPING):
                return

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "ORCH_START_REQUEST",
            f"profile={profile_name} overrides={1 if overrides is not None else 0}",
        )
        self._set_state(OrchestratorState.PRECHECK)

        try:
            profile_cfg = self._load_profile_with_overrides(profile_name, overrides)
        except Exception as ex:
            msg = str(ex)
            if msg == "validate_overrides":
                emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", "stage=validate_overrides err=TypeError")
            elif msg == "apply_overrides":
                emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", "stage=apply_overrides err=TypeError")
            elif msg.startswith("cfg_check "):
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=cfg_check err={msg.split(' ', 1)[1]}",
                )
            else:
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=load_profile err={type(ex).__name__}",
                )
            self._set_state(OrchestratorState.ERROR)
            return

        self._apply_stop_timeout_from_profile(profile_cfg, profile_name)
        self._set_mayak_mode_from_profile(profile_cfg, profile_name)

        jobs, daemons = self._compute_roles(profile_cfg, profile_name)
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "ORCH_SERVICE_ROLES",
            f"jobs={','.join(sorted(jobs))} daemons={','.join(sorted(daemons))}",
        )

        services_map = self._sm.get_services()

        root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
        root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else None)
        services_cfg = root.get("services") if isinstance(root, dict) else None
        if not isinstance(services_cfg, dict):
            services_cfg = {}

        # Start daemons first (only if not already RUNNING)
        daemons_started = 0
        for name in daemons:
            if name == "mayak_spindle" and self._is_mayak_stub_mode():
                emit_log(self._bus, "INFO", "orchestrator", "ORCH_MAYAK_STUB_ACTIVE", "service=mayak_spindle")
                continue
            svc = services_map.get(name)
            if svc is None:
                continue
            if self._is_service_running(name):
                continue
            try:
                svc_section = services_cfg.get(name, {})
                if not isinstance(svc_section, dict):
                    svc_section = {}
                svc.start(svc_section)
                daemons_started += 1
            except Exception as ex:
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=daemon_start service={name} err={type(ex).__name__}",
                )
                # daemon failures do not fail whole job cycle

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_DAEMONS_START", f"count={daemons_started}")

        # v5 policy: mayak spindle daemon readiness may be required before starting jobs.
        if self._should_require_mayak_ready(profile_cfg, profile_name):
            if not self._check_mayak_readiness_before_jobs(services_map, profile_cfg, profile_name):
                self._set_state(OrchestratorState.ERROR)
                return
        else:
            emit_log(self._bus, "INFO", "orchestrator", "ORCH_PRECHECK_SKIPPED", "service=mayak_spindle required=0")

        # New run-cycle job set
        with self._lock:
            self._run_jobs = set(jobs)

        # Start jobs for this run-cycle
        jobs_started = 0
        for name in jobs:
            svc = services_map.get(name)
            if svc is None:
                continue
            try:
                svc_section = services_cfg.get(name, {})
                if not isinstance(svc_section, dict):
                    svc_section = {}
                svc.start(svc_section)
                jobs_started += 1
            except Exception as ex:
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=job_start service={name} err={type(ex).__name__}",
                )
                self._set_state(OrchestratorState.ERROR)
                return

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_JOBS_START", f"count={jobs_started}")

        # No jobs in run-cycle: PRECHECK completes immediately.
        if not jobs:
            emit_log(self._bus, "INFO", "orchestrator", "ORCH_RUN_FINISHED", "to=IDLE reason=no_jobs")
            self._set_state(OrchestratorState.IDLE)
            return

        self._set_state(OrchestratorState.RUNNING)

    def start_daemons(self, profile_name: str, overrides: dict | None = None) -> None:
        """
        Start only daemon services for a profile, without entering RUNNING.
        Error policy: never crash UI; log per-service failures and continue.
        """

        with self._lock:
            if self._state in (
                    OrchestratorState.PRECHECK,
                    OrchestratorState.RUNNING,
                    OrchestratorState.STOPPING,
            ):
                return

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_DAEMONS_AUTOSTART", f"profile={profile_name}")

        try:
            profile_cfg = self._load_profile_with_overrides(profile_name, overrides)
        except Exception as ex:
            # Global failure (profile cannot be loaded / overrides invalid)
            emit_log(
                self._bus,
                "ERROR",
                "system",
                "SYSTEM_DAEMONS_START_FAIL",
                f"error={type(ex).__name__}",
            )
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=load_profile_daemons profile={profile_name} err={type(ex).__name__}",
            )
            return

        self._set_mayak_mode_from_profile(profile_cfg, profile_name)

        root = profile_cfg.get(profile_name)
        if not isinstance(root, dict):
            emit_log(self._bus, "ERROR", "system", "SYSTEM_DAEMONS_START_FAIL", "error=profile_root_invalid")
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", "stage=profile_root_invalid")
            return

        services_cfg = root.get("services")
        if not isinstance(services_cfg, dict):
            emit_log(self._bus, "ERROR", "system", "SYSTEM_DAEMONS_START_FAIL", "error=services_missing")
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", "stage=services_missing")
            return

        _jobs, daemons = self._compute_roles(profile_cfg, profile_name)
        daemon_names = list(daemons)

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "ORCH_DAEMONS_START",
            f"count={len(daemon_names)} daemons={','.join(sorted(daemon_names))}",
        )

        services_map = self._sm.get_services()

        already_running = 0

        for name in daemon_names:
            if name == "mayak_spindle" and self._is_mayak_stub_mode():
                emit_log(self._bus, "INFO", "orchestrator", "ORCH_MAYAK_STUB_ACTIVE", "service=mayak_spindle")
                continue
            svc = services_map.get(name)
            if svc is None:
                continue

            if self._is_service_running(name):
                already_running += 1
                continue

            service_section = services_cfg.get(name, {})

            try:
                svc.start(service_section)
            except Exception as ex:
                # Per-service failure: log as SYSTEM_* and continue
                emit_log(
                    self._bus,
                    "ERROR",
                    "system",
                    "SYSTEM_DAEMONS_START_FAIL",
                    f"service={name} error={type(ex).__name__}",
                )
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=daemon_start service={name} err={type(ex).__name__}",
                )
                # non-fatal

        if already_running:
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "ORCH_DAEMONS_ALREADY_RUNNING",
                f"count={already_running}",
            )

    def stop(self) -> None:
        """
        v4: stop affects only job services from current run-set.
        Daemons keep running; their STOPPED/ERROR must not block STOPPING.
        """
        with self._lock:
            if self._state == OrchestratorState.IDLE:
                return
            if self._state == OrchestratorState.STOPPING:
                return
            if self._state != OrchestratorState.RUNNING:
                # conservative no-op
                return

            run_jobs = set(self._run_jobs)

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_STOP_REQUEST", "req=1")
        self._set_state(OrchestratorState.STOPPING)

        services_map = self._sm.get_services()

        # Synthetic STOPPING log only for jobs (daemons are not being stopped)
        for name in sorted(run_jobs):
            if name in services_map:
                emit_log(self._bus, "INFO", "orchestrator", "SERVICE_STATUS", f"service={name} status=STOPPING")

        # Stop jobs only (best-effort)
        stopped_req = 0
        for name in run_jobs:
            svc = services_map.get(name)
            if svc is None:
                continue
            try:
                svc.stop()
                stopped_req += 1
            except Exception as ex:
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=job_stop service={name} err={type(ex).__name__}",
                )
                self._set_state(OrchestratorState.ERROR)
                return

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_JOBS_STOP", f"count={stopped_req}")

        self._start_stop_waiter()

    # -------------------- Mayak thin command API --------------------

    def set_speed(self, spindle: str, rpm: int, direction: int) -> None:
        """
        Thin proxy to mayak service command API.
        No UI/business logic here: validate minimal shape, route command, log result.
        """
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "MAYAK_CMD",
            f"cmd=set_speed spindle={spindle} rpm={rpm} direction={direction}",
        )
        svc = self._resolve_mayak_service()
        fn = getattr(svc, "set_spindle_speed", None)
        if not callable(fn):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=set_speed err=unsupported_method",
            )
            raise RuntimeError("mayak_spindle does not support set_spindle_speed")

        try:
            fn(str(spindle), direction=int(direction), rpm=int(rpm))
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "MAYAK_CMD",
                f"cmd=set_speed status=ok spindle={spindle}",
            )
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=mayak_cmd cmd=set_speed err={type(ex).__name__}",
            )
            raise

    def stop_spindle(self, spindle: str) -> None:
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "MAYAK_CMD",
            f"cmd=stop_spindle spindle={spindle}",
        )
        svc = self._resolve_mayak_service()
        fn = getattr(svc, "stop_spindle", None)
        if not callable(fn):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=stop_spindle err=unsupported_method",
            )
            raise RuntimeError("mayak_spindle does not support stop_spindle")

        try:
            fn(str(spindle))
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "MAYAK_CMD",
                f"cmd=stop_spindle status=ok spindle={spindle}",
            )
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=mayak_cmd cmd=stop_spindle err={type(ex).__name__}",
            )
            raise

    def emergency_stop(self) -> None:
        emit_log(self._bus, "INFO", "orchestrator", "MAYAK_CMD", "cmd=emergency_stop")
        svc = self._resolve_mayak_service()

        fn = getattr(svc, "emergency_stop", None)
        if not callable(fn):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=emergency_stop err=unsupported_method",
            )
            raise RuntimeError("mayak_spindle does not support emergency_stop")

        try:
            fn()
            emit_log(self._bus, "INFO", "orchestrator", "MAYAK_CMD", "cmd=emergency_stop status=ok")
            scenario_id = self._active_scenario_id_or_none()
            emit_log(
                self._bus,
                "WARNING",
                "orchestrator",
                "MAYAK_TEST_ABORT",
                f"scenario_id={scenario_id} reason=emergency_stop",
            )
            self._clear_active_scenario()
            self._set_phase(OrchestratorPhase.PREPARED, "reason=emergency_stop")
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=mayak_cmd cmd=emergency_stop err={type(ex).__name__}",
            )
            raise

    def apply_profile_linear(self, spindle: str, from_rpm: int, to_rpm: int, duration_sec: float) -> None:
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "MAYAK_CMD",
            f"cmd=apply_profile_linear spindle={spindle} from_rpm={from_rpm} to_rpm={to_rpm} duration_sec={duration_sec}",
        )
        svc = self._resolve_mayak_service()
        fn = getattr(svc, "apply_profile_linear", None)
        if not callable(fn):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=apply_profile_linear err=unsupported_method",
            )
            raise RuntimeError("mayak_spindle does not support apply_profile_linear")

        try:
            fn(
                str(spindle),
                from_rpm=int(from_rpm),
                to_rpm=int(to_rpm),
                duration_sec=float(duration_sec),
            )
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "MAYAK_CMD",
                f"cmd=apply_profile_linear status=ok spindle={spindle}",
            )
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=mayak_cmd cmd=apply_profile_linear err={type(ex).__name__}",
            )
            raise

    def start_mayak_test(
        self,
        *,
        head_start_rpm: int,
        head_end_rpm: int,
        tail_start_rpm: int,
        tail_end_rpm: int,
        profile_type: str,
        duration_sec: float,
        sdr_options: dict[str, Any] | None = None,
    ) -> None:
        """
        Backward-compatible entrypoint.
        New flow: prepare scenario snapshot first, then start from prepared snapshot.
        """
        self.prepare_mayak_test(
            head_start_rpm=head_start_rpm,
            head_end_rpm=head_end_rpm,
            tail_start_rpm=tail_start_rpm,
            tail_end_rpm=tail_end_rpm,
            profile_type=profile_type,
            duration_sec=duration_sec,
            sdr_options=sdr_options,
        )
        self.start_prepared_mayak_test()

    def prepare_mayak_test(
        self,
        *,
        head_start_rpm: int,
        head_end_rpm: int,
        tail_start_rpm: int,
        tail_end_rpm: int,
        profile_type: str,
        duration_sec: float,
        sdr_options: dict[str, Any] | None = None,
    ) -> str:
        self._set_phase(OrchestratorPhase.PREPARING, "action=prepare_mayak_test")
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "MAYAK_CMD",
            (
                "cmd=start_test "
                f"profile={profile_type} duration_sec={duration_sec} "
                f"head_start={head_start_rpm} head_end={head_end_rpm} "
                f"tail_start={tail_start_rpm} tail_end={tail_end_rpm}"
            ),
        )

        if float(duration_sec) <= 0:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=start_test err=duration_invalid",
            )
            raise ValueError("duration_sec must be > 0")

        # Validate command support at prepare-time (fail fast).
        svc = self._resolve_mayak_service()
        fn = getattr(svc, "start_test", None)
        if not callable(fn):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=start_test err=unsupported_method",
            )
            raise RuntimeError("mayak_spindle does not support start_test")

        scenario_id = self._next_scenario_id()
        trajectory = self._find_latest_trajectory_artifact()
        prepared = {
            "scenario_id": scenario_id,
            "prepared_ts": time.time(),
            "source": "mayak_test",
            "trajectory": trajectory,
            "services_snapshot": self._snapshot_service_sections("default", ("mayak_spindle", "gps_sdr_sim")),
            "sdr_options": self._sanitize_sdr_options(sdr_options),
            "mayak": {
                "head_start_rpm": int(head_start_rpm),
                "head_end_rpm": int(head_end_rpm),
                "tail_start_rpm": int(tail_start_rpm),
                "tail_end_rpm": int(tail_end_rpm),
                "profile_type": str(profile_type),
                "duration_sec": float(duration_sec),
            },
        }

        manifest_path = self._write_scenario_manifest(prepared)
        prepared["manifest_path"] = str(manifest_path)
        with self._lock:
            self._prepared_scenario = dict(prepared)

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "SCENARIO_ID",
            f"scenario_id={scenario_id} source=mayak_test",
        )
        if trajectory is None:
            emit_log(
                self._bus,
                "WARNING",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=prepare_scenario scenario_id={scenario_id} warn=trajectory_not_found",
            )
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "MAYAK_TEST_PREPARED",
            f"scenario_id={scenario_id} manifest={manifest_path.as_posix()}",
        )
        self._set_phase(OrchestratorPhase.PREPARED, f"scenario_id={scenario_id}")
        return scenario_id

    def check_readiness(self) -> dict[str, Any]:
        """
        Stage-2 preflight skeleton:
          - validate prepared scenario artifact refs
          - generate a simple PlutoPlayer input artifact
          - check blocking readiness (Mayak + SDR inputs)
          - check camera readiness as warning-only
        """
        self._set_phase(OrchestratorPhase.MONITORING, "action=check_readiness")
        with self._lock:
            prepared = dict(self._prepared_scenario) if isinstance(self._prepared_scenario, dict) else None

        if prepared is None:
            self._set_phase(OrchestratorPhase.PHASE_ERROR, "reason=not_prepared")
            return {
                "ready_to_start": False,
                "blocking_errors": ["scenario_not_prepared"],
                "warnings": [],
                "artifacts": {},
            }

        blocking_errors: list[str] = []
        warnings: list[str] = []
        artifacts: dict[str, str] = {}
        scenario_id = str(prepared.get("scenario_id", "none"))

        # required trajectory artifact
        trajectory = prepared.get("trajectory")
        traj_path = trajectory.get("trajectory_csv") if isinstance(trajectory, dict) else None
        if not isinstance(traj_path, str) or not traj_path.strip() or not Path(traj_path).exists():
            blocking_errors.append("trajectory_missing")

        # required SDR nav file
        sdr = prepared.get("sdr_options")
        gps = sdr.get("gps_sdr_sim") if isinstance(sdr, dict) else None
        nav = gps.get("nav") if isinstance(gps, dict) else None
        if not isinstance(nav, str) or not nav.strip() or not Path(nav).exists():
            blocking_errors.append("gps_nav_missing")

        # mayak readiness is blocking
        try:
            svc = self._resolve_mayak_service()
            fn = getattr(svc, "is_ready", None)
            if callable(fn):
                if not bool(fn()):
                    blocking_errors.append("mayak_not_ready")
            else:
                warnings.append("mayak_is_ready_unavailable")
        except Exception as ex:
            blocking_errors.append(f"mayak_check_failed:{type(ex).__name__}")

        # cameras are warning-only
        services_map = self._sm.get_services()
        for cam_name in ("video_visible", "video_thermal"):
            cam_ready = False
            svc = services_map.get(cam_name)
            fn = getattr(svc, "is_ready", None) if svc is not None else None
            if callable(fn):
                try:
                    cam_ready = bool(fn())
                except Exception:
                    cam_ready = False
            else:
                cam_ready = self._is_service_running(cam_name)
            if not cam_ready:
                warnings.append(f"{cam_name}_not_ready")

        # simple pluto input artifact generation
        try:
            pluto_path = self._write_pluto_input_artifact(prepared)
            artifacts["pluto_input"] = str(pluto_path)
        except Exception as ex:
            blocking_errors.append(f"pluto_input_failed:{type(ex).__name__}")

        # SDR readiness is blocking: short PlutoPlayer probe with cached short IQ.
        sdr_ok, sdr_detail = self._check_sdr_readiness(prepared)
        if not sdr_ok:
            blocking_errors.append("sdr_not_ready")
            if sdr_detail:
                warnings.append(f"sdr_probe:{sdr_detail}")

        ready = not blocking_errors
        if ready:
            self._set_phase(OrchestratorPhase.READY, f"scenario_id={scenario_id}")
        else:
            self._set_phase(OrchestratorPhase.PREPARED, f"scenario_id={scenario_id} ready=0")

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "ORCH_READINESS",
            (
                f"scenario_id={scenario_id} "
                f"ready={1 if ready else 0} "
                f"blocking={','.join(blocking_errors) if blocking_errors else 'none'} "
                f"warnings={','.join(warnings) if warnings else 'none'}"
            ),
        )
        return {
            "ready_to_start": ready,
            "blocking_errors": blocking_errors,
            "warnings": warnings,
            "artifacts": artifacts,
        }

    def start_test_flow(self) -> None:
        report = self.check_readiness()
        if not bool(report.get("ready_to_start")):
            raise RuntimeError("readiness_check_failed")
        self.start_prepared_mayak_test()

    def stop_test_flow(self) -> None:
        self.stop_mayak_test()

    def start_test_session_flow(self) -> dict[str, Any]:
        """
        Unified runtime flow for monitoring test session:
          readiness -> create session -> start video -> start GPS TX -> RUNNING
        """
        report = self.check_readiness()
        ready = bool(report.get("ready_to_start")) if isinstance(report, dict) else False
        if not ready:
            return {"started": False, "readiness": report}
        session = self.start_test_session()
        return {"started": True, "readiness": report, "session": session}

    def stop_test_session_flow(self) -> dict[str, str]:
        """
        Unified stop flow:
          stop trajectory ticker -> stop GPS TX -> stop video -> finalize manifest
        """
        return self.stop_test_session()

    def get_test_session_runtime_state(self) -> dict[str, Any]:
        with self._lock:
            active = self._active_test_session
            phase = self._phase.value
        if active is None:
            return {
                "active": False,
                "phase": phase,
                "session_id": None,
                "status": SessionStatus.STOPPED.value,
                "elapsed_sec": 0.0,
                "video": {"state": "not_running", "degraded": False, "channels": []},
                "gps_tx": {"state": "not_running", "pid": None, "exit_code": None},
                "trajectory_ticker": {"state": "not_running"},
                "degraded": False,
                "error": False,
            }

        elapsed = max(0.0, time.monotonic() - float(active.t0_monotonic))
        video = self._video_recorder.describe(active)
        gps = self._gps_tx_runner.describe(active)
        ticker = self._trajectory_ticker.describe(active)
        degraded = bool(video.get("degraded", False))
        error = active.status == SessionStatus.ERROR
        return {
            "active": True,
            "phase": phase,
            "session_id": active.session_id,
            "status": active.status.value,
            "elapsed_sec": elapsed,
            "video": video,
            "gps_tx": gps,
            "trajectory_ticker": ticker,
            "degraded": degraded,
            "error": error,
        }

    def start_test_session(self) -> dict[str, str]:
        """
        Start lightweight test session (no Mayak dependency).
        v1 writes only session manifest and events log.
        """
        with self._lock:
            if self._active_test_session is not None:
                raise RuntimeError("test_session_already_running")
            prepared = dict(self._prepared_scenario) if isinstance(self._prepared_scenario, dict) else None

        if prepared is None:
            raise RuntimeError("scenario_not_prepared")

        session_id = self._next_test_session_id()
        scenario_id = str(prepared.get("scenario_id", "none"))
        out_dir = (Path("outputs") / "sessions" / session_id).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        events_path = out_dir / "events.log"
        manifest_path = out_dir / "session_manifest.json"

        t0_unix = time.time()
        t0_mono = time.monotonic()

        runtime = SessionRuntime(
            session_id=session_id,
            scenario_id=scenario_id,
            t0_unix=t0_unix,
            t0_monotonic=t0_mono,
            status=SessionStatus.CREATED,
            paths={
                "out_dir": str(out_dir),
                "events_log": str(events_path),
                "manifest": str(manifest_path),
            },
        )
        runtime.handles["prepared_scenario"] = prepared
        runtime.handles["gps_tx_cfg"] = self._build_session_gps_tx_config(prepared)
        self._write_session_manifest(runtime)

        runtime.status = SessionStatus.STARTING
        self._write_session_manifest(runtime)
        self._append_session_event(
            events_path=events_path,
            payload={
                "event": "SESSION_START",
                "session_id": session_id,
                "scenario_id": scenario_id,
                "unix_ts": t0_unix,
                "t_rel_sec": 0.0,
            },
        )
        started_ticker = False
        started_video = False
        started_gps = False
        start_stage = "init"
        try:
            start_stage = "start_timeline"
            timeline_path = self._build_session_trajectory_timeline(runtime, prepared)
            runtime.paths["trajectory_timeline"] = str(timeline_path)
            runtime.handles["trajectory_timeline_meta"] = {
                "points": int(runtime.handles.get("trajectory_points_count", 0)),
                "duration_sec": float(runtime.handles.get("trajectory_duration_sec", 0.0)),
            }
            self._append_session_event(
                events_path=events_path,
                payload={
                    "event": "SESSION_TRAJECTORY_TIMELINE_READY",
                    "session_id": session_id,
                    "scenario_id": scenario_id,
                    "unix_ts": time.time(),
                    "t_rel_sec": max(0.0, time.monotonic() - t0_mono),
                    "details": (
                        f"path={timeline_path.as_posix()} "
                        f"points={int(runtime.handles.get('trajectory_points_count', 0))} "
                        f"duration_sec={float(runtime.handles.get('trajectory_duration_sec', 0.0)):.3f}"
                    ),
                },
            )
            self._trajectory_ticker.start(runtime)
            started_ticker = True
            self._write_session_manifest(runtime)

            start_stage = "start_video"
            self._video_recorder.record_for_session(runtime)
            started_video = True
            self._write_session_manifest(runtime)

            start_stage = "start_gps_tx"
            self._gps_tx_runner.start(runtime)
            started_gps = True
            self._write_session_manifest(runtime)
        except Exception as ex:
            rollback_errors: list[str] = []
            if started_gps:
                try:
                    self._gps_tx_runner.stop(runtime)
                except Exception as rollback_ex:
                    rollback_errors.append(f"rollback_gps:{type(rollback_ex).__name__}")
            if started_video:
                try:
                    self._video_recorder.stop_record_for_session(runtime)
                except Exception as rollback_ex:
                    rollback_errors.append(f"rollback_video:{type(rollback_ex).__name__}")
            if started_ticker:
                try:
                    self._trajectory_ticker.stop(runtime)
                except Exception as rollback_ex:
                    rollback_errors.append(f"rollback_ticker:{type(rollback_ex).__name__}")

            runtime.status = SessionStatus.ERROR
            runtime.handles.pop("gps_tx_proc", None)
            self._write_session_manifest(runtime)
            detail = f"stage={start_stage} err={type(ex).__name__}"
            if rollback_errors:
                detail = f"{detail} rollback={','.join(rollback_errors)}"
            self._append_session_event(
                events_path=events_path,
                payload={
                    "event": "SESSION_ERROR",
                    "session_id": session_id,
                    "scenario_id": scenario_id,
                    "unix_ts": time.time(),
                    "t_rel_sec": max(0.0, time.monotonic() - t0_mono),
                    "details": detail,
                },
            )
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SESSION_ERROR",
                f"session_id={session_id} scenario_id={scenario_id} {detail}",
            )
            raise

        runtime.status = SessionStatus.RUNNING
        self._write_session_manifest(runtime)

        with self._lock:
            self._active_test_session = runtime

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "SESSION_START",
            f"session_id={session_id} scenario_id={scenario_id}",
        )
        self._set_phase(OrchestratorPhase.TEST_RUNNING, f"session_id={session_id}")
        return {
            "session_id": session_id,
            "out_dir": str(out_dir),
            "manifest": str(manifest_path),
            "events": str(events_path),
            "trajectory_timeline": str(Path(runtime.paths.get("trajectory_timeline", ""))),
        }

    def stop_test_session(self) -> dict[str, str]:
        with self._lock:
            active = self._active_test_session

        if active is None:
            raise RuntimeError("test_session_not_running")

        session_id = active.session_id
        scenario_id = active.scenario_id
        t0_mono = float(active.t0_monotonic)
        t1_unix = time.time()
        duration_sec = max(0.0, time.monotonic() - t0_mono)

        events_path = Path(active.paths.get("events_log", ""))
        manifest_path = Path(active.paths.get("manifest", ""))
        active.status = SessionStatus.STOPPING
        active.t1_unix = t1_unix
        active.duration_sec = duration_sec
        self._write_session_manifest(active)

        stop_errors: list[str] = []
        try:
            self._trajectory_ticker.stop(active)
        except Exception as ex:
            stop_errors.append(f"stop_trajectory_ticker:{type(ex).__name__}")
            active.status = SessionStatus.ERROR
            self._write_session_manifest(active)
            self._append_session_event(
                events_path=events_path,
                payload={
                    "event": "SESSION_ERROR",
                    "session_id": session_id,
                    "scenario_id": scenario_id,
                    "unix_ts": time.time(),
                    "t_rel_sec": duration_sec,
                    "details": f"stage=stop_trajectory_ticker err={type(ex).__name__}",
                },
            )
        try:
            self._gps_tx_runner.stop(active)
        except Exception as ex:
            stop_errors.append(f"stop_gps_tx:{type(ex).__name__}")
            active.status = SessionStatus.ERROR
            self._write_session_manifest(active)
            self._append_session_event(
                events_path=events_path,
                payload={
                    "event": "SESSION_ERROR",
                    "session_id": session_id,
                    "scenario_id": scenario_id,
                    "unix_ts": time.time(),
                    "t_rel_sec": duration_sec,
                    "details": f"stage=stop_gps_tx err={type(ex).__name__}",
                },
            )
        try:
            self._video_recorder.stop_record_for_session(active)
        except Exception as ex:
            stop_errors.append(f"stop_video:{type(ex).__name__}")
            active.status = SessionStatus.ERROR
            self._write_session_manifest(active)
            self._append_session_event(
                events_path=events_path,
                payload={
                    "event": "SESSION_ERROR",
                    "session_id": session_id,
                    "scenario_id": scenario_id,
                    "unix_ts": time.time(),
                    "t_rel_sec": duration_sec,
                    "details": f"stage=stop_video err={type(ex).__name__}",
                },
            )

        self._append_session_event(
            events_path=events_path,
            payload={
                "event": "SESSION_STOP",
                "session_id": session_id,
                "scenario_id": scenario_id,
                "unix_ts": t1_unix,
                "t_rel_sec": duration_sec,
            },
        )

        if not stop_errors:
            active.status = SessionStatus.STOPPED
        self._write_session_manifest(active)

        with self._lock:
            self._active_test_session = None

        if not stop_errors:
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "SESSION_STOP",
                f"session_id={session_id} scenario_id={scenario_id} duration_sec={duration_sec:.3f}",
            )
        else:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SESSION_ERROR",
                f"session_id={session_id} scenario_id={scenario_id} stage=stop err={','.join(stop_errors)}",
            )
        self._set_phase(OrchestratorPhase.PREPARED, f"session_id={session_id}")
        if stop_errors:
            raise RuntimeError(f"stop_session_failed:{','.join(stop_errors)}")
        return {
            "session_id": session_id,
            "manifest": str(manifest_path),
            "events": str(events_path),
        }

    def generate_gps_signal_preflight(
        self,
        *,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> dict[str, str]:
        """
        Generate GPS preflight artifacts (NMEA + IQ) without Pluto TX.
        Uses prepared scenario snapshot and current SDR options.
        """
        with self._lock:
            prepared = dict(self._prepared_scenario) if isinstance(self._prepared_scenario, dict) else None

        if prepared is None:
            raise RuntimeError("scenario_not_prepared")

        scenario_id = str(prepared.get("scenario_id", "scn_unknown"))
        trajectory = prepared.get("trajectory")
        traj_csv = trajectory.get("trajectory_csv") if isinstance(trajectory, dict) else None
        if not isinstance(traj_csv, str) or not traj_csv.strip() or not Path(traj_csv).exists():
            raise FileNotFoundError("trajectory_missing")

        sdr = prepared.get("sdr_options")
        gps = sdr.get("gps_sdr_sim") if isinstance(sdr, dict) else {}
        if not isinstance(gps, dict):
            gps = {}

        nav = str(gps.get("nav", "") or "").strip()
        if not nav:
            raise ValueError("gps_nav_missing")
        nav_path = Path(nav).resolve()
        if not nav_path.exists():
            raise FileNotFoundError(f"gps_nav_not_found={nav_path.as_posix()}")

        static_sec = float(gps.get("static_sec", 0.0) or 0.0)
        static_sec = max(0.0, static_sec)
        origin_lat = float(gps.get("origin_lat", 55.7558) or 55.7558)
        origin_lon = float(gps.get("origin_lon", 37.6176) or 37.6176)
        origin_h = float(gps.get("origin_h", 156.0) or 156.0)

        services_snapshot = prepared.get("services_snapshot")
        gps_service = services_snapshot.get("gps_sdr_sim") if isinstance(services_snapshot, dict) else {}
        if not isinstance(gps_service, dict):
            gps_service = {}

        exe = self._resolve_gps_sdr_sim_executable(gps_service, gps)
        bit_depth = int(gps_service.get("bit_depth", 16) or 16)
        if bit_depth not in (8, 16):
            bit_depth = 16
        timeout_sec = int(gps_service.get("gps_timeout_sec", 120) or 120)
        if timeout_sec <= 0:
            timeout_sec = 120
        extra_args = str(gps_service.get("gps_extra_args", "") or "")

        out_dir = (Path("outputs") / "scenarios" / scenario_id / "gps_preflight").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        nmea_txt = out_dir / "nmea_strings.txt"
        iq_bin = out_dir / "gpssim_iq.bin"
        cmdline_txt = out_dir / "gps_sdr_sim.cmdline.txt"

        if progress_cb is not None:
            progress_cb(45, "Генерация NMEA")
        meta = prepare_nmea_input(
            input_trajectory_csv=Path(traj_csv),
            out_nmea_txt=nmea_txt,
            origin_lat_deg=origin_lat,
            origin_lon_deg=origin_lon,
            origin_h_m=origin_h,
            static_sec=static_sec,
        )

        nav_local = out_dir / nav_path.name
        if nav_local.resolve() != nav_path:
            nav_local.write_bytes(nav_path.read_bytes())

        cmd = [exe, "-e", nav_local.name, "-g", nmea_txt.name, "-b", str(bit_depth), "-o", iq_bin.name]
        if extra_args:
            cmd += shlex.split(extra_args)
        cmdline_txt.write_text(" ".join(cmd), encoding="utf-8")

        if progress_cb is not None:
            progress_cb(70, "Генерация IQ")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(out_dir),
                capture_output=True,
                text=True,
                timeout=float(timeout_sec),
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as ex:
            raise FileNotFoundError(f"gps_sdr_sim_exe_not_found={exe}") from ex
        except subprocess.TimeoutExpired as ex:
            raise RuntimeError(f"gps_iq_timeout={timeout_sec}") from ex

        if proc.returncode != 0:
            stderr_short = (proc.stderr or "").strip().splitlines()
            tail = stderr_short[-1] if stderr_short else "unknown_error"
            raise RuntimeError(f"gps_iq_failed rc={proc.returncode} err={tail}")

        if not iq_bin.exists() or iq_bin.stat().st_size <= 0:
            raise RuntimeError("gps_iq_missing")

        meta_path = out_dir / "gps_preflight_meta.json"
        payload = {
            "scenario_id": scenario_id,
            "created_ts": time.time(),
            "nmea": str(nmea_txt),
            "iq": str(iq_bin),
            "cmdline": str(cmdline_txt),
            "static_sec": static_sec,
            "meta": meta,
        }
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "ORCH_GPS_PREFLIGHT_DONE",
            f"scenario_id={scenario_id} nmea={nmea_txt.as_posix()} iq={iq_bin.as_posix()} static_sec={static_sec}",
        )
        if progress_cb is not None:
            progress_cb(95, "GPS preflight завершен")

        return {
            "run_dir": str(out_dir),
            "nmea": str(nmea_txt),
            "iq": str(iq_bin),
            "meta": str(meta_path),
            "cmdline": str(cmdline_txt),
        }

    def start_prepared_mayak_test(self) -> None:
        with self._lock:
            prepared = dict(self._prepared_scenario) if self._prepared_scenario is not None else None

        if prepared is None:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=start_prepared err=not_prepared",
            )
            raise RuntimeError("scenario is not prepared")

        scenario_id = str(prepared.get("scenario_id", ""))
        mayak = prepared.get("mayak")
        if not isinstance(mayak, dict):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=mayak_cmd cmd=start_prepared scenario_id={scenario_id} err=invalid_snapshot",
            )
            raise RuntimeError("prepared scenario is invalid")

        svc = self._resolve_mayak_service()
        fn = getattr(svc, "start_test", None)
        if not callable(fn):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=start_test err=unsupported_method",
            )
            raise RuntimeError("mayak_spindle does not support start_test")

        with self._lock:
            self._active_scenario_id = scenario_id

        try:
            fn(
                head_start_rpm=int(mayak.get("head_start_rpm", 0)),
                head_end_rpm=int(mayak.get("head_end_rpm", 0)),
                tail_start_rpm=int(mayak.get("tail_start_rpm", 0)),
                tail_end_rpm=int(mayak.get("tail_end_rpm", 0)),
                profile_type=str(mayak.get("profile_type", "linear")),
                duration_sec=float(mayak.get("duration_sec", 0.0)),
            )
            emit_log(self._bus, "INFO", "orchestrator", "MAYAK_CMD", "cmd=start_test status=ok")
            trajectory = prepared.get("trajectory")
            traj_path = trajectory.get("trajectory_csv") if isinstance(trajectory, dict) else None
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "MAYAK_TEST_START",
                (
                    f"scenario_id={scenario_id} profile={str(mayak.get('profile_type', 'linear'))} "
                    f"duration_sec={float(mayak.get('duration_sec', 0.0)):.3f} "
                    f"head_start={int(mayak.get('head_start_rpm', 0))} head_end={int(mayak.get('head_end_rpm', 0))} "
                    f"tail_start={int(mayak.get('tail_start_rpm', 0))} tail_end={int(mayak.get('tail_end_rpm', 0))} "
                    f"trajectory={traj_path or 'none'}"
                ),
            )
            self._set_phase(OrchestratorPhase.TEST_RUNNING, f"scenario_id={scenario_id}")
            with self._lock:
                self._prepared_scenario = None
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=mayak_cmd cmd=start_test err={type(ex).__name__}",
            )
            self._clear_scenario_if_matches(scenario_id)
            raise

    def stop_mayak_test(self) -> None:
        emit_log(self._bus, "INFO", "orchestrator", "MAYAK_CMD", "cmd=stop_test")
        svc = self._resolve_mayak_service()
        fn = getattr(svc, "stop_test", None)
        if not callable(fn):
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd cmd=stop_test err=unsupported_method",
            )
            raise RuntimeError("mayak_spindle does not support stop_test")
        try:
            fn()
            emit_log(self._bus, "INFO", "orchestrator", "MAYAK_CMD", "cmd=stop_test status=ok")
            scenario_id = self._active_scenario_id_or_none()
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "MAYAK_TEST_STOP",
                f"scenario_id={scenario_id}",
            )
            self._clear_active_scenario()
            self._set_phase(OrchestratorPhase.PREPARED, f"scenario_id={scenario_id}")
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                f"stage=mayak_cmd cmd=stop_test err={type(ex).__name__}",
            )
            raise

    # -------------------- Service status tracking --------------------

    def _on_service_status_event(self, e: ServiceStatusEvent) -> None:
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "ORCH_ON_SERVICE_STATUS",
            f"state={self.state.value} service={e.service_name} status={e.status}",
        )

        try:
            st = ServiceStatus(e.status)
        except Exception:
            st = ServiceStatus.ERROR

        finish_to: OrchestratorState | None = None
        pending_jobs_csv: str | None = None
        should_log_jobs_done = False

        with self._lock:
            self._service_status[e.service_name] = st
            cur_state = self._state
            run_jobs = set(self._run_jobs)

            # only jobs affect run-cycle completion / failure
            if e.service_name in run_jobs:
                if st == ServiceStatus.ERROR:
                    finish_to = OrchestratorState.ERROR
                else:
                    pending = sorted([j for j in run_jobs if self._service_status.get(j) != ServiceStatus.STOPPED])
                    pending_jobs_csv = ",".join(pending)
                    should_log_jobs_done = True

                    if cur_state == OrchestratorState.RUNNING and not pending:
                        finish_to = OrchestratorState.IDLE
            else:
                # daemon status never completes a run by itself
                pass

        emit_log(self._bus, "INFO", "orchestrator", "SERVICE_STATUS", f"service={e.service_name} status={st.value}")
        if e.service_name == "mayak_spindle":
            scenario_id = self._active_scenario_id_or_none()
            if scenario_id != "none":
                emit_log(
                    self._bus,
                    "INFO",
                    "orchestrator",
                    "SCENARIO_STATUS",
                    f"scenario_id={scenario_id} kind=service service={e.service_name} status={st.value} orch_state={self.state.value}",
                )

        if should_log_jobs_done and self.state in (OrchestratorState.PRECHECK, OrchestratorState.RUNNING, OrchestratorState.STOPPING):
            emit_log(self._bus, "INFO", "orchestrator", "ORCH_JOBS_DONE", f"pending={pending_jobs_csv or ''}")

        if finish_to is not None:
            emit_log(self._bus, "INFO", "orchestrator", "ORCH_RUN_FINISHED", f"to={finish_to.value}")
            self._set_state(finish_to)

        self._status_changed.set()

    # -------------------- STOPPING synchronization --------------------

    def _start_stop_waiter(self) -> None:
        with self._lock:
            if self._stop_wait_thread is not None and self._stop_wait_thread.is_alive():
                self._stop_wait_cancel.set()

            self._stop_wait_cancel = threading.Event()
            self._status_changed.clear()

            t = threading.Thread(target=self._stop_wait_worker, name="Orchestrator.stop_wait", daemon=True)
            self._stop_wait_thread = t
            t.start()

    def _stop_wait_worker(self) -> None:
        with self._lock:
            timeout_sec = int(self._stop_timeout_sec)
            jobs = set(self._run_jobs)
        deadline = time.monotonic() + max(1, timeout_sec)

        # Wait only for jobs (daemons are ignored in STOPPING sync)
        service_names = set(jobs)

        while True:
            if self._stop_wait_cancel.is_set():
                return

            with self._lock:
                if self._state != OrchestratorState.STOPPING:
                    return
                snapshot = {k: self._service_status.get(k) for k in service_names}

            # ERROR only for jobs
            errored = sorted([name for name, st in snapshot.items() if st == ServiceStatus.ERROR])
            if errored:
                emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", f"errored={','.join(errored)}")
                self._set_state(OrchestratorState.ERROR)
                return

            pending = sorted([name for name, st in snapshot.items() if st is None or st != ServiceStatus.STOPPED])
            if not pending:
                self._set_state(OrchestratorState.IDLE)
                return

            now = time.monotonic()
            if now >= deadline:
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"pending={','.join(pending)} timeout_sec={timeout_sec}",
                )
                self._set_state(OrchestratorState.ERROR)
                return

            remaining = max(0.0, deadline - now)
            self._status_changed.wait(timeout=min(0.2, remaining))
            self._status_changed.clear()

    # -------------------- profile helpers --------------------
    def _load_profile_with_overrides(self, profile_name: str, overrides: dict | None) -> dict:
        """
        Load profile using loader + apply overrides in-memory (deep merge),
        same approach as start() (but without any state changes).
        """
        profile_cfg = load_profile(profile_name)

        if overrides is not None:
            if not isinstance(overrides, dict):
                raise TypeError("validate_overrides")

            # profile_cfg in your project is usually: {profile_name: {...}}
            try:
                if isinstance(profile_cfg, dict) and isinstance(profile_cfg.get(profile_name), dict):
                    deep_merge(profile_cfg[profile_name], overrides)
                elif isinstance(profile_cfg, dict):
                    deep_merge(profile_cfg, overrides)
                else:
                    raise TypeError("profile_cfg must be dict")
            except Exception as ex:
                raise TypeError("apply_overrides") from ex

            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "ORCH_PROFILE_OVERRIDES_APPLIED",
                f"keys={count_leaf_values(overrides)}",
            )

        # Minimal cfg-check (keep it simple but fail fast).
        root = None
        if isinstance(profile_cfg, dict) and isinstance(profile_cfg.get(profile_name), dict):
            root = profile_cfg[profile_name]
        elif isinstance(profile_cfg, dict):
            root = profile_cfg

        services = root.get("services") if isinstance(root, dict) else None
        if not isinstance(services, dict):
            raise ValueError("cfg_check services_missing")

        bm = services.get("ballistics_model")
        if bm is not None and not isinstance(bm, dict):
            raise ValueError("cfg_check ballistics_model_not_dict")

        if not self._validate_roles(profile_cfg, profile_name):
            raise ValueError("cfg_check service_roles_invalid")

        return profile_cfg

    def _apply_stop_timeout_from_profile(self, profile_cfg: dict, profile_name: str) -> None:
        default_timeout = 10
        root = profile_cfg.get(profile_name, {}) if isinstance(profile_cfg, dict) else {}
        orch = root.get("orchestrator", {}) if isinstance(root, dict) else {}
        val = orch.get("stop_timeout_sec") if isinstance(orch, dict) else None

        if isinstance(val, int) and val > 0:
            with self._lock:
                self._stop_timeout_sec = val
        else:
            with self._lock:
                self._stop_timeout_sec = default_timeout
            emit_log(self._bus, "WARNING", "orchestrator", "SERVICE_ERROR", f"param=stop_timeout_sec default={default_timeout}")

    def _compute_roles(self, profile_cfg: dict, profile_name: str) -> tuple[list[str], list[str]]:
        jobs: list[str] = []
        daemons: list[str] = []

        root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
        root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else None)
        services = root.get("services") if isinstance(root, dict) else None
        if not isinstance(services, dict):
            return jobs, daemons

        for svc_name, svc_cfg in services.items():
            role = "job"
            if isinstance(svc_cfg, dict):
                role = str(svc_cfg.get("role", "job") or "job")
            if role == "daemon":
                daemons.append(svc_name)
            else:
                jobs.append(svc_name)

        return jobs, daemons

    def _validate_roles(self, profile_cfg: dict, profile_name: str) -> bool:
        root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
        root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else None)
        services = root.get("services") if isinstance(root, dict) else None
        if not isinstance(services, dict):
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", "stage=cfg_check err=services_missing")
            return False

        for svc_name, svc_cfg in services.items():
            if svc_cfg is None:
                continue
            if not isinstance(svc_cfg, dict):
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=cfg_check err=service_not_mapping service={svc_name}",
                )
                return False

            role = svc_cfg.get("role", "job")
            if role is None:
                role = "job"
            if not isinstance(role, str) or role not in ("job", "daemon"):
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=cfg_check err=service_role_invalid service={svc_name} role={role}",
                )
                return False

        return True

    def _is_service_running(self, service_name: str) -> bool:
        with self._lock:
            return self._service_status.get(service_name) == ServiceStatus.RUNNING

    def _resolve_mayak_service(self) -> Any:
        services_map = self._sm.get_services()
        svc = resolve_mayak_controller(mode=self._mayak_mode, services_map=services_map, stub=self._mayak_stub)
        if svc is None:
            emit_log(
                self._bus,
                "ERROR",
                "orchestrator",
                "SERVICE_ERROR",
                "stage=mayak_cmd err=service_missing service=mayak_spindle",
            )
            raise RuntimeError("mayak_spindle service is not registered")
        return svc

    def _check_mayak_readiness_before_jobs(
        self,
        services_map: dict[str, object],
        profile_cfg: dict,
        profile_name: str,
    ) -> bool:
        if self._is_mayak_stub_mode():
            emit_log(self._bus, "INFO", "orchestrator", "ORCH_PRECHECK_OK", "service=mayak_spindle mode=stub ready=1")
            return True

        svc = services_map.get("mayak_spindle")
        if svc is None:
            return True

        is_ready = getattr(svc, "is_ready", None)
        if not callable(is_ready):
            return True

        root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
        root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else {})
        orch = root.get("orchestrator", {}) if isinstance(root, dict) else {}
        timeout_val = orch.get("mayak_ready_timeout_sec") if isinstance(orch, dict) else None
        timeout_sec = float(timeout_val) if isinstance(timeout_val, (int, float)) and float(timeout_val) > 0 else 2.0
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                if bool(is_ready()):
                    emit_log(self._bus, "INFO", "orchestrator", "ORCH_PRECHECK_OK", "service=mayak_spindle ready=1")
                    return True
            except Exception as ex:
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=precheck service=mayak_spindle err={type(ex).__name__}",
                )
                return False
            time.sleep(0.05)

        emit_log(
            self._bus,
            "ERROR",
            "orchestrator",
            "SERVICE_ERROR",
            f"stage=precheck service=mayak_spindle err=not_ready timeout_sec={timeout_sec:.2f}",
        )
        return False

    def _should_require_mayak_ready(self, profile_cfg: dict, profile_name: str) -> bool:
        if self._is_mayak_stub_mode():
            return False

        root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
        root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else {})
        orch = root.get("orchestrator", {}) if isinstance(root, dict) else {}
        flag = orch.get("require_mayak_ready_for_jobs", True) if isinstance(orch, dict) else True
        return bool(flag)

    def _set_mayak_mode_from_profile(self, profile_cfg: dict, profile_name: str) -> None:
        self._mayak_mode = read_mayak_mode(profile_cfg, profile_name)
        emit_log(self._bus, "INFO", "orchestrator", "ORCH_MAYAK_MODE", f"mode={self._mayak_mode}")

    def _is_mayak_stub_mode(self) -> bool:
        return is_stub_mode(self._mayak_mode)

    # -------------------- internals --------------------

    def _set_state(self, st: OrchestratorState) -> None:
        with self._lock:
            if st == self._state:
                return
            prev = self._state
            self._state = st

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_STATE_CHANGE", f"from={prev.value} to={st.value}")
        scenario_id = self._active_scenario_id_or_none()
        if scenario_id != "none":
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "SCENARIO_STATUS",
                f"scenario_id={scenario_id} kind=orchestrator from={prev.value} to={st.value}",
            )
        self._publish_state(st)

    def _set_phase(self, ph: OrchestratorPhase, details: str = "") -> None:
        with self._lock:
            if ph == self._phase:
                return
            prev = self._phase
            self._phase = ph
        msg = f"from={prev.value} to={ph.value}"
        if details:
            msg = f"{msg} {details}"
        emit_log(self._bus, "INFO", "orchestrator", "ORCH_PHASE_CHANGE", msg)

    def _next_scenario_id(self) -> str:
        with self._lock:
            self._scenario_seq += 1
            return f"scn_{int(time.time() * 1000)}_{self._scenario_seq}"

    def _next_test_session_id(self) -> str:
        with self._lock:
            self._test_session_seq += 1
            return f"sess_{int(time.time() * 1000)}_{self._test_session_seq}"

    def _build_session_trajectory_timeline(self, runtime: SessionRuntime, prepared: dict[str, Any]) -> Path:
        trajectory = prepared.get("trajectory")
        traj_csv = trajectory.get("trajectory_csv") if isinstance(trajectory, dict) else None
        if not isinstance(traj_csv, str) or not traj_csv.strip():
            raise FileNotFoundError("trajectory_missing")

        src_path = Path(traj_csv).resolve()
        if not src_path.exists():
            raise FileNotFoundError(f"trajectory_not_found={src_path.as_posix()}")

        with src_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            headers = next(reader, None)
            if not headers:
                raise ValueError("trajectory_header_missing")

            norm = [str(x).strip().lower() for x in headers]
            for key in ("x", "y", "z", "t"):
                if key not in norm:
                    raise ValueError(f"trajectory_column_missing={key}")
            ix = norm.index("x")
            iy = norm.index("y")
            iz = norm.index("z")
            it = norm.index("t")

            points: list[tuple[float, float, float, float]] = []
            for row in reader:
                if not row:
                    continue
                if len(row) <= max(ix, iy, iz, it):
                    continue
                try:
                    t = float(row[it])
                    x = float(row[ix])
                    y = float(row[iy])
                    z = float(row[iz])
                except Exception:
                    continue
                points.append((t, x, y, z))

        if not points:
            raise ValueError("trajectory_no_valid_rows")

        t0_src = float(points[0][0])
        deltas: list[float] = []
        for i, (t, _x, _y, _z) in enumerate(points):
            dt = float(t) - t0_src
            if dt < -1e-6:
                raise ValueError(f"trajectory_negative_time_at={i}")
            if i > 0 and dt + 1e-6 < deltas[-1]:
                raise ValueError(f"trajectory_time_not_monotonic_at={i}")
            deltas.append(max(0.0, dt))

        start_rel = max(0.0, time.monotonic() - float(runtime.t0_monotonic))
        out_path = Path(runtime.paths["out_dir"]) / "trajectory_timeline.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        prev_xyz: tuple[float, float, float] | None = None
        prev_t_rel: float | None = None
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t_rel_sec", "x", "y", "z", "speed"])
            for i, (_t, x, y, z) in enumerate(points):
                t_rel = start_rel + deltas[i]
                if prev_t_rel is None:
                    speed = 0.0
                else:
                    dt = max(0.0, t_rel - prev_t_rel)
                    if dt <= 1e-9 or prev_xyz is None:
                        speed = 0.0
                    else:
                        dx = float(x) - prev_xyz[0]
                        dy = float(y) - prev_xyz[1]
                        dz = float(z) - prev_xyz[2]
                        speed = math.sqrt(dx * dx + dy * dy + dz * dz) / dt
                w.writerow([f"{t_rel:.3f}", f"{float(x):.6f}", f"{float(y):.6f}", f"{float(z):.6f}", f"{speed:.6f}"])
                prev_t_rel = t_rel
                prev_xyz = (float(x), float(y), float(z))

        total_duration = max(0.0, deltas[-1])
        runtime.handles["trajectory_points_count"] = len(points)
        runtime.handles["trajectory_duration_sec"] = total_duration
        if total_duration < 0.0:
            raise ValueError("trajectory_duration_invalid")
        return out_path

    def _append_session_event(self, *, events_path: Path, payload: dict[str, Any]) -> None:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False)
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _write_session_manifest(self, runtime: SessionRuntime) -> None:
        manifest_path = Path(runtime.paths.get("manifest", ""))
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": runtime.session_id,
            "scenario_id": runtime.scenario_id,
            "status": runtime.status.value,
            "t0_unix": runtime.t0_unix,
            "t0_monotonic": runtime.t0_monotonic,
            "t1_unix": runtime.t1_unix,
            "duration_sec": runtime.duration_sec,
            "paths": dict(runtime.paths),
            "handles": sorted(list(runtime.handles.keys())),
        }
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _begin_scenario_id(self) -> str:
        scenario_id = self._next_scenario_id()
        with self._lock:
            self._active_scenario_id = scenario_id
            return scenario_id

    def _find_latest_trajectory_artifact(self) -> Optional[dict[str, str]]:
        """
        Best-effort lookup of the latest generated ballistics trajectory.
        """
        root = Path("outputs") / "ballistics"
        if not root.exists():
            return None

        latest_csv: Optional[Path] = None
        latest_mtime: float = -1.0
        for csv_path in root.glob("*/trajectory.csv"):
            try:
                mtime = float(csv_path.stat().st_mtime)
            except Exception:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_csv = csv_path

        if latest_csv is None:
            return None

        run_dir = latest_csv.parent
        diagnostics = run_dir / "diagnostics.csv"
        return {
            "run_dir": str(run_dir.resolve()),
            "trajectory_csv": str(latest_csv.resolve()),
            "diagnostics_csv": str(diagnostics.resolve()) if diagnostics.exists() else "",
        }

    def _write_scenario_manifest(self, prepared: dict[str, Any]) -> Path:
        scenario_id = str(prepared.get("scenario_id", "scn_unknown"))
        out_dir = (Path("outputs") / "scenarios" / scenario_id).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "scenario_manifest.json"
        manifest_path.write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest_path

    def _sanitize_sdr_options(self, sdr_options: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(sdr_options, dict):
            return {
                "gps_sdr_sim": {
                    "nav": "",
                    "static_sec": 0.0,
                    "origin_lat": 55.7558,
                    "origin_lon": 37.6176,
                    "origin_h": 156.0,
                },
                "pluto_player": {"rf_bw_mhz": 3.0, "tx_atten_db": -20.0},
            }

        gps = sdr_options.get("gps_sdr_sim")
        pluto = sdr_options.get("pluto_player")

        gps_nav = ""
        gps_static = 0.0
        origin_lat = 55.7558
        origin_lon = 37.6176
        origin_h = 156.0
        if isinstance(gps, dict):
            nav = gps.get("nav")
            static_sec = gps.get("static_sec")
            lat = gps.get("origin_lat")
            lon = gps.get("origin_lon")
            h = gps.get("origin_h")
            if isinstance(nav, str):
                gps_nav = nav.strip()
            if isinstance(static_sec, (int, float)):
                gps_static = max(0.0, float(static_sec))
            if isinstance(lat, (int, float)):
                origin_lat = max(-90.0, min(90.0, float(lat)))
            if isinstance(lon, (int, float)):
                origin_lon = max(-180.0, min(180.0, float(lon)))
            if isinstance(h, (int, float)):
                origin_h = float(h)

        rf_bw = 3.0
        tx_att = -20.0
        if isinstance(pluto, dict):
            rf = pluto.get("rf_bw_mhz")
            att = pluto.get("tx_atten_db")
            if isinstance(rf, (int, float)):
                rf_bw = max(1.0, min(5.0, float(rf)))
            if isinstance(att, (int, float)):
                tx_att = max(-80.0, min(0.0, float(att)))

        return {
            "gps_sdr_sim": {
                "nav": gps_nav,
                "static_sec": gps_static,
                "origin_lat": origin_lat,
                "origin_lon": origin_lon,
                "origin_h": origin_h,
            },
            "pluto_player": {"rf_bw_mhz": rf_bw, "tx_atten_db": tx_att},
        }

    def _build_session_gps_tx_config(self, prepared: dict[str, Any]) -> dict[str, Any]:
        scenario_id = str(prepared.get("scenario_id", "scn_unknown"))
        iq_path = (Path("outputs") / "scenarios" / scenario_id / "gps_preflight" / "gpssim_iq.bin").resolve()
        if not iq_path.exists():
            raise FileNotFoundError(f"gps_preflight_iq_missing={iq_path.as_posix()}")

        sdr = prepared.get("sdr_options")
        gps = sdr.get("gps_sdr_sim") if isinstance(sdr, dict) else {}
        pluto = sdr.get("pluto_player") if isinstance(sdr, dict) else {}
        if not isinstance(gps, dict):
            gps = {}
        if not isinstance(pluto, dict):
            pluto = {}

        services_snapshot = prepared.get("services_snapshot")
        gps_service = services_snapshot.get("gps_sdr_sim") if isinstance(services_snapshot, dict) else {}
        if not isinstance(gps_service, dict):
            gps_service = {}

        pluto_exe = self._resolve_pluto_player_executable(gps_service, pluto)
        tx_atten_db = float(pluto.get("tx_atten_db", -20.0) or -20.0)
        rf_bw_mhz = float(pluto.get("rf_bw_mhz", 3.0) or 3.0)
        host = str(pluto.get("host", "") or pluto.get("ip", "") or "").strip()

        return {
            "pluto_exe": str(Path(pluto_exe).resolve()),
            "iq_path": str(iq_path),
            "tx_atten_db": tx_atten_db,
            "rf_bw_mhz": rf_bw_mhz,
            "host": host,
            "graceful_stop_timeout_sec": 3.0,
        }

    def _check_sdr_readiness(self, prepared: dict[str, Any]) -> tuple[bool, str]:
        """
        Blocking SDR check:
          1) ensure short probe IQ exists (cache; generate once if absent)
          2) run short PlutoPlayer transmission probe
        """
        sdr = prepared.get("sdr_options")
        gps = sdr.get("gps_sdr_sim") if isinstance(sdr, dict) else {}
        pluto = sdr.get("pluto_player") if isinstance(sdr, dict) else {}
        if not isinstance(gps, dict):
            gps = {}
        if not isinstance(pluto, dict):
            pluto = {}

        nav = str(gps.get("nav", "") or "").strip()
        if not nav:
            return False, "gps_nav_missing"
        nav_path = Path(nav).resolve()
        if not nav_path.exists():
            return False, f"gps_nav_not_found={nav_path.as_posix()}"

        services_snapshot = prepared.get("services_snapshot")
        gps_service = services_snapshot.get("gps_sdr_sim") if isinstance(services_snapshot, dict) else {}
        if not isinstance(gps_service, dict):
            gps_service = {}

        try:
            gps_exe = self._resolve_gps_sdr_sim_executable(gps_service, gps)
            pluto_exe = self._resolve_pluto_player_executable(gps_service, pluto)
        except Exception as ex:
            return False, str(ex)

        bit_depth = int(gps_service.get("bit_depth", 16) or 16)
        if bit_depth not in (8, 16):
            bit_depth = 16

        origin_lat = float(gps.get("origin_lat", 55.7558) or 55.7558)
        origin_lon = float(gps.get("origin_lon", 37.6176) or 37.6176)
        origin_h = float(gps.get("origin_h", 156.0) or 156.0)
        tx_atten_db = float(pluto.get("tx_atten_db", -20.0) or -20.0)
        rf_bw_mhz = float(pluto.get("rf_bw_mhz", 3.0) or 3.0)
        pluto_host = str(pluto.get("host", "") or pluto.get("ip", "") or "").strip()

        try:
            probe_iq = self._ensure_sdr_probe_iq(
                gps_exe=gps_exe,
                nav_path=nav_path,
                bit_depth=bit_depth,
                origin_lat=origin_lat,
                origin_lon=origin_lon,
                origin_h=origin_h,
            )
        except Exception as ex:
            return False, f"probe_iq_failed:{type(ex).__name__}:{ex}"

        ok, detail = self._run_pluto_probe(
            pluto_exe=pluto_exe,
            iq_path=probe_iq,
            tx_atten_db=tx_atten_db,
            rf_bw_mhz=rf_bw_mhz,
            host=pluto_host or None,
        )
        if (not ok) and isinstance(detail, str) and "failed_creating_iio_context" in detail:
            fallback_host = "192.168.2.1"
            if (not pluto_host) or (pluto_host != fallback_host):
                ok2, detail2 = self._run_pluto_probe(
                    pluto_exe=pluto_exe,
                    iq_path=probe_iq,
                    tx_atten_db=tx_atten_db,
                    rf_bw_mhz=rf_bw_mhz,
                    host=fallback_host,
                )
                if ok2:
                    emit_log(
                        self._bus,
                        "INFO",
                        "orchestrator",
                        "ORCH_SDR_PROBE_OK",
                        f"mode=retry_host host={fallback_host}",
                    )
                    return True, ""
                return False, detail2
        return (ok, detail)

    def _ensure_sdr_probe_iq(
        self,
        *,
        gps_exe: str,
        nav_path: Path,
        bit_depth: int,
        origin_lat: float,
        origin_lon: float,
        origin_h: float,
    ) -> Path:
        out_dir = (Path("outputs") / "gps_sdr_sim" / "probe_cache").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        traj_csv = out_dir / "probe_trajectory.csv"
        nmea_txt = out_dir / "probe_nmea.txt"
        iq_bin = out_dir / "probe_iq.bin"
        cmdline_txt = out_dir / "probe_gps_sdr_sim.cmdline.txt"
        gps_stdout = out_dir / "probe_gps_stdout.log"
        gps_stderr = out_dir / "probe_gps_stderr.log"

        if iq_bin.exists() and iq_bin.stat().st_size > 0:
            return iq_bin

        # Tiny static trajectory for deterministic short probe artifact.
        traj_csv.write_text("t,X,Y,Z\n0.0,0,0,0\n0.1,0,0,0\n", encoding="utf-8")
        prepare_nmea_input(
            input_trajectory_csv=traj_csv,
            out_nmea_txt=nmea_txt,
            origin_lat_deg=float(origin_lat),
            origin_lon_deg=float(origin_lon),
            origin_h_m=float(origin_h),
            static_sec=1.0,
        )

        nav_local = out_dir / nav_path.name
        nav_local.write_bytes(nav_path.read_bytes())

        cmd = [str(gps_exe), "-e", nav_local.name, "-g", nmea_txt.name, "-b", str(int(bit_depth)), "-o", iq_bin.name]
        cmdline_txt.write_text(" ".join(cmd), encoding="utf-8")
        proc = subprocess.run(
            cmd,
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            timeout=45.0,
            encoding="utf-8",
            errors="replace",
        )
        gps_stdout.write_text(proc.stdout or "", encoding="utf-8")
        gps_stderr.write_text(proc.stderr or "", encoding="utf-8")
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()
            msg = tail[-1] if tail else "unknown_error"
            raise RuntimeError(f"gps_probe_rc={proc.returncode} err={msg}")
        if not iq_bin.exists() or iq_bin.stat().st_size <= 0:
            raise RuntimeError("gps_probe_iq_missing")
        return iq_bin

    def _run_pluto_probe(
        self,
        *,
        pluto_exe: str,
        iq_path: Path,
        tx_atten_db: float,
        rf_bw_mhz: float,
        host: str | None = None,
    ) -> tuple[bool, str]:
        out_dir = iq_path.parent
        pluto_exe_path = Path(pluto_exe).resolve()
        iq_abs = iq_path.resolve()
        pluto_cwd = pluto_exe_path.parent
        stdout_path = out_dir / "probe_pluto_stdout.log"
        stderr_path = out_dir / "probe_pluto_stderr.log"
        cmdline_txt = out_dir / "probe_plutoplayer.cmdline.txt"
        cmd = [
            str(pluto_exe_path),
            "-t",
            str(iq_abs),
            "-a",
            f"{float(tx_atten_db):.2f}",
            "-b",
            f"{float(rf_bw_mhz):.2f}",
        ]
        host_txt = str(host or "").strip()
        if host_txt:
            cmd += ["-n", host_txt]
        cmdline_txt.write_text(" ".join(cmd), encoding="utf-8")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(pluto_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as ex:
            return False, f"pluto_start_failed:{type(ex).__name__}"

        def _classify_pluto_output(stdout_text: str, stderr_text: str) -> tuple[bool, str]:
            text = f"{stdout_text}\n{stderr_text}".lower()
            fail_tokens = (
                "failed creating iio context",
                "no such file or directory",
                "unable to create iio",
                "context error",
            )
            for token in fail_tokens:
                if token in text:
                    return False, f"pluto_probe_failed:{token.replace(' ', '_')}"

            success_tokens = (
                "transmit starts",
                "done.",
                "found 192.168.2.1",
                "plutosdr",
            )
            for token in success_tokens:
                if token in text:
                    return True, ""

            return False, "pluto_probe_inconclusive"

        try:
            # Pluto may print successful acquisition lines with noticeable delay.
            # Keep probe window long enough to avoid false "not ready" on slow bring-up.
            deadline = time.monotonic() + 8.0
            rc = proc.poll()
            while rc is None and time.monotonic() < deadline:
                time.sleep(0.1)
                rc = proc.poll()

            if rc is None:
                proc.terminate()
                try:
                    out, err = proc.communicate(timeout=3.0)
                except Exception:
                    proc.kill()
                    out, err = proc.communicate()
                stdout_path.write_text(out or "", encoding="utf-8")
                stderr_path.write_text(err or "", encoding="utf-8")
                ok, detail = _classify_pluto_output(out or "", err or "")
                if ok:
                    emit_log(self._bus, "INFO", "orchestrator", "ORCH_SDR_PROBE_OK", f"mode=hold iq={iq_path.as_posix()}")
                return ok, detail

            out, err = proc.communicate(timeout=1.0)
            stdout_path.write_text(out or "", encoding="utf-8")
            stderr_path.write_text(err or "", encoding="utf-8")
            ok, detail = _classify_pluto_output(out or "", err or "")
            if ok and int(rc) == 0:
                emit_log(self._bus, "INFO", "orchestrator", "ORCH_SDR_PROBE_OK", f"mode=fast_exit iq={iq_path.as_posix()}")
                return True, ""
            if (not ok) and int(rc) == 0 and detail == "pluto_probe_inconclusive":
                # Some PlutoPlayer builds may not flush logs under redirected stdio.
                # If process completed successfully and we did not detect explicit fail markers,
                # accept probe as ready to avoid false negatives.
                emit_log(
                    self._bus,
                    "WARNING",
                    "orchestrator",
                    "SERVICE_ERROR",
                    "stage=sdr_probe warn=pluto_rc0_no_markers",
                )
                return True, ""
            if not ok and int(rc) != 0 and detail == "pluto_probe_inconclusive":
                tail = (err or "").strip().splitlines()
                msg = tail[-1] if tail else f"rc={rc}"
                return False, f"pluto_probe_failed:{msg}"
            return ok, detail
        except Exception as ex:
            try:
                proc.kill()
                out, err = proc.communicate(timeout=1.0)
                stdout_path.write_text(out or "", encoding="utf-8")
                stderr_path.write_text(err or "", encoding="utf-8")
            except Exception:
                pass
            return False, f"pluto_probe_exception:{type(ex).__name__}"

    def _resolve_gps_sdr_sim_executable(self, gps_service: dict[str, Any], gps: dict[str, Any]) -> str:
        """
        Resolve gps-sdr-sim executable with explicit config first, then local bin, then PATH.
        """
        explicit = str(gps_service.get("gps_sdr_sim_exe") or gps.get("gps_sdr_sim_exe") or "").strip()
        local_candidates = [
            Path("bin") / "gps_sdr_sim" / "gps-sdr-sim.exe",
            Path("bin") / "gps_sdr_sim" / "gps-sdr-sim",
        ]
        names_from_path = ["gps-sdr-sim.exe", "gps-sdr-sim"]

        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        candidates.extend(str(p) for p in local_candidates)
        candidates.extend(names_from_path)

        checked: list[str] = []
        for raw in candidates:
            token = str(raw).strip()
            if not token:
                continue

            looks_like_path = any(sep in token for sep in ("/", "\\")) or Path(token).suffix.lower() == ".exe"
            if looks_like_path:
                p = Path(token).expanduser().resolve()
                checked.append(p.as_posix())
                if p.exists() and p.is_file():
                    return str(p)
                continue

            found = shutil.which(token)
            checked.append(found or f"PATH:{token}")
            if found:
                return str(Path(found).resolve())

        checked_txt = ",".join(checked) if checked else "none"
        raise FileNotFoundError(f"gps_sdr_sim_exe_not_found checked={checked_txt}")

    def _resolve_pluto_player_executable(self, gps_service: dict[str, Any], pluto: dict[str, Any]) -> str:
        explicit = str(gps_service.get("pluto_exe") or pluto.get("pluto_exe") or "").strip()
        local_candidates = [
            Path("bin") / "pluto" / "PlutoPlayer.exe",
            Path("bin") / "pluto" / "PlutoPlayer",
        ]
        names_from_path = ["PlutoPlayer.exe", "PlutoPlayer", "plutoplayer.exe", "plutoplayer"]

        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        candidates.extend(str(p) for p in local_candidates)
        candidates.extend(names_from_path)

        checked: list[str] = []
        for raw in candidates:
            token = str(raw).strip()
            if not token:
                continue
            looks_like_path = any(sep in token for sep in ("/", "\\")) or Path(token).suffix.lower() == ".exe"
            if looks_like_path:
                p = Path(token).expanduser().resolve()
                checked.append(p.as_posix())
                if p.exists() and p.is_file():
                    return str(p)
                continue

            found = shutil.which(token)
            checked.append(found or f"PATH:{token}")
            if found:
                return str(Path(found).resolve())

        checked_txt = ",".join(checked) if checked else "none"
        raise FileNotFoundError(f"pluto_exe_not_found checked={checked_txt}")

    def _snapshot_service_sections(self, profile_name: str, service_names: tuple[str, ...]) -> dict[str, Any]:
        """
        Best-effort immutable snapshot of selected profile service sections.
        """
        try:
            profile_cfg = load_profile(profile_name)
        except Exception:
            return {}

        root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
        root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else None)
        services = root.get("services") if isinstance(root, dict) else None
        if not isinstance(services, dict):
            return {}

        out: dict[str, Any] = {}
        for name in service_names:
            sec = services.get(name)
            if isinstance(sec, dict):
                try:
                    out[name] = json.loads(json.dumps(sec, ensure_ascii=False))
                except Exception:
                    out[name] = dict(sec)
        return out

    def _active_scenario_id_or_none(self) -> str:
        with self._lock:
            return self._active_scenario_id or "none"

    def _clear_scenario_if_matches(self, scenario_id: str) -> None:
        with self._lock:
            if self._active_scenario_id == scenario_id:
                self._active_scenario_id = None

    def _clear_active_scenario(self) -> None:
        with self._lock:
            self._active_scenario_id = None

    def _write_pluto_input_artifact(self, prepared: dict[str, Any]) -> Path:
        scenario_id = str(prepared.get("scenario_id", "scn_unknown"))
        out_dir = (Path("outputs") / "scenarios" / scenario_id).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "scenario_id": scenario_id,
            "created_ts": time.time(),
            "sdr_options": prepared.get("sdr_options", {}),
            "trajectory": prepared.get("trajectory", {}),
        }
        out_path = out_dir / "pluto_input.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_path

    def _publish_state(self, st: OrchestratorState) -> None:
        self._bus.publish(OrchestratorStateEvent(state=st.value))
