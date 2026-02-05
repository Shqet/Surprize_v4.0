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


class Orchestrator:
    """
    v1:
      - Tracks service statuses via ServiceStatusEvent ONLY
      - stop(): STOPPING synchronization in a worker thread (no UI blocking)
      - STOPPING -> IDLE only when all services are STOPPED
      - STOPPING -> ERROR if any service reports ERROR OR timeout with pending services
      - Logging: SERVICE_STATUS progress logs, including synthetic STOPPING (log-only)
    """

    def __init__(self, bus: EventBus, service_manager: ServiceManager) -> None:
        self._bus = bus
        self._sm = service_manager

        self._lock = threading.Lock()
        self._state: OrchestratorState = OrchestratorState.IDLE

        # v1: service_name -> last ServiceStatus (source of truth: ServiceStatusEvent)
        self._service_status: dict[str, ServiceStatus] = {}

        # wake-up for stop waiter
        self._status_changed = threading.Event()

        # stop waiter thread control
        self._stop_wait_thread: Optional[threading.Thread] = None
        self._stop_wait_cancel = threading.Event()

        # v1: stop timeout (loaded from profile on start; default applied by orchestrator)
        self._stop_timeout_sec: int = 10

        # Subscribe for status tracking
        self._bus.subscribe(ServiceStatusEvent, self._on_service_status_event)

        # initial state event for UI
        self._publish_state(self._state)

    @property
    def state(self) -> OrchestratorState:
        with self._lock:
            return self._state

    def start(self, profile_name: str) -> None:
        with self._lock:
            if self._state in (OrchestratorState.PRECHECK, OrchestratorState.RUNNING, OrchestratorState.STOPPING):
                return

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_START_REQUEST", f"profile={profile_name}")
        self._set_state(OrchestratorState.PRECHECK)

        try:
            profile_cfg = load_profile(profile_name)
        except Exception as ex:
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", f"stage=load_profile err={type(ex).__name__}")
            self._set_state(OrchestratorState.ERROR)
            return

        self._apply_stop_timeout_from_profile(profile_cfg, profile_name)

        try:
            self._sm.start_all(profile_cfg)
        except Exception as ex:
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", f"stage=start_all err={type(ex).__name__}")
            self._set_state(OrchestratorState.ERROR)
            return

        self._set_state(OrchestratorState.RUNNING)

    def stop(self) -> None:
        with self._lock:
            if self._state in (OrchestratorState.IDLE, OrchestratorState.STOPPING):
                return

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_STOP_REQUEST", "req=1")
        self._set_state(OrchestratorState.STOPPING)

        # v1 logging: synthetic STOPPING progress per service (log-only; not a ServiceStatus enum value)
        for name in sorted(self._sm.get_services().keys()):
            emit_log(self._bus, "INFO", "orchestrator", "SERVICE_STATUS", f"service={name} status=STOPPING")

        try:
            self._sm.stop_all()
        except Exception as ex:
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", f"stage=stop_all err={type(ex).__name__}")
            self._set_state(OrchestratorState.ERROR)
            return

        self._start_stop_waiter()

    # -------------------- v1: Service status tracking --------------------

    def _on_service_status_event(self, e: ServiceStatusEvent) -> None:
        try:
            st = ServiceStatus(e.status)
        except Exception:
            st = ServiceStatus.ERROR

        with self._lock:
            self._service_status[e.service_name] = st

        emit_log(self._bus, "INFO", "orchestrator", "SERVICE_STATUS", f"service={e.service_name} status={st.value}")
        self._status_changed.set()

    # -------------------- v1: STOPPING synchronization --------------------

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
        deadline = time.monotonic() + max(1, timeout_sec)

        service_names = set(self._sm.get_services().keys())

        while True:
            if self._stop_wait_cancel.is_set():
                return

            with self._lock:
                if self._state != OrchestratorState.STOPPING:
                    return
                snapshot = {k: self._service_status.get(k) for k in service_names}

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
