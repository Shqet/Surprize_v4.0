from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.event_bus import EventBus
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.states import OrchestratorPhase
from app.orchestrator.session_runtime import SessionStatus


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


@dataclass
class _CameraService:
    ready: bool = False

    def is_ready(self) -> bool:
        return bool(self.ready)


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
    monkeypatch.setattr(orch, "_check_sdr_readiness", lambda _prepared: (True, ""))

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
    monkeypatch.setattr(orch, "_check_sdr_readiness", lambda _prepared: (True, ""))

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


def test_readiness_warns_when_camera_services_not_connected(tmp_path: Path, monkeypatch) -> None:
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
    cam_v = _CameraService(ready=False)
    cam_t = _CameraService(ready=False)
    sm = _FakeServiceManager({"mayak_spindle": mayak, "video_visible": cam_v, "video_thermal": cam_t})
    orch = Orchestrator(bus, sm)
    monkeypatch.setattr(orch, "_check_sdr_readiness", lambda _prepared: (True, ""))

    monkeypatch.setattr(
        orch,
        "_find_latest_trajectory_artifact",
        lambda: {"run_dir": str(traj_dir), "trajectory_csv": str(traj), "diagnostics_csv": str(diag)},
    )

    orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )
    report = orch.check_readiness()
    assert report["ready_to_start"] is True
    assert "video_visible_not_ready" in report["warnings"]
    assert "video_thermal_not_ready" in report["warnings"]


def test_readiness_blocks_when_sdr_probe_fails(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(orch, "_check_sdr_readiness", lambda _prepared: (False, "pluto_probe_failed"))

    orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )
    report = orch.check_readiness()
    assert report["ready_to_start"] is False
    assert "sdr_not_ready" in report["blocking_errors"]


