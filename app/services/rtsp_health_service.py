# app/services/rtsp_health_service.py
from __future__ import annotations

import random
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.core.event_bus import EventBus
from app.core.events import RtspChannelHealthEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import ServiceStatus
from app.services.rtsp_probe import probe_rtsp_ffprobe


@dataclass(slots=True)
class _ChannelCfg:
    name: str  # "visible" | "thermal"
    url: str


class RtspHealthService:
    """
    rtsp_health v1
    - Channel state: only CONNECTED / RECONNECTING (OFFLINE is forbidden in v1)
    - RTSP unavailability: service stays RUNNING, channel publishes RECONNECTING with backoff
    - Fatal errors (service -> ERROR, workers not started):
        - ffprobe missing / not runnable
        - invalid config (no channels, wrong types)
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._lock = threading.Lock()
        self._status: ServiceStatus = ServiceStatus.IDLE

        self._stop_event = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._cfg: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "rtsp_health"

    def status(self) -> ServiceStatus:
        with self._lock:
            return self._status

    # -------------------- lifecycle --------------------

    def start(self, profile_section: Optional[dict] = None, *args: Any, **kwargs: Any) -> None:
        section = profile_section if isinstance(profile_section, dict) else {}

        with self._lock:
            # idempotency: if already starting/running -> ignore
            if self._status in (ServiceStatus.STARTING, ServiceStatus.RUNNING):
                emit_log(self._bus, "INFO", self.name, "SERVICE_START", "ignored=already_running")
                return

            self._cfg = section
            self._stop_event.clear()

            # ---- FATAL checks BEFORE publishing STARTING ----
            channels = self._load_channels(section)
            if not channels:
                self._fatal_config("no_channels")
                return

            if not self._ffprobe_available():
                self._fatal_service("ffprobe_not_found")
                return

            # ---- Now we can announce STARTING and spawn workers ----
            self._set_status(ServiceStatus.STARTING)

            self._threads = {}
            for ch in channels:
                t = threading.Thread(
                    target=self._channel_loop,
                    name=f"rtsp_health.{ch.name}",
                    args=(ch,),
                    daemon=True,
                )
                self._threads[ch.name] = t
                t.start()

            self._set_status(ServiceStatus.RUNNING)

    def stop(self) -> None:
        with self._lock:
            if self._status in (ServiceStatus.IDLE, ServiceStatus.STOPPED):
                emit_log(self._bus, "INFO", self.name, "SERVICE_STOP", "ignored=already_stopped")
                return

            self._stop_event.set()
            threads = list(self._threads.values())

        for t in threads:
            if t.is_alive():
                t.join(timeout=2.0)

        with self._lock:
            self._threads = {}
            self._set_status(ServiceStatus.STOPPED)

    # -------------------- internals --------------------

    def _set_status(self, st: ServiceStatus) -> None:
        self._status = st
        self._bus.publish(ServiceStatusEvent(service_name=self.name, status=st.value))
        emit_log(self._bus, "INFO", self.name, "SERVICE_STATUS", f"service={self.name} status={st.value}")

    def _fatal_service(self, error_code: str) -> None:
        # v1: fatal => ServiceStatus=ERROR, no workers started
        emit_log(
            self._bus,
            "ERROR",
            self.name,
            "SERVICE_ERROR",
            f"service={self.name} error={error_code}",
        )
        self._status = ServiceStatus.ERROR
        self._bus.publish(ServiceStatusEvent(service_name=self.name, status=self._status.value))

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

    def _ffprobe_available(self) -> bool:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return False

        try:
            subprocess.run(
                [ffprobe, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
            return True
        except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
            return False

    def _load_channels(self, section: dict) -> list[_ChannelCfg]:
        channels = section.get("channels")
        if not isinstance(channels, dict):
            return []

        out: list[_ChannelCfg] = []
        for name in ("visible", "thermal"):
            item = channels.get(name)
            if not isinstance(item, dict):
                continue

            url = item.get("url")
            if not isinstance(url, str):
                continue

            url = url.strip()
            if not url:
                continue

            out.append(_ChannelCfg(name=name, url=url))

        return out

    def _get_probe_timeout(self) -> float:
        v = self._cfg.get("probe_timeout_sec", 3)
        try:
            return float(v)
        except Exception:
            return 3.0

    def _get_period_ok(self) -> float:
        v = self._cfg.get("period_ok_sec", 2)
        try:
            return float(v)
        except Exception:
            return 2.0

    def _get_backoff(self) -> tuple[int, int, int]:
        b = self._cfg.get("backoff", {})
        if not isinstance(b, dict):
            b = {}
        base_ms = int(b.get("base_ms", 300))
        max_ms = int(b.get("max_ms", 5000))
        jitter_ms = int(b.get("jitter_ms", 200))
        base_ms = max(0, base_ms)
        max_ms = max(base_ms, max_ms)
        jitter_ms = max(0, jitter_ms)
        return base_ms, max_ms, jitter_ms

    def _publish_health(self, ch: _ChannelCfg, state: str, attempt: int, last_error: Optional[str]) -> None:
        # v1: state must be only CONNECTED/RECONNECTING
        self._bus.publish(
            RtspChannelHealthEvent(
                service_name=self.name,
                channel=ch.name,
                url=ch.url,
                state=state,
                attempt=attempt,
                last_error=last_error,
            )
        )

    def _channel_loop(self, channel_name: str, ch_cfg: dict, run_dir: str) -> None:
        import os
        import time
        import subprocess
        import random

        url = ch_cfg.get("url")
        snapshot_fps = float(ch_cfg.get("snapshot_fps", 2.0))
        max_frame_age_sec = float(ch_cfg.get("max_frame_age_sec", 5.0))
        rtsp_transport = ch_cfg.get("rtsp_transport", "tcp")

        channel_dir = os.path.join(run_dir, channel_name)
        os.makedirs(channel_dir, exist_ok=True)

        latest_path = os.path.join(channel_dir, "latest.jpg")

        has_ever_written_snapshot = False
        last_snapshot_mtime = None
        restart_attempt = 0

        while not self._stop_event.is_set():

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-rtsp_transport", rtsp_transport,
                "-i", url,
                "-vf", f"fps={snapshot_fps}",
                "-q:v", "2",
                "-update", "1",
                latest_path,
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                self._log_error(
                    f"INGEST_START_FAIL channel={channel_name} error={e}"
                )
                time.sleep(2)
                continue

            self._log_info(
                f"INGEST_START channel={channel_name} url={url} pid={proc.pid}"
            )

            while not self._stop_event.is_set():
                time.sleep(1)

                # process exited?
                if proc.poll() is not None:
                    self._log_warning(
                        f"INGEST_EXIT channel={channel_name} rc={proc.returncode}"
                    )
                    break

                now = time.time()

                if os.path.exists(latest_path):
                    mtime = os.path.getmtime(latest_path)

                    if not has_ever_written_snapshot:
                        has_ever_written_snapshot = True
                        last_snapshot_mtime = mtime
                    else:
                        if mtime != last_snapshot_mtime:
                            last_snapshot_mtime = mtime

                    age_sec = now - mtime

                    if age_sec > max_frame_age_sec:
                        self._log_warning(
                            f"INGEST_STALLED channel={channel_name} "
                            f"last_mtime={mtime:.3f} age_sec={age_sec:.2f}"
                        )
                        proc.kill()
                        break

                else:
                    if not has_ever_written_snapshot:
                        self._log_warning(
                            f"INGEST_NO_FRAMES channel={channel_name}"
                        )
                    else:
                        self._log_warning(
                            f"INGEST_STALLED channel={channel_name} file_missing=1"
                        )
                        proc.kill()
                        break

            # cleanup
            try:
                proc.kill()
            except Exception:
                pass

            if self._stop_event.is_set():
                break

            restart_attempt += 1
            delay = min(10, 1 + restart_attempt * 2) + random.uniform(0, 0.5)

            self._log_info(
                f"INGEST_RESTART channel={channel_name} "
                f"attempt={restart_attempt} delay_sec={delay:.2f}"
            )

            time.sleep(delay)
