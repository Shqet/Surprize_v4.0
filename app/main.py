from __future__ import annotations

import sys
from typing import Optional

from PyQt6.QtWidgets import QApplication

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log, setup_logging
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.services.exe_runner import ExeRunnerService
from app.services.service_manager import ServiceManager
from app.ui.main_window import MainWindow


def main() -> int:
    setup_logging("./data/app.log")

    bus = EventBus()

    # Services + ServiceManager
    sm = ServiceManager(bus)
    sm.register(ExeRunnerService(bus))

    # Orchestrator
    orch = Orchestrator(bus, sm)

    # Qt application (UI thread)
    app = QApplication(sys.argv)

    # Bridge must be created in UI thread
    bridge = UIBridge(bus)

    # Emit SYSTEM_START after we have bus
    emit_log(bus, "INFO", "system", "SYSTEM_START", "v=0")

    win = MainWindow(orch, bridge)
    win.show()

    def on_quit() -> None:
        # Best-effort: stop system before exit (non-blocking in UI thread)
        emit_log(bus, "INFO", "system", "SYSTEM_STOP", "req=1")
        try:
            orch.stop()
        except Exception:
            # Do not block or crash on shutdown
            pass

    app.aboutToQuit.connect(on_quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