def test_start_stop_test_session_writes_manifest_and_events(tmp_path: Path, monkeypatch) -> None:
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

    orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )

    monkeypatch.setattr(
        orch,
        "_build_session_gps_tx_config",
        lambda _prepared: {"pluto_exe": "PlutoPlayer.exe", "iq_path": "dummy.bin", "tx_atten_db": -20.0, "rf_bw_mhz": 3.0},
    )

    class _FakeGpsTxRunner:
        def start(self, session_ctx) -> None:
            session_ctx.handles["gps_tx_proc"] = object()

        def stop(self, session_ctx) -> None:
            session_ctx.handles.pop("gps_tx_proc", None)

        def describe(self, _session_ctx) -> dict[str, object]:
            return {"state": "running", "pid": 1234, "exit_code": None}

    orch._gps_tx_runner = _FakeGpsTxRunner()
    orch._trajectory_ticker = type(
        "_FakeTrajectoryTicker",
        (),
        {
            "start": staticmethod(lambda session_ctx: session_ctx.handles.__setitem__("trajectory_ticker", object())),
            "stop": staticmethod(lambda session_ctx: session_ctx.handles.pop("trajectory_ticker", None)),
            "describe": staticmethod(lambda _session_ctx: {"state": "running"}),
        },
    )()
    orch._video_recorder = type(
        "_FakeVideoRecorder",
        (),
        {
            "record_for_session": staticmethod(lambda session_ctx: session_ctx.handles.__setitem__("video_recording", {})),
            "stop_record_for_session": staticmethod(lambda session_ctx: session_ctx.handles.pop("video_recording", None)),
            "describe": staticmethod(
                lambda _session_ctx: {
                    "state": "running",
                    "degraded": False,
                    "channels": [{"channel": "visible", "frames_written": 1, "degraded": False}],
                }
            ),
        },
    )()

    started = orch.start_test_session()
    assert orch.phase == OrchestratorPhase.TEST_RUNNING
    session_id = started["session_id"]
    manifest_path = Path(started["manifest"])
    events_path = Path(started["events"])
    timeline_path = Path(started["trajectory_timeline"])
    assert session_id.startswith("sess_")
    assert manifest_path.exists()
    assert events_path.exists()
    assert timeline_path.exists()

    manifest_running = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_running["status"] == "RUNNING"
    assert manifest_running["session_id"] == session_id
    assert manifest_running["scenario_id"].startswith("scn_")
    assert manifest_running["paths"]["events_log"] == str(events_path)
    assert manifest_running["paths"]["manifest"] == str(manifest_path)
    assert manifest_running["paths"]["trajectory_timeline"] == str(timeline_path)
    assert "gps_tx_cfg" in manifest_running["handles"]
    assert "prepared_scenario" in manifest_running["handles"]
    assert "trajectory_timeline_meta" in manifest_running["handles"]

    tl_lines = [x for x in timeline_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(tl_lines) >= 2
    assert tl_lines[0] == "t_rel_sec,x,y,z,speed"

    rt = orch.get_test_session_runtime_state()
    assert rt["active"] is True
    assert rt["status"] == "RUNNING"
    assert rt["session_id"] == session_id
    assert rt["video"]["state"] == "running"
    assert rt["gps_tx"]["state"] == "running"

    stopped = orch.stop_test_session()
    assert stopped["session_id"] == session_id
    assert orch.phase == OrchestratorPhase.PREPARED

    manifest_stopped = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_stopped["status"] == "STOPPED"
    assert isinstance(manifest_stopped.get("duration_sec"), (int, float))
    assert float(manifest_stopped["duration_sec"]) >= 0.0
    assert manifest_stopped["t1_unix"] is not None

    lines = [x for x in events_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) >= 2
    first = json.loads(lines[0])
    last = json.loads(lines[-1])
    assert first.get("event") == "SESSION_START"
    assert last.get("event") == "SESSION_STOP"
    assert first.get("session_id") == session_id
    assert last.get("session_id") == session_id


def test_start_test_session_requires_prepared_scenario() -> None:
    bus = EventBus()
    sm = _FakeServiceManager({})
    orch = Orchestrator(bus, sm)

    try:
        orch.start_test_session()
        assert False, "expected RuntimeError"
    except RuntimeError as ex:
        assert str(ex) == "scenario_not_prepared"


def test_get_test_session_runtime_state_is_inactive_by_default() -> None:
    bus = EventBus()
    sm = _FakeServiceManager({})
    orch = Orchestrator(bus, sm)

    rt = orch.get_test_session_runtime_state()
    assert rt["active"] is False
    assert rt["status"] == "STOPPED"
    assert rt["video"]["state"] == "not_running"
    assert rt["gps_tx"]["state"] == "not_running"


def test_start_test_session_marks_error_when_gps_tx_fails(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(
        orch,
        "_build_session_gps_tx_config",
        lambda _prepared: {"pluto_exe": "PlutoPlayer.exe", "iq_path": "dummy.bin", "tx_atten_db": -20.0, "rf_bw_mhz": 3.0},
    )

    rollback = {"video_stop": 0, "ticker_stop": 0}

    class _FailingGpsTxRunner:
        def start(self, _session_ctx) -> None:
            raise RuntimeError("boom")

        def stop(self, _session_ctx) -> None:
            return None

    orch._gps_tx_runner = _FailingGpsTxRunner()
    orch._trajectory_ticker = type(
        "_FakeTrajectoryTicker",
        (),
        {
            "start": staticmethod(lambda session_ctx: session_ctx.handles.__setitem__("trajectory_ticker", object())),
            "stop": staticmethod(
                lambda session_ctx: (
                    rollback.__setitem__("ticker_stop", int(rollback["ticker_stop"]) + 1),
                    session_ctx.handles.pop("trajectory_ticker", None),
                )
            ),
        },
    )()
    orch._video_recorder = type(
        "_FakeVideoRecorder",
        (),
        {
            "record_for_session": staticmethod(lambda session_ctx: session_ctx.handles.__setitem__("video_recording", {})),
            "stop_record_for_session": staticmethod(
                lambda session_ctx: (
                    rollback.__setitem__("video_stop", int(rollback["video_stop"]) + 1),
                    session_ctx.handles.pop("video_recording", None),
                )
            ),
        },
    )()

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
        orch.start_test_session()
        assert False, "expected RuntimeError"
    except RuntimeError as ex:
        assert "boom" in str(ex) or "RuntimeError" in str(ex)

    # Not running after failed start.
    assert orch._active_test_session is None

    sessions_root = tmp_path / "outputs" / "sessions"
    manifests = sorted(sessions_root.glob("*/session_manifest.json"))
    assert manifests, "manifest should exist for failed session start"
    manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))
    assert manifest["status"] == SessionStatus.ERROR.value
    assert rollback["video_stop"] == 1
    assert rollback["ticker_stop"] == 1


