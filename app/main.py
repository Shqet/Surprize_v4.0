from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen

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


def _create_startup_splash(app_icon: QIcon) -> QSplashScreen:
    pix = QPixmap(560, 300)
    pix.fill(QColor("#111827"))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1f2937"))
    painter.drawRoundedRect(18, 18, 524, 264, 18, 18)

    logo = app_icon.pixmap(96, 96) if not app_icon.isNull() else QPixmap()
    if not logo.isNull():
        painter.drawPixmap((pix.width() - 96) // 2, 66, logo)

    painter.setPen(QColor("#e5e7eb"))
    title_font = QFont()
    title_font.setPointSize(15)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.drawText(pix.rect().adjusted(0, 172, 0, 0), Qt.AlignmentFlag.AlignHCenter, "Surprize")

    sub_font = QFont()
    sub_font.setPointSize(10)
    painter.setFont(sub_font)
    painter.setPen(QColor("#9ca3af"))
    painter.drawText(
        pix.rect().adjusted(0, 202, 0, 0),
        Qt.AlignmentFlag.AlignHCenter,
        "Подготовка интерфейса...",
    )
    painter.end()

    splash = QSplashScreen(pix)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    return splash


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
    app_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    splash = _create_startup_splash(app_icon)
    splash.show()
    app.processEvents()
    bridge = UIBridge(bus)
    win = MainWindow(orch, bridge)
    if not app_icon.isNull():
        win.setWindowIcon(app_icon)

    def _show_main_window() -> None:
        win.show()
        splash.finish(win)
        emit_log(bus, "INFO", "system", "SYSTEM_START", "v=0")

        # ---- v4: auto-start daemon services ----
        try:
            orch.start_daemons("default")
            emit_log(bus, "INFO", "system", "SYSTEM_DAEMONS_STARTED", "ok=1")
        except Exception as ex:
            emit_log(bus, "ERROR", "system", "SYSTEM_DAEMONS_START_FAIL", f"error={type(ex).__name__}")

    QTimer.singleShot(3000, _show_main_window)

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
