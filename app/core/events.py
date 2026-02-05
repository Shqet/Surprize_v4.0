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