def test_start_test_session_fails_when_trajectory_time_is_not_monotonic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    nav = tmp_path / "brdc.nav"
    nav.write_text("dummy", encoding="utf-8")
    traj_dir = tmp_path / "ballistics" / "run1"
    traj_dir.mkdir(parents=True, exist_ok=True)
    traj = traj_dir / "trajectory.csv"
    traj.write_text("t,X,Y,Z\n0.0,0,0,0\n1.0,1,0,0\n0.5,2,0,0\n", encoding="utf-8")
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
    monkeypatch.setattr(
        orch,
        "_build_session_gps_tx_config",
        lambda _prepared: {"pluto_exe": "PlutoPlayer.exe", "iq_path": "dummy.bin", "tx_atten_db": -20.0, "rf_bw_mhz": 3.0},
    )
    orch._video_recorder = type(
        "_FakeVideoRecorder",
        (),
        {
            "record_for_session": staticmethod(lambda session_ctx: session_ctx.handles.__setitem__("video_recording", {})),
            "stop_record_for_session": staticmethod(lambda session_ctx: session_ctx.handles.pop("video_recording", None)),
        },
    )()
    orch._trajectory_ticker = type(
        "_FakeTrajectoryTicker",
        (),
        {"start": staticmethod(lambda _session_ctx: None), "stop": staticmethod(lambda _session_ctx: None)},
    )()
    orch._gps_tx_runner = type(
        "_FakeGpsTxRunner",
        (),
        {"start": staticmethod(lambda _session_ctx: None), "stop": staticmethod(lambda _session_ctx: None)},
    )()

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
        orch.start_test_session()
        assert False, "expected ValueError due to timeline validation"
    except ValueError:
        pass

    sessions_root = tmp_path / "outputs" / "sessions"
    manifests = sorted(sessions_root.glob("*/session_manifest.json"))
    assert manifests, "manifest should exist for failed session start"
    manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))
    assert manifest["status"] == SessionStatus.ERROR.value


def test_start_test_session_flow_blocks_when_readiness_not_ready(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(orch, "_check_sdr_readiness", lambda _prepared: (False, "probe_fail"))

    orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )

    out = orch.start_test_session_flow()
    assert out["started"] is False
    readiness = out["readiness"]
    assert readiness["ready_to_start"] is False
    assert "sdr_not_ready" in readiness["blocking_errors"]


