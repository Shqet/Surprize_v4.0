from __future__ import annotations

import sys
import multiprocessing
import runpy
import json
import subprocess
import time

from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log, setup_logging
from app.core.runtime_paths import find_existing_path, resolve_runtime_path
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.services.gps_sdr_sim.engine import prepare_nmea_input
from app.services.ballistics_model import BallisticsModelSubprocessService
from app.services.exe_runner import ExeRunnerService
from app.services.gps_sdr_sim.service import GpsSdrSimService
from app.services.mayak_spindle import MayakSpindleService
from app.services.service_manager import ServiceManager
from app.services.video_channel import VideoChannelDaemonService
from app.ui.main_window import MainWindow

_DEFAULT_UI_THEME = "light"


def _assets_root() -> Path:
    # PyInstaller onedir/onefile places bundled data under _MEIPASS.
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        return Path(frozen_base) / "app" / "ui" / "assets"
    return Path(__file__).resolve().parent / "ui" / "assets"


def _set_windows_appusermodel_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Surprize.SurprizeShell")
    except Exception:
        # Non-fatal: window icon still works even if taskbar AppID cannot be set.
        return


def _read_saved_ui_theme() -> str:
    store = QSettings("Surprize", "SurprizeShell")
    theme = str(store.value("ui_theme", _DEFAULT_UI_THEME) or "").strip().lower()
    if theme not in ("light", "dark"):
        return _DEFAULT_UI_THEME
    return theme


def _resolve_themed_icon(theme: str) -> QIcon:
    root = _assets_root() / "icons"
    dark_path = root / "main_dark_icon.png"
    light_path = root / "main_light_icon.svg"

    def _load_icon(path: Path) -> QIcon:
        if not path.exists():
            return QIcon()
        icon = QIcon(str(path))
        if icon.isNull():
            return QIcon()
        # Some bundled formats can yield empty pixmap in frozen builds.
        if icon.pixmap(32, 32).isNull():
            return QIcon()
        return icon

    preferred = dark_path if str(theme).strip().lower() == "dark" else light_path
    fallback = light_path if preferred == dark_path else dark_path
    icon = _load_icon(preferred)
    if not icon.isNull():
        return icon
    icon = _load_icon(fallback)
    if not icon.isNull():
        return icon
    return QIcon()


