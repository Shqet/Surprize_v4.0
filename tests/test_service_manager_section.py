from __future__ import annotations

from typing import Any

from app.core.event_bus import EventBus
from app.services.service_manager import ServiceManager


class _FakeService:
    def __init__(self, name: str) -> None:
        self._name = name
        self.last_section: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return self._name

    def start(self, profile_section: dict[str, Any]) -> None:
        self.last_section = profile_section

    def stop(self) -> None:
        pass


def test_start_all_passes_service_section_only() -> None:
    bus = EventBus()
    sm = ServiceManager(bus)

    svc = _FakeService("svc1")
    sm.register(svc)

    profile = {
        "default": {
            "services": {
                "svc1": {"a": 1, "b": 2},
            }
        }
    }

    sm.start_all(profile)
    assert svc.last_section == {"a": 1, "b": 2}
