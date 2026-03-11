from __future__ import annotations

import copy
import csv
import json
import math
import os
import subprocess
import time
from bisect import bisect_left
from enum import Enum
from pathlib import Path
from typing import Any, Optional, cast

from PyQt6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import emit_log
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.services.gps_sdr_sim.formats import ecef_to_geodetic, enu_to_ecef
from app.ui.generated.main_window import Ui_MainWindow
from app.ui.trajectory.controller import TrajectoryVisController
from app.ui.trajectory.csv_loader import TrajectoryCsvLoader
from app.ui.trajectory.generate_controller import GenerateController
from app.ui.trajectory.trajectory_3d_view import Trajectory3DView
from app.ui.widgets.config_json_editor import ConfigJsonEditor
from app.ui.widgets.rtsp_preview import RtspPreviewWidget

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

_DEFAULT_CONFIG_JSON: dict[str, Any] = {
    "simulation": {"dt": 0.002, "t_max": 120.0, "max_steps": 2000000},
    "projectile": {"m": 10.0, "S": 0.01, "C_L": 0.0, "C_mp": 0.0, "g": 9.81},
    "rotation": {"Ix": 0.02, "Iy": 0.10, "Iz": 0.10, "k_stab": 1.0},
    "initial_conditions": {
        "V0": 310.0,
        "theta_deg": 15.0,
        "psi_deg": 0.0,
    },
}

_DEFAULT_PREVIEW_VISIBLE = "outputs/video_preview/visible/latest.jpg"
_DEFAULT_PREVIEW_THERMAL = "outputs/video_preview/thermal/latest.jpg"
_DEFAULT_GPS_NAV_PATH = "data/ephemerides/brdc0430.25n"
_DEFAULT_GPS_STATIC_SEC = 0.0
_DEFAULT_PLUTO_RF_BW_MHZ = 3.0
_DEFAULT_PLUTO_TX_ATTEN_DB = -20.0
_DEFAULT_GPS_ORIGIN_LAT = 55.7558
_DEFAULT_GPS_ORIGIN_LON = 37.6176
_DEFAULT_GPS_ORIGIN_H_M = 156.0
_DEFAULT_AUTO_STOP_AFTER_GPS_SEC = 10.0
_DEFAULT_ANIM_WITHOUT_TEST = True
_DEFAULT_SESSION_OUTPUT_ROOT = "outputs/sessions"
_REPLAY_CHANNEL_GAP_SEC = 0.5


class ReplayState(str, Enum):
    IDLE = "IDLE"
    LOADED = "LOADED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    EOF = "EOF"
    ERROR = "ERROR"


class _PrepareTestSignals(QObject):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(object)
    fail = pyqtSignal(str)


class _PrepareTestTask(QRunnable):
    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        head_start: int,
        head_end: int,
        tail_start: int,
        tail_end: int,
        profile_type: str,
        duration_sec: float,
        sdr_options: dict[str, Any],
    ) -> None:
        super().__init__()
        self._orch = orchestrator
        self._head_start = int(head_start)
        self._head_end = int(head_end)
        self._tail_start = int(tail_start)
        self._tail_end = int(tail_end)
        self._profile_type = str(profile_type)
        self._duration_sec = float(duration_sec)
        self._sdr_options = dict(sdr_options)
        self.signals = _PrepareTestSignals()

    def run(self) -> None:
        try:
            self.signals.progress.emit(10, "Подготовка сценария")
            scenario_id = self._orch.prepare_mayak_test(
                head_start_rpm=self._head_start,
                head_end_rpm=self._head_end,
                tail_start_rpm=self._tail_start,
                tail_end_rpm=self._tail_end,
                profile_type=self._profile_type,
                duration_sec=self._duration_sec,
                sdr_options=self._sdr_options,
            )

            self.signals.progress.emit(30, "Генерация GPS preflight")
            gps_artifacts = self._orch.generate_gps_signal_preflight(
                progress_cb=lambda p, m: self.signals.progress.emit(int(p), str(m)),
            )

            self.signals.progress.emit(100, "Готово")
            self.signals.done.emit(
                {
                    "scenario_id": scenario_id,
                    "gps_artifacts": gps_artifacts,
                }
            )
        except Exception as ex:
            self.signals.fail.emit(f"{type(ex).__name__}: {ex}")


class _ReadinessCheckSignals(QObject):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(object)
    fail = pyqtSignal(str)


class _ReadinessCheckTask(QRunnable):
    def __init__(self, *, orchestrator: Orchestrator) -> None:
        super().__init__()
        self._orch = orchestrator
        self.signals = _ReadinessCheckSignals()

    def run(self) -> None:
        try:
            self.signals.progress.emit(15, "Проверка готовности")
            report = self._orch.check_readiness()
            self.signals.progress.emit(100, "Проверка завершена")
            self.signals.done.emit(report)
        except Exception as ex:
            self.signals.fail.emit(f"{type(ex).__name__}: {ex}")


class _SessionFlowSignals(QObject):
    done = pyqtSignal(object)
    fail = pyqtSignal(str)


class _StartSessionFlowTask(QRunnable):
    def __init__(self, *, orchestrator: Orchestrator) -> None:
        super().__init__()
        self._orch = orchestrator
        self.signals = _SessionFlowSignals()

    def run(self) -> None:
        try:
            payload = self._orch.start_test_session_flow()
            self.signals.done.emit(payload)
        except Exception as ex:
            self.signals.fail.emit(f"{type(ex).__name__}: {ex}")


class _StopSessionFlowTask(QRunnable):
    def __init__(self, *, orchestrator: Orchestrator) -> None:
        super().__init__()
        self._orch = orchestrator
        self.signals = _SessionFlowSignals()

    def run(self) -> None:
        try:
            payload = self._orch.stop_test_session_flow()
            self.signals.done.emit(payload)
        except Exception as ex:
            self.signals.fail.emit(f"{type(ex).__name__}: {ex}")


