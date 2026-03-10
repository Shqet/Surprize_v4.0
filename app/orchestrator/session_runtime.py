from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SessionStatus(str, Enum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


@dataclass
class SessionRuntime:
    session_id: str
    scenario_id: str
    t0_unix: float
    t0_monotonic: float
    status: SessionStatus
    paths: dict[str, str]
    handles: dict[str, Any] = field(default_factory=dict)
    t1_unix: Optional[float] = None
    duration_sec: Optional[float] = None

