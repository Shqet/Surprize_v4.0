# tests/test_rtsp_health_v1.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

import pytest

from app.core.events import RtspChannelHealthEvent, ServiceStatusEvent
from app.services.rtsp_health_service import RtspHealthService


class FakeBus:
    """Minimal EventBus stub: just collects published events."""
    def __init__(self) -> None:
        self.events: List[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class ProbeResult:
    ok: bool
    error: str | None = None


def _cfg_one_channel(url: str = "rtsp://example/visible") -> dict:
    return {
        "channels": {
            "visible": {"url": url},
        },
        "probe_timeout_sec": 0.01,
        "period_ok_sec": 0.0,
        "backoff": {"base_ms": 0, "max_ms": 0, "jitter_ms": 0},
    }


def _statuses(bus: FakeBus) -> list[ServiceStatusEvent]:
    return [e for e in bus.events if isinstance(e, ServiceStatusEvent)]


def _health(bus: FakeBus) -> list[RtspChannelHealthEvent]:
    return [e for e in bus.events if isinstance(e, RtspChannelHealthEvent)]


# -------------------- 2.1 ffprobe missing -> ERROR --------------------

def test_ffprobe_missing_fatal_error_no_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.rtsp_health_service as mod
    import threading

    bus = FakeBus()
    svc = RtspHealthService(bus)

    # valid config
    cfg = _cfg_one_channel()

    # ffprobe missing
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    # spy: no worker threads must start
    started = {"count": 0}
    real_start = threading.Thread.start

    def _start_spy(self):
        started["count"] += 1
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", _start_spy)

    svc.start(cfg)

    assert svc.status().value == "ERROR"
    assert started["count"] == 0
    assert any(s.service_name == "rtsp_health" and s.status == "ERROR" for s in _statuses(bus))


# -------------------- 2.2 bad config -> ERROR --------------------

def test_bad_config_fatal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.rtsp_health_service as mod
    import threading

    bus = FakeBus()
    svc = RtspHealthService(bus)

    # ensure ffprobe check passes so we test config path
    monkeypatch.setattr(mod.RtspHealthService, "_ffprobe_available", lambda self: True)

    started = {"count": 0}
    real_start = threading.Thread.start

    def _start_spy(self):
        started["count"] += 1
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", _start_spy)

    svc.start({})  # no channels

    assert svc.status().value == "ERROR"
    assert started["count"] == 0
    assert any(s.service_name == "rtsp_health" and s.status == "ERROR" for s in _statuses(bus))


# -------------------- 2.3 probe fail does NOT error service --------------------

def test_probe_fail_does_not_error_service_publishes_reconnecting(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.rtsp_health_service as mod

    bus = FakeBus()
    svc = RtspHealthService(bus)

    # bypass ffprobe availability check
    monkeypatch.setattr(mod.RtspHealthService, "_ffprobe_available", lambda self: True)

    # avoid real sleeping
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    # mock probe: fail once, then force worker exit
    def _probe(_bus, _url, timeout_sec: float, source: str):
        svc._stop_event.set()
        return ProbeResult(ok=False, error="mock_fail")

    monkeypatch.setattr(mod, "probe_rtsp_ffprobe", _probe)

    svc.start(_cfg_one_channel())

    # service should become RUNNING (at least once)
    assert any(s.service_name == "rtsp_health" and s.status == "RUNNING" for s in _statuses(bus))

    # stop for cleanup (STOPPED is ok; we only require "not ERROR" during probe failure)
    svc.stop()

    st = [s.status for s in _statuses(bus) if s.service_name == "rtsp_health"]
    assert "ERROR" not in st

    health = _health(bus)
    assert any(h.state == "RECONNECTING" and h.attempt >= 1 for h in health)

    # v1: OFFLINE must never appear
    assert all(h.state in ("CONNECTED", "RECONNECTING") for h in health)


# -------------------- 2.4 probe success -> CONNECTED --------------------

def test_probe_success_publishes_connected_attempt_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.rtsp_health_service as mod

    bus = FakeBus()
    svc = RtspHealthService(bus)

    monkeypatch.setattr(mod.RtspHealthService, "_ffprobe_available", lambda self: True)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    def _probe(_bus, _url, timeout_sec: float, source: str):
        svc._stop_event.set()
        return ProbeResult(ok=True, error=None)

    monkeypatch.setattr(mod, "probe_rtsp_ffprobe", _probe)

    svc.start(_cfg_one_channel())
    assert any(s.service_name == "rtsp_health" and s.status == "RUNNING" for s in _statuses(bus))
    svc.stop()

    health = _health(bus)
    assert any(h.state == "CONNECTED" and h.attempt == 0 for h in health)
    assert all(h.state in ("CONNECTED", "RECONNECTING") for h in health)
