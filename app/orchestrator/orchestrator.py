from __future__ import annotations

import threading
import time
from typing import Optional

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
            profile_cfg = load_profile(profile_name)
        except Exception as ex:
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", f"stage=load_profile err={type(ex).__name__}")
            self._set_state(OrchestratorState.ERROR)
            return

        if overrides is not None:
            if not isinstance(overrides, dict):
                emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", "stage=validate_overrides err=TypeError")
                self._set_state(OrchestratorState.ERROR)
                return

            # Apply overrides in-memory (no disk writes)
            try:
                if isinstance(profile_cfg, dict) and isinstance(profile_cfg.get(profile_name), dict):
                    deep_merge(profile_cfg[profile_name], overrides)
                elif isinstance(profile_cfg, dict):
                    deep_merge(profile_cfg, overrides)
                else:
                    raise TypeError("profile_cfg must be dict")
            except Exception as ex:
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    f"stage=apply_overrides err={type(ex).__name__}",
                )
                self._set_state(OrchestratorState.ERROR)
                return

            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "ORCH_PROFILE_OVERRIDES_APPLIED",
                f"keys={count_leaf_values(overrides)}",
            )

            # Fail-fast config sanity check (minimal)
            root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
            root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else None)
            services = root.get("services") if isinstance(root, dict) else None
            if not isinstance(services, dict):
                emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", "stage=cfg_check err=services_missing")
                self._set_state(OrchestratorState.ERROR)
                return
            bm = services.get("ballistics_model")
            if bm is not None and not isinstance(bm, dict):
                emit_log(
                    self._bus,
                    "ERROR",
                    "orchestrator",
                    "SERVICE_ERROR",
                    "stage=cfg_check err=ballistics_model_not_dict",
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

        if overrides is None:
            return profile_cfg

        if not isinstance(overrides, dict):
            raise TypeError("overrides must be dict")

        # profile_cfg in your project is usually: {profile_name: {...}}
        if isinstance(profile_cfg, dict) and isinstance(profile_cfg.get(profile_name), dict):
            deep_merge(profile_cfg[profile_name], overrides)
        elif isinstance(profile_cfg, dict):
            deep_merge(profile_cfg, overrides)
        else:
            raise TypeError("profile_cfg must be dict")

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "ORCH_PROFILE_OVERRIDES_APPLIED",
            f"keys={count_leaf_values(overrides)}",
        )

        # Minimal cfg-check (keep it simple but fail fast)
        root = None
        if isinstance(profile_cfg, dict) and isinstance(profile_cfg.get(profile_name), dict):
            root = profile_cfg[profile_name]
        elif isinstance(profile_cfg, dict):
            root = profile_cfg

        services = root.get("services") if isinstance(root, dict) else None
        if not isinstance(services, dict):
            raise ValueError("cfg_check services_missing")

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

    def _is_service_running(self, service_name: str) -> bool:
        with self._lock:
            return self._service_status.get(service_name) == ServiceStatus.RUNNING

    # -------------------- internals --------------------

    def _set_state(self, st: OrchestratorState) -> None:
        with self._lock:
            if st == self._state:
                return
            prev = self._state
            self._state = st

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_STATE_CHANGE", f"from={prev.value} to={st.value}")
        self._publish_state(st)

    def _publish_state(self, st: OrchestratorState) -> None:
        self._bus.publish(OrchestratorStateEvent(state=st.value))
