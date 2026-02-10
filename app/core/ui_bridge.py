from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.event_bus import EventBus
from app.core.events import LogEvent, OrchestratorStateEvent, ProcessOutputEvent, ServiceStatusEvent


class UIBridge(QObject):
    log_event = pyqtSignal(object)
    service_status_event = pyqtSignal(object)
    orch_state_event = pyqtSignal(object)
    process_output_event = pyqtSignal(object)

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

        # Keep explicit handler refs for unsubscribe/detach
        self._h_log = self._on_log_event
        self._h_svc = self._on_service_status
        self._h_orch = self._on_orch_state
        self._h_proc = self._on_process_output
        self._detached = False

        bus.subscribe(LogEvent, self._h_log)
        bus.subscribe(ServiceStatusEvent, self._h_svc)
        bus.subscribe(OrchestratorStateEvent, self._h_orch)
        bus.subscribe(ProcessOutputEvent, self._h_proc)

    def detach(self) -> None:
        """
        Call on app shutdown before Qt destroys this QObject.
        Prevents EventBus from invoking handlers after UI is gone.
        """
        if self._detached:
            return
        self._detached = True

        # EventBus may or may not implement unsubscribe; handle both.
        unsub = getattr(self._bus, "unsubscribe", None)
        if callable(unsub):
            try:
                unsub(LogEvent, self._h_log)
            except Exception:
                pass
            try:
                unsub(ServiceStatusEvent, self._h_svc)
            except Exception:
                pass
            try:
                unsub(OrchestratorStateEvent, self._h_orch)
            except Exception:
                pass
            try:
                unsub(ProcessOutputEvent, self._h_proc)
            except Exception:
                pass

    def _safe_emit(self, sig: pyqtSignal, event: object) -> None:
        # When app is closing, Qt may delete this QObject while background threads still publish.
        try:
            sig.emit(event)
        except RuntimeError:
            # "wrapped C/C++ object ... has been deleted" -> ignore during shutdown
            return

    def _on_log_event(self, e: LogEvent) -> None:
        if self._detached:
            return
        # During shutdown Qt may delete this QObject; accessing bound signals can raise RuntimeError.
        try:
            self._safe_emit(self.log_event, e)
        except RuntimeError:
            return

    def _on_service_status(self, e: ServiceStatusEvent) -> None:
        if self._detached:
            return
        try:
            self._safe_emit(self.service_status_event, e)
        except RuntimeError:
            return

    def _on_orch_state(self, e: OrchestratorStateEvent) -> None:
        if self._detached:
            return
        try:
            self._safe_emit(self.orch_state_event, e)
        except RuntimeError:
            return

    def _on_process_output(self, e: ProcessOutputEvent) -> None:
        if self._detached:
            return
        try:
            self._safe_emit(self.process_output_event, e)
        except RuntimeError:
            return
