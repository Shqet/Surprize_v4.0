# app/services/video_channel.py
from __future__ import annotations

from dataclasses import dataclass
import inspect
import os
import threading
import time
from typing import Any, Callable, Optional

from app.core.event_bus import EventBus
from app.core.events import ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import ServiceStatus
from app.vendor.video_channel.client.process_worker import ProcessStreamWorker


@dataclass(frozen=True)
class VideoChannelConfig:
    channel: str
    url: str
    width: int
    height: int
    connect_timeout_sec: float
    read_watchdog_sec: float
    reconnect_backoff: list[float]

    @staticmethod
    def from_profile(section: dict) -> "VideoChannelConfig":
        if not isinstance(section, dict):
            raise ValueError("profile section must be dict")

        def req_str(key: str) -> str:
            v = section.get(key)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"missing/invalid '{key}'")
            return v.strip()

        def req_int(key: str) -> int:
            v = section.get(key)
            if not isinstance(v, int) or v <= 0:
                raise ValueError(f"missing/invalid '{key}' (must be positive int)")
            return v

        def req_num(key: str) -> float:
            v = section.get(key)
            if not isinstance(v, (int, float)) or float(v) <= 0:
                raise ValueError(f"missing/invalid '{key}' (must be >0)")
            return float(v)

        channel = req_str("channel")
        url = req_str("url")
        width = req_int("width")
        height = req_int("height")
        connect_timeout_sec = req_num("connect_timeout_sec")
        read_watchdog_sec = req_num("read_watchdog_sec")

        rb = section.get("reconnect_backoff")
        if not isinstance(rb, list) or not rb:
            raise ValueError("missing/invalid 'reconnect_backoff' (must be non-empty list)")
        reconnect_backoff: list[float] = []
        for x in rb:
            if not isinstance(x, (int, float)) or float(x) <= 0:
                raise ValueError("invalid 'reconnect_backoff' element (must be >0 number)")
            reconnect_backoff.append(float(x))

        return VideoChannelConfig(
            channel=channel,
            url=url,
            width=width,
            height=height,
            connect_timeout_sec=connect_timeout_sec,
            read_watchdog_sec=read_watchdog_sec,
            reconnect_backoff=reconnect_backoff,
        )


