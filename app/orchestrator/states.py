from __future__ import annotations

from enum import Enum


class OrchestratorState(str, Enum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class OrchestratorPhase(str, Enum):
    PREPARING = "PREPARING"
    PREPARED = "PREPARED"
    MONITORING = "MONITORING"
    READY = "READY"
    TEST_RUNNING = "TEST_RUNNING"
    PHASE_ERROR = "PHASE_ERROR"
