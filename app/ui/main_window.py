from __future__ import annotations

from typing import Any, Optional, cast
import copy
import json

from PyQt6.QtWidgets import QGridLayout, QMainWindow, QPushButton, QVBoxLayout

from app.core.logging_setup import emit_log
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.ui.generated.main_window import Ui_MainWindow
from app.ui.widgets.config_json_editor import ConfigJsonEditor

from app.ui.trajectory.csv_loader import TrajectoryCsvLoader
from app.ui.trajectory.trajectory_3d_view import Trajectory3DView
from app.ui.trajectory.controller import TrajectoryVisController


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


class MainWindow(QMainWindow):
    def __init__(self, orchestrator: Orchestrator, bridge: UIBridge) -> None:
        super().__init__()
        self._orch = orchestrator
        self._bridge = bridge

        self._bm_running = False

        self._initial_config: dict[str, Any] = self._load_initial_config_json()
        self.current_config: dict[str, Any] = copy.deepcopy(self._initial_config)

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._gl_trajectory_params: Optional[QGridLayout] = self._safe_find_layout(QGridLayout, "gl_trajectory_params")
        self._vl_trajectory_visualization: Optional[QVBoxLayout] = self._safe_find_layout(QVBoxLayout, "vl_trajectory_visualization")

        self._editor: Optional[ConfigJsonEditor] = None
        self._init_editor()

        # 3D view + controller
        self._traj_view = Trajectory3DView(self)
        self._init_trajectory_view()

        self._traj_loader = TrajectoryCsvLoader()
        self._traj_ctl = TrajectoryVisController(bridge=self._bridge, view=self._traj_view, loader=self._traj_loader)

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
                    self._log_info("UI_CONFIG_SOURCE", f"Источник: orchestrator.{attr}")
                    return cfg
        except Exception as e:
            self._log_info("UI_CONFIG_SOURCE_FAILED", f"Не удалось взять из orchestrator: {e!r}")

        self._log_info("UI_CONFIG_SOURCE", "Источник: дефолт (fallback, без чтения файлов)")
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
            btn = QPushButton("Сгенерировать траекторию")
            btn.setObjectName("btn_generate_trajectory")
            glp.addWidget(btn, 1, 0)

        glp.setRowStretch(0, 1)
        glp.setRowStretch(1, 0)
        glp.setColumnStretch(0, 1)

    def _init_trajectory_view(self) -> None:
        vl = self._vl_trajectory_visualization
        if vl is None:
            return
        # replace anything that designer might have put there
        while vl.count():
            it = vl.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        vl.addWidget(self._traj_view)
        vl.setStretch(0, 1)

    # ---------------- wiring ----------------

    def _connect_actions(self) -> None:
        btn = self._get_generate_button()
        if btn is not None:
            btn.clicked.connect(self.on_generate_clicked)

    def _connect_bridge(self) -> None:
        try:
            self._bridge.service_status_event.connect(self._on_service_status_event)
        except Exception:
            pass
        try:
            self._bridge.log_event.connect(self._traj_ctl.on_log_event)
        except Exception:
            pass

    def _get_generate_button(self) -> Optional[QPushButton]:
        return self.findChild(QPushButton, "btn_generate_trajectory")

    def _set_generate_enabled(self, enabled: bool) -> None:
        btn = self._get_generate_button()
        if btn is not None:
            btn.setEnabled(enabled)

    # ---------------- events ----------------

    def _on_service_status_event(self, e: object) -> None:
        service_name = getattr(e, "service_name", None)
        status = getattr(e, "status", None)
        if service_name != "ballistics_model":
            return

        if status == "RUNNING":
            self._bm_running = True
            self._set_generate_enabled(False)
            self._traj_view.set_status("Computing…")
            return

        if status in ("STOPPED", "ERROR"):
            self._bm_running = False
            self._set_generate_enabled(True)

        # delegate STOPPED/ERROR handling to controller (it also emits UI_VIS_* logs)
        self._traj_ctl.on_service_status(e)

    def on_generate_clicked(self) -> None:
        bus = getattr(self._bridge, "_bus", None)
        if bus is None:
            return

        if self._bm_running:
            emit_log(bus, level="INFO", source="ui", code="UI_RUN_ALREADY_RUNNING", message="Generate ignored: already RUNNING")
            return

        emit_log(bus, level="INFO", source="ui", code="UI_GENERATE_CLICKED", message="Нажата кнопка генерации траектории")

        if self._editor is None:
            emit_log(bus, level="ERROR", source="ui", code="UI_CONFIG_INVALID", message="Редактор config_json не найден")
            return

        cfg = copy.deepcopy(self._editor.get_config())
        if not isinstance(cfg, dict):
            emit_log(bus, level="ERROR", source="ui", code="UI_CONFIG_INVALID", message="config_json должен быть dict")
            return

        try:
            json_str = json.dumps(cfg, ensure_ascii=False, indent=None)
        except Exception as e:
            emit_log(bus, level="ERROR", source="ui", code="UI_CONFIG_INVALID", message=f"config_json не сериализуется: {e!r}")
            return

        emit_log(bus, level="INFO", source="ui", code="UI_RUN_REQUESTED", message=f"service=ballistics_model bytes={len(json_str.encode('utf-8'))}")

        overrides = {"services": {"ballistics_model": {"config_json": cfg, "make_plots": False}}}

        self._bm_running = True
        self._set_generate_enabled(False)

        # stale protection + show computing
        self._traj_ctl.new_run_started()

        try:
            self._orch.start("default", overrides=overrides)
        except Exception as e:
            self._bm_running = False
            self._set_generate_enabled(True)
            emit_log(bus, level="ERROR", source="ui", code="UI_RUN_START_FAILED", message=f"Не удалось запустить orchestrator: {e!r}")

    # ---------------- logging helpers ----------------

    def _log_info(self, code: str, message: str) -> None:
        bus = getattr(self._bridge, "_bus", None)
        if bus is not None:
            emit_log(bus, level="INFO", source="ui", code=code, message=message)

    def _log_error(self, code: str, message: str) -> None:
        bus = getattr(self._bridge, "_bus", None)
        if bus is not None:
            emit_log(bus, level="ERROR", source="ui", code=code, message=message)

    # ---------------- layout helper ----------------

    def _safe_find_layout(self, typ: type, name: str):
        obj = self.findChild(typ, name)
        if obj is None and hasattr(self.ui, name):
            obj = getattr(self.ui, name)
        if obj is None:
            self._log_error("UI_LAYOUT_NOT_FOUND", f"layout={name}")
            return None
        return cast(typ, obj)
