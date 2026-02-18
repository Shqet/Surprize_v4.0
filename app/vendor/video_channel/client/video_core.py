# client/video_core.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Callable


class IFrameSource(Protocol):
    """
    Pure interface: no cv2/numpy types here.

    Expected semantics:
      - get_state(): str like CONNECTED/CONNECTING/RECONNECTING/DISCONNECTED
      - get_last_frame(): returns an opaque frame object or None
      - get_last_frame_monotonic(): returns monotonic timestamp of last real frame, or None
    """

    def get_state(self) -> str: ...
    def get_last_frame(self) -> Any | None: ...
    def get_last_frame_monotonic(self) -> float | None: ...


class IWriter(Protocol):
    """
    Pure interface for a recording sink. Frame is opaque (implementation decides expected type).
    """

    def start(self) -> None: ...
    def write(self, frame: Any) -> None: ...
    def stop(self) -> None: ...


class ITimelineSink(Protocol):
    def write_row(self, row: dict) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class RecordParams:
    out_path: str
    record_fps: float
    timeline_path: str
    width: int
    height: int


# Optional helper signature types (for deterministic tests later)
TimeFn = Callable[[], float]
MonoFn = Callable[[], float]
