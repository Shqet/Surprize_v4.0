from __future__ import annotations

from typing import Callable, Optional

from app.core.logging_setup import emit_log
from app.ui.trajectory.csv_loader import TrajectoryCsvLoader
from app.ui.trajectory.trajectory_3d_view import Trajectory3DView


class TrajectoryVisController:
    """
    Step 4.4 UX/states:
      - RUNNING: keep old plot + overlay "Computing…" (if plot exists)
      - STOPPED: load CSV async -> render
      - ERROR: show "Failed"
      - logs: UI_VIS_LOAD_REQUEST/OK/FAIL
    """

    def __init__(
        self,
        bridge,
        view: Trajectory3DView,
        loader: TrajectoryCsvLoader,
        on_duration_resolved: Optional[Callable[[Optional[float]], None]] = None,
        on_points_resolved: Optional[Callable[[list[tuple[float, float, float]]], None]] = None,
    ) -> None:
        self._bridge = bridge
        self._view = view
        self._loader = loader
        self._on_duration_resolved = on_duration_resolved
        self._on_points_resolved = on_points_resolved

        self.last_run_dir: Optional[str] = None
        self._run_seq: int = 0

    def new_run_started(self) -> None:
        # stale protection: invalidate previous in-flight loads
        self._run_seq += 1
        self.last_run_dir = None
        # keep old plot if any, just show overlay/status
        self._view.set_status("Computing…")

    @staticmethod
    def _parse_kv_message(message: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for tok in (message or "").split():
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            if not k:
                continue
            out[k] = v.strip().strip(",")
        return out

    def on_log_event(self, e: object) -> None:
        source = getattr(e, "source", None)
        if source != "ballistics_model":
            return

        message = getattr(e, "message", "") or ""
        kv = self._parse_kv_message(message)

        out_dir = kv.get("out_dir")
        run_dir = kv.get("run_dir")
        if out_dir:
            self.last_run_dir = out_dir
        elif run_dir:
            self.last_run_dir = run_dir

    def on_service_status(self, e: object) -> None:
        service_name = getattr(e, "service_name", None)
        status = getattr(e, "status", None)
        if service_name != "ballistics_model":
            return

        bus = getattr(self._bridge, "_bus", None)

        if status == "RUNNING":
            # keep old plot + overlay
            self._view.set_status("Computing…")
            return

        if status == "ERROR":
            if bus is not None:
                emit_log(bus, level="ERROR", source="ui", code="UI_RUN_FINISHED", message="status=ERROR")
            # hard fail message (clear view)
            self._view.show_failed()
            return

        if status != "STOPPED":
            return

        if bus is not None:
            emit_log(bus, level="INFO", source="ui", code="UI_RUN_FINISHED", message="status=STOPPED")

        if not self.last_run_dir:
            self._view.show_message("Failed\nResult path unknown")
            return

        seq = self._run_seq

        if bus is not None:
            emit_log(bus, level="INFO", source="ui", code="UI_VIS_LOAD_REQUEST", message=f"run_dir={self.last_run_dir}")

        # keep old plot, but show loading status
        self._view.set_status("Loading trajectory.csv…")

        self._loader.start(
            seq=seq,
            run_dir=self.last_run_dir,
            on_ok=self._on_loaded_ok,
            on_fail=self._on_loaded_fail,
        )

    def _on_loaded_ok(self, seq: int, points_obj: object) -> None:
        if seq != self._run_seq:
            return

        payload = points_obj if isinstance(points_obj, dict) else {}
        points = payload.get("points", points_obj)  # list[tuple[float,float,float]]
        duration_sec = payload.get("duration_sec") if isinstance(payload, dict) else None
        points_list = list(points) if isinstance(points, list) else []
        bus = getattr(self._bridge, "_bus", None)

        if bus is not None:
            emit_log(
                bus,
                level="INFO",
                source="ui",
                code="UI_VIS_LOAD_OK",
                message=f"points={len(points_list)}",
            )

        # render
        self._view.set_points(points_list)
        if self._on_duration_resolved is not None:
            try:
                resolved = float(duration_sec) if isinstance(duration_sec, (int, float)) else None
                self._on_duration_resolved(resolved)
            except Exception:
                pass
        if self._on_points_resolved is not None:
            try:
                self._on_points_resolved(points_list)
            except Exception:
                pass

        if bus is not None:
            emit_log(
                bus,
                level="INFO",
                source="ui",
                code="UI_VIS_RENDER_OK",
                message=f"points={len(points_list)}",
            )

    def _on_loaded_fail(self, seq: int, error: str) -> None:
        if seq != self._run_seq:
            return

        bus = getattr(self._bridge, "_bus", None)
        if bus is not None:
            emit_log(bus, level="ERROR", source="ui", code="UI_VIS_LOAD_FAIL", message=f"error={error}")

        # hard fail (clear view)
        self._view.show_failed(error)
