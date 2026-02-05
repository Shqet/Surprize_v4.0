from __future__ import annotations

from typing import Any, Optional

from app.core.event_bus import EventBus
from app.core.events import OrchestratorStateEvent
from app.core.logging_setup import emit_log
from app.orchestrator.states import OrchestratorState
from app.profiles.loader import load_profile
from app.services.service_manager import ServiceManager


class Orchestrator:
    """
    v0 Orchestrator:
      - UI calls start/stop only (no subprocess/devices in UI)
      - uses ServiceManager to manage services
      - publishes OrchestratorStateEvent on every state change
      - logs with standard codes
    """

    def __init__(self, bus: EventBus, service_manager: ServiceManager) -> None:
        self._bus = bus
        self._sm = service_manager
        self._state: OrchestratorState = OrchestratorState.IDLE

        # initial state event for UI
        self._publish_state(self._state)

    @property
    def state(self) -> OrchestratorState:
        return self._state

    def start(self, profile_name: str) -> None:
        # idempotent: ignore start if already running/starting/stopping
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

        try:
            self._sm.start_all(profile_cfg)
        except Exception as ex:
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", f"stage=start_all err={type(ex).__name__}")
            self._set_state(OrchestratorState.ERROR)
            return

        self._set_state(OrchestratorState.RUNNING)

    def stop(self) -> None:
        # idempotent: if already idle or stopping, do nothing
        if self._state in (OrchestratorState.IDLE, OrchestratorState.STOPPING):
            return

        emit_log(self._bus, "INFO", "orchestrator", "ORCH_STOP_REQUEST", "req=1")
        self._set_state(OrchestratorState.STOPPING)

        try:
            self._sm.stop_all()
        except Exception as ex:
            # stop_all is best-effort and should not raise, but protect anyway
            emit_log(self._bus, "ERROR", "orchestrator", "SERVICE_ERROR", f"stage=stop_all err={type(ex).__name__}")
            self._set_state(OrchestratorState.ERROR)
            return

        self._set_state(OrchestratorState.IDLE)

    # -------------------- internals --------------------

    def _set_state(self, st: OrchestratorState) -> None:
        if st == self._state:
            return
        prev = self._state
        self._state = st
        emit_log(self._bus, "INFO", "orchestrator", "ORCH_STATE_CHANGE", f"from={prev.value} to={st.value}")
        self._publish_state(st)

    def _publish_state(self, st: OrchestratorState) -> None:
        self._bus.publish(OrchestratorStateEvent(state=st.value))
