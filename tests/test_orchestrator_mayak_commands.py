from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.orchestrator.orchestrator import Orchestrator


class _FakeServiceManager:
    def __init__(self, services: dict[str, Any]) -> None:
        self._services = dict(services)

    def get_services(self) -> dict[str, Any]:
        return dict(self._services)


@dataclass
class _MayakLikeService:
    set_speed_calls: list[tuple[str, int, int]] = field(default_factory=list)
    stop_calls: list[str] = field(default_factory=list)
    ge_calls: list[bool] = field(default_factory=list)

    def set_spindle_speed(self, spindle: str, *, direction: int, rpm: int) -> None:
        self.set_speed_calls.append((spindle, direction, rpm))

    def stop_spindle(self, spindle: str) -> None:
        self.stop_calls.append(spindle)

    def set_global_enable(self, enabled: bool) -> None:
        self.ge_calls.append(bool(enabled))


def test_set_speed_routes_to_mayak_service() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    orch.set_speed("sp1", 1200, 1)

    assert mayak.set_speed_calls == [("sp1", 1, 1200)]


def test_emergency_stop_uses_fallback_when_method_missing() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    orch.emergency_stop()

    assert mayak.stop_calls == ["sp1", "sp2"]
    assert mayak.ge_calls == [False]


def test_apply_profile_linear_raises_when_service_does_not_support_it() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    with pytest.raises(RuntimeError):
        orch.apply_profile_linear("sp1", 0, 1000, 2.0)

