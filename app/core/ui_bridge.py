from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.event_bus import EventBus
from app.core.events import (
    LogEvent,
    OrchestratorStateEvent,
    ProcessOutputEvent,
    ServiceStatusEvent,
)


class UIBridge(QObject):
    """
    Qt bridge for delivering EventBus events into UI thread via signals.

    Rules:
      - No UI updates here.
      - Handlers ONLY emit Qt signals.
      - Create this object in UI thread to ensure queued delivery.
    """

    log_event = pyqtSignal(object)              # LogEvent
    service_status_event = pyqtSignal(object)   # ServiceStatusEvent
    orch_state_event = pyqtSignal(object)       # OrchestratorStateEvent
    process_output_event = pyqtSignal(object)   # ProcessOutputEvent

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

        # Subscribe to EventBus by event types.
        self._bus.subscribe(LogEvent, self._on_log_event)
        self._bus.subscribe(ServiceStatusEvent, self._on_service_status_event)
        self._bus.subscribe(OrchestratorStateEvent, self._on_orch_state_event)
        self._bus.subscribe(ProcessOutputEvent, self._on_process_output_event)

    # EventBus handlers (may run on any thread). Emit signals only.
    def _on_log_event(self, e: LogEvent) -> None:
        self.log_event.emit(e)

    def _on_service_status_event(self, e: ServiceStatusEvent) -> None:
        self.service_status_event.emit(e)

    def _on_orch_state_event(self, e: OrchestratorStateEvent) -> None:
        self.orch_state_event.emit(e)

    def _on_process_output_event(self, e: ProcessOutputEvent) -> None:
        self.process_output_event.emit(e)
