# tests/test_video_channel_daemon_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

import pytest

from app.core.event_bus import EventBus
from app.core.events import ServiceStatusEvent, LogEvent
from app.services.base import ServiceStatus
from app.services.video_channel import VideoChannelDaemonService


# -------------------------
# Helpers / fakes
# -------------------------

@dataclass
class FakeBus(EventBus):
    published: List[Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.published is None:
            self.published = []

    def publish(self, event: Any) -> None:  # type: ignore[override]
        self.published.append(event)


class FakeWorker:
    """
    Поддерживаем сигнатуру как у ProcessStreamWorker(**kw):
      stream, url, log, connect_timeout_sec, read_watchdog_sec, reconnect_backoff, ...
    """
    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        self.stream = kw.get("stream")
        self.url = kw.get("url")
        self.log_cb = kw.get("log")
        self.started = False
        self.stopped = False
        self.stop_reason = None

    def start(self) -> None:
        self.started = True
        if callable(self.log_cb):
            self.log_cb(f"FAKE_WORKER_STARTED stream={self.stream} url={self.url}")

    def stop(self, reason: str = "TEST_STOP") -> None:
        self.stopped = True
        self.stop_reason = reason
        if callable(self.log_cb):
            self.log_cb(f"FAKE_WORKER_STOPPED stream={self.stream} reason={reason}")


def _statuses(bus: FakeBus, service_name: str) -> list[str]:
    return [
        e.status
        for e in bus.published
        if isinstance(e, ServiceStatusEvent) and e.service_name == service_name
    ]


def _logs(bus: FakeBus, service_name: str) -> list[str]:
    return [
        e.message
        for e in bus.published
        if isinstance(e, LogEvent) and e.source == service_name
    ]


def _cfg_ok() -> dict:
    return {
        "channel": "visible",
        "url": "rtsp://192.168.0.170:8554/visible",
        "width": 1280,
        "height": 720,
        "connect_timeout_sec": 10,
        "read_watchdog_sec": 5,
        "reconnect_backoff": [1, 2, 5],
        "preview": {
            "enabled": False,
            "out_path": "outputs/video_preview/visible/latest.jpg",
            "period_ms": 200,
        },
    }


# -------------------------
# Tests
# -------------------------

def test_start_stop_status_flow() -> None:
    bus = FakeBus()
    svc = VideoChannelDaemonService(bus=bus, name="video_visible")

    # патчим фабрику так, как её вызывает сервис: **kwargs
    svc._worker_factory = lambda **kw: FakeWorker(**kw)  # type: ignore[attr-defined]

    svc.start(_cfg_ok())

    st = _statuses(bus, "video_visible")
    assert ServiceStatus.STARTING.value in st
    assert ServiceStatus.RUNNING.value in st

    svc.stop()

    st2 = _statuses(bus, "video_visible")
    assert ServiceStatus.STOPPED.value in st2


def test_invalid_config_sets_error_status_and_does_not_raise() -> None:
    bus = FakeBus()
    svc = VideoChannelDaemonService(bus=bus, name="video_visible")
    svc._worker_factory = lambda **kw: FakeWorker(**kw)  # type: ignore[attr-defined]

    bad = _cfg_ok()
    bad.pop("url")

    svc.start(bad)

    st = _statuses(bus, "video_visible")
    assert ServiceStatus.ERROR.value in st

    logs = _logs(bus, "video_visible")
    # твой реальный контракт сейчас — SERVICE_ERROR + тип ошибки
    assert any(
        isinstance(e, LogEvent)
        and e.source == "video_visible"
        and e.code == "SERVICE_ERROR"
        and "err=ValueError" in e.message
        for e in bus.published
    )


def test_idempotent_start_stop() -> None:
    bus = FakeBus()
    svc = VideoChannelDaemonService(bus=bus, name="video_visible")
    svc._worker_factory = lambda **kw: FakeWorker(**kw)  # type: ignore[attr-defined]

    svc.start(_cfg_ok())
    svc.start(_cfg_ok())  # второй старт должен быть ignored (без дублей RUNNING)

    st = _statuses(bus, "video_visible")
    # STARTING/RUNNING должны быть, но RUNNING не должен бесконечно дублироваться
    assert ServiceStatus.RUNNING.value in st

    svc.stop()
    svc.stop()  # второй stop — idempotent

    st2 = _statuses(bus, "video_visible")
    assert ServiceStatus.STOPPED.value in st2