def _create_startup_splash(app_icon: QIcon, theme: str) -> QSplashScreen:
    is_dark = str(theme).strip().lower() == "dark"
    bg = QColor("#111827") if is_dark else QColor("#f3f4f6")
    card = QColor("#1f2937") if is_dark else QColor("#ffffff")
    title = QColor("#e5e7eb") if is_dark else QColor("#111827")
    subtitle = QColor("#9ca3af") if is_dark else QColor("#4b5563")

    pix = QPixmap(560, 300)
    pix.fill(bg)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(card)
    painter.drawRoundedRect(18, 18, 524, 264, 18, 18)

    logo = app_icon.pixmap(96, 96) if not app_icon.isNull() else QPixmap()
    if not logo.isNull():
        painter.drawPixmap((pix.width() - 96) // 2, 66, logo)

    painter.setPen(title)
    title_font = QFont()
    title_font.setPointSize(15)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.drawText(pix.rect().adjusted(0, 172, 0, 0), Qt.AlignmentFlag.AlignHCenter, "Surprize")

    sub_font = QFont()
    sub_font.setPointSize(10)
    painter.setFont(sub_font)
    painter.setPen(subtitle)
    painter.drawText(
        pix.rect().adjusted(0, 202, 0, 0),
        Qt.AlignmentFlag.AlignHCenter,
        "Подготовка интерфейса...",
    )
    painter.end()

    splash = QSplashScreen(pix)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    return splash


def _run_frozen_runtime_smoke() -> int:
    """
    Headless runtime smoke for frozen EXE:
      1) trajectory generation via embedded ballistics worker
      2) gps preflight IQ generation via gps-sdr-sim
    """
    out_root = resolve_runtime_path(Path("outputs") / "smoke" / "frozen_runtime")
    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    model_root = resolve_runtime_path("model_ballistics")
    calc_entry = model_root / "run_vkr.py"
    if not calc_entry.exists():
        print(f"SMOKE_FAIL missing_calc_entry={calc_entry.as_posix()}")
        return 2

    cfg = {
        "simulation": {"dt": 0.01, "t_max": 2.0, "max_steps": 20000},
        "projectile": {"m": 10.0, "S": 0.01, "C_L": 0.0, "C_mp": 0.0, "g": 9.81},
        "rotation": {"Ix": 0.02, "Iy": 0.10, "Iz": 0.10, "k_stab": 1.0},
        "initial_conditions": {"V0": 310.0, "theta_deg": 12.0, "psi_deg": 0.0},
    }
    cfg_path = run_dir / "smoke_vkr_config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    ballistics_cmd = [
        sys.executable,
        "--ballistics-worker",
        str(calc_entry),
        "--config",
        str(cfg_path),
        "--out",
        str(run_dir),
    ]
    ballistics = subprocess.run(
        ballistics_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120.0,
    )
    (run_dir / "ballistics_stdout.log").write_text(ballistics.stdout or "", encoding="utf-8")
    (run_dir / "ballistics_stderr.log").write_text(ballistics.stderr or "", encoding="utf-8")
    if ballistics.returncode != 0:
        print(f"SMOKE_FAIL ballistics_rc={ballistics.returncode}")
        return 3

    traj_csv = run_dir / "trajectory.csv"
    diag_csv = run_dir / "diagnostics.csv"
    if not traj_csv.exists() or not diag_csv.exists():
        print("SMOKE_FAIL missing_ballistics_artifacts=1")
        return 4

    nav = find_existing_path("data/ephemerides/brdc0430.25n")
    if nav is None:
        print("SMOKE_FAIL missing_nav=1")
        return 5
    gps_exe = find_existing_path("bin/gps_sdr_sim/GPS-SDR-SIM.exe") or find_existing_path("bin/gps_sdr_sim/gps-sdr-sim.exe")
    if gps_exe is None:
        print("SMOKE_FAIL missing_gps_sdr_sim_exe=1")
        return 6

    preflight_dir = run_dir / "gps_preflight"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    nmea_txt = preflight_dir / "nmea_strings.txt"
    iq_bin = preflight_dir / "gpssim_iq.bin"
    nav_local = preflight_dir / nav.name
    nav_local.write_bytes(nav.read_bytes())
    prepare_nmea_input(
        input_trajectory_csv=traj_csv,
        out_nmea_txt=nmea_txt,
        origin_lat_deg=55.7558,
        origin_lon_deg=37.6176,
        origin_h_m=156.0,
        static_sec=0.0,
    )
    gps_cmd = [
        str(gps_exe),
        "-e",
        nav_local.name,
        "-g",
        nmea_txt.name,
        "-b",
        "16",
        "-o",
        iq_bin.name,
    ]
    gps = subprocess.run(
        gps_cmd,
        cwd=str(preflight_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120.0,
    )
    (preflight_dir / "gps_stdout.log").write_text(gps.stdout or "", encoding="utf-8")
    (preflight_dir / "gps_stderr.log").write_text(gps.stderr or "", encoding="utf-8")
    if gps.returncode != 0:
        print(f"SMOKE_FAIL gps_preflight_rc={gps.returncode}")
        return 7
    if not iq_bin.exists() or iq_bin.stat().st_size <= 0:
        print("SMOKE_FAIL gps_iq_missing=1")
        return 8

    print(f"SMOKE_OK run_dir={run_dir.as_posix()}")
    return 0


def main() -> int:
    setup_logging()
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
    theme = _read_saved_ui_theme()
    app_icon = _resolve_themed_icon(theme)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    splash = _create_startup_splash(app_icon, theme)
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
    # Required for frozen builds that spawn worker processes.
    multiprocessing.freeze_support()
    if "--frozen-runtime-smoke" in sys.argv:
        raise SystemExit(_run_frozen_runtime_smoke())
    if "--ballistics-worker" in sys.argv:
        idx = sys.argv.index("--ballistics-worker")
        if idx + 1 >= len(sys.argv):
            raise SystemExit(2)
        script = str(Path(sys.argv[idx + 1]).resolve())
        script_dir = str(Path(script).parent)
        if script_dir and script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        script_argv = [script, *sys.argv[idx + 2 :]]
        sys.argv = script_argv
        runpy.run_path(script, run_name="__main__")
        raise SystemExit(0)
    if "--video-reader-worker" in sys.argv:
        idx = sys.argv.index("--video-reader-worker")
        worker_argv = [sys.argv[0], *sys.argv[idx + 1 :]]
        sys.argv = worker_argv
        from app.vendor.video_channel.client.reader_process import main as _reader_process_main

        raise SystemExit(_reader_process_main())
    raise SystemExit(main())
