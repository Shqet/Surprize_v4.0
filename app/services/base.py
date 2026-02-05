from __future__ import annotations

from enum import Enum
from typing import Any, Protocol


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

    Notes:
      - start/stop must be idempotent (enforced in concrete services)
      - profile config is passed via args/kwargs by ServiceManager (no contract change)
    """

    @property
    def name(self) -> str: ...

    def start(self, *args: Any, **kwargs: Any) -> None: ...

    def stop(self) -> None: ...

    def status(self) -> ServiceStatus: ...
