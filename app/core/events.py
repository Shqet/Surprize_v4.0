from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LogEvent:
    level: str
    source: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ServiceStatusEvent:
    service_name: str
    status: str


@dataclass(frozen=True, slots=True)
class OrchestratorStateEvent:
    state: str


@dataclass(frozen=True, slots=True)
class ProcessOutputEvent:
    service_name: str
    stream: str
    line: str

@dataclass(frozen=True, slots=True)
class RtspChannelHealthEvent:
    service_name: str
    channel: str          # "visible" | "thermal"
    url: str
    state: str            # "CONNECTED" | "RECONNECTING" | "OFFLINE"
    attempt: int
    last_error: str | None
@dataclass(frozen=True, slots=True)
class RtspIngestStatsEvent:
    service: str            # always "rtsp_ingest"
    channel: str            # e.g. "visible" | "thermal" | any configured key
    state: str              # "INGESTING" | "RESTARTING" | "STALLED"
    fps_est: float
    last_frame_age_sec: float
    restarts: int
    ts: float               # unix seconds


@dataclass(frozen=True, slots=True)
class MayakHealthEvent:
    service_name: str
    ready: bool
    global_enable: bool | None
    error_code: int
    io_error_streak: int
    io_degraded: bool
    sp1_state: str
    sp2_state: str
    sp1_connected: bool | None
    sp2_connected: bool | None
    ts: float
