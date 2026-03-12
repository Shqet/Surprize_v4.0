# client/adapters/ffmpeg_writer.py
from __future__ import annotations

from typing import Any, Optional
import subprocess
import threading
from pathlib import Path

from app.core.subprocess_utils import windows_no_console_kwargs


class FfmpegWriter:
    """
    Minimal standalone ffmpeg writer adapter.
    Frame expected: bytes-like (raw bgr24) OR object with .tobytes().
    """

    def __init__(self, out_path: str, fps: float, width: int, height: int) -> None:
        self.out_path = out_path
        self.fps = float(fps)
        self.width = int(width)
        self.height = int(height)

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return

            Path(self.out_path).parent.mkdir(parents=True, exist_ok=True)

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{self.width}x{self.height}",
                "-r",
                f"{self.fps}",
                "-i",
                "-",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
                self.out_path,
            ]

            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    **windows_no_console_kwargs(),
                )
            except FileNotFoundError:
                raise RuntimeError("ffmpeg not found in PATH")

    def write(self, frame: Any) -> None:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise RuntimeError("ffmpeg writer is not running")
            if self._proc.stdin is None:
                raise RuntimeError("ffmpeg stdin is closed")

            if isinstance(frame, (bytes, bytearray, memoryview)):
                data = bytes(frame)
            elif hasattr(frame, "tobytes"):
                data = frame.tobytes()
            else:
                raise TypeError("frame must be bytes-like or have .tobytes()")

            self._proc.stdin.write(data)

    def stop(self, timeout_s: float = 5.0) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None

        if proc is None:
            return

        try:
            if proc.stdin:
                try:
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            proc.wait(timeout=timeout_s)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
