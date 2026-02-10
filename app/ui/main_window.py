from __future__ import annotations

from typing import Any, Optional, cast
import copy
import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from app.core.logging_setup import emit_log
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.ui.generated.main_window import Ui_MainWindow
from app.ui.widgets.config_json_editor import ConfigJsonEditor


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

        # --- UI Step 2: in-memory config_json ---
        self._initial_config: dict[str, Any] = self._load_initial_config_json()
        self.current_config: dict[str, Any] = copy.deepcopy(self._initial_config)

        # UI from generated .ui
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # layouts
        self._gl_trajectory_params: Optional[QGridLayout] = None
        self._vl_trajectory_visualization: Optional[QVBoxLayout] = None

        self._gl_trajectory_params = self._safe_find_layout(
            QGridLayout, "gl_trajectory_params"
        )
        self._vl_trajectory_visualization = self._safe_find_layout(
            QVBoxLayout, "vl_trajectory_visualization"
        )

        # embed editor widget
        self._editor: Optional[ConfigJsonEditor] = None
        self._init_trajectory_params_area()

        # visualization placeholder (unchanged)
        self._init_trajectory_visualization_area()

        # connect Generate
        self._connect_actions()

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

            for meth in ("get_profile", "get_profile_dict", "get_profile_data"):
                fn = getattr(self._orch, meth, None)
                if callable(fn):
                    prof = fn()
                    cfg = self._extract_ballistics_config_json(prof)
                    if cfg is not None:
                        self._log_info("UI_CONFIG_SOURCE", f"Источник: orchestrator.{meth}()")
                        return cfg

            for attr in ("profiles", "profile_loader", "loader"):
                obj = getattr(self._orch, attr, None)
                cfg = self._extract_ballistics_config_json(obj)
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

    # ---------------- UI: params panel ----------------

    def _init_trajectory_params_area(self) -> None:
        gl = self._gl_trajectory_params
        if gl is None:
            return

        # avoid duplicates
        if self.findChild(ConfigJsonEditor, "w_cfg_editor_container") is not None:
            return

        self._editor = ConfigJsonEditor(initial_config=self._initial_config)
        gl.addWidget(self._editor, 0, 0)

        # Ensure generate button exists
        btn_generate = self.findChild(QPushButton, "btn_generate_trajectory")
        if btn_generate is None:
            btn_generate = QPushButton("Сгенерировать траекторию")
            btn_generate.setObjectName("btn_generate_trajectory")
            gl.addWidget(btn_generate, 1, 0)

        gl.setRowStretch(0, 1)
        gl.setRowStretch(1, 0)
        gl.setColumnStretch(0, 1)

    # ---------------- visualization placeholder ----------------

    def _init_trajectory_visualization_area(self) -> None:
        vl = self._vl_trajectory_visualization
        if vl is None:
            return

        existing = self.findChild(QLabel, "lbl_trajectory_visualization_placeholder")
        if existing is not None:
            return

        lbl = QLabel("Визуализация траектории (3D)\nДанные отсутствуют")
        lbl.setObjectName("lbl_trajectory_visualization_placeholder")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        vl.addWidget(lbl)
        vl.setStretch(0, 1)

    # ---------------- actions ----------------

    def _connect_actions(self) -> None:
        btn = self.findChild(QPushButton, "btn_generate_trajectory")
        if btn is not None:
            btn.clicked.connect(self.on_generate_clicked)

    def on_generate_clicked(self) -> None:
        """
        Step 2 / Шаг 5:
        - финальная валидация current_config (dict + json.dumps)
        - json stringify (compact: indent=None)
        - bytes
        - keys: количество leaf-ключей (не-dict значений)
        - логи: UI_GENERATE_CLICKED + UI_CONFIG_READY
        """
        bus = getattr(self._bridge, "_bus", None)
        if bus is None:
            return

        emit_log(
            bus,
            level="INFO",
            source="ui",
            code="UI_GENERATE_CLICKED",
            message="Нажата кнопка генерации траектории",
        )

        cfg = self._editor.get_config() if self._editor is not None else self.current_config

        # финальная валидация
        if not isinstance(cfg, dict):
            emit_log(
                bus,
                level="ERROR",
                source="ui",
                code="UI_CONFIG_INVALID",
                message="config_json должен быть dict",
            )
            return

        try:
            json_str = json.dumps(cfg, ensure_ascii=False, indent=None)
        except Exception as e:
            emit_log(
                bus,
                level="ERROR",
                source="ui",
                code="UI_CONFIG_INVALID",
                message=f"config_json не сериализуется: {e!r}",
            )
            return

        self.current_config = cfg

        n_bytes = len(json_str.encode("utf-8"))
        n_keys = self._count_leaf_keys(cfg)

        emit_log(
            bus,
            level="INFO",
            source="ui",
            code="UI_CONFIG_READY",
            message=f"bytes={n_bytes} keys={n_keys}",
        )

    def _count_leaf_keys(self, obj: Any) -> int:
        """
        keys=<K> фиксируем как количество leaf-ключей:
        сколько значений в dict-дереве, которые не являются dict.
        list считается одним leaf.
        """
        if isinstance(obj, dict):
            total = 0
            for v in obj.values():
                total += self._count_leaf_keys(v)
            return total
        # dict закончился => leaf (scalar/list/etc.)
        return 1

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
