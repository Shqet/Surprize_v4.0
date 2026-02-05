from __future__ import annotations

from enum import Enum
from typing import Protocol


class ServiceStatus(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class BaseService(Protocol):
    """
    Contract (v0):
      - name: str
      - start() -> None
      - stop() -> None
      - status() -> ServiceStatus
    Rules:
      - start/stop are idempotent
      - errors must be logged + sent as ServiceStatusEvent(ERROR) (will be done in concrete services)
    """

    @property
    def name(self) -> str: ...

    def start(self, *args, **kwargs) -> None: ...

    def stop(self) -> None: ...

    def status(self) -> ServiceStatus: ...
