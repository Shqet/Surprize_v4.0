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

        bus.subscribe(LogEvent, self._on_log_event)
        bus.subscribe(ServiceStatusEvent, self._on_service_status)
        bus.subscribe(OrchestratorStateEvent, self._on_orch_state)
        bus.subscribe(ProcessOutputEvent, self._on_process_output)

    def _safe_emit(self, sig: pyqtSignal, event: object) -> None:
        # When app is closing, Qt may delete this QObject while background threads still publish.
        try:
            sig.emit(event)
        except RuntimeError:
            # "wrapped C/C++ object ... has been deleted" -> ignore during shutdown
            return

    def _on_log_event(self, e: LogEvent) -> None:
        self._safe_emit(self.log_event, e)

    def _on_service_status(self, e: ServiceStatusEvent) -> None:
        self._safe_emit(self.service_status_event, e)

    def _on_orch_state(self, e: OrchestratorStateEvent) -> None:
        self._safe_emit(self.orch_state_event, e)

    def _on_process_output(self, e: ProcessOutputEvent) -> None:
        self._safe_emit(self.process_output_event, e)
