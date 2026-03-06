from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.event_bus import EventBus
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.states import OrchestratorPhase


class _FakeServiceManager:
    def __init__(self, services: dict[str, Any]) -> None:
        self._services = dict(services)

    def get_services(self) -> dict[str, Any]:
        return dict(self._services)


@dataclass
class _MayakService:
    start_test_calls: list[tuple[int, int, int, int, str, float]] = field(default_factory=list)
    stop_test_calls: int = 0
    ready: bool = True

    def is_ready(self) -> bool:
        return bool(self.ready)

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


def test_prepare_readiness_start_stop_flow_sets_phase(tmp_path: Path, monkeypatch) -> None:
    nav = tmp_path / "brdc.nav"
    nav.write_text("dummy", encoding="utf-8")
    traj_dir = tmp_path / "ballistics" / "run1"
    traj_dir.mkdir(parents=True, exist_ok=True)
    traj = traj_dir / "trajectory.csv"
    traj.write_text("t,X,Y,Z\n0,0,0,0\n", encoding="utf-8")
    diag = traj_dir / "diagnostics.csv"
    diag.write_text("t,V\n0,0\n", encoding="utf-8")

    bus = EventBus()
    mayak = _MayakService(ready=True)
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr(
        orch,
        "_find_latest_trajectory_artifact",
        lambda: {"run_dir": str(traj_dir), "trajectory_csv": str(traj), "diagnostics_csv": str(diag)},
    )

    sid = orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )
    assert sid.startswith("scn_")
    assert orch.phase == OrchestratorPhase.PREPARED

    report = orch.check_readiness()
    assert report["ready_to_start"] is True
    assert "pluto_input" in report["artifacts"]
    assert orch.phase == OrchestratorPhase.READY

    orch.start_test_flow()
    assert mayak.start_test_calls == [(100, 200, 300, 400, "linear", 5.0)]
    assert orch.phase == OrchestratorPhase.TEST_RUNNING

    orch.stop_test_flow()
    assert mayak.stop_test_calls == 1
    assert orch.phase == OrchestratorPhase.PREPARED


def test_readiness_fails_when_nav_or_traj_missing(monkeypatch) -> None:
    bus = EventBus()
    mayak = _MayakService(ready=True)
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr(orch, "_find_latest_trajectory_artifact", lambda: None)

    orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": "missing.nav", "static_sec": 0.0}},
    )
    report = orch.check_readiness()
    assert report["ready_to_start"] is False
    assert "trajectory_missing" in report["blocking_errors"]
    assert "gps_nav_missing" in report["blocking_errors"]


def test_generate_gps_preflight_reports_missing_exe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    nav = tmp_path / "brdc.nav"
    nav.write_text("dummy", encoding="utf-8")
    traj_dir = tmp_path / "ballistics" / "run1"
    traj_dir.mkdir(parents=True, exist_ok=True)
    traj = traj_dir / "trajectory.csv"
    traj.write_text("t,X,Y,Z\n0,0,0,0\n", encoding="utf-8")
    diag = traj_dir / "diagnostics.csv"
    diag.write_text("t,V\n0,0\n", encoding="utf-8")

    bus = EventBus()
    mayak = _MayakService(ready=True)
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr(
        orch,
        "_find_latest_trajectory_artifact",
        lambda: {"run_dir": str(traj_dir), "trajectory_csv": str(traj), "diagnostics_csv": str(diag)},
    )
    monkeypatch.setattr("app.orchestrator.orchestrator.shutil.which", lambda _name: None)

    orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )

    try:
        orch.generate_gps_signal_preflight()
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as ex:
        msg = str(ex)
        assert "gps_sdr_sim_exe_not_found" in msg
        assert "checked=" in msg
