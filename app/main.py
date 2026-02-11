from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log, setup_logging
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.services.exe_runner import ExeRunnerService
from app.services.rtsp_health_service import RtspHealthService
from app.services.rtsp_ingest_service import RtspIngestService
from app.services.service_manager import ServiceManager
from app.ui.main_window import MainWindow

# v2: new real service
from app.services.ballistics_model import BallisticsModelSubprocessService


def main() -> int:
    setup_logging("./data/app.log")

    bus = EventBus()

    # services
    sm = ServiceManager(bus)
    sm.register(ExeRunnerService(bus))
    sm.register(BallisticsModelSubprocessService(bus))  # v2
    sm.register(RtspIngestService(bus))
    sm.register(RtspHealthService(bus))

    # orchestrator
    orch = Orchestrator(bus, sm)

    # UI
    app = QApplication(sys.argv)
    bridge = UIBridge(bus)
    win = MainWindow(orch, bridge)
    win.show()

    emit_log(bus, "INFO", "system", "SYSTEM_START", "v=0")

    def _on_quit() -> None:
        """
        v4 shutdown semantics:
          - orch.stop() stops only jobs (run-cycle isolation)
          - sm.stop_all() best-effort stops all services (jobs + daemons)
        """
        try:
            # Stop current job run-cycle (if any)
            orch.stop()
        except Exception:
            pass

        try:
            # Best-effort: stop all services (including daemons)
            sm.stop_all()
        except Exception:
            pass

        emit_log(bus, "INFO", "system", "SYSTEM_STOP", "req=1")

    app.aboutToQuit.connect(_on_quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
