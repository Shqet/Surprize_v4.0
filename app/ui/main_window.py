from __future__ import annotations

import copy
import os
from typing import Any, Optional, cast

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
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import emit_log
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.ui.generated.main_window import Ui_MainWindow
from app.ui.trajectory.controller import TrajectoryVisController
from app.ui.trajectory.csv_loader import TrajectoryCsvLoader
from app.ui.trajectory.generate_controller import GenerateController
from app.ui.trajectory.trajectory_3d_view import Trajectory3DView
from app.ui.widgets.config_json_editor import ConfigJsonEditor
from app.ui.widgets.rtsp_preview import RtspPreviewWidget

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
        self._ui_debug: bool = str(os.getenv("SURPRIZE_UI_DEBUG", "0")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._gl_trajectory_params: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "gl_trajectory_params")
        self._vl_trajectory_visualization: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "vl_trajectory_visualization")
        self._vl_rtsp_visible: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "vl_rtsp_visible")
        self._vl_rtsp_thermal: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "vl_rtsp_thermal")
        self._vl_mayak_params: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "l_Mayak_params")
        self._gl_sdr_options: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "l_SDR_options")

        self._editor: Optional[ConfigJsonEditor] = None
        self._init_editor()

        # 3D view + controller
        self._traj_view = Trajectory3DView(self)
        self._init_trajectory_view()

        self._init_rtsp_previews()
        self._gps_nav_path_edit: Optional[QLineEdit] = None
        self._btn_gps_nav_browse: Optional[QPushButton] = None
        self._gps_static_sec_spin: Optional[QDoubleSpinBox] = None
        self._pluto_rf_bw_spin: Optional[QDoubleSpinBox] = None
        self._pluto_tx_atten_spin: Optional[QDoubleSpinBox] = None
        self._init_sdr_options_panel()

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

        self._traj_loader = TrajectoryCsvLoader()
        self._traj_ctl = TrajectoryVisController(
            bridge=self._bridge,
            view=self._traj_view,
            loader=self._traj_loader,
            on_duration_resolved=self._on_trajectory_duration_resolved,
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
        gl = self._gl_sdr_options
        if gl is None:
            return

        self._clear_layout(gl)

        gps_box = QGroupBox("GPS SDR Sim", self)
        gps_form = QFormLayout(gps_box)

        self._gps_nav_path_edit = QLineEdit(gps_box)
        self._gps_nav_path_edit.setText(_DEFAULT_GPS_NAV_PATH)
        self._gps_nav_path_edit.setPlaceholderText("data/ephemerides/brdc0430.25n")
        self._btn_gps_nav_browse = QPushButton("...", gps_box)
        self._btn_gps_nav_browse.setFixedWidth(34)
        self._btn_gps_nav_browse.clicked.connect(self._on_gps_nav_browse_clicked)
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.addWidget(self._gps_nav_path_edit)
        nav_row.addWidget(self._btn_gps_nav_browse)

        self._gps_static_sec_spin = QDoubleSpinBox(gps_box)
        self._gps_static_sec_spin.setRange(0.0, 36000.0)
        self._gps_static_sec_spin.setDecimals(1)
        self._gps_static_sec_spin.setSingleStep(1.0)
        self._gps_static_sec_spin.setValue(_DEFAULT_GPS_STATIC_SEC)

        nav_label = QLabel("Путь к эфемеридам", gps_box)
        gps_form.addRow(nav_label)
        gps_form.addRow(nav_row)
        gps_form.addRow("Время статики, сек", self._gps_static_sec_spin)

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

        gl.addWidget(gps_box, 0, 0)
        gl.addWidget(pluto_box, 1, 0)
        gl.setRowStretch(2, 1)
        gl.setColumnStretch(0, 1)

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

    def get_sdr_options(self) -> dict[str, Any]:
        nav = self._gps_nav_path_edit.text().strip() if self._gps_nav_path_edit is not None else _DEFAULT_GPS_NAV_PATH
        static_sec = float(self._gps_static_sec_spin.value()) if self._gps_static_sec_spin is not None else _DEFAULT_GPS_STATIC_SEC
        rf_bw_mhz = float(self._pluto_rf_bw_spin.value()) if self._pluto_rf_bw_spin is not None else _DEFAULT_PLUTO_RF_BW_MHZ
        tx_atten_db = float(self._pluto_tx_atten_spin.value()) if self._pluto_tx_atten_spin is not None else _DEFAULT_PLUTO_TX_ATTEN_DB

        if not nav:
            nav = _DEFAULT_GPS_NAV_PATH

        return {
            "gps_sdr_sim": {
                "nav": nav,
                "static_sec": static_sec,
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
        return {
            "services": {
                "gps_sdr_sim": {
                    "nav": gps.get("nav", _DEFAULT_GPS_NAV_PATH),
                    "static_sec": gps.get("static_sec", _DEFAULT_GPS_STATIC_SEC),
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
