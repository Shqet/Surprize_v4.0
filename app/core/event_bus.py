from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any, DefaultDict, TypeVar

TEvent = TypeVar("TEvent")


class EventBus:
    """
    Contract v0:
      - subscribe by event class
      - publish delivers handlers in the publish thread
      - thread-safe subscribe/publish (Lock around subscriptions)
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._handlers: dict[type[Any], list[Callable[[Any], None]]] = {}

    def subscribe(self, event_type: type[TEvent], handler: Callable[[TEvent], None]) -> None:
        if handler is None:
            raise ValueError("handler must not be None")
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)  # type: ignore[arg-type]

    def publish(self, event: Any) -> None:
        # Handlers must run in the publish thread (no dispatch threads here).
        event_type = type(event)
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
        for h in handlers:
            h(event)
