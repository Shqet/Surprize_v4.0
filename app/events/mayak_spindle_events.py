from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class MayakSpindleTelemetryEvent:
    """Telemetry snapshot for one spindle.

    Notes:
    - Keep it small and stable (v1).
    - angle_deg is only meaningful for SP1 in your current emulator contract.
    """
    service: str
    spindle: str  # "sp1" | "sp2"
    connected: bool
    status_word: int
    actual_speed_rpm: int
    actual_torque: int
    angle_deg: Optional[int]
    sim_time_ms: Optional[int]
    error_code: Optional[int]
    ts: float


@dataclass(frozen=True, slots=True)
class MayakSpindleCommandEvent:
    """Command issued by service (useful for UI/debug).

    This is an *emit-only* event; the service itself is controlled via methods
    (or later via some higher-level Orchestrator/UI command bus).
    """
    service: str
    spindle: str  # "sp1" | "sp2" | "global"
    global_enable: Optional[bool]
    control_word: Optional[int]
    target_speed_rpm: Optional[int]
    ts: float
