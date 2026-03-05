from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import cv2


def _mono() -> float:
    return time.monotonic()


@dataclass
class WorkerOptions:
    heartbeat_seconds: int = 2
    fps_window_seconds: float = 3.0
    no_data_timeout_seconds: float = 7.0

    open_timeout_ms: int = 3000
    read_timeout_ms: int = 3000

    force_tcp: bool = True
    ffmpeg_timeout_us: int = 3_000_000

    jitter_enabled: bool = True
    jitter_frac: float = 0.15
    jitter_seed: Optional[int] = None


class StreamWorker:
    """
    Windows RTSP client based on OpenCV (FFmpeg backend).

    ВАЖНО: VideoCapture создаём/закрываем ТОЛЬКО в worker-потоке.
    update_url() не делает release(), а только ставит флаг "нужен реконнект".
    """

    def __init__(
        self,
        stream: str,
        url: str,
        log: Callable[[str], None],
        opts: Optional[WorkerOptions] = None,
        # legacy kwargs for compatibility with existing main.py
        heartbeat_seconds: Optional[int] = None,
        fps_window_seconds: Optional[float] = None,
        no_data_timeout_seconds: Optional[float] = None,
        jitter: Optional[bool] = None,
    ) -> None:
        self.stream = stream
        self.url = url
        self.log = log

        base_opts = opts or WorkerOptions()
        if heartbeat_seconds is not None:
            base_opts.heartbeat_seconds = int(heartbeat_seconds)
        if fps_window_seconds is not None:
            base_opts.fps_window_seconds = float(fps_window_seconds)
        if no_data_timeout_seconds is not None:
            base_opts.no_data_timeout_seconds = float(no_data_timeout_seconds)
        if jitter is not None:
            base_opts.jitter_enabled = bool(jitter)

        self.opts = base_opts

        self.state = "DISCONNECTED"  # DISCONNECTED/CONNECTING/CONNECTED/RECONNECTING
        self.attempt: int = 0

        self._cap: Optional[cv2.VideoCapture] = None

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # metrics
        self.last_frame_ts: Optional[float] = None
        self._window_start: Optional[float] = None
        self._frames_in_window: int = 0
        self.fps: float = 0.0

        seed = self.opts.jitter_seed if self.opts.jitter_seed is not None else time.time_ns()
        self._rng = random.Random(seed)

        self._no_data_triggered = False

        # --- thread-safe control plane ---
        self._control_lock = threading.Lock()
        self._pending_url: Optional[str] = None
        self._pending_reason: Optional[str] = None
        self._reconnect_requested: bool = False

        # --- optional video snapshot for UI ---
        self._frame_lock = threading.Lock()
        self._last_frame = None  # numpy ndarray when available
        self._frame_logged = False

    # ------------------------
    # Public API
    # ------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self.log(f"START_IGNORED stream={self.stream}")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f"StreamWorker-{self.stream}", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "USER_STOP") -> None:
        if not self._thread:
            self.log(f"STOP_IGNORED stream={self.stream}")
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        # release happens in worker thread loop end, but do best-effort here too
        self._release()
        self.state = "DISCONNECTED"
        self.log(f"STREAM_STOPPED stream={self.stream} reason={reason}")

    def update_url(self, new_url: str, reason: str = "CONFIG_UPDATED") -> None:
        new_url = (new_url or "").strip()
        if not new_url:
            return
        if new_url == self.url:
            self.log(f"STREAM_URL_UPDATE_IGNORED stream={self.stream}")
            return

        old = self.url
        self.url = new_url  # keep for visibility in logs/heartbeats

        self.log(
            f"STREAM_URL_UPDATE stream={self.stream} old_url={old} new_url={new_url} reason={reason}"
        )

        # IMPORTANT: do not touch VideoCapture here (different thread).
        with self._control_lock:
            self._pending_url = new_url
            self._pending_reason = reason
            self._reconnect_requested = True

    def get_last_frame(self):
        with self._frame_lock:
            if self._last_frame is None:
                return None
            return self._last_frame.copy()

    # ------------------------
    # Core loop
    # ------------------------

    def _run(self) -> None:
        next_heartbeat = time.time() + self.opts.heartbeat_seconds

        while not self._stop_event.is_set():
            # apply pending reconnect requests in worker thread
            self._apply_pending_reconnect_if_any()

            if self.state != "CONNECTED":
                self._connect_once()

            if self.state == "CONNECTED":
                try:
                    ret, frame = self._cap.read() if self._cap else (False, None)
                except cv2.error as e:
                    # critical: never die — go reconnect
                    self.log(f"STREAM_ERROR stream={self.stream} err=CV2_ERROR:{e}")
                    self._enter_reconnecting(reason="ERROR")
                    continue
                except Exception as e:
                    self.log(f"STREAM_ERROR stream={self.stream} err=EXCEPTION:{type(e).__name__}:{e}")
                    self._enter_reconnecting(reason="ERROR")
                    continue

                if not ret or frame is None:
                    self.log(f"STREAM_ERROR stream={self.stream} err=READ_FAILED")
                    self._enter_reconnecting(reason="ERROR")
                else:
                    self._on_frame(frame)
                    self._watchdog_tick()

            if time.time() >= next_heartbeat:
                self._heartbeat()
                next_heartbeat = time.time() + self.opts.heartbeat_seconds

            if self.state != "CONNECTED":
                time.sleep(0.05)

        self._release()

    def _apply_pending_reconnect_if_any(self) -> None:
        with self._control_lock:
            if not self._reconnect_requested:
                return
            new_url = self._pending_url
            reason = self._pending_reason or "CONFIG_UPDATED"
            self._pending_url = None
            self._pending_reason = None
            self._reconnect_requested = False

        if new_url:
            # release + transition are safe here (worker thread)
            self.log(f"STREAM_RECONNECTING stream={self.stream} attempt=0 delay=0.0 reason={reason}")
            self._release()
            self.state = "DISCONNECTED"
            self.attempt = 0
            self.last_frame_ts = None
            self._window_start = None
            self._frames_in_window = 0
            self.fps = 0.0
            self._no_data_triggered = False

    # ------------------------
    # URL tweaks
    # ------------------------

    def _normalize_rtsp_url(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            if parts.scheme.lower() not in ("rtsp", "rtsps"):
                return url

            q = dict(parse_qsl(parts.query, keep_blank_values=True))

            if self.opts.force_tcp and "rtsp_transport" not in q:
                q["rtsp_transport"] = "tcp"

            if "stimeout" not in q and "timeout" not in q:
                q["stimeout"] = str(int(self.opts.ffmpeg_timeout_us))

            new_query = urlencode(q, doseq=True)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
        except Exception:
            return url

    # ------------------------
    # Connect / reconnect
    # ------------------------

    def _connect_once(self) -> None:
        self.state = "CONNECTING"
        url_eff = self._normalize_rtsp_url(self.url)

        self.log(f"STREAM_CONNECT stream={self.stream} url={self.url}")

        t0 = time.time()
        cap = cv2.VideoCapture(url_eff, cv2.CAP_FFMPEG)
        open_ms = int((time.time() - t0) * 1000)

        try:
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, float(self.opts.open_timeout_ms))
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, float(self.opts.read_timeout_ms))
        except Exception:
            pass

        if not cap.isOpened():
            try:
                cap.release()
            except Exception:
                pass
            self.log(f"STREAM_ERROR stream={self.stream} err=OPEN_FAILED open_ms={open_ms}")
            self._enter_reconnecting(reason="ERROR")
            return

        self._cap = cap
        self.state = "CONNECTED"
        self.attempt = 0
        self._no_data_triggered = False
        self.log(f"STREAM_CONNECTED stream={self.stream} open_ms={open_ms}")

    def _enter_reconnecting(self, reason: str) -> None:
        if self.state == "RECONNECTING":
            return

        self.state = "RECONNECTING"
        self._release()

        self.attempt += 1
        delay = self._compute_delay(self.attempt)
        self.log(
            f"STREAM_RECONNECTING stream={self.stream} attempt={self.attempt} delay={delay:.1f} reason={reason}"
        )

        slept = 0.0
        while slept < delay and not self._stop_event.is_set():
            time.sleep(0.1)
            slept += 0.1

        if not self._stop_event.is_set():
            self.state = "DISCONNECTED"

    def _compute_delay(self, attempt: int) -> float:
        seq = [1.0, 2.0, 5.0, 10.0, 20.0, 30.0]
        base = seq[min(max(attempt, 1) - 1, len(seq) - 1)]
        if not self.opts.jitter_enabled:
            return base
        j = self._rng.uniform(-self.opts.jitter_frac, self.opts.jitter_frac)
        d = base * (1.0 + j)
        return max(0.2, min(d, 30.0))

    def _release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    # ------------------------
    # Metrics / watchdog
    # ------------------------

    def _on_frame(self, frame) -> None:
        now = _mono()
        self.last_frame_ts = now

        # store frame for UI (thread-safe)
        with self._frame_lock:
            try:
                self._last_frame = frame.copy()
            except Exception:
                self._last_frame = None

        if not self._frame_logged:
            self._frame_logged = True
            try:
                h, w = frame.shape[:2]
                self.log(f"VIDEO_CHILD_FRAME_RECEIVED w={int(w)} h={int(h)}")
            except Exception:
                self.log("VIDEO_CHILD_FRAME_RECEIVED w=? h=?")

        if self._window_start is None:
            self._window_start = now
            self._frames_in_window = 0

        self._frames_in_window += 1
        elapsed = now - self._window_start
        if elapsed >= self.opts.fps_window_seconds:
            self.fps = self._frames_in_window / max(elapsed, 1e-6)
            self._window_start = now
            self._frames_in_window = 0

        if self._no_data_triggered:
            self._no_data_triggered = False
            self.log(f"STREAM_DATA_RESUMED stream={self.stream}")

    def _watchdog_tick(self) -> None:
        if self.state != "CONNECTED":
            return
        if self.last_frame_ts is None:
            return

        age_s = _mono() - self.last_frame_ts
        if age_s > self.opts.no_data_timeout_seconds and not self._no_data_triggered:
            self._no_data_triggered = True
            self.log(f"NO_DATA_TIMEOUT stream={self.stream}")
            self._enter_reconnecting(reason="NO_DATA_TIMEOUT")

    def _heartbeat(self) -> None:
        if self.last_frame_ts is None:
            age_ms = -1
        else:
            age_ms = int((_mono() - self.last_frame_ts) * 1000)

        self.log(
            f"STREAM_HEALTH stream={self.stream} state={self.state} "
            f"fps={self.fps:.1f} last_frame_age_ms={age_ms}"
        )
