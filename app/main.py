from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log, setup_logging
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.services.ballistics_model import BallisticsModelSubprocessService
from app.services.exe_runner import ExeRunnerService
from app.services.gps_sdr_sim.service import GpsSdrSimService
from app.services.mayak_spindle import MayakSpindleService
from app.services.service_manager import ServiceManager
from app.services.video_channel import VideoChannelDaemonService
from app.ui.main_window import MainWindow


def _set_windows_appusermodel_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Surprize.SurprizeShell")
    except Exception:
        # Non-fatal: window icon still works even if taskbar AppID cannot be set.
        return


def main() -> int:
    setup_logging("./data/app.log")
    _set_windows_appusermodel_id()

    bus = EventBus()

    # services
    sm = ServiceManager(bus)
    sm.register(ExeRunnerService(bus))
    sm.register(BallisticsModelSubprocessService(bus))  # job
    sm.register(GpsSdrSimService(bus))  # job (start by orchestrator)
    sm.register(MayakSpindleService(bus))  # daemon (transport from profile)

    # daemons (names MUST match profile keys)
    sm.register(VideoChannelDaemonService(bus, "video_visible"))
    sm.register(VideoChannelDaemonService(bus, "video_thermal"))

    # orchestrator
    orch = Orchestrator(bus, sm)

    # UI
    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parent / "ui" / "assets" / "icons" / "main_icon.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    bridge = UIBridge(bus)
    win = MainWindow(orch, bridge)
    if icon_path.exists():
        win.setWindowIcon(QIcon(str(icon_path)))
    win.show()

    emit_log(bus, "INFO", "system", "SYSTEM_START", "v=0")

    # ---- v4: auto-start daemon services ----
    try:
        orch.start_daemons("default")
        emit_log(bus, "INFO", "system", "SYSTEM_DAEMONS_STARTED", "ok=1")
    except Exception as ex:
        emit_log(bus, "ERROR", "system", "SYSTEM_DAEMONS_START_FAIL", f"error={type(ex).__name__}")

    def _on_quit() -> None:
        """
        v4 shutdown semantics:
          - orch.stop() stops only jobs (run-cycle isolation)
          - sm.stop_all() best-effort stops all services (jobs + daemons)
        """
        try:
            orch.stop()
        except Exception:
            pass

        try:
            sm.stop_all()
        except Exception:
            pass

        emit_log(bus, "INFO", "system", "SYSTEM_STOP", "req=1")

    app.aboutToQuit.connect(_on_quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
