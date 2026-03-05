from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.core.event_bus import EventBus
from app.core.events import OrchestratorStateEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.orchestrator.states import OrchestratorState
from app.profiles.loader import load_profile
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
        self._active_scenario_id: Optional[str] = None
        self._prepared_scenario: Optional[dict[str, Any]] = None

        self._bus.subscribe(ServiceStatusEvent, self._on_service_status_event)

        # initial state event for UI
        self._publish_state(self._state)

    @property
    def state(self) -> OrchestratorState:
        with self._lock:
            return self._state

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
        return scenario_id

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
        svc = services_map.get("mayak_spindle")
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
        root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
        root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else {})
        orch = root.get("orchestrator", {}) if isinstance(root, dict) else {}
        flag = orch.get("require_mayak_ready_for_jobs", True) if isinstance(orch, dict) else True
        return bool(flag)

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

    def _next_scenario_id(self) -> str:
        with self._lock:
            self._scenario_seq += 1
            return f"scn_{int(time.time() * 1000)}_{self._scenario_seq}"

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
                "gps_sdr_sim": {"nav": "", "static_sec": 0.0},
                "pluto_player": {"rf_bw_mhz": 3.0, "tx_atten_db": -20.0},
            }

        gps = sdr_options.get("gps_sdr_sim")
        pluto = sdr_options.get("pluto_player")

        gps_nav = ""
        gps_static = 0.0
        if isinstance(gps, dict):
            nav = gps.get("nav")
            static_sec = gps.get("static_sec")
            if isinstance(nav, str):
                gps_nav = nav.strip()
            if isinstance(static_sec, (int, float)):
                gps_static = max(0.0, float(static_sec))

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
            "gps_sdr_sim": {"nav": gps_nav, "static_sec": gps_static},
            "pluto_player": {"rf_bw_mhz": rf_bw, "tx_atten_db": tx_att},
        }

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

    def _publish_state(self, st: OrchestratorState) -> None:
        self._bus.publish(OrchestratorStateEvent(state=st.value))
