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
    stop_emits_stopped: bool = True

    start_calls: int = 0
    stop_calls: int = 0
    last_profile_cfg: dict[str, Any] | None = None

    def start(self, profile_cfg: dict[str, Any]) -> None:
        self.start_calls += 1
        self.last_profile_cfg = profile_cfg
        self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.STARTING.value))
        self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.RUNNING.value))

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_emits_stopped:
            self.bus.publish(ServiceStatusEvent(service_name=self.name, status=ServiceStatus.STOPPED.value))


class _FakeServiceManager:
    def __init__(self, services: dict[str, _FakeService]) -> None:
        self._services = dict(services)

    def get_services(self) -> dict[str, _FakeService]:
        return dict(self._services)


def _wait_state(states: list[str], expected: str, timeout: float = 0.8) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if states and states[-1] == expected:
            return True
        time.sleep(0.01)
    return False


def _subscribe_states(bus: EventBus) -> list[str]:
    states: list[str] = []
    bus.subscribe(OrchestratorStateEvent, lambda e: states.append(e.state))
    return states


def _subscribe_logs(bus: EventBus) -> list[LogEvent]:
    logs: list[LogEvent] = []
    bus.subscribe(LogEvent, lambda e: logs.append(e))
    return logs


def test_default_role_is_job_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    logs = _subscribe_logs(bus)
    states = _subscribe_states(bus)

    job1 = _FakeService(name="job1", bus=bus)
    daemon1 = _FakeService(name="daemon1", bus=bus)
    sm = _FakeServiceManager({"job1": job1, "daemon1": daemon1})

    from app.orchestrator import orchestrator as orch_mod

    # job1 has no role -> must be treated as job
    monkeypatch.setattr(
        orch_mod,
        "load_profile",
        lambda profile_name: {
            profile_name: {
                "orchestrator": {"stop_timeout_sec": 1},
                "services": {
                    "job1": {},
                    "daemon1": {"role": "daemon"},
                },
            }
        },
    )

    orch = Orchestrator(bus, sm)
    orch.start("p")

    assert _wait_state(states, OrchestratorState.RUNNING.value)

    role_logs = [e for e in logs if e.code == "ORCH_SERVICE_ROLES"]
    assert role_logs, "expected ORCH_SERVICE_ROLES log"
    msg = role_logs[-1].message
    assert "jobs=job1" in msg
    assert "daemons=daemon1" in msg

    # Finish job -> ORCH to IDLE
    bus.publish(ServiceStatusEvent(service_name="job1", status=ServiceStatus.STOPPED.value))
    assert _wait_state(states, OrchestratorState.IDLE.value)


def test_daemon_running_does_not_keep_orchestrator_running_after_jobs_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    states = _subscribe_states(bus)

    job1 = _FakeService(name="job1", bus=bus)
    job2 = _FakeService(name="job2", bus=bus)
    daemon1 = _FakeService(name="daemon1", bus=bus)
    sm = _FakeServiceManager({"job1": job1, "job2": job2, "daemon1": daemon1})

    daemon_statuses: list[str] = []
    bus.subscribe(
        ServiceStatusEvent,
        lambda e: daemon_statuses.append(e.status) if e.service_name == "daemon1" else None,
    )

    from app.orchestrator import orchestrator as orch_mod

    monkeypatch.setattr(
        orch_mod,
        "load_profile",
        lambda profile_name: {
            profile_name: {
                "orchestrator": {"stop_timeout_sec": 1},
                "services": {
                    "daemon1": {"role": "daemon"},
                    "job1": {"role": "job"},
                    "job2": {"role": "job"},
                },
            }
        },
    )

    orch = Orchestrator(bus, sm)
    orch.start("p")
    assert _wait_state(states, OrchestratorState.RUNNING.value)

    # jobs complete -> ORCH must go IDLE even though daemon is RUNNING
    bus.publish(ServiceStatusEvent(service_name="job1", status=ServiceStatus.STOPPED.value))
    bus.publish(ServiceStatusEvent(service_name="job2", status=ServiceStatus.STOPPED.value))

    assert _wait_state(states, OrchestratorState.IDLE.value)

    # daemon never got STOPPED (still running)
    assert ServiceStatus.RUNNING.value in daemon_statuses
    assert ServiceStatus.STOPPED.value not in daemon_statuses


def test_stop_stops_jobs_only_daemon_keeps_running(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    states = _subscribe_states(bus)

    # job stop emits STOPPED immediately; daemon should not be stopped
    job1 = _FakeService(name="job1", bus=bus, stop_emits_stopped=True)
    daemon1 = _FakeService(name="daemon1", bus=bus, stop_emits_stopped=True)
    sm = _FakeServiceManager({"job1": job1, "daemon1": daemon1})

    from app.orchestrator import orchestrator as orch_mod

    monkeypatch.setattr(
        orch_mod,
        "load_profile",
        lambda profile_name: {
            profile_name: {
                "orchestrator": {"stop_timeout_sec": 1},
                "services": {
                    "daemon1": {"role": "daemon"},
                    "job1": {"role": "job"},
                },
            }
        },
    )

    orch = Orchestrator(bus, sm)
    orch.start("p")
    assert _wait_state(states, OrchestratorState.RUNNING.value)

    orch.stop()
    assert _wait_state(states, OrchestratorState.STOPPING.value)

    # job should be stopped, daemon should NOT receive stop() call
    assert job1.stop_calls == 1
    assert daemon1.stop_calls == 0

    # STOPPING waiter waits only jobs -> should reach IDLE
    assert _wait_state(states, OrchestratorState.IDLE.value)
