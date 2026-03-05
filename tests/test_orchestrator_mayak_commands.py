from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.core.event_bus import EventBus
from app.core.events import LogEvent
from app.orchestrator.orchestrator import Orchestrator


class _FakeServiceManager:
    def __init__(self, services: dict[str, Any]) -> None:
        self._services = dict(services)

    def get_services(self) -> dict[str, Any]:
        return dict(self._services)


@dataclass
class _MayakLikeService:
    set_speed_calls: list[tuple[str, int, int]] = field(default_factory=list)
    stop_calls: list[str] = field(default_factory=list)
    ge_calls: list[bool] = field(default_factory=list)
    profile_calls: list[tuple[str, int, int, float]] = field(default_factory=list)
    start_test_calls: list[tuple[int, int, int, int, str, float]] = field(default_factory=list)
    stop_test_calls: int = 0
    emergency_calls: int = 0

    def set_spindle_speed(self, spindle: str, *, direction: int, rpm: int) -> None:
        self.set_speed_calls.append((spindle, direction, rpm))

    def stop_spindle(self, spindle: str) -> None:
        self.stop_calls.append(spindle)

    def set_global_enable(self, enabled: bool) -> None:
        self.ge_calls.append(bool(enabled))

    def apply_profile_linear(self, spindle: str, *, from_rpm: int, to_rpm: int, duration_sec: float) -> None:
        self.profile_calls.append((spindle, from_rpm, to_rpm, duration_sec))

    def start_test(
        self,
        *,
        head_start_rpm: int,
        head_end_rpm: int,
        tail_start_rpm: int,
        tail_end_rpm: int,
        profile_type: str,
        duration_sec: float,
    ) -> None:
        self.start_test_calls.append(
            (head_start_rpm, head_end_rpm, tail_start_rpm, tail_end_rpm, profile_type, duration_sec)
        )

    def stop_test(self) -> None:
        self.stop_test_calls += 1

    def emergency_stop(self) -> None:
        self.emergency_calls += 1


def test_set_speed_routes_to_mayak_service() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    orch.set_speed("sp1", 1200, 1)

    assert mayak.set_speed_calls == [("sp1", 1, 1200)]


def test_emergency_stop_routes_to_service_method() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    orch.emergency_stop()

    assert mayak.emergency_calls == 1


def test_apply_profile_linear_raises_when_service_does_not_support_it() -> None:
    @dataclass
    class _NoProfileService:
        set_speed_calls: list[tuple[str, int, int]] = field(default_factory=list)
        stop_calls: list[str] = field(default_factory=list)
        ge_calls: list[bool] = field(default_factory=list)

        def set_spindle_speed(self, spindle: str, *, direction: int, rpm: int) -> None:
            self.set_speed_calls.append((spindle, direction, rpm))

        def stop_spindle(self, spindle: str) -> None:
            self.stop_calls.append(spindle)

        def set_global_enable(self, enabled: bool) -> None:
            self.ge_calls.append(bool(enabled))

    bus = EventBus()
    mayak = _NoProfileService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    with pytest.raises(RuntimeError):
        orch.apply_profile_linear("sp1", 0, 1000, 2.0)


def test_start_mayak_test_runs_both_spindles_with_linear_profile() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    orch.start_mayak_test(
        head_start_rpm=100,
        head_end_rpm=500,
        tail_start_rpm=200,
        tail_end_rpm=600,
        profile_type="linear",
        duration_sec=5.0,
    )

    assert mayak.start_test_calls == [(100, 500, 200, 600, "linear", 5.0)]


def test_stop_mayak_test_stops_both_spindles() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    orch.stop_mayak_test()

    assert mayak.stop_test_calls == 1


def test_mayak_timeline_logs_include_scenario_id_start_stop_abort() -> None:
    bus = EventBus()
    logs: list[LogEvent] = []
    bus.subscribe(LogEvent, lambda e: logs.append(e))
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    orch.start_mayak_test(
        head_start_rpm=100,
        head_end_rpm=500,
        tail_start_rpm=200,
        tail_end_rpm=600,
        profile_type="linear",
        duration_sec=5.0,
    )
    orch.stop_mayak_test()
    orch.start_mayak_test(
        head_start_rpm=120,
        head_end_rpm=520,
        tail_start_rpm=220,
        tail_end_rpm=620,
        profile_type="linear",
        duration_sec=6.0,
    )
    orch.emergency_stop()

    scenario_logs = [e for e in logs if e.source == "orchestrator" and e.code in ("SCENARIO_ID", "MAYAK_TEST_START", "MAYAK_TEST_STOP", "MAYAK_TEST_ABORT")]
    assert any(e.code == "SCENARIO_ID" for e in scenario_logs)
    assert any(e.code == "MAYAK_TEST_START" for e in scenario_logs)
    assert any(e.code == "MAYAK_TEST_STOP" for e in scenario_logs)
    assert any(e.code == "MAYAK_TEST_ABORT" for e in scenario_logs)
    assert all("scenario_id=" in e.message for e in scenario_logs)


def test_prepare_then_start_uses_prepared_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr(
        orch,
        "_find_latest_trajectory_artifact",
        lambda: {"run_dir": "R", "trajectory_csv": "T", "diagnostics_csv": "D"},
    )
    captured_prepared: dict[str, Any] = {}

    def _capture_manifest(prepared: dict[str, Any]) -> Path:
        captured_prepared.clear()
        captured_prepared.update(prepared)
        return Path("outputs/scenarios") / str(prepared.get("scenario_id", "x")) / "scenario_manifest.json"

    monkeypatch.setattr(orch, "_write_scenario_manifest", _capture_manifest)

    sid = orch.prepare_mayak_test(
        head_start_rpm=111,
        head_end_rpm=511,
        tail_start_rpm=222,
        tail_end_rpm=622,
        profile_type="linear",
        duration_sec=7.0,
        sdr_options={
            "gps_sdr_sim": {"nav": "data/ephemerides/custom.nav", "static_sec": 12.0},
            "pluto_player": {"rf_bw_mhz": 4.5, "tx_atten_db": -10.0},
        },
    )
    assert sid.startswith("scn_")
    assert captured_prepared.get("sdr_options", {}).get("gps_sdr_sim", {}).get("nav") == "data/ephemerides/custom.nav"
    assert captured_prepared.get("sdr_options", {}).get("pluto_player", {}).get("rf_bw_mhz") == 4.5

    orch.start_prepared_mayak_test()
    assert mayak.start_test_calls == [(111, 511, 222, 622, "linear", 7.0)]


def test_start_prepared_requires_prepare_first() -> None:
    bus = EventBus()
    mayak = _MayakLikeService()
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    with pytest.raises(RuntimeError):
        orch.start_prepared_mayak_test()
