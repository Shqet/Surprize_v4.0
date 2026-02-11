# app/services/rtsp_ingest_service.py
from __future__ import annotations

import random
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.core.event_bus import EventBus
from app.core.events import RtspIngestStatsEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import ServiceStatus


@dataclass(frozen=True, slots=True)
class _BackoffCfg:
    base_ms: int
    max_ms: int
    jitter_ms: int


@dataclass(frozen=True, slots=True)
class _ChannelCfg:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class _IngestCfg:
    channels: list[_ChannelCfg]
    restart_backoff: _BackoffCfg
    out_root: str
    snapshot_fps: float
    ffmpeg_path: str
    max_frame_age_sec: float


@dataclass(frozen=True, slots=True)
class _ChannelPaths:
    channel_dir: Path
    latest_jpg: Path
    latest_tmp_jpg: Path


@dataclass(slots=True)
class _ChannelRuntime:
    state: str  # "INGESTING" | "RESTARTING" | "STALLED"
    restarts: int
    fps_est: float
    last_frame_age_sec: float

    _last_mtime: float
    _last_mtime_seen_ts: float
    _last_stalled_log_ts: float
    _last_stats_emit_ts: float


class RtspIngestService:
    """
    rtsp_ingest v1 (up to Step 8)

    - Config validation (fail-fast).
    - ffmpeg availability check (fail-fast).
    - Output layout:
        <out_root>/rtsp_ingest/<run_id>/<channel>/latest.jpg

    - Worker per channel starts ffmpeg and monitors latest.jpg updates.
    - Restart/backoff on failure (service stays RUNNING).
    - State machine:
        RESTARTING: during restart/backoff / when ffmpeg not running
        INGESTING: latest.jpg is updating (age <= max_frame_age_sec)
        STALLED: latest.jpg too old (age > max_frame_age_sec) while ffmpeg is running

    - Step 8: periodic RtspIngestStatsEvent publishing per channel (every 1s by default).
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._lock = threading.Lock()
        self._status: ServiceStatus = ServiceStatus.IDLE

        self._raw_cfg: dict[str, Any] = {}
        self._cfg: Optional[_IngestCfg] = None

        self._run_id: Optional[str] = None
        self._run_dir: Optional[Path] = None
        self._paths: dict[str, _ChannelPaths] = {}

        self._stop_event = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._procs: dict[str, subprocess.Popen] = {}

        # runtime stats
        self._rt: dict[str, _ChannelRuntime] = {}

        # publish period (v1): 1–2 sec; keep 1 sec for “snappy” UI
        self._stats_period_sec = 1.0

    @property
    def name(self) -> str:
        return "rtsp_ingest"

    def status(self) -> ServiceStatus:
        with self._lock:
            return self._status

    # -------------------- lifecycle --------------------

    def start(self, profile_section: Optional[dict] = None, *args: Any, **kwargs: Any) -> None:
        section = profile_section if isinstance(profile_section, dict) else {}

        with self._lock:
            if self._status in (ServiceStatus.STARTING, ServiceStatus.RUNNING):
                emit_log(self._bus, "INFO", self.name, "SERVICE_START", "ignored=already_running")
                return

            self._raw_cfg = section
            self._stop_event.clear()

            # ---- Step 3: config fail-fast ----
            parsed = self._parse_config(section)
            if parsed is None:
                self._fatal_config("invalid_or_empty_channels")
                return
            self._cfg = parsed

            # ---- Step 4: ffmpeg fail-fast ----
            if not self._check_ffmpeg_available(parsed.ffmpeg_path):
                emit_log(
                    self._bus,
                    "ERROR",
                    self.name,
                    "SERVICE_ERROR",
                    f"service={self.name} error=ffmpeg_not_found",
                )
                self._status = ServiceStatus.ERROR
                self._bus.publish(ServiceStatusEvent(service_name=self.name, status=self._status.value))
                return

            # ---- Step 5: run_id + run_dir + channel dirs/paths ----
            run_id = self._make_run_id()
            out_root = Path(parsed.out_root)
            run_dir = out_root / "rtsp_ingest" / run_id

            paths: dict[str, _ChannelPaths] = {}
            try:
                for ch in parsed.channels:
                    ch_dir = run_dir / ch.name
                    ch_dir.mkdir(parents=True, exist_ok=True)
                    paths[ch.name] = _ChannelPaths(
                        channel_dir=ch_dir,
                        latest_jpg=ch_dir / "latest.jpg",
                        latest_tmp_jpg=ch_dir / "latest.tmp.jpg",
                    )
            except Exception as e:
                emit_log(
                    self._bus,
                    "ERROR",
                    self.name,
                    "SERVICE_ERROR",
                    f"service={self.name} error=out_dir_create_failed detail={type(e).__name__}",
                )
                self._status = ServiceStatus.ERROR
                self._bus.publish(ServiceStatusEvent(service_name=self.name, status=self._status.value))
                return

            self._run_id = run_id
            self._run_dir = run_dir
            self._paths = paths

            emit_log(self._bus, "INFO", self.name, "SERVICE_START", f"service={self.name}")
            self._set_status(ServiceStatus.STARTING)

            # ---- Step 6-8: spawn worker thread per channel ----
            self._threads = {}
            self._procs = {}
            self._rt = {}

            now = time.time()
            for ch in parsed.channels:
                self._rt[ch.name] = _ChannelRuntime(
                    state="RESTARTING",
                    restarts=0,
                    fps_est=0.0,
                    last_frame_age_sec=1e9,
                    _last_mtime=0.0,
                    _last_mtime_seen_ts=now,
                    _last_stalled_log_ts=0.0,
                    _last_stats_emit_ts=0.0,
                )

                t = threading.Thread(
                    target=self._channel_worker,
                    name=f"rtsp_ingest.{ch.name}",
                    args=(ch,),
                    daemon=True,
                )
                self._threads[ch.name] = t
                t.start()

            self._set_status(ServiceStatus.RUNNING)
            emit_log(
                self._bus,
                "INFO",
                self.name,
                "SERVICE_RUNNING",
                f"service={self.name} out_dir={str(run_dir)} run_id={run_id}",
            )

    def stop(self) -> None:
        with self._lock:
            if self._status in (ServiceStatus.IDLE, ServiceStatus.STOPPED):
                emit_log(self._bus, "INFO", self.name, "SERVICE_STOP", "ignored=already_stopped")
                return

            emit_log(self._bus, "INFO", self.name, "SERVICE_STOP", f"service={self.name}")
            self._stop_event.set()

            try:
                self._set_status(ServiceStatus.STOPPING)  # type: ignore[attr-defined]
            except Exception:
                pass

            procs = list(self._procs.items())
            threads = list(self._threads.values())

        for ch_name, p in procs:
            self._terminate_process(ch_name, p, timeout_sec=2.0)

        for t in threads:
            if t.is_alive():
                t.join(timeout=3.0)

        with self._lock:
            self._procs = {}
            self._threads = {}

            self._set_status(ServiceStatus.STOPPED)
            emit_log(self._bus, "INFO", self.name, "SERVICE_STOPPED", f"service={self.name}")

            self._run_id = None
            self._run_dir = None
            self._paths = {}
            self._rt = {}

    # -------------------- worker (restart loop + state machine + stats publish) --------------------

    def _channel_worker(self, ch: _ChannelCfg) -> None:
        cfg = self._cfg
        if cfg is None:
            return

        paths = self._paths.get(ch.name)
        rt = self._rt.get(ch.name)
        if paths is None or rt is None:
            return

        backoff = cfg.restart_backoff
        max_age = float(cfg.max_frame_age_sec)

        attempt = 0
        poll_sec = 0.25
        stalled_log_period_sec = 3.0

        # initial stats publish
        self._maybe_publish_stats(ch.name, rt, force=True)

        while not self._stop_event.is_set():
            # -------- start/restart ffmpeg --------
            rt.state = "RESTARTING"
            self._maybe_publish_stats(ch.name, rt)

            cmd = self._build_ffmpeg_cmd(
                ffmpeg_path=cfg.ffmpeg_path,
                url=ch.url,
                snapshot_fps=cfg.snapshot_fps,
                out_jpg_path=str(paths.latest_jpg),
            )

            p: Optional[subprocess.Popen] = None
            try:
                p = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=self._popen_creationflags(),
                )
                with self._lock:
                    self._procs[ch.name] = p

                emit_log(
                    self._bus,
                    "INFO",
                    self.name,
                    "INGEST_START",
                    f"channel={ch.name} url={ch.url} pid={p.pid}",
                )

                # -------- monitor loop --------
                while not self._stop_event.is_set():
                    rc = p.poll()
                    if rc is not None:
                        emit_log(self._bus, "WARNING", self.name, "INGEST_EXIT", f"channel={ch.name} rc={rc}")
                        break

                    now = time.time()
                    mtime = self._safe_mtime(paths.latest_jpg)

                    if mtime > 0:
                        if mtime != rt._last_mtime:
                            dt = max(1e-6, now - rt._last_mtime_seen_ts)
                            rt._last_mtime = mtime
                            rt._last_mtime_seen_ts = now
                            rt.fps_est = 1.0 / dt
                        rt.last_frame_age_sec = max(0.0, now - mtime)
                    else:
                        rt.last_frame_age_sec = 1e9

                    if rt.last_frame_age_sec <= max_age:
                        rt.state = "INGESTING"
                    else:
                        rt.state = "STALLED"
                        if (now - rt._last_stalled_log_ts) >= stalled_log_period_sec:
                            rt._last_stalled_log_ts = now
                            emit_log(
                                self._bus,
                                "WARNING",
                                self.name,
                                "INGEST_STALLED",
                                f"channel={ch.name} age_sec={rt.last_frame_age_sec:.2f}",
                            )

                    self._maybe_publish_stats(ch.name, rt)
                    time.sleep(poll_sec)

            except Exception as e:
                emit_log(
                    self._bus,
                    "ERROR",
                    self.name,
                    "INGEST_EXIT",
                    f"channel={ch.name} rc=-1 error={type(e).__name__}",
                )
            finally:
                if p is not None:
                    self._terminate_process(ch.name, p, timeout_sec=1.5)
                    with self._lock:
                        if self._procs.get(ch.name) is p:
                            self._procs.pop(ch.name, None)

            if self._stop_event.is_set():
                break

            # -------- restart/backoff --------
            attempt += 1
            rt.restarts = attempt
            rt.state = "RESTARTING"
            self._maybe_publish_stats(ch.name, rt)

            delay_ms = self._compute_backoff_ms(backoff, attempt)
            emit_log(
                self._bus,
                "INFO",
                self.name,
                "INGEST_RESTART",
                f"channel={ch.name} attempt={attempt} delay_ms={delay_ms}",
            )

            self._sleep_interruptible(delay_ms / 1000.0, ch.name)

        # worker exiting
        rt.state = "RESTARTING"
        self._maybe_publish_stats(ch.name, rt, force=True)

    # -------------------- stats publish --------------------

    def _maybe_publish_stats(self, channel: str, rt: _ChannelRuntime, force: bool = False) -> None:
        now = time.time()
        if (not force) and (now - rt._last_stats_emit_ts) < self._stats_period_sec:
            return

        rt._last_stats_emit_ts = now
        self._bus.publish(
            RtspIngestStatsEvent(
                service=self.name,
                channel=channel,
                state=rt.state,
                fps_est=float(rt.fps_est),
                last_frame_age_sec=float(rt.last_frame_age_sec),
                restarts=int(rt.restarts),
                ts=float(now),
            )
        )

    # -------------------- internals --------------------

    def _set_status(self, st: ServiceStatus) -> None:
        self._status = st
        self._bus.publish(ServiceStatusEvent(service_name=self.name, status=st.value))
        emit_log(self._bus, "INFO", self.name, "SERVICE_STATUS", f"service={self.name} status={st.value}")

    def _fatal_config(self, reason: str) -> None:
        emit_log(
            self._bus,
            "ERROR",
            self.name,
            "SERVICE_CONFIG_INVALID",
            f"service={self.name} reason={reason}",
        )
        self._status = ServiceStatus.ERROR
        self._bus.publish(ServiceStatusEvent(service_name=self.name, status=self._status.value))

    def _parse_config(self, section: dict) -> Optional[_IngestCfg]:
        ch = section.get("channels")
        if not isinstance(ch, dict) or not ch:
            return None

        channels: list[_ChannelCfg] = []
        for name, item in ch.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            channels.append(_ChannelCfg(name=name.strip(), url=url.strip()))

        if not channels:
            return None

        b = section.get("restart_backoff", {})
        if not isinstance(b, dict):
            return None
        try:
            base_ms = int(b.get("base_ms", 300))
            max_ms = int(b.get("max_ms", 5000))
            jitter_ms = int(b.get("jitter_ms", 200))
        except Exception:
            return None

        base_ms = max(0, base_ms)
        max_ms = max(base_ms, max_ms)
        jitter_ms = max(0, jitter_ms)

        out_root = section.get("out_root", "outputs")
        if not isinstance(out_root, str) or not out_root.strip():
            return None

        try:
            snapshot_fps = float(section.get("snapshot_fps", 2.0))
        except Exception:
            return None
        if snapshot_fps <= 0:
            return None

        ffmpeg_path = section.get("ffmpeg_path", "ffmpeg")
        if not isinstance(ffmpeg_path, str) or not ffmpeg_path.strip():
            return None

        try:
            max_frame_age_sec = float(section.get("max_frame_age_sec", 4.0))
        except Exception:
            return None
        if max_frame_age_sec <= 0:
            return None

        return _IngestCfg(
            channels=channels,
            restart_backoff=_BackoffCfg(base_ms=base_ms, max_ms=max_ms, jitter_ms=jitter_ms),
            out_root=out_root.strip(),
            snapshot_fps=snapshot_fps,
            ffmpeg_path=ffmpeg_path.strip(),
            max_frame_age_sec=max_frame_age_sec,
        )

    def _check_ffmpeg_available(self, ffmpeg_path: str) -> bool:
        resolved = shutil.which(ffmpeg_path) if ffmpeg_path == "ffmpeg" else ffmpeg_path
        if not resolved:
            return False
        try:
            subprocess.run(
                [resolved, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            return True
        except Exception:
            return False

    def _make_run_id(self) -> str:
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        suffix = uuid.uuid4().hex[:8]
        return f"{ts}_{suffix}"

    def _build_ffmpeg_cmd(self, ffmpeg_path: str, url: str, snapshot_fps: float, out_jpg_path: str) -> list[str]:
        resolved = shutil.which(ffmpeg_path) if ffmpeg_path == "ffmpeg" else ffmpeg_path
        exe = resolved or ffmpeg_path

        return [
            exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-vf",
            f"fps={snapshot_fps}",
            "-q:v",
            "2",
            "-update",
            "1",
            out_jpg_path,
        ]

    def _terminate_process(self, channel: str, p: subprocess.Popen, timeout_sec: float) -> None:
        try:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass

                try:
                    p.wait(timeout=timeout_sec)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass

            for stream in (p.stdin, p.stdout, p.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
        except Exception:
            pass

    def _popen_creationflags(self) -> int:
        try:
            return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        except Exception:
            return 0

    def _compute_backoff_ms(self, b: _BackoffCfg, attempt: int) -> int:
        exp = 2 ** min(max(0, attempt - 1), 10)
        delay = min(b.max_ms, b.base_ms * exp)
        if b.jitter_ms > 0:
            delay += random.randint(0, b.jitter_ms)
        return int(max(0, delay))

    def _sleep_interruptible(self, seconds: float, channel: str) -> None:
        # sleep in small chunks so stop() reacts quickly AND stats keep flowing
        end = time.time() + max(0.0, seconds)
        while not self._stop_event.is_set():
            now = time.time()
            if now >= end:
                return

            rt = self._rt.get(channel)
            if rt is not None:
                self._maybe_publish_stats(channel, rt)

            time.sleep(min(0.2, end - now))

    def _safe_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except Exception:
            return 0.0
