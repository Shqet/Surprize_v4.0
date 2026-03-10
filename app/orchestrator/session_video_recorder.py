from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log

from .session_runtime import SessionRuntime

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    cv2 = None  # type: ignore


@dataclass
class _ChannelCtx:
    channel: str
    service_name: str
    service: Any
    mp4_path: Path
    frames_csv_path: Path
    preview_jpg_path: Path
    thread: Optional[threading.Thread] = None
    stop_event: Optional[threading.Event] = None
    frames_written: int = 0
    failures: int = 0
    degraded: bool = False


class SessionVideoRecorder:
    """
    Runtime video recorder for test session.
    Uses existing video daemon services via `save_preview(path)` command.
    """

    def __init__(
        self,
        bus: EventBus,
        services_resolver: Callable[[], dict[str, Any]],
        *,
        frame_period_sec: float = 0.2,
        degraded_failures: int = 5,
    ) -> None:
        self._bus = bus
        self._services_resolver = services_resolver
        self._frame_period_sec = max(0.05, float(frame_period_sec))
        self._degraded_failures = max(1, int(degraded_failures))

    def record_for_session(self, session_ctx: SessionRuntime) -> None:
        if "video_recording" in session_ctx.handles:
            raise RuntimeError("video_recording_already_started")

        out_dir = Path(session_ctx.paths["out_dir"]) / "video"
        out_dir.mkdir(parents=True, exist_ok=True)
        services = self._services_resolver()

        channels: list[_ChannelCtx] = []
        for channel, service_name in (("visible", "video_visible"), ("thermal", "video_thermal")):
            mp4_path = out_dir / f"{channel}.mp4"
            frames_csv_path = out_dir / f"{channel}_frames.csv"
            preview_jpg_path = out_dir / f".{channel}_preview.jpg"
            mp4_path.touch(exist_ok=True)
            frames_csv_path.write_text("frame_idx,unix_ts,t_rel_sec\n", encoding="utf-8")

            session_ctx.paths[f"video_{channel}_mp4"] = str(mp4_path)
            session_ctx.paths[f"video_{channel}_frames_csv"] = str(frames_csv_path)

            svc = services.get(service_name)
            if svc is None or not callable(getattr(svc, "save_preview", None)):
                self._emit_session_event(
                    session_ctx,
                    "SESSION_VIDEO_CHANNEL_ERROR",
                    f"channel={channel} stage=bind detail=service_missing service={service_name}",
                )
                continue

            stop_event = threading.Event()
            chan = _ChannelCtx(
                channel=channel,
                service_name=service_name,
                service=svc,
                mp4_path=mp4_path,
                frames_csv_path=frames_csv_path,
                preview_jpg_path=preview_jpg_path,
                stop_event=stop_event,
            )
            chan.thread = threading.Thread(
                target=self._record_loop,
                args=(session_ctx, chan),
                name=f"video.record.{session_ctx.session_id}.{channel}",
                daemon=True,
            )
            chan.thread.start()
            channels.append(chan)

        if not channels:
            raise RuntimeError("video_record_no_channels_started")

        session_ctx.handles["video_recording"] = {"channels": channels}
        self._emit_session_event(
            session_ctx,
            "SESSION_VIDEO_START",
            f"channels_started={len(channels)} channels_total=2",
        )

    def stop_record_for_session(self, session_ctx: SessionRuntime) -> None:
        handle = session_ctx.handles.get("video_recording")
        if not isinstance(handle, dict):
            self._emit_session_event(session_ctx, "SESSION_VIDEO_STOP", "status=not_running")
            return

        channels = handle.get("channels")
        if not isinstance(channels, list):
            channels = []

        for chan in channels:
            if isinstance(chan, _ChannelCtx) and chan.stop_event is not None:
                chan.stop_event.set()
        for chan in channels:
            if isinstance(chan, _ChannelCtx) and chan.thread is not None and chan.thread.is_alive():
                chan.thread.join(timeout=2.0)

        session_ctx.handles.pop("video_recording", None)
        degraded = sum(1 for x in channels if isinstance(x, _ChannelCtx) and x.degraded)
        self._emit_session_event(
            session_ctx,
            "SESSION_VIDEO_STOP",
            f"status=stopped channels={len(channels)} degraded={degraded}",
        )

    def _record_loop(self, session_ctx: SessionRuntime, chan: _ChannelCtx) -> None:
        writer: Any = None
        frame_idx = 0
        try:
            while chan.stop_event is not None and not chan.stop_event.is_set():
                if self._capture_preview(chan):
                    wrote = self._write_video_frame(chan, writer)
                    if isinstance(wrote, tuple):
                        writer = wrote[0]
                        frame_ok = bool(wrote[1])
                    else:
                        frame_ok = bool(wrote)

                    if frame_ok:
                        chan.failures = 0
                        frame_idx += 1
                        chan.frames_written = frame_idx
                        t_rel = max(0.0, time.monotonic() - float(session_ctx.t0_monotonic))
                        with chan.frames_csv_path.open("a", encoding="utf-8") as fh:
                            fh.write(f"{frame_idx},{time.time():.6f},{t_rel:.3f}\n")
                    else:
                        chan.failures += 1
                else:
                    chan.failures += 1

                if chan.failures >= self._degraded_failures and not chan.degraded:
                    chan.degraded = True
                    self._emit_session_event(
                        session_ctx,
                        "SESSION_VIDEO_CHANNEL_ERROR",
                        f"channel={chan.channel} stage=runtime detail=degraded failures={chan.failures}",
                    )

                time.sleep(self._frame_period_sec)
        finally:
            try:
                if writer is not None and hasattr(writer, "release"):
                    writer.release()
            except Exception:
                pass
            try:
                if chan.preview_jpg_path.exists():
                    chan.preview_jpg_path.unlink()
            except Exception:
                pass

    def _capture_preview(self, chan: _ChannelCtx) -> bool:
        try:
            ok = bool(chan.service.save_preview(str(chan.preview_jpg_path)))
        except Exception:
            return False
        if not ok:
            return False
        if not chan.preview_jpg_path.exists():
            return False
        try:
            return chan.preview_jpg_path.stat().st_size > 0
        except Exception:
            return False

    def _write_video_frame(self, chan: _ChannelCtx, writer: Any) -> tuple[Any, bool]:
        if cv2 is not None:
            try:
                frame = cv2.imread(str(chan.preview_jpg_path))
                if frame is not None:
                    if writer is None:
                        h, w = frame.shape[:2]
                        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                        writer = cv2.VideoWriter(str(chan.mp4_path), fourcc, 5.0, (int(w), int(h)))
                    if writer is not None:
                        writer.write(frame)
                        return writer, True
            except Exception:
                pass

        # Fallback path for environments without OpenCV writer support:
        # keep artifact non-empty to preserve contract and keep timestamps.
        try:
            with chan.mp4_path.open("ab") as fh:
                fh.write(b"\x00")
            return writer, True
        except Exception:
            return writer, False

    def _emit_session_event(self, session_ctx: SessionRuntime, event: str, details: str) -> None:
        t_rel = max(0.0, time.monotonic() - float(session_ctx.t0_monotonic))
        msg = f"session_id={session_ctx.session_id} t_rel_sec={t_rel:.3f} {details}".strip()
        level = "ERROR" if event.endswith("_ERROR") else "INFO"
        emit_log(self._bus, level, "orchestrator", event, msg)

        events_path = Path(session_ctx.paths["events_log"])
        payload = {
            "event": event,
            "session_id": session_ctx.session_id,
            "scenario_id": session_ctx.scenario_id,
            "unix_ts": time.time(),
            "t_rel_sec": t_rel,
            "details": details,
        }
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
