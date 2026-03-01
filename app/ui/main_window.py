from __future__ import annotations

import copy
from typing import Any, Optional, cast

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
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
        "X0": 0.0,
        "Y0": 0.0,
        "Z0": 1.0,
        "omega_body": [0.0, 0.0, 100.0],
    },
}

_DEFAULT_PREVIEW_VISIBLE = "outputs/video_preview/visible/latest.jpg"
_DEFAULT_PREVIEW_THERMAL = "outputs/video_preview/thermal/latest.jpg"


class MainWindow(QMainWindow):
    def __init__(self, orchestrator: Orchestrator, bridge: UIBridge) -> None:
        super().__init__()
        self._orch = orchestrator
        self._bridge = bridge

        self._initial_config: dict[str, Any] = self._load_initial_config_json()
        self.current_config: dict[str, Any] = copy.deepcopy(self._initial_config)
        self._last_mayak_ready: Optional[bool] = None
        self._last_mayak_health_event: Optional[object] = None

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._gl_trajectory_params: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "gl_trajectory_params")
        self._vl_trajectory_visualization: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "vl_trajectory_visualization")
        self._vl_rtsp_visible: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "vl_rtsp_visible")
        self._vl_rtsp_thermal: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "vl_rtsp_thermal")
        self._vl_mayak_params: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "l_Mayak_params")

        self._editor: Optional[ConfigJsonEditor] = None
        self._init_editor()

        # 3D view + controller
        self._traj_view = Trajectory3DView(self)
        self._init_trajectory_view()

        self._init_rtsp_previews()

        # Mayak panel refs
        self._mayak_spindle_combo: Optional[QComboBox] = None
        self._mayak_direction_combo: Optional[QComboBox] = None
        self._mayak_rpm_spin: Optional[QSpinBox] = None
        self._btn_mayak_start: Optional[QPushButton] = None
        self._btn_mayak_stop: Optional[QPushButton] = None
        self._btn_mayak_emergency: Optional[QPushButton] = None
        self._lbl_mayak_ready: Optional[QLabel] = None
        self._lbl_mayak_connected: Optional[QLabel] = None
        self._lbl_mayak_state: Optional[QLabel] = None
        self._lbl_mayak_error: Optional[QLabel] = None
        self._lbl_mayak_reason: Optional[QLabel] = None
        self._mayak_tel_labels: dict[str, dict[str, QLabel]] = {}
        self._init_mayak_panel()

        self._traj_loader = TrajectoryCsvLoader()
        self._traj_ctl = TrajectoryVisController(bridge=self._bridge, view=self._traj_view, loader=self._traj_loader)

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

    # ---------------- UI init ----------------

    def _init_editor(self) -> None:
        glp = self._gl_trajectory_params
        if glp is None:
            return

        self._editor = ConfigJsonEditor(initial_config=self._initial_config)
        glp.addWidget(self._editor, 0, 0)

        btn = self._get_generate_button()
        if btn is None:
            btn = QPushButton("Generate Trajectory")
            btn.setObjectName("btn_generate_trajectory")
            glp.addWidget(btn, 1, 0)

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
            self._vl_rtsp_visible.addWidget(w, 0, 0)
        if self._vl_rtsp_thermal is not None:
            w = RtspPreviewWidget(_DEFAULT_PREVIEW_THERMAL, title="Thermal", poll_ms=200, parent=self)
            self._vl_rtsp_thermal.addWidget(w, 0, 0)

    def _init_mayak_panel(self) -> None:
        vl = self._vl_mayak_params
        if vl is None:
            return

        self._clear_layout(vl)

        panel = QWidget(self)
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)

        control_box = QGroupBox("Mayak Control", panel)
        control_form = QFormLayout(control_box)

        self._mayak_spindle_combo = QComboBox(control_box)
        self._mayak_spindle_combo.addItems(["sp1", "sp2"])

        self._mayak_direction_combo = QComboBox(control_box)
        self._mayak_direction_combo.addItem("Forward", 1)
        self._mayak_direction_combo.addItem("Reverse", -1)
        self._mayak_direction_combo.addItem("Stop", 0)

        self._mayak_rpm_spin = QSpinBox(control_box)
        self._mayak_rpm_spin.setRange(0, 6000)
        self._mayak_rpm_spin.setSingleStep(50)
        self._mayak_rpm_spin.setValue(500)

        control_form.addRow("Spindle", self._mayak_spindle_combo)
        control_form.addRow("Direction", self._mayak_direction_combo)
        control_form.addRow("RPM", self._mayak_rpm_spin)

        btn_row = QHBoxLayout()
        self._btn_mayak_start = QPushButton("Start", control_box)
        self._btn_mayak_stop = QPushButton("Stop", control_box)
        self._btn_mayak_emergency = QPushButton("Emergency Stop", control_box)
        btn_row.addWidget(self._btn_mayak_start)
        btn_row.addWidget(self._btn_mayak_stop)
        btn_row.addWidget(self._btn_mayak_emergency)
        control_form.addRow(btn_row)

        status_box = QGroupBox("Mayak Status", panel)
        status_form = QFormLayout(status_box)
        self._lbl_mayak_ready = QLabel("-", status_box)
        self._lbl_mayak_connected = QLabel("-", status_box)
        self._lbl_mayak_state = QLabel("-", status_box)
        self._lbl_mayak_error = QLabel("-", status_box)
        self._lbl_mayak_reason = QLabel("-", status_box)
        status_form.addRow("Ready", self._lbl_mayak_ready)
        status_form.addRow("Connected", self._lbl_mayak_connected)
        status_form.addRow("State", self._lbl_mayak_state)
        status_form.addRow("Error", self._lbl_mayak_error)
        status_form.addRow("Reason", self._lbl_mayak_reason)

        telemetry_box = QGroupBox("Mayak Telemetry", panel)
        telemetry_grid = QGridLayout(telemetry_box)
        telemetry_grid.addWidget(QLabel("Spindle", telemetry_box), 0, 0)
        telemetry_grid.addWidget(QLabel("RPM", telemetry_box), 0, 1)
        telemetry_grid.addWidget(QLabel("Torque", telemetry_box), 0, 2)
        telemetry_grid.addWidget(QLabel("Angle", telemetry_box), 0, 3)

        for row, sp in ((1, "sp1"), (2, "sp2")):
            telemetry_grid.addWidget(QLabel(sp, telemetry_box), row, 0)
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

        root.addWidget(control_box)
        root.addWidget(status_box)
        root.addWidget(telemetry_box)
        root.addStretch(1)

        vl.addWidget(panel)

    # ---------------- wiring ----------------

    def _connect_actions(self) -> None:
        btn = self._get_generate_button()
        if btn is not None and self._gen_ctl is not None:
            btn.clicked.connect(self._gen_ctl.on_generate_clicked)

        if self._btn_mayak_start is not None:
            self._btn_mayak_start.clicked.connect(self._on_mayak_start_clicked)
        if self._btn_mayak_stop is not None:
            self._btn_mayak_stop.clicked.connect(self._on_mayak_stop_clicked)
        if self._btn_mayak_emergency is not None:
            self._btn_mayak_emergency.clicked.connect(self._on_mayak_emergency_clicked)
        if self._mayak_spindle_combo is not None:
            self._mayak_spindle_combo.currentIndexChanged.connect(self._on_mayak_spindle_changed)

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
            self._lbl_mayak_ready.setText("YES" if ready else "NO")
        if self._lbl_mayak_connected is not None:
            sp1_conn = self._format_opt_bool(getattr(e, "sp1_connected", None))
            sp2_conn = self._format_opt_bool(getattr(e, "sp2_connected", None))
            self._lbl_mayak_connected.setText(f"sp1={sp1_conn}, sp2={sp2_conn}")
        if self._lbl_mayak_state is not None:
            sp1 = str(getattr(e, "sp1_state", "UNKNOWN"))
            sp2 = str(getattr(e, "sp2_state", "UNKNOWN"))
            self._lbl_mayak_state.setText(f"sp1={sp1}, sp2={sp2}")
        if self._lbl_mayak_error is not None:
            self._lbl_mayak_error.setText(str(int(getattr(e, "error_code", 0))))
        if self._lbl_mayak_reason is not None:
            self._lbl_mayak_reason.setText(str(getattr(e, "degraded_reason", "none")))

        self._update_mayak_rpm_limit_from_health(e)

        try:
            sp1 = str(getattr(e, "sp1_state", "UNKNOWN"))
            sp2 = str(getattr(e, "sp2_state", "UNKNOWN"))
            err = int(getattr(e, "error_code", 0))
            reason = str(getattr(e, "degraded_reason", "none"))
            self.statusBar().showMessage(
                f"Mayak ready={1 if ready else 0} sp1={sp1} sp2={sp2} err={err} reason={reason}",
                3000,
            )
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
        sp = self._selected_mayak_spindle()
        direction = self._selected_mayak_direction()
        rpm = int(self._mayak_rpm_spin.value()) if self._mayak_rpm_spin is not None else 0
        try:
            self._orch.set_speed(sp, rpm, direction)
            self._log_info("UI_MAYAK_CMD", f"cmd=set_speed spindle={sp} direction={direction} rpm={rpm}")
        except Exception as ex:
            self._log_error("UI_MAYAK_CMD_FAILED", f"cmd=set_speed spindle={sp} err={type(ex).__name__}")
            self.statusBar().showMessage(f"Mayak command failed: {type(ex).__name__}", 3000)

    def _on_mayak_stop_clicked(self) -> None:
        sp = self._selected_mayak_spindle()
        try:
            self._orch.stop_spindle(sp)
            self._log_info("UI_MAYAK_CMD", f"cmd=stop_spindle spindle={sp}")
        except Exception as ex:
            self._log_error("UI_MAYAK_CMD_FAILED", f"cmd=stop_spindle spindle={sp} err={type(ex).__name__}")
            self.statusBar().showMessage(f"Mayak stop failed: {type(ex).__name__}", 3000)

    def _on_mayak_emergency_clicked(self) -> None:
        try:
            self._orch.emergency_stop()
            self._log_info("UI_MAYAK_CMD", "cmd=emergency_stop")
        except Exception as ex:
            self._log_error("UI_MAYAK_CMD_FAILED", f"cmd=emergency_stop err={type(ex).__name__}")
            self.statusBar().showMessage(f"Mayak emergency failed: {type(ex).__name__}", 3000)

    def _on_mayak_spindle_changed(self) -> None:
        if self._last_mayak_health_event is not None:
            self._update_mayak_rpm_limit_from_health(self._last_mayak_health_event)

    def _get_generate_button(self) -> Optional[QPushButton]:
        return self.findChild(QPushButton, "btn_generate_trajectory")

    def _set_generate_enabled(self, enabled: bool) -> None:
        btn = self._get_generate_button()
        if btn is not None:
            btn.setEnabled(enabled)

    def _selected_mayak_spindle(self) -> str:
        if self._mayak_spindle_combo is None:
            return "sp1"
        txt = self._mayak_spindle_combo.currentText().strip().lower()
        return "sp2" if txt == "sp2" else "sp1"

    def _selected_mayak_direction(self) -> int:
        if self._mayak_direction_combo is None:
            return 1
        data = self._mayak_direction_combo.currentData()
        try:
            return int(data)
        except Exception:
            return 1

    def _update_mayak_rpm_limit_from_health(self, e: object) -> None:
        if self._mayak_rpm_spin is None:
            return
        sp = self._selected_mayak_spindle()
        max_rpm = int(getattr(e, "effective_max_rpm_sp2", 6000)) if sp == "sp2" else int(getattr(e, "effective_max_rpm_sp1", 6000))
        max_rpm = max(1, max_rpm)
        self._mayak_rpm_spin.setMaximum(max_rpm)
        if self._mayak_rpm_spin.value() > max_rpm:
            self._mayak_rpm_spin.setValue(max_rpm)

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
            return "YES"
        if v is False:
            return "NO"
        return "UNKNOWN"

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
