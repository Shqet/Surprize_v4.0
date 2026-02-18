# client/adapters/opencv_frame_source.py
from __future__ import annotations

from typing import Any

from app.vendor.video_channel.client.video_core import IFrameSource


class OpenCvStreamWorkerFrameSource(IFrameSource):
    """
    Adapter over existing StreamWorker.
    IMPORTANT: This module is allowed to depend on StreamWorker (which imports cv2).
    Tests must not import this adapter.
    """

    def __init__(self, worker: Any) -> None:
        self._w = worker

    def get_state(self) -> str:
        return str(getattr(self._w, "state", "UNKNOWN"))

    def get_last_frame(self) -> Any | None:
        try:
            return self._w.get_last_frame()
        except Exception:
            return None

    def get_last_frame_monotonic(self) -> float | None:
        ts = getattr(self._w, "last_frame_ts", None)
        return float(ts) if ts is not None else None
