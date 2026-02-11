# tests/test_rtsp_ingest_v1.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import shutil
import subprocess

from app.core.events import RtspIngestStatsEvent, ServiceStatusEvent
from app.services.base import ServiceStatus
from app.services.rtsp_ingest_service import RtspIngestService


class DummyBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, ev: Any) -> None:
        self.published.append(ev)


@dataclass
class _FakePopen:
    """
    Minimal fake for subprocess.Popen used by rtsp_ingest_service.
    """
    pid: int = 12345
    _rc: int | None = None
    stdin: Any = None
    stdout: Any = None
    stderr: Any = None

    def poll(self) -> int | None:
        return self._rc

    def wait(self, timeout: float | None = None) -> int:
        if self._rc is None:
            self._rc = 0
        return self._rc

    def terminate(self) -> None:
        self._rc = 0

    def kill(self) -> None:
        self._rc = 0


def _profile_ok(tmp_path, **overrides) -> dict:
    cfg = {
        "channels": {
            "visible": {"url": "rtsp://127.0.0.1:8554/visible"},
        },
        "restart_backoff": {"base_ms": 0, "max_ms": 0, "jitter_ms": 0},
        "out_root": str(tmp_path),
        "snapshot_fps": 2.0,
        "ffmpeg_path": "ffmpeg",
        "max_frame_age_sec": 1.0,
    }
    cfg.update(overrides)
    return cfg


def test_ffmpeg_missing_sets_error_and_no_workers(monkeypatch, tmp_path):
    bus = DummyBus()
    svc = RtspIngestService(bus)

    # ffmpeg missing
    monkeypatch.setattr(shutil, "which", lambda _: None)

    # keep start fast; if it tries to run ffmpeg -version, fail test loudly
    def _run_should_not_happen(*a, **kw):
        raise AssertionError("subprocess.run() should not be called when ffmpeg is missing")

    monkeypatch.setattr(subprocess, "run", _run_should_not_happen)

    svc.start(_profile_ok(tmp_path))

    assert svc.status() == ServiceStatus.ERROR
    assert any(isinstance(e, ServiceStatusEvent) and e.status == "ERROR" for e in bus.published)

    # workers must not start
    assert getattr(svc, "_threads", {}) == {}


def test_bad_config_sets_error_and_no_workers(tmp_path):
    bus = DummyBus()
    svc = RtspIngestService(bus)

    # missing channels
    svc.start({"out_root": str(tmp_path)})

    assert svc.status() == ServiceStatus.ERROR
    assert any(isinstance(e, ServiceStatusEvent) and e.status == "ERROR" for e in bus.published)
    assert getattr(svc, "_threads", {}) == {}


def test_restart_path_emits_restarting_stats_and_logs_ingest_restart(monkeypatch, tmp_path):
    bus = DummyBus()
    svc = RtspIngestService(bus)

    # make stats very frequent for a fast test
    svc._stats_period_sec = 0.0  # type: ignore[attr-defined]

    # ffmpeg available
    monkeypatch.setattr(shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)

    # capture logs
    logs: list[str] = []

    def fake_emit_log(_bus, level, service, code, msg):
        logs.append(f"{code} {msg}")

    monkeypatch.setattr("app.services.rtsp_ingest_service.emit_log", fake_emit_log)

    # popen exits immediately with rc=1 to trigger restart loop
    def fake_popen(*a, **kw):
        p = _FakePopen(pid=111)
        p._rc = 1
        return p

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    # avoid STALLED taking over: pretend file is always fresh (age small)
    now = time.time()
    monkeypatch.setattr(
        RtspIngestService,
        "_safe_mtime",
        lambda self, path: now,
    )

    svc.start(_profile_ok(tmp_path, max_frame_age_sec=5.0))

    # let worker spin at least once
    time.sleep(0.2)

    # Service must stay RUNNING (restart is non-fatal)
    assert svc.status() == ServiceStatus.RUNNING

    # Should publish stats with state=RESTARTING at least once
    states = [e.state for e in bus.published if isinstance(e, RtspIngestStatsEvent)]
    assert "RESTARTING" in states

    # Should log INGEST_RESTART
    assert any(s.startswith("INGEST_RESTART") for s in logs)

    svc.stop()


def test_stalled_detection(monkeypatch, tmp_path):
    bus = DummyBus()
    svc = RtspIngestService(bus)
    svc._stats_period_sec = 0.0  # type: ignore[attr-defined]

    # ffmpeg available
    monkeypatch.setattr(shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)

    # Popen stays alive until stop()
    p = _FakePopen(pid=222)
    p._rc = None

    def fake_popen(*a, **kw):
        return p

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    # Make latest.jpg look old -> STALLED
    monkeypatch.setattr(
        RtspIngestService,
        "_safe_mtime",
        lambda self, path: time.time() - 10.0,
    )

    svc.start(_profile_ok(tmp_path, max_frame_age_sec=0.5))

    time.sleep(0.4)

    states = [e.state for e in bus.published if isinstance(e, RtspIngestStatsEvent)]
    assert "STALLED" in states

    svc.stop()
