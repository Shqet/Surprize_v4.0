from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log

from .session_runtime import SessionRuntime


@dataclass
class _TickerHandle:
    stop_event: threading.Event
    thread: threading.Thread


class SessionTrajectoryTicker:
    """
    Runtime ticker over generated trajectory_timeline.csv.
    Keeps lightweight time synchronization cursor for active session.
    """

    def __init__(self, bus: EventBus, *, tick_period_sec: float = 0.2) -> None:
        self._bus = bus
        self._tick_period_sec = max(0.05, float(tick_period_sec))

    def start(self, session_ctx: SessionRuntime) -> None:
        if "trajectory_ticker" in session_ctx.handles:
            raise RuntimeError("trajectory_ticker_already_started")

        timeline_path = Path(session_ctx.paths.get("trajectory_timeline", ""))
        if not timeline_path.exists():
            raise FileNotFoundError(f"trajectory_timeline_missing={timeline_path.as_posix()}")

        points = self._load_timeline(timeline_path)
        if not points:
            raise RuntimeError("trajectory_timeline_empty")

        tick_log = Path(session_ctx.paths["out_dir"]) / "trajectory_ticks.log"
        session_ctx.paths["trajectory_ticks"] = str(tick_log)
        stop_event = threading.Event()
        t = threading.Thread(
            target=self._run_loop,
            args=(session_ctx, points, tick_log, stop_event),
            name=f"trajectory.ticker.{session_ctx.session_id}",
            daemon=True,
        )
        t.start()
        session_ctx.handles["trajectory_ticker"] = _TickerHandle(stop_event=stop_event, thread=t)

        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "SESSION_TRAJECTORY_TICKER_START",
            f"session_id={session_ctx.session_id} points={len(points)}",
        )

    def stop(self, session_ctx: SessionRuntime) -> None:
        handle = session_ctx.handles.get("trajectory_ticker")
        if not isinstance(handle, _TickerHandle):
            emit_log(
                self._bus,
                "INFO",
                "orchestrator",
                "SESSION_TRAJECTORY_TICKER_STOP",
                f"session_id={session_ctx.session_id} status=not_running",
            )
            return

        handle.stop_event.set()
        if handle.thread.is_alive():
            handle.thread.join(timeout=2.0)
        session_ctx.handles.pop("trajectory_ticker", None)
        emit_log(
            self._bus,
            "INFO",
            "orchestrator",
            "SESSION_TRAJECTORY_TICKER_STOP",
            f"session_id={session_ctx.session_id} status=stopped",
        )

    def describe(self, session_ctx: SessionRuntime) -> dict[str, object]:
        handle = session_ctx.handles.get("trajectory_ticker")
        if not isinstance(handle, _TickerHandle):
            return {"state": "not_running"}
        alive = bool(handle.thread.is_alive())
        return {"state": "running" if alive else "stopped"}

    @staticmethod
    def _load_timeline(path: Path) -> list[tuple[float, float, float, float, float]]:
        out: list[tuple[float, float, float, float, float]] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            headers = next(reader, None)
            if not headers:
                return out
            for row in reader:
                if not row or len(row) < 5:
                    continue
                try:
                    t_rel = float(row[0])
                    x = float(row[1])
                    y = float(row[2])
                    z = float(row[3])
                    speed = float(row[4])
                except Exception:
                    continue
                out.append((t_rel, x, y, z, speed))
        return out

    def _run_loop(
        self,
        session_ctx: SessionRuntime,
        points: list[tuple[float, float, float, float, float]],
        tick_log: Path,
        stop_event: threading.Event,
    ) -> None:
        idx = 0
        tick_log.parent.mkdir(parents=True, exist_ok=True)
        with tick_log.open("a", encoding="utf-8") as fh:
            while not stop_event.is_set() and idx < len(points):
                now_rel = max(0.0, time.monotonic() - float(session_ctx.t0_monotonic))
                while idx + 1 < len(points) and points[idx + 1][0] <= now_rel:
                    idx += 1

                t_rel, x, y, z, speed = points[idx]
                fh.write(
                    f"{time.time():.6f},"
                    f"{now_rel:.3f},"
                    f"{t_rel:.3f},"
                    f"{x:.6f},{y:.6f},{z:.6f},{speed:.6f}\n"
                )
                fh.flush()
                time.sleep(self._tick_period_sec)