class VideoChannelDaemonService:
    """
    v0 integration:
      - daemon service wrapper around ProcessStreamWorker
      - publishes ServiceStatusEvent
      - logs via emit_log
      - no apply_config, no recording, no preview in this step
    """

    def __init__(
        self,
        bus: EventBus,
        name: str,
        worker_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._bus = bus
        self._name = name
        self._status: ServiceStatus = ServiceStatus.IDLE

        self._cfg: Optional[VideoChannelConfig] = None
        self._worker: Optional[Any] = None
        self._preview_thread: Optional[threading.Thread] = None
        self._preview_stop = threading.Event()
        self._preview_path: Optional[str] = None
        self._preview_period_sec: float = 0.0
        self._preview_log_every_sec: float = 5.0
        self._preview_last_log: float = 0.0

        self._worker_factory = worker_factory or (lambda **kw: ProcessStreamWorker(**kw))

    @property
    def name(self) -> str:
        return self._name

    def status(self) -> ServiceStatus:
        return self._status

    def _publish_status(self, st: ServiceStatus) -> None:
        self._status = st
        self._bus.publish(ServiceStatusEvent(service_name=self._name, status=st.value))

    def _log(self, line: str) -> None:
        # vendor worker пишет строки k=v — прокидываем как LogEvent через общий логгер
        emit_log(self._bus, "INFO", self._name, "VIDEO", line)

    def _log_preview_enabled(self, enabled: bool) -> None:
        emit_log(
            self._bus,
            "INFO",
            self._name,
            "VIDEO_PREVIEW_ENABLED",
            f"value={1 if enabled else 0}",
        )

    def _parse_preview_cfg(self, section: dict) -> tuple[bool, Optional[str], float]:
        preview = section.get("preview") if isinstance(section, dict) else None
        if not isinstance(preview, dict):
            self._log_preview_enabled(False)
            return False, None, 0.0

        enabled = bool(preview.get("enabled", False))
        self._log_preview_enabled(enabled)
        if not enabled:
            return False, None, 0.0

        out_path = preview.get("out_path")
        period_ms = preview.get("period_ms")

        if not isinstance(out_path, str) or not out_path.strip():
            emit_log(self._bus, "ERROR", self._name, "VIDEO_PREVIEW_CONFIG_INVALID", "missing=out_path")
            return False, None, 0.0
        if not isinstance(period_ms, int) or period_ms <= 0:
            emit_log(self._bus, "ERROR", self._name, "VIDEO_PREVIEW_CONFIG_INVALID", "missing=period_ms")
            return False, None, 0.0

        abs_path = os.path.abspath(out_path)
        emit_log(self._bus, "INFO", self._name, "VIDEO_PREVIEW_PATH", f"abs={abs_path}")
        return True, abs_path, float(period_ms) / 1000.0

    def _start_preview_loop(self) -> None:
        if not self._preview_path or self._preview_period_sec <= 0:
            return
        if self._preview_thread and self._preview_thread.is_alive():
            return

        self._preview_stop.clear()
        emit_log(
            self._bus,
            "INFO",
            self._name,
            "VIDEO_PREVIEW_INIT",
            f"path={self._preview_path} period_ms={int(self._preview_period_sec * 1000)} file={__file__}",
        )

        t = threading.Thread(target=self._preview_loop, name=f"preview.{self._name}", daemon=True)
        self._preview_thread = t
        t.start()

    def _preview_loop(self) -> None:
        next_tick = time.monotonic()
        while not self._preview_stop.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.2, next_tick - now))
                continue

            path = self._preview_path
            if path:
                now_mono = time.monotonic()
                if now_mono - self._preview_last_log >= self._preview_log_every_sec:
                    emit_log(self._bus, "INFO", self._name, "VIDEO_PREVIEW_TICK", f"path={path}")
                    self._preview_last_log = now_mono
                w = self._worker
                if w is not None and hasattr(w, "send_cmd"):
                    if not self._is_worker_stream_connected(w):
                        continue
                    try:
                        w.send_cmd({"cmd": "SAVE_PREVIEW", "path": path})
                        if now_mono - self._preview_last_log <= 0.001:
                            emit_log(self._bus, "INFO", self._name, "VIDEO_PREVIEW_CMD_SENT", f"path={path}")
                    except Exception as ex:
                        emit_log(
                            self._bus,
                            "ERROR",
                            self._name,
                            "VIDEO_PREVIEW_CMD_SEND_FAIL",
                            f"err={type(ex).__name__}",
                        )

            next_tick = time.monotonic() + max(0.2, self._preview_period_sec)

    def _is_worker_stream_connected(self, worker: Any) -> bool:
        """
        Treat stream as connected only if child heartbeat confirms CONNECTED
        and frame age is fresh enough.
        """
        try:
            get_health = getattr(worker, "get_health", None)
            if callable(get_health):
                h = get_health()
                state = str(getattr(h, "state", ""))
                age_ms = int(getattr(h, "last_frame_age_ms", -1))
                return state == "CONNECTED" and age_ms >= 0 and age_ms <= 3000
        except Exception:
            return False
        return False

    def start(self, profile_section: dict | None = None) -> None:
        # idempotent
        if self._status in (ServiceStatus.STARTING, ServiceStatus.RUNNING):
            emit_log(self._bus, "INFO", self._name, "VIDEO_DAEMON_START_IGNORED", "reason=ALREADY_RUNNING")
            return

        section = profile_section or {}
        try:
            cfg = VideoChannelConfig.from_profile(section)
        except Exception as ex:
            # Contract: do not raise. Publish ERROR + LogEvent with message containing code and error type.
            emit_log(
                self._bus,
                "ERROR",
                self._name,
                "SERVICE_ERROR",
                f"SERVICE_ERROR service={self._name} err={type(ex).__name__}",
            )
            self._publish_status(ServiceStatus.ERROR)
            return

        # STARTING
        self._cfg = cfg
        self._publish_status(ServiceStatus.STARTING)

        emit_log(
            self._bus,
            "INFO",
            self._name,
            "VIDEO_DAEMON_START",
            f"service={self._name} channel={cfg.channel} url={cfg.url}",
        )

        try:
            # ProcessStreamWorker API: (stream, url, log, **opts)
            # opts may be ignored if not supported — безопасно для v0
            self._worker = self._worker_factory(
                stream=cfg.channel,
                url=cfg.url,
                log=self._log,
                connect_timeout_sec=cfg.connect_timeout_sec,
                read_watchdog_sec=cfg.read_watchdog_sec,
                reconnect_backoff=cfg.reconnect_backoff,
            )
            self._worker.start()
        except TypeError:
            # fallback: если worker_factory не принимает opts (или vendor пока их не поддерживает)
            self._worker = self._worker_factory(stream=cfg.channel, url=cfg.url, log=self._log)
            self._worker.start()
        except Exception as ex:
            emit_log(self._bus, "ERROR", self._name, "VIDEO_DAEMON_START_FAILED", f"err={type(ex).__name__}")
            self._publish_status(ServiceStatus.ERROR)
            return

        impl_name = type(self._worker).__name__ if self._worker is not None else "unknown"
        impl_file = __file__
        try:
            if self._worker is not None:
                impl_file = inspect.getfile(type(self._worker))
        except Exception:
            pass
        emit_log(
            self._bus,
            "INFO",
            self._name,
            "VIDEO_DAEMON_IMPL",
            f"impl={impl_name} file={impl_file}",
        )

        self._publish_status(ServiceStatus.RUNNING)

        # preview loop (optional)
        enabled, path, period_sec = self._parse_preview_cfg(section)
        if enabled and path:
            self._preview_path = path
            self._preview_period_sec = period_sec
            self._start_preview_loop()

    def stop(self) -> None:
        # idempotent
        if self._status in (ServiceStatus.STOPPED, ServiceStatus.IDLE):
            emit_log(self._bus, "INFO", self._name, "VIDEO_DAEMON_STOP_IGNORED", "reason=ALREADY_STOPPED")
            self._publish_status(ServiceStatus.STOPPED)
            return

        self._publish_status(ServiceStatus.STOPPING) if hasattr(ServiceStatus, "STOPPING") else None  # safety
        # В твоём enum STOPPING нет, поэтому делаем просто лог + STOPPED.
        emit_log(self._bus, "INFO", self._name, "VIDEO_DAEMON_STOP", f"service={self._name}")

        w = self._worker
        self._worker = None

        try:
            if w is not None:
                # ProcessStreamWorker.stop(reason=...)
                try:
                    w.stop(reason="SERVICE_STOP")
                except TypeError:
                    w.stop()
        except Exception as ex:
            emit_log(self._bus, "ERROR", self._name, "VIDEO_DAEMON_STOP_FAILED", f"err={type(ex).__name__}")

        self._preview_stop.set()
        t = self._preview_thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._preview_thread = None
        self._preview_path = None
        self._preview_period_sec = 0.0

        self._cfg = None
        self._publish_status(ServiceStatus.STOPPED)
