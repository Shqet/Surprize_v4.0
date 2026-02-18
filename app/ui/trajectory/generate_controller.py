from __future__ import annotations

import copy
import json
from typing import Any, Callable, Optional

from app.core.logging_setup import emit_log
from app.orchestrator.orchestrator import Orchestrator
from app.ui.trajectory.trajectory_3d_view import Trajectory3DView
from app.ui.trajectory.controller import TrajectoryVisController
from app.ui.widgets.config_json_editor import ConfigJsonEditor


class GenerateController:
    """
    Handles UI Generate flow:
      - validate config_json
      - invoke Orchestrator.start() with overrides
      - update UI state (button enabled/disabled, status text)
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        editor: ConfigJsonEditor,
        traj_view: Trajectory3DView,
        traj_ctl: TrajectoryVisController,
        set_generate_enabled: Callable[[bool], None],
        bus_getter: Callable[[], Any],
    ) -> None:
        self._orch = orchestrator
        self._editor = editor
        self._traj_view = traj_view
        self._traj_ctl = traj_ctl
        self._set_generate_enabled = set_generate_enabled
        self._bus_getter = bus_getter

        self._bm_running = False

    def on_service_status_event(self, e: object) -> None:
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
        bus = self._bus_getter()
        if bus is None:
            return

        if self._bm_running:
            emit_log(bus, level="INFO", source="ui", code="UI_RUN_ALREADY_RUNNING", message="Generate ignored: already RUNNING")
            return

        emit_log(bus, level="INFO", source="ui", code="UI_GENERATE_CLICKED", message="Нажата кнопка генерации траектории")

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