class MainWindow(QMainWindow):
    def __init__(self, orchestrator: Orchestrator, bridge: UIBridge) -> None:
        super().__init__()
        self._orch = orchestrator
        self._bridge = bridge

        self._initial_config: dict[str, Any] = self._normalize_ballistics_config_for_ui(self._load_initial_config_json())
        self.current_config: dict[str, Any] = copy.deepcopy(self._initial_config)
        self._last_mayak_ready: Optional[bool] = None
        self._last_mayak_health_event: Optional[object] = None
        self._trajectory_duration_sec: Optional[float] = None
        self._last_sent_mayak_duration_sec: Optional[float] = None
        self._prepare_task: Optional[_PrepareTestTask] = None
        self._readiness_task: Optional[_ReadinessCheckTask] = None
        self._start_session_task: Optional[_StartSessionFlowTask] = None
        self._stop_session_task: Optional[_StopSessionFlowTask] = None
        self._session_runtime_last: dict[str, Any] = {}
        self._runtime_prev_active: bool = False
        self._last_finished_session_notified: Optional[str] = None
        self._session_out_dir_hints: dict[str, str] = {}
        self._last_completed_out_dir: str = ""
        self._settings_store = QSettings("Surprize", "SurprizeShell")
        self._settings = self._load_ui_settings()
        self._anim_without_test_enabled: bool = bool(
            self._settings.get("monitor_anim_without_test", _DEFAULT_ANIM_WITHOUT_TEST)
        )
        self._ui_debug: bool = str(os.getenv("SURPRIZE_UI_DEBUG", "0")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._gl_trajectory_params: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "gl_trajectory_params")
        self._gl_trajectory_params_m: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "gl_trajectory_params_m")
        self._vl_trajectory_visualization: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "vl_trajectory_visualization")
        self._vl_trajectory_visualization_m: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "vl_trajectory_visualization_m")
        self._vl_rtsp_visible: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "vl_rtsp_visible")
        self._vl_rtsp_thermal: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "vl_rtsp_thermal")
        self._vl_mayak_params: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "l_Mayak_params")
        self._gl_sdr_options: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "l_SDR_options")
        self._gl_gps_sdr_options_m: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "l_gpsSDRSim_options_m")
        self._gl_functional_buttons: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "l_functionalButtons")
        self._gl_functional_buttons_m: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "l_functionalButtons_m")
        replay_obj = self.findChild(QGridLayout, "l_replay")
        if replay_obj is None and hasattr(self.ui, "l_replay"):
            replay_obj = getattr(self.ui, "l_replay")
        self._gl_replay: Optional[QGridLayout] = cast(Optional[QGridLayout], replay_obj)
        self._gl_replay_video: Optional[QGridLayout] = self._safe_find_layout_any(QGridLayout, "l_video")
        self._gl_replay_graph: Optional[QGridLayout] = self._safe_find_layout_any(QGridLayout, "l_graph", "l_graphs")
        self._gl_replay_3d: Optional[QGridLayout] = self._safe_find_layout_any(
            QGridLayout, "l_3DGraph", "l_3dGraph", "l_3d_graph", "gridLayout_5"
        )
        self._gl_replay_value_manage: Optional[QGridLayout] = self._safe_find_layout_any(QGridLayout, "l_value_manage")
        self._gl_options: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "l_options")

        self._editor: Optional[ConfigJsonEditor] = None
        self._init_editor()

        # 3D view + controller
        self._traj_view = Trajectory3DView(self)
        self._traj_view_m = Trajectory3DView(self)
        self._traj_view_r = Trajectory3DView(self)
        self._latest_trajectory_points: list[tuple[float, float, float]] = []
        self._monitor_points: list[tuple[float, float, float]] = []
        self._monitor_cum_dist_m: list[float] = []
        self._monitor_sample_dt: float = 0.0
        self._monitor_duration_sec: float = 0.0
        self._monitor_started_at: Optional[float] = None
        self._monitor_load_seq: int = 0
        self._replay_session_dir: Optional[Path] = None
        self._replay_timeline: list[tuple[float, float, float, float, float]] = []
        self._replay_timeline_times: list[float] = []
        self._replay_visible_frames: list[tuple[float, int]] = []
        self._replay_visible_times: list[float] = []
        self._replay_thermal_frames: list[tuple[float, int]] = []
        self._replay_thermal_times: list[float] = []
        self._replay_t_min_sec: float = 0.0
        self._replay_t_max_sec: float = 0.0
        self._replay_duration_sec: float = 0.0
        self._replay_t_sec: float = 0.0
        self._replay_state: ReplayState = ReplayState.IDLE
        self._replay_rate: float = 1.0
        self._replay_play_started_mono: float = 0.0
        self._replay_play_started_t_sec: float = 0.0
        self._replay_cap_visible: Any = None
        self._replay_cap_thermal: Any = None
        self._m_speed_lbl: Optional[QLabel] = None
        self._m_coords_lbl: Optional[QLabel] = None
        self._m_geo_lbl: Optional[QLabel] = None
        self._m_height_lbl: Optional[QLabel] = None
        self._m_distance_lbl: Optional[QLabel] = None
        self._m_anim_without_test_chk: Optional[QCheckBox] = None
        self._monitor_timer = QTimer(self)
        self._monitor_timer.setInterval(50)
        self._monitor_timer.timeout.connect(self._on_monitor_timer_tick)
        self._init_trajectory_view()
        self._init_monitor_trajectory_view()
        self._init_monitor_params_panel()
        self._init_replay_panel()

        self._init_rtsp_previews()
        self._gps_nav_path_edit: Optional[QLineEdit] = None
        self._btn_gps_nav_browse: Optional[QPushButton] = None
        self._btn_gps_nav_default: Optional[QPushButton] = None
        self._gps_static_sec_spin: Optional[QDoubleSpinBox] = None
        self._gps_origin_lat_spin: Optional[QDoubleSpinBox] = None
        self._gps_origin_lon_spin: Optional[QDoubleSpinBox] = None
        self._gps_origin_h_spin: Optional[QDoubleSpinBox] = None
        self._gps_finish_lat_lbl: Optional[QLabel] = None
        self._gps_finish_lon_lbl: Optional[QLabel] = None
        self._gps_finish_h_lbl: Optional[QLabel] = None
        self._pluto_rf_bw_spin: Optional[QDoubleSpinBox] = None
        self._pluto_tx_atten_spin: Optional[QDoubleSpinBox] = None
        self._opt_auto_stop_spin: Optional[QDoubleSpinBox] = None
        self._opt_anim_without_test_chk: Optional[QCheckBox] = None
        self._opt_nav_default_edit: Optional[QLineEdit] = None
        self._opt_nav_default_browse: Optional[QPushButton] = None
        self._opt_session_output_root_edit: Optional[QLineEdit] = None
        self._opt_session_output_root_browse: Optional[QPushButton] = None
        self._session_output_root_m_edit: Optional[QLineEdit] = None
        self._btn_session_output_root_m_browse: Optional[QPushButton] = None
        self._btn_session_output_root_m_default: Optional[QPushButton] = None
        self._opt_reset_defaults_btn: Optional[QPushButton] = None
        self._last_trajectory_end_local: Optional[tuple[float, float, float]] = None
        self._init_sdr_options_panel()
        self._init_options_panel()
        self._apply_ui_settings_to_runtime()

        # Mayak panel refs
        self._mayak_profile_combo: Optional[QComboBox] = None
        self._mayak_duration_spin: Optional[QDoubleSpinBox] = None
        self._mayak_duration_override: Optional[QCheckBox] = None
        self._lbl_mayak_duration_calc: Optional[QLabel] = None
        self._lbl_mayak_duration_sent: Optional[QLabel] = None
        self._head_start_spin: Optional[QSpinBox] = None
        self._head_end_spin: Optional[QSpinBox] = None
        self._tail_start_spin: Optional[QSpinBox] = None
        self._tail_end_spin: Optional[QSpinBox] = None
        self._btn_mayak_start_test: Optional[QPushButton] = None
        self._btn_mayak_stop_test: Optional[QPushButton] = None
        self._btn_mayak_emergency: Optional[QPushButton] = None
        self._lbl_mayak_ready: Optional[QLabel] = None
        self._lbl_mayak_connected: Optional[QLabel] = None
        self._lbl_mayak_state: Optional[QLabel] = None
        self._lbl_mayak_error: Optional[QLabel] = None
        self._lbl_mayak_reason: Optional[QLabel] = None
        self._mayak_tel_labels: dict[str, dict[str, QLabel]] = {}
        self._init_mayak_panel()
        self._btn_prepare_test: Optional[QPushButton] = None
        self._btn_check_readiness_m: Optional[QPushButton] = None
        self._btn_start_test_m: Optional[QPushButton] = None
        self._btn_stop_test_m: Optional[QPushButton] = None
        self._prep_progress: Optional[QProgressBar] = None
        self._readiness_progress_m: Optional[QProgressBar] = None
        self._lbl_session_id_m: Optional[QLabel] = None
        self._lbl_session_status_m: Optional[QLabel] = None
        self._lbl_session_elapsed_m: Optional[QLabel] = None
        self._lbl_session_video_m: Optional[QLabel] = None
        self._lbl_session_gps_m: Optional[QLabel] = None
        self._lbl_session_degraded_m: Optional[QLabel] = None
        self._lbl_runtime_gate_m: Optional[QLabel] = None
        self._lbl_last_test_result_m: Optional[QLabel] = None
        self._btn_open_last_results_m: Optional[QPushButton] = None
        self._session_output_root_m_edit: Optional[QLineEdit] = None
        self._btn_session_output_root_m_browse: Optional[QPushButton] = None
        self._btn_session_output_root_m_default: Optional[QPushButton] = None
        self._btn_replay_open_m: Optional[QPushButton] = None
        self._btn_replay_play_m: Optional[QPushButton] = None
        self._btn_replay_stop_m: Optional[QPushButton] = None
        self._btn_replay_back_m: Optional[QPushButton] = None
        self._btn_replay_fwd_m: Optional[QPushButton] = None
        self._btn_replay_step_back_m: Optional[QPushButton] = None
        self._btn_replay_step_fwd_m: Optional[QPushButton] = None
        self._replay_slider_m: Optional[QSlider] = None
        self._replay_t_spin_m: Optional[QDoubleSpinBox] = None
        self._replay_rate_combo_m: Optional[QComboBox] = None
        self._replay_shortcuts_m: list[QShortcut] = []
        self._lbl_replay_session_m: Optional[QLabel] = None
        self._lbl_replay_trel_m: Optional[QLabel] = None
        self._lbl_replay_visible_info_m: Optional[QLabel] = None
        self._lbl_replay_thermal_info_m: Optional[QLabel] = None
        self._lbl_replay_traj_info_m: Optional[QLabel] = None
        self._lbl_replay_visible_img_m: Optional[QLabel] = None
        self._lbl_replay_thermal_img_m: Optional[QLabel] = None
        self._init_functional_buttons()

        self._traj_loader = TrajectoryCsvLoader()
        self._traj_ctl = TrajectoryVisController(
            bridge=self._bridge,
            view=self._traj_view,
            loader=self._traj_loader,
            on_duration_resolved=self._on_trajectory_duration_resolved,
            on_points_resolved=self._on_trajectory_points_resolved,
        )

        if self._editor is not None:
            self._gen_ctl = GenerateController(
                orchestrator=self._orch,
                editor=self._editor,
                traj_view=self._traj_view,
                traj_ctl=self._traj_ctl,
                set_generate_enabled=self._set_generate_enabled,
                bus_getter=lambda: getattr(self._bridge, "_bus", None),
            )
        else:
            self._gen_ctl = None

        self._connect_actions()
        self._connect_bridge()
        self._runtime_ui_timer = QTimer(self)
        self._runtime_ui_timer.setInterval(100)
        self._runtime_ui_timer.timeout.connect(self._on_runtime_ui_tick)
        self._runtime_ui_timer.start()
        self._on_runtime_ui_tick()
        self._replay_timer = QTimer(self)
        self._replay_timer.setInterval(125)
        self._replay_timer.timeout.connect(self._on_replay_timer_tick)
        self._replay_timer.start()

    # ---------------- config source ----------------

    def _load_initial_config_json(self) -> dict[str, Any]:
        cfg: Optional[dict[str, Any]] = None

        try:
            for attr in ("profile", "profile_dict", "profile_data", "profile_cfg"):
                prof = getattr(self._orch, attr, None)
                cfg = self._extract_ballistics_config_json(prof)
                if cfg is not None:
                    self._log_info("UI_CONFIG_SOURCE", f"source=orchestrator.{attr}")
                    return cfg
        except Exception as ex:
            self._log_info("UI_CONFIG_SOURCE_FAILED", f"source=orchestrator err={type(ex).__name__}")

        self._log_info("UI_CONFIG_SOURCE", "source=default")
        return copy.deepcopy(_DEFAULT_CONFIG_JSON)

    def _extract_ballistics_config_json(self, profile_obj: Any) -> Optional[dict[str, Any]]:
        if not isinstance(profile_obj, dict):
            return None
        services = profile_obj.get("services")
        if isinstance(services, dict):
            bm = services.get("ballistics_model")
            if isinstance(bm, dict):
                cj = bm.get("config_json")
                if isinstance(cj, dict):
                    return copy.deepcopy(cj)
        cj2 = profile_obj.get("config_json")
        if isinstance(cj2, dict):
            return copy.deepcopy(cj2)
        return None

    def _normalize_ballistics_config_for_ui(self, cfg: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(cfg) if isinstance(cfg, dict) else copy.deepcopy(_DEFAULT_CONFIG_JSON)
        ic = out.get("initial_conditions")
        if not isinstance(ic, dict):
            return out

        # Keep only operator-facing keys: speed and angles.
        for legacy_key in ("Vx0", "Vy0", "Vz0", "wx0", "wy0", "wz0", "X0", "Y0", "Z0", "omega_body"):
            ic.pop(legacy_key, None)

        if "psi_deg" not in ic:
            ic["psi_deg"] = 0.0
        return out

    def _load_ui_settings(self) -> dict[str, Any]:
        nav_path = str(self._settings_store.value("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH) or "").strip()
        if not nav_path:
            nav_path = _DEFAULT_GPS_NAV_PATH
        session_output_root_raw = str(
            self._settings_store.value("session_output_root", _DEFAULT_SESSION_OUTPUT_ROOT) or ""
        ).strip()
        session_output_root = self._normalize_session_output_root(session_output_root_raw)
        auto_stop = self._settings_store.value("auto_stop_after_gps_sec", _DEFAULT_AUTO_STOP_AFTER_GPS_SEC)
        anim = self._settings_store.value("monitor_anim_without_test", _DEFAULT_ANIM_WITHOUT_TEST)
        try:
            auto_stop_val = float(auto_stop)
        except Exception:
            auto_stop_val = _DEFAULT_AUTO_STOP_AFTER_GPS_SEC
        auto_stop_val = max(0.0, min(3600.0, auto_stop_val))
        anim_val = str(anim).strip().lower() in ("1", "true", "yes", "on")
        return {
            "gps_nav_default_path": nav_path,
            "session_output_root": session_output_root,
            "auto_stop_after_gps_sec": auto_stop_val,
            "monitor_anim_without_test": anim_val,
        }

    def _save_ui_settings(self) -> None:
        self._settings_store.setValue("gps_nav_default_path", str(self._settings.get("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH)))
        self._settings_store.setValue(
            "session_output_root",
            str(self._settings.get("session_output_root", _DEFAULT_SESSION_OUTPUT_ROOT)),
        )
        self._settings_store.setValue(
            "auto_stop_after_gps_sec",
            float(self._settings.get("auto_stop_after_gps_sec", _DEFAULT_AUTO_STOP_AFTER_GPS_SEC)),
        )
        self._settings_store.setValue(
            "monitor_anim_without_test",
            bool(self._settings.get("monitor_anim_without_test", _DEFAULT_ANIM_WITHOUT_TEST)),
        )
        self._settings_store.sync()

    def _apply_ui_settings_to_runtime(self) -> None:
        nav = str(self._settings.get("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH)).strip()
        if self._gps_nav_path_edit is not None:
            self._gps_nav_path_edit.setText(nav)
        session_output_root = self._normalize_session_output_root(
            str(self._settings.get("session_output_root", _DEFAULT_SESSION_OUTPUT_ROOT))
        )
        self._settings["session_output_root"] = session_output_root
        try:
            applied_output_root = self._orch.set_test_session_output_root(session_output_root)
        except Exception:
            applied_output_root = self._orch.get_test_session_output_root()
        if self._session_output_root_m_edit is not None:
            self._session_output_root_m_edit.setText(applied_output_root)
        if self._opt_session_output_root_edit is not None:
            self._opt_session_output_root_edit.setText(applied_output_root)
        auto_stop = float(self._settings.get("auto_stop_after_gps_sec", _DEFAULT_AUTO_STOP_AFTER_GPS_SEC))
        self._orch.set_auto_stop_after_gps_sec(auto_stop)
        self._anim_without_test_enabled = bool(
            self._settings.get("monitor_anim_without_test", _DEFAULT_ANIM_WITHOUT_TEST)
        )

    @staticmethod
    def _normalize_session_output_root(value: str) -> str:
        txt = str(value or "").strip()
        if not txt:
            txt = _DEFAULT_SESSION_OUTPUT_ROOT
        return str(Path(txt).expanduser().resolve())

    # ---------------- UI init ----------------

    def _init_editor(self) -> None:
        glp = self._gl_trajectory_params
        if glp is None:
            return

        self._editor = ConfigJsonEditor(initial_config=self._initial_config)
        glp.addWidget(self._editor, 0, 0)

        btn = self._get_generate_button()
        if btn is None:
            btn = QPushButton("Сгенерировать траекторию")
            btn.setObjectName("btn_generate_trajectory")
            glp.addWidget(btn, 1, 0)
        else:
            btn.setText("Сгенерировать траекторию")

        glp.setRowStretch(0, 1)
        glp.setRowStretch(1, 0)
        glp.setColumnStretch(0, 1)

    def _init_trajectory_view(self) -> None:
        vl = self._vl_trajectory_visualization
        if vl is None:
            return
        while vl.count():
            it = vl.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        vl.addWidget(self._traj_view)
        vl.setStretch(0, 1)

    def _init_monitor_trajectory_view(self) -> None:
        vl = self._vl_trajectory_visualization_m
        if vl is None:
            return
        while vl.count():
            it = vl.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        vl.addWidget(self._traj_view_m)
        vl.setStretch(0, 1)
        self._traj_view_m.set_status("Мониторинг траектории (3D)\nОжидание подготовленного сценария")

    def _init_monitor_params_panel(self) -> None:
        gl = self._gl_trajectory_params_m
        if gl is None:
            return
        self._clear_layout(gl)

        box = QGroupBox("Параметры траектории (мониторинг)", self)
        form = QFormLayout(box)

        self._m_speed_lbl = QLabel("-", box)
        self._m_coords_lbl = QLabel("-", box)
        self._m_geo_lbl = QLabel("-", box)
        self._m_height_lbl = QLabel("-", box)
        self._m_distance_lbl = QLabel("-", box)

        form.addRow("Скорость, м/с", self._m_speed_lbl)
        form.addRow("Координаты X/Y/Z, м", self._m_coords_lbl)
        form.addRow("Земные координаты lat/lon/h", self._m_geo_lbl)
        form.addRow("Высота, м", self._m_height_lbl)
        form.addRow("Пройдено, м", self._m_distance_lbl)

        gl.addWidget(box, 0, 0)
        gl.setRowStretch(1, 1)
        gl.setColumnStretch(0, 1)

    def _init_replay_panel(self) -> None:
        gl_video = self._gl_replay_video
        gl_graph = self._gl_replay_graph
        gl_3d = self._gl_replay_3d
        gl_value = self._gl_replay_value_manage
        gl_legacy = self._gl_replay
        has_new_layout = gl_video is not None and gl_3d is not None and gl_value is not None
        if not has_new_layout and gl_legacy is None:
            return
        if has_new_layout:
            self._clear_layout(cast(QLayout, gl_video))
            self._clear_layout(cast(QLayout, gl_3d))
            if gl_graph is not None:
                self._clear_layout(cast(QLayout, gl_graph))
            self._clear_layout(cast(QLayout, gl_value))
        elif gl_legacy is not None:
            self._clear_layout(gl_legacy)

        value_box = QGroupBox("Синхронный просмотр (офлайн)", self)
        value_form = QFormLayout(value_box)

        self._btn_replay_open_m = QPushButton("Открыть сессию", value_box)
        self._btn_replay_play_m = QPushButton("Воспроизвести", value_box)
        self._btn_replay_play_m.setCheckable(True)
        self._btn_replay_play_m.setEnabled(False)
        self._btn_replay_stop_m = QPushButton("Стоп", value_box)
        self._btn_replay_stop_m.setEnabled(False)
        self._btn_replay_back_m = QPushButton("<<", value_box)
        self._btn_replay_back_m.setEnabled(False)
        self._btn_replay_fwd_m = QPushButton(">>", value_box)
        self._btn_replay_fwd_m.setEnabled(False)
        self._btn_replay_step_back_m = QPushButton("-1 c", value_box)
        self._btn_replay_step_back_m.setEnabled(False)
        self._btn_replay_step_fwd_m = QPushButton("+1 c", value_box)
        self._btn_replay_step_fwd_m.setEnabled(False)
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.addWidget(self._btn_replay_open_m)
        hdr_row.addWidget(self._btn_replay_play_m)
        hdr_row.addWidget(self._btn_replay_stop_m)
        hdr_row.addWidget(self._btn_replay_back_m)
        hdr_row.addWidget(self._btn_replay_step_back_m)
        hdr_row.addWidget(self._btn_replay_step_fwd_m)
        hdr_row.addWidget(self._btn_replay_fwd_m)

        self._lbl_replay_session_m = QLabel("Сессия: -", value_box)
        self._lbl_replay_trel_m = QLabel("t: 0.000 c", value_box)
        self._replay_slider_m = QSlider(Qt.Orientation.Horizontal, value_box)
        self._replay_slider_m.setRange(0, 0)
        self._replay_slider_m.setEnabled(False)
        self._replay_t_spin_m = QDoubleSpinBox(value_box)
        self._replay_t_spin_m.setDecimals(3)
        self._replay_t_spin_m.setSuffix(" c")
        self._replay_t_spin_m.setRange(0.0, 0.0)
        self._replay_t_spin_m.setSingleStep(0.1)
        self._replay_t_spin_m.setEnabled(False)
        self._replay_rate_combo_m = QComboBox(value_box)
        self._replay_rate_combo_m.addItems(["0.5x", "1x", "2x"])
        self._replay_rate_combo_m.setCurrentText("1x")
        self._replay_rate_combo_m.setEnabled(False)
        control_row = QHBoxLayout()
        control_row.setContentsMargins(0, 0, 0, 0)
        control_row.addWidget(QLabel("t, c:", value_box))
        control_row.addWidget(self._replay_t_spin_m, 1)
        control_row.addSpacing(8)
        control_row.addWidget(QLabel("Скорость:", value_box))
        control_row.addWidget(self._replay_rate_combo_m)

        self._lbl_replay_visible_info_m = QLabel("Видимый канал: -", value_box)
        self._lbl_replay_thermal_info_m = QLabel("Тепловой канал: -", value_box)
        self._lbl_replay_traj_info_m = QLabel("Траектория: -", value_box)
        self._lbl_replay_visible_img_m = QLabel("нет кадра", self)
        self._lbl_replay_thermal_img_m = QLabel("нет кадра", self)
        for img_lbl in (self._lbl_replay_visible_img_m, self._lbl_replay_thermal_img_m):
            img_lbl.setMinimumSize(240, 135)
            img_lbl.setStyleSheet("border:1px solid #666; background:#111; color:#ddd;")
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        video_box = QGroupBox("Видео (синхронные кадры)", self)
        video_layout = QVBoxLayout(video_box)
        img_col = QVBoxLayout()
        img_col.setContentsMargins(0, 0, 0, 0)
        img_col.setSpacing(8)
        for img_lbl in (self._lbl_replay_visible_img_m, self._lbl_replay_thermal_img_m):
            img_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        img_col.addWidget(self._lbl_replay_visible_img_m)
        img_col.addWidget(self._lbl_replay_thermal_img_m)
        video_layout.addLayout(img_col, 1)

        value_form.addRow(hdr_row)
        value_form.addRow(self._lbl_replay_session_m)
        value_form.addRow(self._lbl_replay_trel_m)
        value_form.addRow(self._replay_slider_m)
        value_form.addRow(control_row)
        value_form.addRow(self._lbl_replay_visible_info_m)
        value_form.addRow(self._lbl_replay_thermal_info_m)
        value_form.addRow(self._lbl_replay_traj_info_m)
        self._replay_shortcuts_m = [
            QShortcut(QKeySequence(Qt.Key.Key_Space), value_box),
            QShortcut(QKeySequence(Qt.Key.Key_Left), value_box),
            QShortcut(QKeySequence(Qt.Key.Key_Right), value_box),
        ]
        self._replay_shortcuts_m[0].activated.connect(self._on_replay_shortcut_toggle_play)
        self._replay_shortcuts_m[1].activated.connect(self._on_replay_shortcut_step_back)
        self._replay_shortcuts_m[2].activated.connect(self._on_replay_shortcut_step_fwd)
        for sc in self._replay_shortcuts_m:
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

        self._traj_view_r.set_status("Траектория просмотра (3D)\nОткройте завершенную сессию")
        if has_new_layout:
            cast(QGridLayout, gl_video).addWidget(video_box, 0, 0)
            cast(QGridLayout, gl_3d).addWidget(self._traj_view_r, 0, 0)
            value_layout = cast(QGridLayout, gl_value)
            value_layout.addWidget(value_box, 1, 0, alignment=Qt.AlignmentFlag.AlignBottom)
            if gl_graph is not None:
                graph_hint = QLabel("Графики (2D) размещаются в этом блоке", self)
                graph_hint.setStyleSheet("color:#666;")
                cast(QGridLayout, gl_graph).addWidget(graph_hint, 0, 0)
            cast(QGridLayout, gl_video).setColumnStretch(0, 1)
            cast(QGridLayout, gl_video).setRowStretch(0, 1)
            cast(QGridLayout, gl_3d).setColumnStretch(0, 1)
            cast(QGridLayout, gl_3d).setRowStretch(0, 1)
            if gl_graph is not None:
                cast(QGridLayout, gl_graph).setColumnStretch(0, 1)
                cast(QGridLayout, gl_graph).setRowStretch(0, 1)
            value_layout.setColumnStretch(0, 1)
            value_layout.setRowStretch(0, 1)
            value_layout.setRowStretch(1, 0)
        elif gl_legacy is not None:
            gl_legacy.addWidget(self._traj_view_r, 0, 0)
            gl_legacy.addWidget(value_box, 0, 1)
            gl_legacy.setColumnStretch(0, 2)
            gl_legacy.setColumnStretch(1, 1)

    def _init_options_panel(self) -> None:
        gl = self._gl_options
        if gl is None:
            return
        self._clear_layout(gl)

        box = QGroupBox("Пользовательские настройки", self)
        form = QFormLayout(box)

        self._opt_auto_stop_spin = QDoubleSpinBox(box)
        self._opt_auto_stop_spin.setRange(0.0, 3600.0)
        self._opt_auto_stop_spin.setDecimals(1)
        self._opt_auto_stop_spin.setSingleStep(1.0)
        self._opt_auto_stop_spin.setValue(float(self._settings.get("auto_stop_after_gps_sec", _DEFAULT_AUTO_STOP_AFTER_GPS_SEC)))
        self._opt_auto_stop_spin.valueChanged.connect(self._on_setting_auto_stop_changed)

        self._opt_anim_without_test_chk = QCheckBox("Анимировать полет без испытания", box)
        self._opt_anim_without_test_chk.setChecked(
            bool(self._settings.get("monitor_anim_without_test", _DEFAULT_ANIM_WITHOUT_TEST))
        )
        self._opt_anim_without_test_chk.toggled.connect(self._on_setting_anim_without_test_toggled)

        self._opt_nav_default_edit = QLineEdit(box)
        self._opt_nav_default_edit.setText(str(self._settings.get("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH)))
        self._opt_nav_default_edit.setPlaceholderText(_DEFAULT_GPS_NAV_PATH)
        self._opt_nav_default_edit.editingFinished.connect(self._on_setting_nav_path_edited)
        self._opt_nav_default_browse = QPushButton("...", box)
        self._opt_nav_default_browse.setFixedWidth(34)
        self._opt_nav_default_browse.clicked.connect(self._on_setting_nav_path_browse)
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.addWidget(self._opt_nav_default_edit)
        nav_row.addWidget(self._opt_nav_default_browse)

        self._opt_session_output_root_edit = QLineEdit(box)
        self._opt_session_output_root_edit.setText(
            str(self._settings.get("session_output_root", _DEFAULT_SESSION_OUTPUT_ROOT))
        )
        self._opt_session_output_root_edit.setPlaceholderText(_DEFAULT_SESSION_OUTPUT_ROOT)
        self._opt_session_output_root_edit.editingFinished.connect(self._on_setting_session_output_root_edited)
        self._opt_session_output_root_browse = QPushButton("...", box)
        self._opt_session_output_root_browse.setFixedWidth(34)
        self._opt_session_output_root_browse.clicked.connect(self._on_setting_session_output_root_browse)
        session_root_row = QHBoxLayout()
        session_root_row.setContentsMargins(0, 0, 0, 0)
        session_root_row.addWidget(self._opt_session_output_root_edit)
        session_root_row.addWidget(self._opt_session_output_root_browse)

        self._opt_reset_defaults_btn = QPushButton("Вернуть к дефолтным", box)
        self._opt_reset_defaults_btn.clicked.connect(self._on_settings_reset_defaults_clicked)

        form.addRow("Авто-стоп после завершения GPS трансляции, сек", self._opt_auto_stop_spin)
        form.addRow("", self._opt_anim_without_test_chk)
        form.addRow("Дефолтный путь к эфемеридам", nav_row)
        form.addRow("Папка сессий по умолчанию", session_root_row)
        form.addRow(self._opt_reset_defaults_btn)

        gl.addWidget(box, 0, 0)
        gl.setRowStretch(1, 1)
        gl.setColumnStretch(0, 1)

    def _init_rtsp_previews(self) -> None:
        if self._vl_rtsp_visible is not None:
            w = RtspPreviewWidget(_DEFAULT_PREVIEW_VISIBLE, title="Visible", poll_ms=200, parent=self)
            w.setMaximumWidth(500)
            self._vl_rtsp_visible.addWidget(w, 0, 0)
        if self._vl_rtsp_thermal is not None:
            w = RtspPreviewWidget(_DEFAULT_PREVIEW_THERMAL, title="Thermal", poll_ms=200, parent=self)
            w.setMaximumWidth(500)
            self._vl_rtsp_thermal.addWidget(w, 0, 0)

    def _init_sdr_options_panel(self) -> None:
        gl_scenario = self._gl_sdr_options
        gl_monitor = self._gl_gps_sdr_options_m
        if gl_scenario is None and gl_monitor is None:
            return

        if gl_scenario is not None:
            self._clear_layout(gl_scenario)
        if gl_monitor is not None:
            self._clear_layout(gl_monitor)

        gps_box = QGroupBox("GPS SDR Sim", self)
        gps_form = QFormLayout(gps_box)

        self._gps_nav_path_edit = QLineEdit(gps_box)
        self._gps_nav_path_edit.setText(str(self._settings.get("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH)))
        self._gps_nav_path_edit.setPlaceholderText("data/ephemerides/brdc0430.25n")
        self._btn_gps_nav_browse = QPushButton("...", gps_box)
        self._btn_gps_nav_browse.setFixedWidth(34)
        self._btn_gps_nav_browse.clicked.connect(self._on_gps_nav_browse_clicked)
        self._btn_gps_nav_default = QPushButton("Путь по умолчанию", gps_box)
        self._btn_gps_nav_default.clicked.connect(self._on_gps_nav_use_default_clicked)
        nav_hdr_row = QHBoxLayout()
        nav_hdr_row.setContentsMargins(0, 0, 0, 0)
        nav_hdr_row.addWidget(QLabel("Путь к эфемеридам", gps_box))
        nav_hdr_row.addStretch(1)
        nav_hdr_row.addWidget(self._btn_gps_nav_default)
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.addWidget(self._gps_nav_path_edit)
        nav_row.addWidget(self._btn_gps_nav_browse)

        self._gps_static_sec_spin = QDoubleSpinBox(gps_box)
        self._gps_static_sec_spin.setRange(0.0, 36000.0)
        self._gps_static_sec_spin.setDecimals(1)
        self._gps_static_sec_spin.setSingleStep(1.0)
        self._gps_static_sec_spin.setValue(_DEFAULT_GPS_STATIC_SEC)

        self._gps_origin_lat_spin = QDoubleSpinBox(gps_box)
        self._gps_origin_lat_spin.setRange(-90.0, 90.0)
        self._gps_origin_lat_spin.setDecimals(6)
        self._gps_origin_lat_spin.setSingleStep(0.0001)
        self._gps_origin_lat_spin.setValue(_DEFAULT_GPS_ORIGIN_LAT)
        self._gps_origin_lat_spin.valueChanged.connect(self._on_gps_origin_changed)

        self._gps_origin_lon_spin = QDoubleSpinBox(gps_box)
        self._gps_origin_lon_spin.setRange(-180.0, 180.0)
        self._gps_origin_lon_spin.setDecimals(6)
        self._gps_origin_lon_spin.setSingleStep(0.0001)
        self._gps_origin_lon_spin.setValue(_DEFAULT_GPS_ORIGIN_LON)
        self._gps_origin_lon_spin.valueChanged.connect(self._on_gps_origin_changed)

        self._gps_origin_h_spin = QDoubleSpinBox(gps_box)
        self._gps_origin_h_spin.setRange(-500.0, 12000.0)
        self._gps_origin_h_spin.setDecimals(2)
        self._gps_origin_h_spin.setSingleStep(1.0)
        self._gps_origin_h_spin.setValue(_DEFAULT_GPS_ORIGIN_H_M)
        self._gps_origin_h_spin.valueChanged.connect(self._on_gps_origin_changed)

        self._gps_finish_lat_lbl = QLabel("Нет траектории", gps_box)
        self._gps_finish_lon_lbl = QLabel("Нет траектории", gps_box)
        self._gps_finish_h_lbl = QLabel("Нет траектории", gps_box)

        gps_form.addRow(nav_hdr_row)
        gps_form.addRow(nav_row)
        gps_form.addRow("Время статики, сек", self._gps_static_sec_spin)
        gps_form.addRow("Старт: широта, °", self._gps_origin_lat_spin)
        gps_form.addRow("Старт: долгота, °", self._gps_origin_lon_spin)
        gps_form.addRow("Старт: высота, м", self._gps_origin_h_spin)
        gps_form.addRow("Финиш: широта, °", self._gps_finish_lat_lbl)
        gps_form.addRow("Финиш: долгота, °", self._gps_finish_lon_lbl)
        gps_form.addRow("Финиш: высота, м", self._gps_finish_h_lbl)

        pluto_box = QGroupBox("PlutoPlayer", self)
        pluto_form = QFormLayout(pluto_box)

        self._pluto_rf_bw_spin = QDoubleSpinBox(pluto_box)
        self._pluto_rf_bw_spin.setRange(1.0, 5.0)
        self._pluto_rf_bw_spin.setDecimals(2)
        self._pluto_rf_bw_spin.setSingleStep(0.25)
        self._pluto_rf_bw_spin.setValue(_DEFAULT_PLUTO_RF_BW_MHZ)

        self._pluto_tx_atten_spin = QDoubleSpinBox(pluto_box)
        self._pluto_tx_atten_spin.setRange(-80.0, 0.0)
        self._pluto_tx_atten_spin.setDecimals(2)
        self._pluto_tx_atten_spin.setSingleStep(0.25)
        self._pluto_tx_atten_spin.setValue(_DEFAULT_PLUTO_TX_ATTEN_DB)

        pluto_form.addRow("Полоса пропускания, МГц", self._pluto_rf_bw_spin)
        pluto_form.addRow("Ослабление TX, дБ", self._pluto_tx_atten_spin)

        if gl_scenario is not None:
            gl_scenario.addWidget(gps_box, 0, 0)
            gl_scenario.setRowStretch(1, 1)
            gl_scenario.setColumnStretch(0, 1)

        # Pluto settings are operator controls for monitoring phase.
        if gl_monitor is not None:
            gl_monitor.addWidget(pluto_box, 0, 0)
            gl_monitor.setRowStretch(1, 1)
            gl_monitor.setColumnStretch(0, 1)

    def _init_mayak_panel(self) -> None:
        vl = self._vl_mayak_params
        if vl is None:
            return

        self._clear_layout(vl)

        panel = QWidget(self)
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)

        control_box = QGroupBox("Управление Маяком", panel)
        control_form = QFormLayout(control_box)

        self._head_start_spin = QSpinBox(control_box)
        self._head_start_spin.setRange(0, 6000)
        self._head_start_spin.setSingleStep(50)
        self._head_start_spin.setValue(300)

        self._head_end_spin = QSpinBox(control_box)
        self._head_end_spin.setRange(0, 6000)
        self._head_end_spin.setSingleStep(50)
        self._head_end_spin.setValue(1000)

        self._tail_start_spin = QSpinBox(control_box)
        self._tail_start_spin.setRange(0, 6000)
        self._tail_start_spin.setSingleStep(50)
        self._tail_start_spin.setValue(300)

        self._tail_end_spin = QSpinBox(control_box)
        self._tail_end_spin.setRange(0, 6000)
        self._tail_end_spin.setSingleStep(50)
        self._tail_end_spin.setValue(1000)

        self._mayak_profile_combo = QComboBox(control_box)
        self._mayak_profile_combo.addItem("Линейный", "linear")
        self._mayak_profile_combo.addItem("Ступенька", "step")

        self._mayak_duration_spin = QDoubleSpinBox(control_box)
        self._mayak_duration_spin.setRange(0.1, 3600.0)
        self._mayak_duration_spin.setDecimals(1)
        self._mayak_duration_spin.setSingleStep(1.0)
        self._mayak_duration_spin.setValue(10.0)
        self._mayak_duration_spin.setEnabled(False)
        self._mayak_duration_override = QCheckBox("Переопределить длительность вручную", control_box)
        self._mayak_duration_override.setChecked(False)
        self._lbl_mayak_duration_calc = QLabel("-", control_box)
        self._lbl_mayak_duration_sent = QLabel("-", control_box)

        control_form.addRow("Головной: старт RPM", self._head_start_spin)
        control_form.addRow("Головной: конечный RPM", self._head_end_spin)
        control_form.addRow("Хвостовой: старт RPM", self._tail_start_spin)
        control_form.addRow("Хвостовой: конечный RPM", self._tail_end_spin)
        control_form.addRow("Тип изменения скорости", self._mayak_profile_combo)
        control_form.addRow("Длительность по траектории, сек", self._lbl_mayak_duration_calc)
        control_form.addRow("", self._mayak_duration_override)
        control_form.addRow("Override длительности, сек", self._mayak_duration_spin)
        control_form.addRow("Отправлено в Маяк, сек", self._lbl_mayak_duration_sent)

        btn_row = QHBoxLayout()
        self._btn_mayak_start_test = QPushButton("Запуск теста", control_box)
        self._btn_mayak_stop_test = QPushButton("Остановить тест", control_box)
        self._btn_mayak_emergency = QPushButton("Аварийный стоп", control_box)
        btn_row.addWidget(self._btn_mayak_start_test)
        btn_row.addWidget(self._btn_mayak_stop_test)
        btn_row.addWidget(self._btn_mayak_emergency)
        control_form.addRow(btn_row)

        status_box = QGroupBox("Состояние Маяка", panel)
        status_form = QFormLayout(status_box)
        self._lbl_mayak_ready = QLabel("-", status_box)
        self._lbl_mayak_state = QLabel("-", status_box)
        status_form.addRow("Готов", self._lbl_mayak_ready)
        status_form.addRow("Состояние", self._lbl_mayak_state)
        if self._ui_debug:
            self._lbl_mayak_connected = QLabel("-", status_box)
            self._lbl_mayak_error = QLabel("-", status_box)
            self._lbl_mayak_reason = QLabel("-", status_box)
            status_form.addRow("Связь", self._lbl_mayak_connected)
            status_form.addRow("Ошибка", self._lbl_mayak_error)
            status_form.addRow("Причина", self._lbl_mayak_reason)

            telemetry_box = QGroupBox("Телеметрия Маяка", panel)
            telemetry_grid = QGridLayout(telemetry_box)
            telemetry_grid.addWidget(QLabel("Шпиндель", telemetry_box), 0, 0)
            telemetry_grid.addWidget(QLabel("RPM", telemetry_box), 0, 1)
            telemetry_grid.addWidget(QLabel("Момент", telemetry_box), 0, 2)
            telemetry_grid.addWidget(QLabel("Угол", telemetry_box), 0, 3)

            for row, sp, title in ((1, "sp1", "Головной"), (2, "sp2", "Хвостовой")):
                telemetry_grid.addWidget(QLabel(title, telemetry_box), row, 0)
                lbl_rpm = QLabel("-", telemetry_box)
                lbl_torque = QLabel("-", telemetry_box)
                lbl_angle = QLabel("-", telemetry_box)
                telemetry_grid.addWidget(lbl_rpm, row, 1)
                telemetry_grid.addWidget(lbl_torque, row, 2)
                telemetry_grid.addWidget(lbl_angle, row, 3)
                self._mayak_tel_labels[sp] = {
                    "rpm": lbl_rpm,
                    "torque": lbl_torque,
                    "angle": lbl_angle,
                }
        else:
            self._lbl_mayak_connected = None
            self._lbl_mayak_error = None
            self._lbl_mayak_reason = None

        root.addWidget(control_box)
        root.addWidget(status_box)
        if self._ui_debug:
            root.addWidget(telemetry_box)
        root.addStretch(1)

        vl.addWidget(panel)
        self._refresh_duration_labels()

    def _init_functional_buttons(self) -> None:
        gl = self._gl_functional_buttons
        if gl is not None:
            self._clear_layout(gl)

            self._btn_prepare_test = QPushButton("Подготовиться к тесту", self)
            self._btn_prepare_test.setObjectName("btn_prepare_test")

            self._prep_progress = QProgressBar(self)
            self._prep_progress.setObjectName("pb_prepare_test")
            self._prep_progress.setRange(0, 100)
            self._prep_progress.setValue(0)
            self._prep_progress.setVisible(False)
            self._prep_progress.setTextVisible(True)

            gl.addWidget(self._btn_prepare_test, 0, 0)
            gl.addWidget(self._prep_progress, 0, 1)
            gl.setColumnStretch(0, 0)
            gl.setColumnStretch(1, 1)

        glm = self._gl_functional_buttons_m
        if glm is not None:
            self._clear_layout(glm)
            self._btn_check_readiness_m = QPushButton("Проверить готовность систем", self)
            self._btn_check_readiness_m.setObjectName("btn_check_readiness_m")
            self._btn_start_test_m = QPushButton("Начать испытание", self)
            self._btn_start_test_m.setObjectName("btn_start_test_m")
            self._btn_stop_test_m = QPushButton("Остановить испытание", self)
            self._btn_stop_test_m.setObjectName("btn_stop_test_m")
            self._readiness_progress_m = QProgressBar(self)
            self._readiness_progress_m.setObjectName("pb_readiness_check_m")
            self._readiness_progress_m.setRange(0, 100)
            self._readiness_progress_m.setValue(0)
            self._readiness_progress_m.setVisible(False)
            self._readiness_progress_m.setTextVisible(True)
            self._lbl_runtime_gate_m = QLabel("Критический статус: готов к запуску", self)
            self._lbl_runtime_gate_m.setStyleSheet("font-weight:700; color:#2e7d32;")
            self._lbl_session_id_m = QLabel("Сессия: -", self)
            self._lbl_session_status_m = QLabel("Статус: -", self)
            self._lbl_session_elapsed_m = QLabel("Время: 00:00.0", self)
            self._lbl_session_video_m = QLabel("Видео: -", self)
            self._lbl_session_gps_m = QLabel("GPS трансляция: -", self)
            self._lbl_session_degraded_m = QLabel("Режим: -", self)
            self._lbl_last_test_result_m = QLabel("Последнее испытание: не запускалось", self)
            self._btn_open_last_results_m = QPushButton("Открыть папку результатов", self)
            self._btn_open_last_results_m.setEnabled(False)
            self._session_output_root_m_edit = QLineEdit(self)
            self._session_output_root_m_edit.setText(str(self._settings.get("session_output_root", _DEFAULT_SESSION_OUTPUT_ROOT)))
            self._session_output_root_m_edit.setPlaceholderText(_DEFAULT_SESSION_OUTPUT_ROOT)
            self._session_output_root_m_edit.setFixedHeight(26)
            self._session_output_root_m_edit.setStyleSheet("color:#444;")
            self._btn_session_output_root_m_browse = QPushButton("...", self)
            self._btn_session_output_root_m_browse.setFixedWidth(34)
            self._btn_session_output_root_m_default = QPushButton("Путь по умолчанию", self)
            session_row = QHBoxLayout()
            session_row.setContentsMargins(0, 0, 0, 0)
            session_row.addWidget(self._session_output_root_m_edit, 1)
            session_row.addWidget(self._btn_session_output_root_m_browse)
            session_row.addWidget(self._btn_session_output_root_m_default)
            session_row_widget = QWidget(self)
            session_row_widget.setLayout(session_row)
            last_row = QHBoxLayout()
            last_row.setContentsMargins(0, 0, 0, 0)
            last_row.addWidget(self._lbl_last_test_result_m, 1)
            last_row.addWidget(self._btn_open_last_results_m, 0)
            last_row_widget = QWidget(self)
            last_row_widget.setLayout(last_row)
            glm.addWidget(self._btn_check_readiness_m, 0, 0)
            glm.addWidget(self._btn_start_test_m, 0, 1)
            glm.addWidget(self._btn_stop_test_m, 0, 2)
            glm.addWidget(self._readiness_progress_m, 1, 0, 1, 3)
            glm.addWidget(self._lbl_runtime_gate_m, 2, 0, 1, 3)
            glm.addWidget(QLabel("Папка результатов:", self), 3, 0, 1, 3)
            glm.addWidget(session_row_widget, 4, 0, 1, 3)
            glm.addWidget(self._lbl_session_id_m, 5, 0, 1, 3)
            glm.addWidget(self._lbl_session_status_m, 6, 0, 1, 3)
            glm.addWidget(self._lbl_session_elapsed_m, 7, 0, 1, 3)
            glm.addWidget(self._lbl_session_video_m, 8, 0, 1, 3)
            glm.addWidget(self._lbl_session_gps_m, 9, 0, 1, 3)
            glm.addWidget(self._lbl_session_degraded_m, 10, 0, 1, 3)
            glm.addWidget(last_row_widget, 11, 0, 1, 3)
            glm.setColumnStretch(0, 1)
            glm.setColumnStretch(1, 1)
            glm.setColumnStretch(2, 1)

    # ---------------- wiring ----------------

    def _connect_actions(self) -> None:
        btn = self._get_generate_button()
        if btn is not None and self._gen_ctl is not None:
            btn.clicked.connect(self._gen_ctl.on_generate_clicked)

        if self._btn_mayak_start_test is not None:
            self._btn_mayak_start_test.clicked.connect(self._on_mayak_start_clicked)
        if self._btn_mayak_stop_test is not None:
            self._btn_mayak_stop_test.clicked.connect(self._on_mayak_stop_clicked)
        if self._btn_mayak_emergency is not None:
            self._btn_mayak_emergency.clicked.connect(self._on_mayak_emergency_clicked)
        if self._mayak_duration_override is not None:
            self._mayak_duration_override.toggled.connect(self._on_mayak_duration_override_toggled)
        if self._btn_prepare_test is not None:
            self._btn_prepare_test.clicked.connect(self._on_prepare_test_clicked)
        if self._btn_check_readiness_m is not None:
            self._btn_check_readiness_m.clicked.connect(self._on_monitor_check_readiness_clicked)
        if self._btn_start_test_m is not None:
            self._btn_start_test_m.clicked.connect(self._on_monitor_start_test_clicked)
        if self._btn_stop_test_m is not None:
            self._btn_stop_test_m.clicked.connect(self._on_monitor_stop_test_clicked)
        if self._session_output_root_m_edit is not None:
            self._session_output_root_m_edit.editingFinished.connect(self._on_monitor_session_output_root_edited)
        if self._btn_session_output_root_m_browse is not None:
            self._btn_session_output_root_m_browse.clicked.connect(self._on_monitor_session_output_root_browse)
        if self._btn_session_output_root_m_default is not None:
            self._btn_session_output_root_m_default.clicked.connect(self._on_monitor_session_output_root_use_default)
        if self._btn_open_last_results_m is not None:
            self._btn_open_last_results_m.clicked.connect(self._on_open_last_results_clicked)
        if self._btn_replay_open_m is not None:
            self._btn_replay_open_m.clicked.connect(self._on_replay_open_session_clicked)
        if self._btn_replay_play_m is not None:
            self._btn_replay_play_m.clicked.connect(self._on_replay_play_toggled)
        if self._btn_replay_stop_m is not None:
            self._btn_replay_stop_m.clicked.connect(self._on_replay_stop_clicked)
        if self._btn_replay_back_m is not None:
            self._btn_replay_back_m.clicked.connect(self._on_replay_back_clicked)
        if self._btn_replay_fwd_m is not None:
            self._btn_replay_fwd_m.clicked.connect(self._on_replay_fwd_clicked)
        if self._btn_replay_step_back_m is not None:
            self._btn_replay_step_back_m.clicked.connect(self._on_replay_step_back_clicked)
        if self._btn_replay_step_fwd_m is not None:
            self._btn_replay_step_fwd_m.clicked.connect(self._on_replay_step_fwd_clicked)
        if self._replay_slider_m is not None:
            self._replay_slider_m.valueChanged.connect(self._on_replay_slider_changed)
        if self._replay_t_spin_m is not None:
            self._replay_t_spin_m.valueChanged.connect(self._on_replay_t_spin_changed)
        if self._replay_rate_combo_m is not None:
            self._replay_rate_combo_m.currentTextChanged.connect(self._on_replay_rate_combo_changed)

    def _connect_bridge(self) -> None:
        try:
            if self._gen_ctl is not None:
                self._bridge.service_status_event.connect(self._gen_ctl.on_service_status_event)
        except Exception:
            pass
        try:
            self._bridge.log_event.connect(self._traj_ctl.on_log_event)
        except Exception:
            pass
        try:
            self._bridge.mayak_health_event.connect(self._on_mayak_health_event)
        except Exception:
            pass
        try:
            self._bridge.mayak_telemetry_event.connect(self._on_mayak_telemetry_event)
        except Exception:
            pass

    def _on_mayak_health_event(self, e: object) -> None:
        service_name = getattr(e, "service_name", "")
        if service_name != "mayak_spindle":
            return

        self._last_mayak_health_event = e
        ready = bool(getattr(e, "ready", False))
        if self._last_mayak_ready is None or self._last_mayak_ready != ready:
            self._last_mayak_ready = ready
            self._log_info("UI_MAYAK_READY", f"ready={1 if ready else 0}")

        if self._lbl_mayak_ready is not None:
            self._lbl_mayak_ready.setText("Да" if ready else "Нет")
        if self._lbl_mayak_connected is not None:
            sp1_conn = self._format_opt_bool(getattr(e, "sp1_connected", None))
            sp2_conn = self._format_opt_bool(getattr(e, "sp2_connected", None))
            self._lbl_mayak_connected.setText(f"Головной={sp1_conn}, Хвостовой={sp2_conn}")
        if self._lbl_mayak_state is not None:
            sp1 = str(getattr(e, "sp1_state", "UNKNOWN"))
            sp2 = str(getattr(e, "sp2_state", "UNKNOWN"))
            self._lbl_mayak_state.setText(f"Головной={sp1}, Хвостовой={sp2}")
        if self._ui_debug:
            if self._lbl_mayak_error is not None:
                self._lbl_mayak_error.setText(str(int(getattr(e, "error_code", 0))))
            if self._lbl_mayak_reason is not None:
                self._lbl_mayak_reason.setText(str(getattr(e, "degraded_reason", "none")))

        self._update_mayak_rpm_limits_from_health(e)

        try:
            sp1 = str(getattr(e, "sp1_state", "UNKNOWN"))
            sp2 = str(getattr(e, "sp2_state", "UNKNOWN"))
            if self._ui_debug:
                err = int(getattr(e, "error_code", 0))
                reason = str(getattr(e, "degraded_reason", "none"))
                msg = f"Маяк готов={1 if ready else 0} головной={sp1} хвостовой={sp2} err={err} reason={reason}"
            else:
                msg = f"Маяк: готов={1 if ready else 0}, головной={sp1}, хвостовой={sp2}"
            self.statusBar().showMessage(msg, 3000)
        except Exception:
            pass

    def _on_mayak_telemetry_event(self, e: object) -> None:
        if str(getattr(e, "service", "")) != "mayak_spindle":
            return
        sp = str(getattr(e, "spindle", "")).lower()
        labels = self._mayak_tel_labels.get(sp)
        if labels is None:
            return

        labels["rpm"].setText(str(int(getattr(e, "actual_speed_rpm", 0))))
        labels["torque"].setText(str(int(getattr(e, "actual_torque", 0))))
        angle = getattr(e, "angle_deg", None)
        labels["angle"].setText("-" if angle is None else str(int(angle)))

    def _on_mayak_start_clicked(self) -> None:
        head_start = int(self._head_start_spin.value()) if self._head_start_spin is not None else 0
        head_end = int(self._head_end_spin.value()) if self._head_end_spin is not None else 0
        tail_start = int(self._tail_start_spin.value()) if self._tail_start_spin is not None else 0
        tail_end = int(self._tail_end_spin.value()) if self._tail_end_spin is not None else 0
        profile_type = str(self._mayak_profile_combo.currentData()) if self._mayak_profile_combo is not None else "linear"
        duration_sec = self._resolve_duration_for_mayak()
        try:
            self._orch.start_mayak_test(
                head_start_rpm=head_start,
                head_end_rpm=head_end,
                tail_start_rpm=tail_start,
                tail_end_rpm=tail_end,
                profile_type=profile_type,
                duration_sec=duration_sec,
                sdr_options=self.get_sdr_options(),
            )
            self._last_sent_mayak_duration_sec = float(duration_sec)
            self._refresh_duration_labels()
            self._log_info(
                "UI_MAYAK_CMD",
                (
                    "cmd=start_test "
                    f"profile={profile_type} duration_sec={duration_sec} "
                    f"head_start={head_start} head_end={head_end} "
                    f"tail_start={tail_start} tail_end={tail_end}"
                ),
            )
            self.statusBar().showMessage(f"Тест Маяка запущен: duration_sec={duration_sec:.1f}", 3000)
        except Exception as ex:
            self._log_error("UI_MAYAK_CMD_FAILED", f"cmd=start_test err={type(ex).__name__}")
            self.statusBar().showMessage(f"Ошибка запуска теста: {type(ex).__name__}", 3000)

    def _on_mayak_stop_clicked(self) -> None:
        try:
            self._orch.stop_mayak_test()
            self._log_info("UI_MAYAK_CMD", "cmd=stop_test")
        except Exception as ex:
            self._log_error("UI_MAYAK_CMD_FAILED", f"cmd=stop_test err={type(ex).__name__}")
            self.statusBar().showMessage(f"Ошибка остановки теста: {type(ex).__name__}", 3000)

    def _on_prepare_test_clicked(self) -> None:
        precheck_errors = self._validate_prepare_inputs()
        if precheck_errors:
            msg = "\n".join(f"- {x}" for x in precheck_errors)
            QMessageBox.critical(self, "Подготовка к тесту", f"Подготовка остановлена:\n{msg}")
            self._log_error("UI_PREPARE_VALIDATE_FAIL", f"errors={','.join(precheck_errors)}")
            return

        if not self._confirm_prepare_test():
            self._log_info("UI_PREPARE_CONFIRM_CANCELLED", "action=prepare_test")
            return

        self._log_info("UI_PREPARE_CONFIRM_ACCEPTED", "action=prepare_test")

        self._set_prepare_progress_running(True)
        head_start = int(self._head_start_spin.value()) if self._head_start_spin is not None else 0
        head_end = int(self._head_end_spin.value()) if self._head_end_spin is not None else 0
        tail_start = int(self._tail_start_spin.value()) if self._tail_start_spin is not None else 0
        tail_end = int(self._tail_end_spin.value()) if self._tail_end_spin is not None else 0
        profile_type = str(self._mayak_profile_combo.currentData()) if self._mayak_profile_combo is not None else "linear"
        duration_sec = self._resolve_duration_for_mayak()

        task = _PrepareTestTask(
            orchestrator=self._orch,
            head_start=head_start,
            head_end=head_end,
            tail_start=tail_start,
            tail_end=tail_end,
            profile_type=profile_type,
            duration_sec=duration_sec,
            sdr_options=self.get_sdr_options(),
        )
        self._prepare_task = task
        task.signals.progress.connect(self._on_prepare_progress)
        task.signals.done.connect(self._on_prepare_done)
        task.signals.fail.connect(self._on_prepare_fail)
        QThreadPool.globalInstance().start(task)

    def _confirm_prepare_test(self) -> bool:
        msg = QMessageBox(self)
        msg.setWindowTitle("Подготовка к тесту")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(
            "Сейчас будет подготовлен GPS-сигнал для испытания.\n"
            "Операция может занять некоторое время.\n\n"
            "Продолжить?"
        )
        yes_btn = msg.addButton(QMessageBox.StandardButton.Yes)
        no_btn = msg.addButton(QMessageBox.StandardButton.No)
        yes_btn.setText("Да")
        no_btn.setText("Нет")
        msg.setDefaultButton(cast(QPushButton, no_btn))
        msg.exec()
        return msg.clickedButton() == yes_btn

    def _validate_prepare_inputs(self) -> list[str]:
        errors: list[str] = []

        # Strict check: use trajectory from current session only.
        run_dir = getattr(self._traj_ctl, "last_run_dir", None)
        traj_csv = os.path.join(run_dir, "trajectory.csv") if isinstance(run_dir, str) and run_dir.strip() else ""
        if not traj_csv or not os.path.exists(traj_csv):
            errors.append("траектория текущего сеанса не сгенерирована")

        nav = self._gps_nav_path_edit.text().strip() if self._gps_nav_path_edit is not None else ""
        if not nav:
            errors.append("не указан путь к эфемеридам")
        elif not os.path.exists(nav):
            errors.append("файл эфемерид не найден")

        return errors

    def _set_prepare_progress_running(self, running: bool) -> None:
        if self._btn_prepare_test is not None:
            self._btn_prepare_test.setEnabled(not running)
        if self._prep_progress is None:
            return
        if running:
            self._prep_progress.setVisible(True)
            self._prep_progress.setRange(0, 100)
            self._prep_progress.setValue(0)
            self._prep_progress.setFormat("Подготовка...")
        else:
            self._prep_progress.setRange(0, 100)
            self._prep_progress.setValue(100)
            self._prep_progress.setVisible(False)

    def _on_prepare_progress(self, value: int, message: str) -> None:
        if self._prep_progress is not None:
            self._prep_progress.setRange(0, 100)
            self._prep_progress.setValue(max(0, min(100, int(value))))
            self._prep_progress.setFormat(message if message else "Подготовка...")
        if message:
            self.statusBar().showMessage(message, 1500)

    def _on_prepare_done(self, payload: object) -> None:
        self._set_prepare_progress_running(False)
        self._prepare_task = None

        data = payload if isinstance(payload, dict) else {}
        scenario_id = str(data.get("scenario_id", "none"))
        gps_artifacts = data.get("gps_artifacts", {})
        iq_path = str(gps_artifacts.get("iq", "")) if isinstance(gps_artifacts, dict) else ""
        self._log_info(
            "UI_PREPARE_DONE",
            (
                f"scenario_id={scenario_id} "
                f"iq={iq_path or 'none'}"
            ),
        )
        QMessageBox.information(
            self,
            "Подготовка к тесту",
            (
                "Подготовка завершена успешно.\n"
                f"scenario_id: {scenario_id}\n"
                f"iq: {iq_path or 'n/a'}\n\n"
                "Для проверки подключения SDR используйте кнопку "
                "\"Проверить готовность систем\"."
            ),
        )
        self.statusBar().showMessage("Подготовка к тесту завершена", 4000)
        self._start_monitor_trajectory_animation()
        try:
            idx = self.ui.tw_research.indexOf(self.ui.monitoringTab)
            if idx >= 0:
                self.ui.tw_research.setCurrentIndex(idx)
        except Exception:
            pass

    def _on_prepare_fail(self, error: str) -> None:
        self._set_prepare_progress_running(False)
        self._prepare_task = None
        self._log_error("UI_PREPARE_FAILED", f"stage=run err={error}")
        QMessageBox.critical(self, "Подготовка к тесту", f"Ошибка подготовки: {error}")

    def _set_readiness_check_running(self, running: bool) -> None:
        if self._btn_check_readiness_m is not None:
            self._btn_check_readiness_m.setEnabled(not running)

        if self._readiness_progress_m is None:
            self._refresh_monitor_flow_controls(self._session_runtime_last)
            return

        if running:
            self._readiness_progress_m.setVisible(True)
            self._readiness_progress_m.setRange(0, 100)
            self._readiness_progress_m.setValue(0)
            self._readiness_progress_m.setFormat("Проверка готовности...")
        else:
            self._readiness_progress_m.setRange(0, 100)
            self._readiness_progress_m.setValue(100)
            self._readiness_progress_m.setVisible(False)
        self._refresh_monitor_flow_controls(self._session_runtime_last)

    def _set_start_test_flow_running(self, running: bool) -> None:
        if self._readiness_progress_m is None:
            self._refresh_monitor_flow_controls(self._session_runtime_last)
            return
        if running:
            self._readiness_progress_m.setVisible(True)
            self._readiness_progress_m.setRange(0, 0)
            self._readiness_progress_m.setFormat("Предстартовые проверки...")
        else:
            self._readiness_progress_m.setRange(0, 100)
            self._readiness_progress_m.setValue(100)
            self._readiness_progress_m.setVisible(False)
        self._refresh_monitor_flow_controls(self._session_runtime_last)

    def _on_readiness_progress(self, value: int, message: str) -> None:
        if self._readiness_progress_m is not None:
            self._readiness_progress_m.setRange(0, 100)
            self._readiness_progress_m.setValue(max(0, min(100, int(value))))
            self._readiness_progress_m.setFormat(message if message else "Проверка готовности...")
        if message:
            self.statusBar().showMessage(message, 1500)

    def _present_readiness_report(self, report: dict[str, Any]) -> None:
        ready = bool(report.get("ready_to_start"))
        blocking = report.get("blocking_errors", [])
        warnings = report.get("warnings", [])
        blocking_txt = ",".join(str(x) for x in blocking) if isinstance(blocking, list) and blocking else "none"
        warnings_txt = ",".join(str(x) for x in warnings) if isinstance(warnings, list) and warnings else "none"
        details_html = self._build_readiness_details_html(report)

        if ready:
            self._log_info("UI_MONITOR_READINESS", "ready=1")
            msg = QMessageBox(self)
            msg.setWindowTitle("Мониторинг")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText("Результат проверки готовности систем")
            msg.setInformativeText(details_html)
            msg.exec()
            self.statusBar().showMessage("Проверка готовности: готово", 3000)
            return

        self._log_error("UI_MONITOR_READINESS", f"ready=0 blocking={blocking_txt} warnings={warnings_txt}")
        msg = QMessageBox(self)
        msg.setWindowTitle("Мониторинг")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText("Результат проверки готовности систем")
        msg.setInformativeText(details_html)
        msg.exec()
        self.statusBar().showMessage("Проверка готовности: не готово", 3000)

    def _on_readiness_done(self, payload: object) -> None:
        self._set_readiness_check_running(False)
        self._readiness_task = None
        report = payload if isinstance(payload, dict) else {}
        self._present_readiness_report(report)
        self._on_runtime_ui_tick()

    @staticmethod
    def _status_icon_html(kind: str) -> str:
        if kind == "ok":
            return '<span style="color:#2e7d32; font-weight:700;">&#10004;</span>'
        if kind == "warn":
            return '<span style="color:#f9a825; font-weight:700;">&#9888;</span>'
        return '<span style="color:#c62828; font-weight:700;">&#10006;</span>'

    def _build_readiness_details_html(self, report: dict[str, Any]) -> str:
        ready_to_start = bool(report.get("ready_to_start"))
        blocking_raw = report.get("blocking_errors", [])
        warnings_raw = report.get("warnings", [])
        blocking = {str(x) for x in blocking_raw} if isinstance(blocking_raw, list) else set()
        warnings = {str(x) for x in warnings_raw} if isinstance(warnings_raw, list) else set()

        def has_blocking(prefix: str) -> bool:
            return any(item == prefix or item.startswith(prefix) for item in blocking)

        rows: list[tuple[str, str, str]] = []
        sdr_probe_details: list[str] = []
        for warn in warnings:
            if warn.startswith("sdr_probe:"):
                sdr_probe_details.append(warn.split(":", 1)[1].strip())

        if has_blocking("sdr_not_ready"):
            rows.append(("SDR / Pluto", "err", "не готово"))
        else:
            rows.append(("SDR / Pluto", "ok", "готово"))

        if has_blocking("mayak_not_ready") or has_blocking("mayak_check_failed"):
            rows.append(("Маяк", "err", "не готово"))
        elif "mayak_is_ready_unavailable" in warnings:
            rows.append(("Маяк", "warn", "не готово (допустимо)"))
        else:
            rows.append(("Маяк", "ok", "готово"))

        if "trajectory_missing" in blocking:
            rows.append(("Траектория", "err", "не готово"))
        else:
            rows.append(("Траектория", "ok", "готово"))

        if has_blocking("gps_nav_missing") or has_blocking("gps_nav_not_found"):
            rows.append(("Эфемериды GPS", "err", "не готово"))
        else:
            rows.append(("Эфемериды GPS", "ok", "готово"))

        if has_blocking("pluto_input_failed"):
            rows.append(("Pluto input", "err", "не готово"))
        else:
            rows.append(("Pluto input", "ok", "готово"))

        if "video_visible_not_ready" in warnings:
            rows.append(("Камера Visible", "warn", "не готово (допустимо)"))
        else:
            rows.append(("Камера Visible", "ok", "готово"))

        if "video_thermal_not_ready" in warnings:
            rows.append(("Камера Thermal", "warn", "не готово (допустимо)"))
        else:
            rows.append(("Камера Thermal", "ok", "готово"))

        lines = ["<div>"]
        for title, kind, state_txt in rows:
            icon = self._status_icon_html(kind)
            lines.append(f"{icon} <b>{title}</b>: {state_txt}<br/>")
        if sdr_probe_details:
            details_txt = "; ".join(x for x in sdr_probe_details if x)
            if details_txt:
                lines.append(f"<br/><b>SDR детали:</b> {details_txt}<br/>")
        lines.append("<br/>")
        if ready_to_start:
            lines.append('<span style="color:#2e7d32; font-weight:700;">Итог: тест может быть запущен.</span>')
        else:
            lines.append('<span style="color:#c62828; font-weight:700;">Итог: тест НЕ может быть запущен.</span>')
        lines.append("</div>")
        return "".join(lines)

    def _on_readiness_fail(self, error: str) -> None:
        self._set_readiness_check_running(False)
        self._readiness_task = None
        self._log_error("UI_MONITOR_READINESS_FAILED", f"err={error}")
        QMessageBox.critical(self, "Мониторинг", f"Ошибка проверки готовности: {error}")
        self._on_runtime_ui_tick()

    def _on_runtime_ui_tick(self) -> None:
        prev_state = self._session_runtime_last if isinstance(self._session_runtime_last, dict) else {}
        try:
            state = self._orch.get_test_session_runtime_state()
        except Exception:
            state = {
                "active": False,
                "status": "ERROR",
                "elapsed_sec": 0.0,
                "video": {"state": "not_running", "degraded": False, "channels": []},
                "gps_tx": {"state": "not_running"},
                "degraded": False,
                "error": True,
            }
        self._session_runtime_last = state if isinstance(state, dict) else {}
        active = bool(self._session_runtime_last.get("active", False))
        if active and (not self._runtime_prev_active):
            self._start_monitor_trajectory_animation(force=True)
        if (not active) and self._runtime_prev_active:
            if not bool(self._anim_without_test_enabled):
                # Operator disabled animation outside active test.
                self._monitor_timer.stop()
                self._log_info("UI_MONITOR_ANIM_STOP", "reason=test_finished")
            finished_session_id = str(prev_state.get("session_id") or "").strip()
            if finished_session_id and finished_session_id != str(self._last_finished_session_notified or ""):
                out_dir = str(self._session_out_dir_hints.get(finished_session_id, "")).strip()
                self._show_test_finished_notification(finished_session_id, out_dir)
                self._last_finished_session_notified = finished_session_id
        self._runtime_prev_active = active
        self._render_runtime_state(self._session_runtime_last)
        self._refresh_monitor_flow_controls(self._session_runtime_last)

    def _render_runtime_state(self, state: dict[str, Any]) -> None:
        session_id = str(state.get("session_id") or "-")
        status = str(state.get("status") or "-")
        elapsed = float(state.get("elapsed_sec") or 0.0)
        video = state.get("video") if isinstance(state.get("video"), dict) else {}
        gps = state.get("gps_tx") if isinstance(state.get("gps_tx"), dict) else {}
        degraded = bool(state.get("degraded", False))
        error = bool(state.get("error", False))
        busy = (self._readiness_task is not None) or (self._start_session_task is not None) or (self._stop_session_task is not None)
        is_active = bool(state.get("active", False))

        if self._lbl_runtime_gate_m is not None:
            if error:
                self._lbl_runtime_gate_m.setText("Критический статус: ошибка сессии")
                self._lbl_runtime_gate_m.setStyleSheet("font-weight:700; color:#c62828;")
            elif is_active:
                self._lbl_runtime_gate_m.setText("Критический статус: идет испытание")
                self._lbl_runtime_gate_m.setStyleSheet("font-weight:700; color:#1565c0;")
            elif busy:
                self._lbl_runtime_gate_m.setText("Критический статус: идет предстартовая операция")
                self._lbl_runtime_gate_m.setStyleSheet("font-weight:700; color:#f9a825;")
            else:
                self._lbl_runtime_gate_m.setText("Критический статус: готов к запуску")
                self._lbl_runtime_gate_m.setStyleSheet("font-weight:700; color:#2e7d32;")

        if self._lbl_session_id_m is not None:
            self._lbl_session_id_m.setText(f"Сессия: {session_id}")
        if self._lbl_session_status_m is not None:
            self._lbl_session_status_m.setText(f"Статус: {self._session_status_ru(status)}")
            if error:
                self._lbl_session_status_m.setStyleSheet("color:#c62828;")
            elif status in ("RUNNING", "STARTING", "STOPPING"):
                self._lbl_session_status_m.setStyleSheet("color:#2e7d32;")
            else:
                self._lbl_session_status_m.setStyleSheet("")
        if self._lbl_session_elapsed_m is not None:
            self._lbl_session_elapsed_m.setText(f"Время: {self._format_elapsed(elapsed)}")
        if self._lbl_session_video_m is not None:
            video_state = str(video.get("state", "not_running"))
            channels = video.get("channels") if isinstance(video.get("channels"), list) else []
            channel_txt_parts: list[str] = []
            for item in channels:
                if isinstance(item, dict):
                    nm = str(item.get("channel", "?"))
                    fr = int(item.get("frames_written", 0))
                    d = bool(item.get("degraded", False))
                    suffix = " (деградация)" if d else ""
                    channel_txt_parts.append(f"{nm}:{fr}{suffix}")
            channels_txt = ", ".join(channel_txt_parts) if channel_txt_parts else "нет данных"
            self._lbl_session_video_m.setText(f"Видео: {self._runtime_component_state_ru(video_state)} [{channels_txt}]")
        if self._lbl_session_gps_m is not None:
            gps_state = str(gps.get("state", "not_running"))
            pid = gps.get("pid")
            pid_txt = f", pid={pid}" if isinstance(pid, int) else ""
            self._lbl_session_gps_m.setText(f"GPS трансляция: {self._runtime_component_state_ru(gps_state)}{pid_txt}")
        if self._lbl_session_degraded_m is not None:
            mode_txt = "деградация" if degraded else "нормальный"
            err_txt = "ошибка" if error else "ок"
            self._lbl_session_degraded_m.setText(f"Режим: {mode_txt}, состояние: {err_txt}")
            degrade_details: list[str] = []
            channels = video.get("channels") if isinstance(video.get("channels"), list) else []
            bad_channels = [str(x.get("channel", "?")) for x in channels if isinstance(x, dict) and bool(x.get("degraded", False))]
            if bad_channels:
                degrade_details.append("деградировали каналы видео: " + ", ".join(bad_channels))
            gps_state = str(gps.get("state", ""))
            if gps_state in ("exited", "error"):
                degrade_details.append(f"GPS трансляция: {self._runtime_component_state_ru(gps_state)}")
            if error:
                degrade_details.append("сессия завершилась с ошибкой")
            self._lbl_session_degraded_m.setToolTip("; ".join(degrade_details) if degrade_details else "Деградация не зафиксирована")
            if error:
                self._lbl_session_degraded_m.setStyleSheet("color:#c62828;")
            elif degraded:
                self._lbl_session_degraded_m.setStyleSheet("color:#f9a825;")
            else:
                self._lbl_session_degraded_m.setStyleSheet("color:#2e7d32;")

    @staticmethod
    def _session_status_ru(status: str) -> str:
        mapping = {
            "CREATED": "создано",
            "STARTING": "запуск",
            "RUNNING": "выполняется",
            "STOPPING": "остановка",
            "STOPPED": "остановлено",
            "ERROR": "ошибка",
        }
        return mapping.get(str(status), str(status))

    @staticmethod
    def _runtime_component_state_ru(state: str) -> str:
        mapping = {
            "running": "работает",
            "not_running": "не запущено",
            "exited": "завершено",
            "error": "ошибка",
            "starting": "запуск",
            "stopping": "остановка",
        }
        return mapping.get(str(state), str(state))

    def _refresh_monitor_flow_controls(self, state: dict[str, Any]) -> None:
        status = str(state.get("status") or "STOPPED")
        active = bool(state.get("active", False))
        busy = (self._readiness_task is not None) or (self._start_session_task is not None) or (self._stop_session_task is not None)
        can_start = (not busy) and (not active) and status in ("STOPPED", "ERROR")
        can_stop = (not busy) and active and status in ("CREATED", "STARTING", "RUNNING", "STOPPING", "ERROR")

        if self._btn_check_readiness_m is not None:
            self._btn_check_readiness_m.setEnabled((not busy) and (not active))
        if self._btn_start_test_m is not None:
            self._btn_start_test_m.setEnabled(can_start)
        if self._btn_stop_test_m is not None:
            self._btn_stop_test_m.setEnabled(can_stop)
        can_change_output_root = (not busy) and (not active)
        if self._session_output_root_m_edit is not None:
            self._session_output_root_m_edit.setEnabled(can_change_output_root)
        if self._btn_session_output_root_m_browse is not None:
            self._btn_session_output_root_m_browse.setEnabled(can_change_output_root)
        if self._btn_session_output_root_m_default is not None:
            self._btn_session_output_root_m_default.setEnabled(can_change_output_root)
        if self._btn_open_last_results_m is not None:
            has_results_dir = bool(self._last_completed_out_dir) and Path(self._last_completed_out_dir).exists()
            self._btn_open_last_results_m.setEnabled(has_results_dir)

    @staticmethod
    def _format_elapsed(value_sec: float) -> str:
        v = max(0.0, float(value_sec))
        mm = int(v // 60.0)
        ss = int(v % 60.0)
        ds = int((v - int(v)) * 10.0)
        return f"{mm:02d}:{ss:02d}.{ds:d}"

    def _on_replay_open_session_clicked(self) -> None:
        base_dir = str(Path(self._orch.get_test_session_output_root()).resolve())
        selected = QFileDialog.getExistingDirectory(self, "Открыть сессию просмотра", base_dir)
        if not selected:
            return
        try:
            self.load_session(Path(selected))
            self.statusBar().showMessage(f"Просмотр: загружена сессия {Path(selected).name}", 3000)
        except Exception as ex:
            self._log_error("UI_REPLAY_LOAD_FAILED", f"err={type(ex).__name__} detail={ex}")
            QMessageBox.critical(self, "Просмотр", f"Не удалось загрузить сессию: {type(ex).__name__}\n{ex}")

    def load_session(self, session_dir: Path | str) -> None:
        try:
            self._load_replay_session(Path(session_dir))
        except Exception:
            self._set_replay_state(ReplayState.ERROR)
            raise

    def play(self) -> None:
        if not self._replay_timeline:
            self._set_replay_state(ReplayState.ERROR)
            self._sync_replay_controls()
            return
        if self._replay_t_sec >= float(self._replay_t_max_sec):
            self.seek(float(self._replay_t_min_sec))
        self._replay_play_started_mono = time.monotonic()
        self._replay_play_started_t_sec = float(self._replay_t_sec)
        self._set_replay_state(ReplayState.PLAYING)
        self._sync_replay_controls()

    def pause(self) -> None:
        if self._replay_state != ReplayState.PLAYING:
            return
        self._apply_replay_t_sec(self._current_playback_t_sec(), from_slider=False)
        self._set_replay_state(ReplayState.PAUSED)
        self._sync_replay_controls()

    def stop(self) -> None:
        if not self._replay_timeline:
            return
        self.pause()
        self.seek(float(self._replay_t_min_sec))
        if self._replay_state in {ReplayState.PAUSED, ReplayState.EOF}:
            self._set_replay_state(ReplayState.LOADED)
        self._sync_replay_controls()

    def seek(self, t: float) -> None:
        self._apply_replay_t_sec(float(t), from_slider=False)
        if self._replay_state == ReplayState.EOF and self._replay_t_sec < float(self._replay_t_max_sec):
            self._set_replay_state(ReplayState.PAUSED)
        if self._replay_state == ReplayState.PLAYING:
            self._replay_play_started_mono = time.monotonic()
            self._replay_play_started_t_sec = float(self._replay_t_sec)
        self._sync_replay_controls()

    def step(self, dt: float) -> None:
        self.pause()
        self.seek(float(self._replay_t_sec) + float(dt))

    def set_rate(self, x: float) -> None:
        r = max(0.25, min(4.0, float(x)))
        if abs(r - self._replay_rate) < 1e-9:
            return
        self._replay_rate = r
        if self._replay_state == ReplayState.PLAYING:
            self._apply_replay_t_sec(self._current_playback_t_sec(), from_slider=False)
            self._replay_play_started_mono = time.monotonic()
            self._replay_play_started_t_sec = float(self._replay_t_sec)
        self._sync_replay_controls()

    def _current_playback_t_sec(self) -> float:
        if self._replay_state != ReplayState.PLAYING:
            return float(self._replay_t_sec)
        elapsed = max(0.0, time.monotonic() - float(self._replay_play_started_mono))
        raw_t = float(self._replay_play_started_t_sec) + elapsed * float(self._replay_rate)
        return min(max(raw_t, float(self._replay_t_min_sec)), float(self._replay_t_max_sec))

    def _set_replay_state(self, new_state: ReplayState) -> None:
        allowed: dict[ReplayState, set[ReplayState]] = {
            ReplayState.IDLE: {ReplayState.LOADED, ReplayState.ERROR},
            ReplayState.LOADED: {ReplayState.PLAYING, ReplayState.PAUSED, ReplayState.ERROR, ReplayState.IDLE},
            ReplayState.PLAYING: {ReplayState.PAUSED, ReplayState.EOF, ReplayState.ERROR, ReplayState.LOADED},
            ReplayState.PAUSED: {ReplayState.PLAYING, ReplayState.EOF, ReplayState.ERROR, ReplayState.LOADED},
            ReplayState.EOF: {ReplayState.PLAYING, ReplayState.PAUSED, ReplayState.LOADED, ReplayState.ERROR},
            ReplayState.ERROR: {ReplayState.IDLE, ReplayState.LOADED},
        }
        cur = self._replay_state
        if new_state == cur:
            return
        if new_state not in allowed.get(cur, set()):
            self._log_error("UI_REPLAY_STATE_INVALID", f"from={cur.value} to={new_state.value}")
            self._replay_state = ReplayState.ERROR
        else:
            self._replay_state = new_state

    def _load_replay_session(self, session_dir: Path) -> None:
        manifest_path = session_dir / "session_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest_missing={manifest_path.as_posix()}")
        try:
            manifest = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception:
            manifest = {}
        status = str(manifest.get("status", "")) if isinstance(manifest, dict) else ""
        if status and status != "STOPPED":
            raise RuntimeError(f"сессия не завершена, статус={status}")

        timeline = self._read_replay_timeline(session_dir / "trajectory_timeline.csv")
        if not timeline:
            raise RuntimeError("trajectory_timeline_empty")

        self._release_replay_caps()
        self._replay_session_dir = session_dir
        self._replay_timeline = timeline
        self._replay_visible_frames = self._read_replay_frames(session_dir / "video" / "visible_frames.csv")
        self._replay_thermal_frames = self._read_replay_frames(session_dir / "video" / "thermal_frames.csv")
        self._replay_build_indices()
        self._replay_t_sec = float(self._replay_t_min_sec)
        self._replay_play_started_mono = 0.0
        self._replay_play_started_t_sec = self._replay_t_sec
        self._replay_rate = 1.0

        if cv2 is not None:
            v_mp4 = session_dir / "video" / "visible.mp4"
            t_mp4 = session_dir / "video" / "thermal.mp4"
            self._replay_cap_visible = cv2.VideoCapture(str(v_mp4)) if v_mp4.exists() else None
            self._replay_cap_thermal = cv2.VideoCapture(str(t_mp4)) if t_mp4.exists() else None

        if self._replay_slider_m is not None:
            self._replay_slider_m.setEnabled(True)
            self._replay_slider_m.setRange(0, max(0, int(self._replay_duration_sec * 1000.0)))
        if self._replay_t_spin_m is not None:
            self._replay_t_spin_m.setEnabled(True)
            self._replay_t_spin_m.setRange(float(self._replay_t_min_sec), float(self._replay_t_max_sec))
            self._replay_t_spin_m.blockSignals(True)
            self._replay_t_spin_m.setValue(float(self._replay_t_min_sec))
            self._replay_t_spin_m.blockSignals(False)
        if self._replay_rate_combo_m is not None:
            self._replay_rate_combo_m.setEnabled(True)
            self._replay_rate_combo_m.blockSignals(True)
            self._replay_rate_combo_m.setCurrentText("1x")
            self._replay_rate_combo_m.blockSignals(False)
        if self._lbl_replay_session_m is not None:
            self._lbl_replay_session_m.setText(f"Сессия: {session_dir.name}")

        pts = [(x, y, z) for (_t, x, y, z, _s) in timeline]
        self._traj_view_r.set_points(pts)
        self._set_replay_state(ReplayState.LOADED)
        self._sync_replay_controls()
        self._apply_replay_t_sec(self._replay_t_sec, from_slider=False)

    def _replay_build_indices(self) -> None:
        self._replay_timeline_times = [float(x[0]) for x in self._replay_timeline]
        self._replay_visible_times = [float(x[0]) for x in self._replay_visible_frames]
        self._replay_thermal_times = [float(x[0]) for x in self._replay_thermal_frames]
        if self._replay_timeline_times:
            self._replay_t_min_sec = float(self._replay_timeline_times[0])
            self._replay_t_max_sec = float(self._replay_timeline_times[-1])
        else:
            self._replay_t_min_sec = 0.0
            self._replay_t_max_sec = 0.0
        self._replay_duration_sec = max(0.0, float(self._replay_t_max_sec - self._replay_t_min_sec))

    def _release_replay_caps(self) -> None:
        for cap in (self._replay_cap_visible, self._replay_cap_thermal):
            try:
                if cap is not None and hasattr(cap, "release"):
                    cap.release()
            except Exception:
                pass
        self._replay_cap_visible = None
        self._replay_cap_thermal = None

    def _read_replay_timeline(self, path: Path) -> list[tuple[float, float, float, float, float]]:
        if not path.exists():
            raise FileNotFoundError(f"timeline_missing={path.as_posix()}")
        out: list[tuple[float, float, float, float, float]] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            _hdr = next(reader, None)
            for row in reader:
                if not row or len(row) < 5:
                    continue
                try:
                    out.append((float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
                except Exception:
                    continue
        out.sort(key=lambda x: x[0])
        return out

    def _read_replay_frames(self, path: Path) -> list[tuple[float, int]]:
        if not path.exists():
            return []
        out: list[tuple[float, int]] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            _hdr = next(reader, None)
            for row in reader:
                if not row or len(row) < 3:
                    continue
                try:
                    out.append((float(row[2]), int(row[0])))
                except Exception:
                    continue
        out.sort(key=lambda x: x[0])
        return out

    def _on_replay_play_toggled(self, checked: bool) -> None:
        if not self._replay_timeline:
            if self._btn_replay_play_m is not None:
                self._btn_replay_play_m.setChecked(False)
            return
        if checked:
            self.play()
        else:
            self.pause()
        self._sync_replay_controls()

    def _on_replay_stop_clicked(self) -> None:
        self.stop()

    def _on_replay_back_clicked(self) -> None:
        self.step(-10.0)

    def _on_replay_fwd_clicked(self) -> None:
        self.step(10.0)

    def _on_replay_step_back_clicked(self) -> None:
        self.step(-1.0)

    def _on_replay_step_fwd_clicked(self) -> None:
        self.step(1.0)

    def _on_replay_slider_changed(self, value: int) -> None:
        if not self._replay_timeline:
            return
        t = float(self._replay_t_min_sec) + max(0.0, float(value) / 1000.0)
        self._apply_replay_t_sec(t, from_slider=True)

    def _on_replay_t_spin_changed(self, value: float) -> None:
        if not self._replay_timeline:
            return
        self.seek(float(value))

    def _on_replay_rate_combo_changed(self, text: str) -> None:
        raw = str(text).strip().lower().replace("x", "")
        try:
            value = float(raw)
        except Exception:
            value = 1.0
        self.set_rate(value)

    def _on_replay_shortcut_toggle_play(self) -> None:
        if not self._is_replay_control_active():
            return
        if self._replay_state == ReplayState.PLAYING:
            self.pause()
        else:
            self.play()

    def _on_replay_shortcut_step_back(self) -> None:
        if not self._is_replay_control_active():
            return
        self.step(-1.0)

    def _on_replay_shortcut_step_fwd(self) -> None:
        if not self._is_replay_control_active():
            return
        self.step(1.0)

    def _on_replay_timer_tick(self) -> None:
        if self._replay_state != ReplayState.PLAYING or not self._replay_timeline:
            return
        t = self._current_playback_t_sec()
        t_max = float(self._replay_t_max_sec)
        if t >= t_max:
            t = t_max
            self._set_replay_state(ReplayState.EOF)
            self._sync_replay_controls()
        self._apply_replay_t_sec(t, from_slider=False)

    def _apply_replay_t_sec(self, t_sec: float, *, from_slider: bool) -> None:
        if not self._replay_timeline:
            return
        t_min = float(self._replay_t_min_sec)
        t_max = float(self._replay_t_max_sec)
        self._replay_t_sec = min(max(float(t_sec), t_min), t_max)
        if self._replay_slider_m is not None and not from_slider:
            self._replay_slider_m.blockSignals(True)
            self._replay_slider_m.setValue(max(0, int((self._replay_t_sec - t_min) * 1000.0)))
            self._replay_slider_m.blockSignals(False)
        if self._replay_t_spin_m is not None:
            self._replay_t_spin_m.blockSignals(True)
            self._replay_t_spin_m.setValue(float(self._replay_t_sec))
            self._replay_t_spin_m.blockSignals(False)
        self._render_replay_state()

    def _sync_replay_controls(self) -> None:
        has_data = bool(self._replay_timeline)
        is_playing = self._replay_state == ReplayState.PLAYING
        can_control = has_data and self._replay_state not in {ReplayState.IDLE, ReplayState.ERROR}
        if self._btn_replay_play_m is not None:
            self._btn_replay_play_m.setEnabled(has_data)
            self._btn_replay_play_m.blockSignals(True)
            self._btn_replay_play_m.setChecked(is_playing)
            self._btn_replay_play_m.setText("Пауза" if is_playing else "Воспроизвести")
            self._btn_replay_play_m.blockSignals(False)
        for btn in (
            self._btn_replay_stop_m,
            self._btn_replay_back_m,
            self._btn_replay_fwd_m,
            self._btn_replay_step_back_m,
            self._btn_replay_step_fwd_m,
        ):
            if btn is not None:
                btn.setEnabled(can_control)
        if self._replay_slider_m is not None:
            self._replay_slider_m.setEnabled(has_data)
        if self._replay_t_spin_m is not None:
            self._replay_t_spin_m.setEnabled(can_control)
        if self._replay_rate_combo_m is not None:
            self._replay_rate_combo_m.setEnabled(can_control)

    def _is_replay_control_active(self) -> bool:
        btn = self._btn_replay_play_m
        if btn is None:
            return False
        if not btn.isVisible():
            return False
        return bool(self._replay_timeline)

    def _render_replay_state(self) -> None:
        if not self._replay_timeline:
            return
        t = float(self._replay_t_sec)
        if self._lbl_replay_trel_m is not None:
            self._lbl_replay_trel_m.setText(f"t: {t:.3f} c")

        idx = self._nearest_timeline_index(t)
        pt = self._replay_timeline[idx]
        self._traj_view_r.set_marker_point((pt[1], pt[2], pt[3]))
        if self._lbl_replay_traj_info_m is not None:
            self._lbl_replay_traj_info_m.setText(
                f"Траектория: idx={idx} t={pt[0]:.3f} x={pt[1]:.1f} y={pt[2]:.1f} z={pt[3]:.1f} v={pt[4]:.2f}"
            )

        vis = self._nearest_frame(self._replay_visible_frames, self._replay_visible_times, t)
        thr = self._nearest_frame(self._replay_thermal_frames, self._replay_thermal_times, t)
        self._render_replay_channel(
            name="Видимый канал",
            t_master=t,
            frame_info=vis,
            has_stream=bool(self._replay_visible_frames),
            label_info=self._lbl_replay_visible_info_m,
            label_img=self._lbl_replay_visible_img_m,
            cap=self._replay_cap_visible,
        )
        self._render_replay_channel(
            name="Тепловой канал",
            t_master=t,
            frame_info=thr,
            has_stream=bool(self._replay_thermal_frames),
            label_info=self._lbl_replay_thermal_info_m,
            label_img=self._lbl_replay_thermal_img_m,
            cap=self._replay_cap_thermal,
        )

    def _render_replay_channel(
        self,
        *,
        name: str,
        t_master: float,
        frame_info: Optional[tuple[float, int]],
        has_stream: bool,
        label_info: Optional[QLabel],
        label_img: Optional[QLabel],
        cap: Any,
    ) -> None:
        has_video = cv2 is not None and cap is not None
        status, reason = self._replay_channel_status(
            t_master=t_master,
            frame_info=frame_info,
            has_stream=has_stream,
            has_video=has_video,
        )
        detail = ""
        if frame_info is None:
            detail = "frame=- t=- dt=-"
            if label_info is not None:
                label_info.setText(f"{name}: {status} | {detail} | {reason}")
            if label_img is not None:
                label_img.setText(status)
                label_img.setPixmap(QPixmap())
            return

        t_frame, idx = frame_info
        dt = abs(float(t_frame) - float(t_master))
        detail = f"frame={idx} t={t_frame:.3f} dt={dt:.3f}"
        if has_video and label_img is not None:
            pix = self._read_video_frame_pixmap(cap, idx)
            if pix is None:
                status = "GAP"
                reason = "кадр недоступен"
                label_img.setText(status)
                label_img.setPixmap(QPixmap())
            else:
                label_img.setText("")
                label_img.setPixmap(
                    pix.scaled(
                        label_img.width(),
                        label_img.height(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        elif label_img is not None:
            label_img.setText(status)
            label_img.setPixmap(QPixmap())

        if label_info is not None:
            label_info.setText(f"{name}: {status} | {detail} | {reason}")

    @staticmethod
    def _replay_channel_status(
        *,
        t_master: float,
        frame_info: Optional[tuple[float, int]],
        has_stream: bool,
        has_video: bool,
    ) -> tuple[str, str]:
        if not has_stream:
            return ("N/A", "канал не записан")
        if frame_info is None:
            return ("GAP", "нет ближайшего кадра")
        t_frame = float(frame_info[0])
        if abs(t_frame - float(t_master)) > float(_REPLAY_CHANNEL_GAP_SEC):
            return ("GAP", "разрыв по времени")
        if not has_video:
            return ("N/A", "видео недоступно")
        return ("OK", "синхронизация в норме")

    def _read_video_frame_pixmap(self, cap: Any, frame_idx: int) -> Optional[QPixmap]:
        if cv2 is None or cap is None:
            return None
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_idx) - 1))
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, int(w), int(h), int(ch * w), QImage.Format.Format_RGB888)
            return QPixmap.fromImage(qimg.copy())
        except Exception:
            return None

    @staticmethod
    def _nearest_frame(
        frames: list[tuple[float, int]],
        times: list[float],
        t_sec: float,
    ) -> Optional[tuple[float, int]]:
        if not frames or not times:
            return None
        pos = bisect_left(times, float(t_sec))
        if pos <= 0:
            return frames[0]
        if pos >= len(times):
            return frames[-1]
        a = frames[pos - 1]
        b = frames[pos]
        return a if abs(a[0] - t_sec) <= abs(b[0] - t_sec) else b

    def _nearest_timeline_index(self, t_sec: float) -> int:
        if not self._replay_timeline or not self._replay_timeline_times:
            return 0
        pos = bisect_left(self._replay_timeline_times, float(t_sec))
        if pos <= 0:
            return 0
        if pos >= len(self._replay_timeline_times):
            return len(self._replay_timeline_times) - 1
        l = self._replay_timeline_times
        return pos - 1 if abs(l[pos - 1] - t_sec) <= abs(l[pos] - t_sec) else pos

    def _on_monitor_check_readiness_clicked(self) -> None:
        if self._readiness_task is not None or self._start_session_task is not None or self._stop_session_task is not None:
            return
        if bool(self._session_runtime_last.get("active", False)):
            self.statusBar().showMessage("Нельзя запускать readiness во время активной сессии", 2500)
            return

        self._set_readiness_check_running(True)
        task = _ReadinessCheckTask(orchestrator=self._orch)
        self._readiness_task = task
        task.signals.progress.connect(self._on_readiness_progress)
        task.signals.done.connect(self._on_readiness_done)
        task.signals.fail.connect(self._on_readiness_fail)
        QThreadPool.globalInstance().start(task)

    def _on_monitor_start_test_clicked(self) -> None:
        if self._readiness_task is not None or self._start_session_task is not None or self._stop_session_task is not None:
            self.statusBar().showMessage("Дождитесь завершения проверки готовности", 2500)
            return
        task = _StartSessionFlowTask(orchestrator=self._orch)
        self._start_session_task = task
        self._set_start_test_flow_running(True)
        task.signals.done.connect(self._on_monitor_start_flow_done)
        task.signals.fail.connect(self._on_monitor_start_flow_fail)
        QThreadPool.globalInstance().start(task)

    def _on_monitor_stop_test_clicked(self) -> None:
        if self._readiness_task is not None or self._start_session_task is not None or self._stop_session_task is not None:
            return
        task = _StopSessionFlowTask(orchestrator=self._orch)
        self._stop_session_task = task
        self._refresh_monitor_flow_controls(self._session_runtime_last)
        task.signals.done.connect(self._on_monitor_stop_flow_done)
        task.signals.fail.connect(self._on_monitor_stop_flow_fail)
        QThreadPool.globalInstance().start(task)

    def _on_monitor_start_flow_done(self, payload: object) -> None:
        self._start_session_task = None
        self._set_start_test_flow_running(False)
        flow = payload if isinstance(payload, dict) else {}
        ready = bool(flow.get("started")) if isinstance(flow, dict) else False
        if ready:
            session = flow.get("session") if isinstance(flow, dict) else {}
            session_id = str(session.get("session_id", "unknown")) if isinstance(session, dict) else "unknown"
            out_dir = str(session.get("out_dir", "")).strip() if isinstance(session, dict) else ""
            if session_id and out_dir:
                self._session_out_dir_hints[session_id] = out_dir
            self._log_info("UI_MONITOR_START_TEST", f"status=ok session_id={session_id}")
            self.statusBar().showMessage(f"Испытание началось ({session_id})", 3000)
            self._set_last_test_result_label(f"Последнее испытание: выполняется ({session_id})", "#1565c0")
            self._start_monitor_trajectory_animation(force=True)
        else:
            self._log_error("UI_MONITOR_START_TEST", "status=blocked readiness=0")
            self._set_last_test_result_label("Последнее испытание: запуск отклонен (проверка не пройдена)", "#c62828")
            report = flow.get("readiness") if isinstance(flow, dict) else {}
            self._present_readiness_report(report if isinstance(report, dict) else {})
        self._on_runtime_ui_tick()

    def _on_monitor_start_flow_fail(self, error: str) -> None:
        self._start_session_task = None
        self._set_start_test_flow_running(False)
        self._log_error("UI_MONITOR_START_TEST_FAILED", f"err={error}")
        self._set_last_test_result_label("Последнее испытание: ошибка запуска", "#c62828")
        QMessageBox.critical(self, "Мониторинг", f"Не удалось начать испытание: {error}")
        self._on_runtime_ui_tick()

    def _on_monitor_stop_flow_done(self, payload: object) -> None:
        self._stop_session_task = None
        data = payload if isinstance(payload, dict) else {}
        session_id = str(data.get("session_id", "unknown")) if isinstance(data, dict) else "unknown"
        out_dir = str(data.get("out_dir", "")).strip() if isinstance(data, dict) else ""
        if session_id and out_dir:
            self._session_out_dir_hints[session_id] = out_dir
        self._log_info("UI_MONITOR_STOP_TEST", f"status=ok session_id={session_id}")
        self._show_test_finished_notification(session_id, out_dir)
        self._last_finished_session_notified = session_id
        self._on_runtime_ui_tick()

    def _on_monitor_stop_flow_fail(self, error: str) -> None:
        self._stop_session_task = None
        self._log_error("UI_MONITOR_STOP_TEST_FAILED", f"err={error}")
        self._set_last_test_result_label("Последнее испытание: ошибка остановки", "#c62828")
        QMessageBox.critical(self, "Мониторинг", f"Не удалось остановить испытание: {error}")
        self._on_runtime_ui_tick()

    def _show_test_finished_notification(self, session_id: str, out_dir: str) -> None:
        sid = str(session_id or "unknown")
        out = str(out_dir or "").strip()
        self.statusBar().showMessage(f"Испытание завершено ({sid}). Результаты сохранены.", 5000)
        self._set_last_test_result_label(f"Последнее испытание: успешно завершено ({sid})", "#2e7d32")
        if out:
            self._last_completed_out_dir = out
            if self._btn_open_last_results_m is not None:
                self._btn_open_last_results_m.setEnabled(True)
        if out:
            QMessageBox.information(
                self,
                "Мониторинг",
                (
                    f"Испытание успешно завершено ({sid}).\n"
                    "Результаты сохранены.\n\n"
                    f"Папка: {out}"
                ),
            )

    def _on_open_last_results_clicked(self) -> None:
        out = str(self._last_completed_out_dir or "").strip()
        if not out:
            QMessageBox.information(self, "Мониторинг", "Папка результатов еще не зафиксирована.")
            return
        p = Path(out)
        if not p.exists():
            QMessageBox.warning(self, "Мониторинг", f"Папка не найдена:\n{p.as_posix()}")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as ex:
            QMessageBox.critical(self, "Мониторинг", f"Не удалось открыть папку:\n{type(ex).__name__}: {ex}")

    def _set_last_test_result_label(self, text: str, color: str = "") -> None:
        if self._lbl_last_test_result_m is None:
            return
        self._lbl_last_test_result_m.setText(str(text))
        if color:
            self._lbl_last_test_result_m.setStyleSheet(f"color:{color};")
        else:
            self._lbl_last_test_result_m.setStyleSheet("")

    @staticmethod
    def _build_camera_warning_text(warnings: list[object]) -> str:
        keys = {str(x) for x in warnings}
        missing_visible = "video_visible_not_ready" in keys
        missing_thermal = "video_thermal_not_ready" in keys
        if not missing_visible and not missing_thermal:
            return ""

        if missing_visible and missing_thermal:
            cams = "видимая и тепловая камеры не подключены"
        elif missing_visible:
            cams = "видимая камера не подключена"
        else:
            cams = "тепловая камера не подключена"

        return (
            f"Внимание: {cams}.\n"
            "Испытание можно выполнить, но результаты будут без видео."
        )

    def _on_mayak_emergency_clicked(self) -> None:
        try:
            self._orch.emergency_stop()
            self._log_info("UI_MAYAK_CMD", "cmd=emergency_stop")
        except Exception as ex:
            self._log_error("UI_MAYAK_CMD_FAILED", f"cmd=emergency_stop err={type(ex).__name__}")
            self.statusBar().showMessage(f"Ошибка аварийного стопа: {type(ex).__name__}", 3000)

    def _on_trajectory_duration_resolved(self, duration_sec: Optional[float]) -> None:
        self._trajectory_duration_sec = float(duration_sec) if isinstance(duration_sec, (int, float)) else None
        self._refresh_duration_labels()
        if self._trajectory_duration_sec is not None:
            self._log_info("UI_TRAJ_DURATION", f"duration_sec={self._trajectory_duration_sec:.3f}")

    def _on_trajectory_points_resolved(self, points: list[tuple[float, float, float]]) -> None:
        self._latest_trajectory_points = list(points) if isinstance(points, list) else []
        if points:
            x, y, z = points[-1]
            self._last_trajectory_end_local = (float(x), float(y), float(z))
        else:
            self._last_trajectory_end_local = None
        self._refresh_gps_finish_point()

    def _start_monitor_trajectory_animation(self, *, force: bool = False) -> None:
        enabled = bool(self._anim_without_test_enabled)
        active = bool(self._session_runtime_last.get("active", False))
        animate = bool(force or active or enabled)

        points = list(self._latest_trajectory_points)
        if points:
            self._apply_monitor_points(points, self._trajectory_duration_sec, animate=animate)
            return

        run_dir = getattr(self._traj_ctl, "last_run_dir", None)
        if not isinstance(run_dir, str) or not run_dir.strip():
            self._traj_view_m.set_status("Мониторинг траектории (3D)\nНет данных trajectory.csv")
            return

        self._monitor_load_seq += 1
        seq = self._monitor_load_seq
        self._traj_view_m.set_status("Мониторинг траектории (3D)\nЗагрузка trajectory.csv…")
        self._traj_loader.start(
            seq=seq,
            run_dir=run_dir,
            on_ok=self._on_monitor_trajectory_loaded_ok,
            on_fail=self._on_monitor_trajectory_loaded_fail,
        )

    def _on_monitor_trajectory_loaded_ok(self, seq: int, payload_obj: object) -> None:
        if seq != self._monitor_load_seq:
            return
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        points_raw = payload.get("points", payload_obj) if isinstance(payload, dict) else payload_obj
        points = list(points_raw) if isinstance(points_raw, list) else []
        duration_raw = payload.get("duration_sec") if isinstance(payload, dict) else None
        duration = float(duration_raw) if isinstance(duration_raw, (int, float)) else None
        active = bool(self._session_runtime_last.get("active", False))
        animate = bool(active or self._anim_without_test_enabled)
        self._apply_monitor_points(points, duration, animate=animate)

    def _on_monitor_trajectory_loaded_fail(self, seq: int, error: str) -> None:
        if seq != self._monitor_load_seq:
            return
        self._traj_view_m.show_failed(error)

    def _apply_monitor_points(
        self,
        points: list[tuple[float, float, float]],
        duration_sec: Optional[float],
        *,
        animate: bool = True,
    ) -> None:
        self._monitor_timer.stop()
        if not points:
            self._traj_view_m.set_status("Мониторинг траектории (3D)\nНет точек для анимации")
            return

        self._monitor_points = points
        self._monitor_cum_dist_m = []
        d = float(duration_sec) if isinstance(duration_sec, (int, float)) and float(duration_sec) > 0 else 0.0
        if d <= 0.0:
            d = max((len(points) - 1) / 10.0, 0.1)
        self._monitor_duration_sec = d
        self._monitor_sample_dt = (d / (len(points) - 1)) if len(points) > 1 else 0.0
        self._monitor_started_at = time.monotonic()

        cum: list[float] = [0.0]
        for i in range(1, len(points)):
            x0, y0, z0 = points[i - 1]
            x1, y1, z1 = points[i]
            seg = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
            cum.append(cum[-1] + float(seg))
        self._monitor_cum_dist_m = cum

        self._traj_view_m.set_points(points)
        self._update_monitor_params(0)
        self._traj_view_m.set_marker_point(points[0])
        self._traj_view_m.set_status(None)
        if animate:
            self._monitor_timer.start()
            self._log_info(
                "UI_MONITOR_ANIM_START",
                f"points={len(points)} duration_sec={self._monitor_duration_sec:.3f}",
            )
        else:
            self._log_info(
                "UI_MONITOR_ANIM_STATIC",
                f"points={len(points)} reason=disabled_by_operator",
            )

    def _on_monitor_timer_tick(self) -> None:
        points = self._monitor_points
        if not points:
            self._monitor_timer.stop()
            return
        if len(points) == 1:
            self._traj_view_m.set_marker_point(points[0])
            return

        d = max(self._monitor_duration_sec, 0.001)
        active = bool(self._session_runtime_last.get("active", False))
        if active:
            elapsed = max(0.0, float(self._session_runtime_last.get("elapsed_sec", 0.0)))
            t = min(elapsed, d)
        else:
            started = self._monitor_started_at
            if started is None:
                self._monitor_started_at = time.monotonic()
                started = self._monitor_started_at
            elapsed = max(0.0, time.monotonic() - float(started))
            t = elapsed % d
        idx = int((t / d) * (len(points) - 1))
        idx = max(0, min(len(points) - 1, idx))
        self._traj_view_m.set_marker_point(points[idx])
        self._update_monitor_params(idx)

    def _update_monitor_params(self, idx: int) -> None:
        points = self._monitor_points
        if not points:
            return
        i = max(0, min(len(points) - 1, int(idx)))
        x, y, z = points[i]

        speed = 0.0
        dt = self._monitor_sample_dt
        if len(points) > 1 and dt > 0.0:
            i0 = max(0, i - 1)
            i1 = min(len(points) - 1, i + 1)
            if i1 > i0:
                x0, y0, z0 = points[i0]
                x1, y1, z1 = points[i1]
                ds = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
                speed = ds / (float(i1 - i0) * dt)

        dist = self._monitor_cum_dist_m[i] if i < len(self._monitor_cum_dist_m) else 0.0

        if self._m_speed_lbl is not None:
            self._m_speed_lbl.setText(f"{speed:.2f}")
        if self._m_coords_lbl is not None:
            self._m_coords_lbl.setText(f"{x:.2f} / {y:.2f} / {z:.2f}")
        if self._m_geo_lbl is not None:
            lat0 = float(self._gps_origin_lat_spin.value()) if self._gps_origin_lat_spin is not None else _DEFAULT_GPS_ORIGIN_LAT
            lon0 = float(self._gps_origin_lon_spin.value()) if self._gps_origin_lon_spin is not None else _DEFAULT_GPS_ORIGIN_LON
            h0 = float(self._gps_origin_h_spin.value()) if self._gps_origin_h_spin is not None else _DEFAULT_GPS_ORIGIN_H_M
            try:
                x_ecef, y_ecef, z_ecef = enu_to_ecef(float(x), float(y), float(z), lat0, lon0, h0)
                lat, lon, h = ecef_to_geodetic(x_ecef, y_ecef, z_ecef)
                self._m_geo_lbl.setText(f"{lat:.6f} / {lon:.6f} / {h:.2f}")
            except Exception:
                self._m_geo_lbl.setText("Ошибка")
        if self._m_height_lbl is not None:
            self._m_height_lbl.setText(f"{z:.2f}")
        if self._m_distance_lbl is not None:
            self._m_distance_lbl.setText(f"{dist:.2f}")

    def _on_monitor_anim_toggled(self, checked: bool) -> None:
        self._anim_without_test_enabled = bool(checked)
        self._settings["monitor_anim_without_test"] = bool(checked)
        self._save_ui_settings()
        if bool(self._session_runtime_last.get("active", False)):
            # During active test we always animate marker by runtime session clock.
            if self._monitor_points and not self._monitor_timer.isActive():
                self._monitor_timer.start()
            return
        if not checked:
            self._monitor_timer.stop()
            if self._monitor_points:
                self._apply_monitor_points(list(self._monitor_points), self._monitor_duration_sec, animate=False)
            else:
                self._traj_view_m.set_status("Мониторинг траектории (3D)\nАнимация отключена оператором")
            self._log_info("UI_MONITOR_ANIM_STOP", "reason=disabled_by_operator")
            return
        if self._monitor_points:
            self._apply_monitor_points(list(self._monitor_points), self._monitor_duration_sec, animate=True)

    def _on_setting_auto_stop_changed(self, value: float) -> None:
        v = max(0.0, min(3600.0, float(value)))
        self._settings["auto_stop_after_gps_sec"] = v
        self._save_ui_settings()
        self._orch.set_auto_stop_after_gps_sec(v)

    def _on_setting_anim_without_test_toggled(self, checked: bool) -> None:
        self._on_monitor_anim_toggled(bool(checked))

    def _on_setting_nav_path_edited(self) -> None:
        txt = self._opt_nav_default_edit.text().strip() if self._opt_nav_default_edit is not None else ""
        if not txt:
            txt = _DEFAULT_GPS_NAV_PATH
            if self._opt_nav_default_edit is not None:
                self._opt_nav_default_edit.setText(txt)
        self._settings["gps_nav_default_path"] = txt
        self._save_ui_settings()
        if self._gps_nav_path_edit is not None:
            self._gps_nav_path_edit.setText(txt)

    def _on_setting_nav_path_browse(self) -> None:
        current = self._opt_nav_default_edit.text().strip() if self._opt_nav_default_edit is not None else ""
        start_dir = os.path.dirname(current) if current else ""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите дефолтный файл эфемерид",
            start_dir,
            "Ephemeris files (*.*n);;All files (*.*)",
        )
        if not file_path:
            return
        if self._opt_nav_default_edit is not None:
            self._opt_nav_default_edit.setText(file_path)
        self._on_setting_nav_path_edited()

    def _on_setting_session_output_root_edited(self) -> None:
        txt = self._opt_session_output_root_edit.text().strip() if self._opt_session_output_root_edit is not None else ""
        normalized = self._normalize_session_output_root(txt)
        try:
            applied = self._orch.set_test_session_output_root(normalized)
        except Exception as ex:
            QMessageBox.critical(self, "Настройки", f"Не удалось применить папку сессий:\n{type(ex).__name__}: {ex}")
            return
        self._settings["session_output_root"] = applied
        self._save_ui_settings()
        if self._opt_session_output_root_edit is not None:
            self._opt_session_output_root_edit.setText(applied)
        if self._session_output_root_m_edit is not None:
            self._session_output_root_m_edit.setText(applied)

    def _on_setting_session_output_root_browse(self) -> None:
        current = self._opt_session_output_root_edit.text().strip() if self._opt_session_output_root_edit is not None else ""
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку сессий по умолчанию", current)
        if not selected:
            return
        if self._opt_session_output_root_edit is not None:
            self._opt_session_output_root_edit.setText(selected)
        self._on_setting_session_output_root_edited()

    def _on_settings_reset_defaults_clicked(self) -> None:
        self._settings = {
            "gps_nav_default_path": _DEFAULT_GPS_NAV_PATH,
            "session_output_root": self._normalize_session_output_root(_DEFAULT_SESSION_OUTPUT_ROOT),
            "auto_stop_after_gps_sec": _DEFAULT_AUTO_STOP_AFTER_GPS_SEC,
            "monitor_anim_without_test": _DEFAULT_ANIM_WITHOUT_TEST,
        }
        if self._opt_auto_stop_spin is not None:
            self._opt_auto_stop_spin.setValue(_DEFAULT_AUTO_STOP_AFTER_GPS_SEC)
        if self._opt_anim_without_test_chk is not None:
            self._opt_anim_without_test_chk.setChecked(_DEFAULT_ANIM_WITHOUT_TEST)
        if self._opt_nav_default_edit is not None:
            self._opt_nav_default_edit.setText(_DEFAULT_GPS_NAV_PATH)
        if self._opt_session_output_root_edit is not None:
            self._opt_session_output_root_edit.setText(self._settings["session_output_root"])
        self._save_ui_settings()
        self._apply_ui_settings_to_runtime()

    def _on_gps_origin_changed(self, _value: float) -> None:
        self._refresh_gps_finish_point()

    def _refresh_gps_finish_point(self) -> None:
        if self._gps_finish_lat_lbl is None or self._gps_finish_lon_lbl is None or self._gps_finish_h_lbl is None:
            return

        if self._last_trajectory_end_local is None:
            self._gps_finish_lat_lbl.setText("Нет траектории")
            self._gps_finish_lon_lbl.setText("Нет траектории")
            self._gps_finish_h_lbl.setText("Нет траектории")
            return

        lat0 = float(self._gps_origin_lat_spin.value()) if self._gps_origin_lat_spin is not None else _DEFAULT_GPS_ORIGIN_LAT
        lon0 = float(self._gps_origin_lon_spin.value()) if self._gps_origin_lon_spin is not None else _DEFAULT_GPS_ORIGIN_LON
        h0 = float(self._gps_origin_h_spin.value()) if self._gps_origin_h_spin is not None else _DEFAULT_GPS_ORIGIN_H_M

        try:
            e, n, u = self._last_trajectory_end_local
            x_ecef, y_ecef, z_ecef = enu_to_ecef(e, n, u, lat0, lon0, h0)
            lat, lon, h = ecef_to_geodetic(x_ecef, y_ecef, z_ecef)
        except Exception as ex:
            self._gps_finish_lat_lbl.setText("Ошибка")
            self._gps_finish_lon_lbl.setText("Ошибка")
            self._gps_finish_h_lbl.setText("Ошибка")
            self._log_error("UI_SDR_FINISH_POINT_FAILED", f"err={type(ex).__name__}")
            return

        self._gps_finish_lat_lbl.setText(f"{lat:.6f}")
        self._gps_finish_lon_lbl.setText(f"{lon:.6f}")
        self._gps_finish_h_lbl.setText(f"{h:.2f}")

    def _on_mayak_duration_override_toggled(self, checked: bool) -> None:
        if self._mayak_duration_spin is not None:
            self._mayak_duration_spin.setEnabled(bool(checked))
        self._refresh_duration_labels()

    def _resolve_duration_for_mayak(self) -> float:
        override = self._mayak_duration_override is not None and self._mayak_duration_override.isChecked()
        if override and self._mayak_duration_spin is not None:
            return float(self._mayak_duration_spin.value())
        if self._trajectory_duration_sec is not None and self._trajectory_duration_sec > 0:
            return float(self._trajectory_duration_sec)
        if self._mayak_duration_spin is not None:
            fallback = float(self._mayak_duration_spin.value())
            self._log_info("UI_MAYAK_DURATION_FALLBACK", f"reason=no_trajectory_duration fallback_sec={fallback:.3f}")
            return fallback
        return 1.0

    def _refresh_duration_labels(self) -> None:
        if self._lbl_mayak_duration_calc is not None:
            if self._trajectory_duration_sec is None:
                self._lbl_mayak_duration_calc.setText("Нет данных")
            else:
                self._lbl_mayak_duration_calc.setText(f"{self._trajectory_duration_sec:.1f}")
        if self._lbl_mayak_duration_sent is not None:
            if self._last_sent_mayak_duration_sec is None:
                self._lbl_mayak_duration_sent.setText("Еще не отправлялось")
            else:
                self._lbl_mayak_duration_sent.setText(f"{self._last_sent_mayak_duration_sec:.1f}")

    def _get_generate_button(self) -> Optional[QPushButton]:
        return self.findChild(QPushButton, "btn_generate_trajectory")

    def _set_generate_enabled(self, enabled: bool) -> None:
        btn = self._get_generate_button()
        if btn is not None:
            btn.setEnabled(enabled)

    def _on_gps_nav_browse_clicked(self) -> None:
        current = self._gps_nav_path_edit.text().strip() if self._gps_nav_path_edit is not None else ""
        start_dir = os.path.dirname(current) if current else ""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл эфемерид",
            start_dir,
            "Ephemeris files (*.*n);;All files (*.*)",
        )
        if file_path and self._gps_nav_path_edit is not None:
            self._gps_nav_path_edit.setText(file_path)

    def _on_gps_nav_use_default_clicked(self) -> None:
        nav_default = str(self._settings.get("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH)).strip()
        if not nav_default:
            nav_default = _DEFAULT_GPS_NAV_PATH
        if self._gps_nav_path_edit is not None:
            self._gps_nav_path_edit.setText(nav_default)

    def _on_monitor_session_output_root_edited(self) -> None:
        txt = self._session_output_root_m_edit.text().strip() if self._session_output_root_m_edit is not None else ""
        normalized = self._normalize_session_output_root(txt)
        try:
            applied = self._orch.set_test_session_output_root(normalized)
        except Exception as ex:
            QMessageBox.critical(self, "Мониторинг", f"Не удалось применить папку результатов:\n{type(ex).__name__}: {ex}")
            return
        if self._session_output_root_m_edit is not None:
            self._session_output_root_m_edit.setText(applied)

    def _on_monitor_session_output_root_browse(self) -> None:
        current = self._session_output_root_m_edit.text().strip() if self._session_output_root_m_edit is not None else ""
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку для результатов сессии", current)
        if not selected:
            return
        if self._session_output_root_m_edit is not None:
            self._session_output_root_m_edit.setText(selected)
        self._on_monitor_session_output_root_edited()

    def _on_monitor_session_output_root_use_default(self) -> None:
        default_root = self._normalize_session_output_root(
            str(self._settings.get("session_output_root", _DEFAULT_SESSION_OUTPUT_ROOT))
        )
        try:
            applied = self._orch.set_test_session_output_root(default_root)
        except Exception as ex:
            QMessageBox.critical(self, "Мониторинг", f"Не удалось применить дефолтную папку:\n{type(ex).__name__}: {ex}")
            return
        if self._session_output_root_m_edit is not None:
            self._session_output_root_m_edit.setText(applied)

    def get_sdr_options(self) -> dict[str, Any]:
        nav_default = str(self._settings.get("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH))
        nav = self._gps_nav_path_edit.text().strip() if self._gps_nav_path_edit is not None else nav_default
        static_sec = float(self._gps_static_sec_spin.value()) if self._gps_static_sec_spin is not None else _DEFAULT_GPS_STATIC_SEC
        origin_lat = float(self._gps_origin_lat_spin.value()) if self._gps_origin_lat_spin is not None else _DEFAULT_GPS_ORIGIN_LAT
        origin_lon = float(self._gps_origin_lon_spin.value()) if self._gps_origin_lon_spin is not None else _DEFAULT_GPS_ORIGIN_LON
        origin_h = float(self._gps_origin_h_spin.value()) if self._gps_origin_h_spin is not None else _DEFAULT_GPS_ORIGIN_H_M
        rf_bw_mhz = float(self._pluto_rf_bw_spin.value()) if self._pluto_rf_bw_spin is not None else _DEFAULT_PLUTO_RF_BW_MHZ
        tx_atten_db = float(self._pluto_tx_atten_spin.value()) if self._pluto_tx_atten_spin is not None else _DEFAULT_PLUTO_TX_ATTEN_DB

        if not nav:
            nav = nav_default

        return {
            "gps_sdr_sim": {
                "nav": nav,
                "static_sec": static_sec,
                "origin_lat": origin_lat,
                "origin_lon": origin_lon,
                "origin_h": origin_h,
            },
            "pluto_player": {
                "rf_bw_mhz": rf_bw_mhz,
                "tx_atten_db": tx_atten_db,
            },
        }

    def get_sdr_profile_overrides(self) -> dict[str, Any]:
        opts = self.get_sdr_options()
        gps = opts.get("gps_sdr_sim", {}) if isinstance(opts, dict) else {}
        pluto = opts.get("pluto_player", {}) if isinstance(opts, dict) else {}
        nav_default = str(self._settings.get("gps_nav_default_path", _DEFAULT_GPS_NAV_PATH))
        return {
            "services": {
                "gps_sdr_sim": {
                    "nav": gps.get("nav", nav_default),
                    "static_sec": gps.get("static_sec", _DEFAULT_GPS_STATIC_SEC),
                    "origin_lat": gps.get("origin_lat", _DEFAULT_GPS_ORIGIN_LAT),
                    "origin_lon": gps.get("origin_lon", _DEFAULT_GPS_ORIGIN_LON),
                    "origin_h": gps.get("origin_h", _DEFAULT_GPS_ORIGIN_H_M),
                    "rf_bw_mhz": pluto.get("rf_bw_mhz", _DEFAULT_PLUTO_RF_BW_MHZ),
                    "tx_atten_db": pluto.get("tx_atten_db", _DEFAULT_PLUTO_TX_ATTEN_DB),
                }
            }
        }

    def _update_mayak_rpm_limits_from_health(self, e: object) -> None:
        max_sp1 = max(1, int(getattr(e, "effective_max_rpm_sp1", 6000)))
        max_sp2 = max(1, int(getattr(e, "effective_max_rpm_sp2", 6000)))
        if self._head_start_spin is not None:
            self._head_start_spin.setMaximum(max_sp1)
            if self._head_start_spin.value() > max_sp1:
                self._head_start_spin.setValue(max_sp1)
        if self._head_end_spin is not None:
            self._head_end_spin.setMaximum(max_sp1)
            if self._head_end_spin.value() > max_sp1:
                self._head_end_spin.setValue(max_sp1)
        if self._tail_start_spin is not None:
            self._tail_start_spin.setMaximum(max_sp2)
            if self._tail_start_spin.value() > max_sp2:
                self._tail_start_spin.setValue(max_sp2)
        if self._tail_end_spin is not None:
            self._tail_end_spin.setMaximum(max_sp2)
            if self._tail_end_spin.value() > max_sp2:
                self._tail_end_spin.setValue(max_sp2)

    # ---------------- logging helpers ----------------

    def _log_info(self, code: str, message: str) -> None:
        bus = getattr(self._bridge, "_bus", None)
        if bus is not None:
            emit_log(bus, level="INFO", source="ui", code=code, message=message)

    def _log_error(self, code: str, message: str) -> None:
        bus = getattr(self._bridge, "_bus", None)
        if bus is not None:
            emit_log(bus, level="ERROR", source="ui", code=code, message=message)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._release_replay_caps()
        except Exception:
            pass
        super().closeEvent(event)

    @staticmethod
    def _format_opt_bool(v: object) -> str:
        if v is True:
            return "Да"
        if v is False:
            return "Нет"
        return "Неизвестно"

    # ---------------- layout helper ----------------

    def _clear_layout(self, layout: QLayout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            child_layout = it.layout()
            if child_layout is not None:
                self._clear_layout(child_layout)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _safe_find_layout(self, typ: type, name: str):
        obj = self.findChild(typ, name)
        if obj is None and hasattr(self.ui, name):
            obj = getattr(self.ui, name)
        if obj is None:
            self._log_error("UI_LAYOUT_NOT_FOUND", f"layout={name}")
            return None
        return cast(typ, obj)

    def _safe_find_layout_any(self, typ: type, *names: str):
        for name in names:
            obj = self.findChild(typ, name)
            if obj is None and hasattr(self.ui, name):
                obj = getattr(self.ui, name)
            if obj is not None:
                return cast(typ, obj)
        if names:
            self._log_error("UI_LAYOUT_NOT_FOUND", f"layout_any={','.join(names)}")
        return None
