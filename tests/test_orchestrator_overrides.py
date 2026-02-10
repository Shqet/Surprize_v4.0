from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.events import LogEvent
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.states import OrchestratorState


@dataclass
class _DummyService:
    name: str


class _CapturingServiceManager:
    """Minimal ServiceManager double for Orchestrator tests (no subprocess, no Qt)."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._services: dict[str, _DummyService] = {}
        self.last_profile_cfg: dict[str, Any] | None = None
        self.start_all_calls: int = 0

    def register(self, svc: _DummyService) -> None:
        self._services[svc.name] = svc

    def get_services(self) -> dict[str, _DummyService]:
        return dict(self._services)

    def start_all(self, profile_cfg: dict[str, Any]) -> None:
        self.last_profile_cfg = profile_cfg
        self.start_all_calls += 1

    def stop_all(self) -> None:
        return


def _subscribe_logs(bus: EventBus) -> list[LogEvent]:
    logs: list[LogEvent] = []
    bus.subscribe(LogEvent, lambda e: logs.append(e))
    return logs


def test_start_without_overrides_forwards_profile_cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    logs = _subscribe_logs(bus)
    sm = _CapturingServiceManager(bus)
    orch = Orchestrator(bus, sm)

    from app.orchestrator import orchestrator as orch_mod

    base_cfg = {
        "default": {
            "services": {
                "ballistics_model": {"config_json": {}},
                "exe_runner": {"path": "cmd", "args": "/c echo hi"},
            },
            "orchestrator": {"stop_timeout_sec": 10},
        }
    }

    monkeypatch.setattr(orch_mod, "load_profile", lambda profile_name: base_cfg)

    orch.start("default")

    assert sm.start_all_calls == 1
    assert sm.last_profile_cfg is base_cfg
    assert orch.state == OrchestratorState.RUNNING
    assert any(e.code == "ORCH_START_REQUEST" for e in logs)


def test_start_with_overrides_applies_deep_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    sm = _CapturingServiceManager(bus)
    orch = Orchestrator(bus, sm)

    from app.orchestrator import orchestrator as orch_mod

    base_cfg = {
        "default": {
            "services": {
                "ballistics_model": {"config_json": {}},
                "exe_runner": {"path": "cmd", "args": "/c echo hi"},
            },
            "orchestrator": {"stop_timeout_sec": 10},
        }
    }

    monkeypatch.setattr(orch_mod, "load_profile", lambda profile_name: base_cfg)

    overrides = {
        "services": {
            "ballistics_model": {
                "config_json": {"k": 123, "nested": {"x": 1}},
            }
        }
    }

    orch.start("default", overrides=overrides)

    assert sm.start_all_calls == 1
    assert sm.last_profile_cfg is base_cfg
    assert sm.last_profile_cfg["default"]["services"]["ballistics_model"]["config_json"] == {"k": 123, "nested": {"x": 1}}
    assert orch.state == OrchestratorState.RUNNING


def test_start_with_invalid_overrides_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    logs = _subscribe_logs(bus)
    sm = _CapturingServiceManager(bus)
    orch = Orchestrator(bus, sm)

    from app.orchestrator import orchestrator as orch_mod

    base_cfg = {
        "default": {
            "services": {
                "ballistics_model": {"config_json": {}},
                "exe_runner": {"path": "cmd", "args": "/c echo hi"},
            }
        }
    }

    monkeypatch.setattr(orch_mod, "load_profile", lambda profile_name: base_cfg)

    orch.start("default", overrides=[1, 2, 3])  # type: ignore[arg-type]

    assert sm.start_all_calls == 0
    assert orch.state == OrchestratorState.ERROR
    assert any((e.code == "SERVICE_ERROR" and "validate_overrides" in e.message) for e in logs)