def test_session_auto_stops_after_gps_finish(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    nav = tmp_path / "brdc.nav"
    nav.write_text("dummy", encoding="utf-8")
    traj_dir = tmp_path / "ballistics" / "run1"
    traj_dir.mkdir(parents=True, exist_ok=True)
    traj = traj_dir / "trajectory.csv"
    traj.write_text("t,X,Y,Z\n0.0,0,0,0\n0.2,1,0,0\n", encoding="utf-8")
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
    monkeypatch.setattr(
        orch,
        "_build_session_gps_tx_config",
        lambda _prepared: {"pluto_exe": "PlutoPlayer.exe", "iq_path": "dummy.bin", "tx_atten_db": -20.0, "rf_bw_mhz": 3.0},
    )
    orch._auto_stop_after_gps_sec = 0.0

    class _GpsExitedRunner:
        def start(self, session_ctx) -> None:
            session_ctx.handles["gps_tx_proc"] = object()

        def stop(self, session_ctx) -> None:
            session_ctx.handles.pop("gps_tx_proc", None)

        def describe(self, _session_ctx) -> dict[str, object]:
            return {"state": "exited", "pid": 123, "exit_code": 0}

    orch._gps_tx_runner = _GpsExitedRunner()
    orch._trajectory_ticker = type(
        "_FakeTrajectoryTicker",
        (),
        {
            "start": staticmethod(lambda session_ctx: session_ctx.handles.__setitem__("trajectory_ticker", object())),
            "stop": staticmethod(lambda session_ctx: session_ctx.handles.pop("trajectory_ticker", None)),
            "describe": staticmethod(lambda _session_ctx: {"state": "running"}),
        },
    )()
    orch._video_recorder = type(
        "_FakeVideoRecorder",
        (),
        {
            "record_for_session": staticmethod(lambda session_ctx: session_ctx.handles.__setitem__("video_recording", {})),
            "stop_record_for_session": staticmethod(lambda session_ctx: session_ctx.handles.pop("video_recording", None)),
            "describe": staticmethod(lambda _session_ctx: {"state": "running", "degraded": False, "channels": []}),
        },
    )()

    orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=5.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )
    started = orch.start_test_session()
    manifest_path = Path(started["manifest"])

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if orch._active_test_session is None:
            break
        time.sleep(0.02)
    assert orch._active_test_session is None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "STOPPED"


