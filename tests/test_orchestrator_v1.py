from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.events import LogEvent, OrchestratorStateEvent, ServiceStatusEvent
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.states import OrchestratorState
from app.services.base import ServiceStatus


@dataclass
class _FakeService:
    name: str
    bus: EventBus
    stop_emits_stopped: bool = False  # tests control STOPPED explicitly unless enabled

    def start(self, profile_cfg: dict[str, Any]) -> None:
        self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.STARTING.value))
        self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.RUNNING.value))

    def stop(self) -> None:
        if self.stop_emits_stopped:
            self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.STOPPED.value))


class _FakeServiceManager:
    def __init__(self, services: dict[str, _FakeService]) -> None:
        self._services = dict(services)

    def get_services(self) -> dict[str, _FakeService]:
        return dict(self._services)


def _wait_state(bus_states: list[str], expected: str, timeout: float = 0.8) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bus_states and bus_states[-1] == expected:
            return True
        time.sleep(0.01)
    return False


def test_orchestrator_stop_sync_reaches_idle_when_all_jobs_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()

    svc1 = _FakeService(name="svc1", bus=bus)
    svc2 = _FakeService(name="svc2", bus=bus)
    sm = _FakeServiceManager({"svc1": svc1, "svc2": svc2})

    states: list[str] = []
    bus.subscribe(OrchestratorStateEvent, lambda e: states.append(e.state))

    from app.orchestrator import orchestrator as orch_mod

    # Both services are jobs in this profile
    monkeypatch.setattr(
        orch_mod,
        "load_profile",
        lambda profile_name: {
            profile_name: {
                "orchestrator": {"stop_timeout_sec": 2},
                "services": {
                    "svc1": {"role": "job"},
                    "svc2": {"role": "job"},
                },
            }
        },
    )

    orch = Orchestrator(bus, sm)

    orch.start("p")
    assert _wait_state(states, OrchestratorState.RUNNING.value)

    orch.stop()
    assert _wait_state(states, OrchestratorState.STOPPING.value)

    # Now simulate jobs confirming STOPPED via ServiceStatusEvent (source of truth)
    bus.publish(ServiceStatusEvent(service_name="svc1", status=ServiceStatus.STOPPED.value))
    bus.publish(ServiceStatusEvent(service_name="svc2", status=ServiceStatus.STOPPED.value))

    assert _wait_state(states, OrchestratorState.IDLE.value)


def test_orchestrator_stop_timeout_goes_error_and_logs_pending_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()

    svc1 = _FakeService(name="svc1", bus=bus)
    svc2 = _FakeService(name="svc2", bus=bus)
    sm = _FakeServiceManager({"svc1": svc1, "svc2": svc2})

    states: list[str] = []
    bus.subscribe(OrchestratorStateEvent, lambda e: states.append(e.state))

    errors: list[str] = []
    bus.subscribe(
        LogEvent,
        lambda e: errors.append(e.message) if (e.level == "ERROR" and e.code == "SERVICE_ERROR") else None,
    )

    from app.orchestrator import orchestrator as orch_mod

    # stop_timeout_sec small for fast test
    monkeypatch.setattr(
        orch_mod,
        "load_profile",
        lambda profile_name: {
            profile_name: {
                "orchestrator": {"stop_timeout_sec": 1},
                "services": {
                    "svc1": {"role": "job"},
                    "svc2": {"role": "job"},
                },
            }
        },
    )

    orch = Orchestrator(bus, sm)

    orch.start("p")
    assert _wait_state(states, OrchestratorState.RUNNING.value)

    orch.stop()
    assert _wait_state(states, OrchestratorState.STOPPING.value)

    # Do NOT publish STOPPED -> expect timeout -> ERROR
    assert _wait_state(states, OrchestratorState.ERROR.value, timeout=1.6)

    joined = "\n".join(errors)
    assert "pending=" in joined
    assert "timeout_sec=1" in joined


def test_orchestrator_default_stop_timeout_logs_warning_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()

    svc1 = _FakeService(name="svc1", bus=bus)
    sm = _FakeServiceManager({"svc1": svc1})

    warnings: list[str] = []
    bus.subscribe(
        LogEvent,
        lambda e: warnings.append(e.message) if (e.level == "WARNING" and e.code == "SERVICE_ERROR") else None,
    )

    from app.orchestrator import orchestrator as orch_mod

    # No orchestrator.stop_timeout_sec in profile -> must default=10 and log WARNING
    monkeypatch.setattr(
        orch_mod,
        "load_profile",
        lambda profile_name: {
            profile_name: {
                "services": {
                    "svc1": {"role": "job"},
                },
            }
        },
    )

    orch = Orchestrator(bus, sm)
    orch.start("p")

    assert any("param=stop_timeout_sec" in m and "default=10" in m for m in warnings)
