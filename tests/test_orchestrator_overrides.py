from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.events import LogEvent
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.states import OrchestratorState
from app.services.base import ServiceStatus
from app.core.events import ServiceStatusEvent


@dataclass
class _CapturingService:
    name: str
    bus: EventBus

    start_calls: int = 0
    last_profile_cfg: dict[str, Any] | None = None

    def start(self, profile_cfg: dict[str, Any]) -> None:
        self.start_calls += 1
        self.last_profile_cfg = profile_cfg
        # minimal status progression (helps orchestrator record RUNNING statuses)
        self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.STARTING.value))
        self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.RUNNING.value))

    def stop(self) -> None:
        return


class _CapturingServiceManager:
    """ServiceManager double for Orchestrator v4 tests (no subprocess, no Qt)."""

    def __init__(self, services: dict[str, _CapturingService]) -> None:
        self._services = dict(services)

    def get_services(self) -> dict[str, _CapturingService]:
        return dict(self._services)


def _subscribe_logs(bus: EventBus) -> list[LogEvent]:
    logs: list[LogEvent] = []
    bus.subscribe(LogEvent, lambda e: logs.append(e))
    return logs


def test_start_without_overrides_forwards_profile_cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    logs = _subscribe_logs(bus)

    svc_a = _CapturingService(name="exe_runner", bus=bus)
    svc_b = _CapturingService(name="ballistics_model", bus=bus)
    sm = _CapturingServiceManager({"exe_runner": svc_a, "ballistics_model": svc_b})

    orch = Orchestrator(bus, sm)

    from app.orchestrator import orchestrator as orch_mod

    base_cfg = {
        "default": {
            "services": {
                "ballistics_model": {"role": "job", "config_json": {}},
                "exe_runner": {"role": "job", "path": "cmd", "args": "/c echo hi"},
            },
            "orchestrator": {"stop_timeout_sec": 10},
        }
    }

    monkeypatch.setattr(orch_mod, "load_profile", lambda profile_name: base_cfg)

    orch.start("default")

    # v4: start() calls svc.start(service_section) per service in profile
    assert svc_a.start_calls == 1
    assert svc_b.start_calls == 1
    assert svc_a.last_profile_cfg == {"role": "job", "path": "cmd", "args": "/c echo hi"}
    assert svc_b.last_profile_cfg == {"role": "job", "config_json": {}}
    assert orch.state == OrchestratorState.RUNNING
    assert any(e.code == "ORCH_START_REQUEST" for e in logs)


def test_start_with_overrides_applies_deep_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()

    svc_a = _CapturingService(name="exe_runner", bus=bus)
    svc_b = _CapturingService(name="ballistics_model", bus=bus)
    sm = _CapturingServiceManager({"exe_runner": svc_a, "ballistics_model": svc_b})

    orch = Orchestrator(bus, sm)

    from app.orchestrator import orchestrator as orch_mod

    base_cfg = {
        "default": {
            "services": {
                "ballistics_model": {"role": "job", "config_json": {}},
                "exe_runner": {"role": "job", "path": "cmd", "args": "/c echo hi"},
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

    assert svc_a.start_calls == 1
    assert svc_b.start_calls == 1
    assert svc_b.last_profile_cfg == {"role": "job", "config_json": {"k": 123, "nested": {"x": 1}}}
    assert base_cfg["default"]["services"]["ballistics_model"]["config_json"] == {"k": 123, "nested": {"x": 1}}
    assert orch.state == OrchestratorState.RUNNING


def test_start_with_invalid_overrides_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    logs = _subscribe_logs(bus)

    svc_a = _CapturingService(name="exe_runner", bus=bus)
    sm = _CapturingServiceManager({"exe_runner": svc_a})

    orch = Orchestrator(bus, sm)

    from app.orchestrator import orchestrator as orch_mod

    base_cfg = {
        "default": {
            "services": {
                "exe_runner": {"role": "job", "path": "cmd", "args": "/c echo hi"},
            }
        }
    }

    monkeypatch.setattr(orch_mod, "load_profile", lambda profile_name: base_cfg)

    orch.start("default", overrides=[1, 2, 3])  # type: ignore[arg-type]

    assert svc_a.start_calls == 0
    assert orch.state == OrchestratorState.ERROR
    assert any((e.code == "SERVICE_ERROR" and "validate_overrides" in e.message) for e in logs)
