from __future__ import annotations

from enum import Enum


class OrchestratorState(str, Enum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"