def test_pluto_probe_fast_exit_rc0_is_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.returncode = 0

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return ("", "")

        def terminate(self):
            return None

        def kill(self):
            return None

    bus = EventBus()
    sm = _FakeServiceManager({})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr("app.orchestrator.orchestrator.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("app.orchestrator.orchestrator.time.sleep", lambda _sec: None)

    ok, detail = orch._run_pluto_probe(
        pluto_exe="PlutoPlayer.exe",
        iq_path=iq,
        tx_atten_db=-20.0,
        rf_bw_mhz=3.0,
    )

    assert ok is True
    assert detail == ""


def test_pluto_probe_detects_missing_sdr_from_iio_context_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.returncode = 1

        def poll(self):
            return 1

        def communicate(self, timeout=None):
            return ("", "Failed creating IIO context: No such file or directory (2)")

        def terminate(self):
            return None

        def kill(self):
            return None

    bus = EventBus()
    sm = _FakeServiceManager({})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr("app.orchestrator.orchestrator.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("app.orchestrator.orchestrator.time.sleep", lambda _sec: None)

    ok, detail = orch._run_pluto_probe(
        pluto_exe="PlutoPlayer.exe",
        iq_path=iq,
        tx_atten_db=-20.0,
        rf_bw_mhz=3.0,
    )

    assert ok is False
    assert detail == "pluto_probe_failed:failed_creating_iio_context"


def test_pluto_probe_detects_success_from_pluto_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.returncode = 0

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return (
                "* Found 192.168.2.1 (FISH Ball PlutoSDR Rev.A)\n* Transmit starts...\nDone.\n",
                "",
            )

        def terminate(self):
            return None

        def kill(self):
            return None

    bus = EventBus()
    sm = _FakeServiceManager({})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr("app.orchestrator.orchestrator.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("app.orchestrator.orchestrator.time.sleep", lambda _sec: None)

    ok, detail = orch._run_pluto_probe(
        pluto_exe="PlutoPlayer.exe",
        iq_path=iq,
        tx_atten_db=-20.0,
        rf_bw_mhz=3.0,
    )

    assert ok is True
    assert detail == ""


def test_pluto_probe_allows_delayed_success_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self._poll_calls = 0

        def poll(self):
            self._poll_calls += 1
            if self._poll_calls < 6:
                return None
            return 0

        def communicate(self, timeout=None):
            return (
                "* Acquiring IIO context\n* Found 192.168.2.1 (PlutoSDR)\n* Transmit starts...\nDone.\n",
                "",
            )

        def terminate(self):
            return None

        def kill(self):
            return None

    bus = EventBus()
    sm = _FakeServiceManager({})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr("app.orchestrator.orchestrator.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("app.orchestrator.orchestrator.time.sleep", lambda _sec: None)

    ok, detail = orch._run_pluto_probe(
        pluto_exe="PlutoPlayer.exe",
        iq_path=iq,
        tx_atten_db=-20.0,
        rf_bw_mhz=3.0,
    )

    assert ok is True
    assert detail == ""


def test_pluto_probe_uses_absolute_iq_path_and_pluto_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    pluto_dir = tmp_path / "bin" / "pluto"
    pluto_dir.mkdir(parents=True, exist_ok=True)
    pluto_exe = pluto_dir / "PlutoPlayer.exe"
    pluto_exe.write_bytes(b"MZ")

    iq = tmp_path / "outputs" / "gps_sdr_sim" / "probe_cache" / "probe_iq.bin"
    iq.parent.mkdir(parents=True, exist_ok=True)
    iq.write_bytes(b"\x00\x01")

    captured: dict[str, Any] = {}

    class _FakePopen:
        def __init__(self, cmd, cwd=None, **kwargs) -> None:
            captured["cmd"] = list(cmd)
            captured["cwd"] = cwd

        def poll(self):
            return 0

        def communicate(self, timeout=None):
            return ("* Found 192.168.2.1 (PlutoSDR)\nDone.\n", "")

        def terminate(self):
            return None

        def kill(self):
            return None

    bus = EventBus()
    sm = _FakeServiceManager({})
    orch = Orchestrator(bus, sm)

    monkeypatch.setattr("app.orchestrator.orchestrator.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("app.orchestrator.orchestrator.time.sleep", lambda _sec: None)

    ok, detail = orch._run_pluto_probe(
        pluto_exe=str(pluto_exe),
        iq_path=iq,
        tx_atten_db=-20.0,
        rf_bw_mhz=3.0,
    )

    assert ok is True
    assert detail == ""
    cmd = captured.get("cmd", [])
    assert len(cmd) >= 3
    assert cmd[1] == "-t"
    assert cmd[2] == str(iq.resolve())
    assert captured.get("cwd") == str(pluto_exe.resolve().parent)


def test_check_sdr_readiness_retries_with_default_pluto_host_on_iio_error(tmp_path: Path, monkeypatch) -> None:
    nav = tmp_path / "brdc.nav"
    nav.write_text("dummy", encoding="utf-8")
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")

    bus = EventBus()
    mayak = _MayakService(ready=True)
    sm = _FakeServiceManager({"mayak_spindle": mayak})
    orch = Orchestrator(bus, sm)

    prepared = {
        "sdr_options": {
            "gps_sdr_sim": {"nav": str(nav), "origin_lat": 55.0, "origin_lon": 37.0, "origin_h": 100.0},
            "pluto_player": {"rf_bw_mhz": 3.0, "tx_atten_db": -20.0},
        },
        "services_snapshot": {"gps_sdr_sim": {}},
    }

    monkeypatch.setattr(orch, "_resolve_gps_sdr_sim_executable", lambda *_args, **_kwargs: "gps-sdr-sim.exe")
    monkeypatch.setattr(orch, "_resolve_pluto_player_executable", lambda *_args, **_kwargs: "PlutoPlayer.exe")
    monkeypatch.setattr(orch, "_ensure_sdr_probe_iq", lambda **_kwargs: iq)

    calls: list[str | None] = []

    def _fake_run_pluto_probe(**kwargs):
        host = kwargs.get("host")
        calls.append(host)
        if len(calls) == 1:
            return False, "pluto_probe_failed:failed_creating_iio_context"
        if host == "192.168.2.1":
            return True, ""
        return False, "pluto_probe_inconclusive"

    monkeypatch.setattr(orch, "_run_pluto_probe", _fake_run_pluto_probe)

    ok, detail = orch._check_sdr_readiness(prepared)
    assert ok is True
    assert detail == ""
    assert calls == [None, "192.168.2.1"]
