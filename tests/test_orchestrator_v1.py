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
class _DummyService:
    name: str


class _FakeServiceManager:
    """
    Fake ServiceManager for unit tests:
      - No subprocess, no Qt
      - Emits ServiceStatusEvent for start only
      - stop_all() does NOT emit STOPPED automatically (tests control it explicitly)
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._services: dict[str, _DummyService] = {}

    def register(self, svc: _DummyService) -> None:
        self._services[svc.name] = svc
        # mimic v0: services start IDLE on register
        self._bus.publish(ServiceStatusEvent(service_name=svc.name, status=ServiceStatus.IDLE.value))

    def get_services(self) -> dict[str, _DummyService]:
        return dict(self._services)

    def start_all(self, profile_cfg: dict[str, Any]) -> None:
        # mimic typical progression: STARTING -> RUNNING
        for name in sorted(self._services.keys()):
            self._bus.publish(ServiceStatusEvent(service_name=name, status=ServiceStatus.STARTING.value))
            self._bus.publish(ServiceStatusEvent(service_name=name, status=ServiceStatus.RUNNING.value))

    def stop_all(self) -> None:
        # tests will publish STOPPED or ERROR when desired
        return


def _wait_state(bus_states: list[str], expected: str, timeout: float = 0.6) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bus_states and bus_states[-1] == expected:
            return True
        time.sleep(0.01)
    return False


def test_orchestrator_stop_sync_reaches_idle_when_all_services_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    sm = _FakeServiceManager(bus)
    sm.register(_DummyService(name="svc1"))
    sm.register(_DummyService(name="svc2"))

    # Capture state transitions from events
    states: list[str] = []
    bus.subscribe(OrchestratorStateEvent, lambda e: states.append(e.state))

    # Patch profile loader used inside Orchestrator.start()
    from app.orchestrator import orchestrator as orch_mod

    monkeypatch.setattr(
        orch_mod,
        "load_profile",
        lambda profile_name: {
            profile_name: {
                "orchestrator": {"stop_timeout_sec": 2},
                "services": {"exe_runner": {"path": "cmd", "args": "/c echo hi", "timeout_sec": 1}},
            }
        },
    )

    orch = Orchestrator(bus, sm)

    orch.start("p")
    assert _wait_state(states, OrchestratorState.RUNNING.value)

    orch.stop()
    assert _wait_state(states, OrchestratorState.STOPPING.value)

    # Now simulate services confirming STOPPED via ServiceStatusEvent (source of truth)
    bus.publish(ServiceStatusEvent(service_name="svc1", status=ServiceStatus.STOPPED.value))
    bus.publish(ServiceStatusEvent(service_name="svc2", status=ServiceStatus.STOPPED.value))

    assert _wait_state(states, OrchestratorState.IDLE.value)


def test_orchestrator_stop_timeout_goes_error_and_logs_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    sm = _FakeServiceManager(bus)
    sm.register(_DummyService(name="svc1"))
    sm.register(_DummyService(name="svc2"))

    states: list[str] = []
    bus.subscribe(OrchestratorStateEvent, lambda e: states.append(e.state))

    # Capture ERROR log message
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
                "services": {"exe_runner": {"path": "cmd", "args": "/c echo hi", "timeout_sec": 1}},
            }
        },
    )

    orch = Orchestrator(bus, sm)

    orch.start("p")
    assert _wait_state(states, OrchestratorState.RUNNING.value)

    orch.stop()
    assert _wait_state(states, OrchestratorState.STOPPING.value)

    # Do NOT publish STOPPED -> expect timeout -> ERROR
    assert _wait_state(states, OrchestratorState.ERROR.value, timeout=1.5)

    # Must include pending services and timeout_sec per v1
    # Example: "pending=svc1,svc2 timeout_sec=1"
    joined = "\n".join(errors)
    assert "pending=" in joined
    assert "timeout_sec=1" in joined


def test_orchestrator_default_stop_timeout_logs_warning_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    sm = _FakeServiceManager(bus)
    sm.register(_DummyService(name="svc1"))

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
                "services": {"exe_runner": {"path": "cmd", "args": "/c echo hi", "timeout_sec": 1}},
            }
        },
    )

    orch = Orchestrator(bus, sm)
    orch.start("p")

    assert any("param=stop_timeout_sec" in m and "default=10" in m for m in warnings)
