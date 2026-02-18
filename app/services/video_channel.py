# app/services/video_channel.py
from __future__ import annotations

from dataclasses import dataclass
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

        emit_log(self._bus, "INFO", self._name, "VIDEO_DAEMON_IMPL", "impl=ProcessStreamWorker")
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

        self._publish_status(ServiceStatus.RUNNING)

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

        self._cfg = None
        self._publish_status(ServiceStatus.STOPPED)
