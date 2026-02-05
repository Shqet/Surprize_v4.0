from __future__ import annotations

from app.core.event_bus import EventBus
from app.core.events import LogEvent


def test_eventbus_publish_subscribe_single_handler() -> None:
    bus = EventBus()
    got: list[str] = []

    def h(e: LogEvent) -> None:
        got.append(e.code)

    bus.subscribe(LogEvent, h)
    bus.publish(LogEvent(level="INFO", source="t", code="SYSTEM_START", message="k=v"))

    assert got == ["SYSTEM_START"]


def test_eventbus_multiple_subscribers_receive_event() -> None:
    bus = EventBus()
    got1: list[str] = []
    got2: list[str] = []

    bus.subscribe(LogEvent, lambda e: got1.append(e.code))
    bus.subscribe(LogEvent, lambda e: got2.append(e.code))

    bus.publish(LogEvent(level="INFO", source="t", code="X", message="k=v"))

    assert got1 == ["X"]
    assert got2 == ["X"]
