from __future__ import annotations

import json
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

    started = orch.start_test_session()
    assert orch.phase == OrchestratorPhase.TEST_RUNNING
    session_id = started["session_id"]
    manifest_path = Path(started["manifest"])
    events_path = Path(started["events"])
    assert session_id.startswith("sess_")
    assert manifest_path.exists()
    assert events_path.exists()

    manifest_running = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_running["status"] == "RUNNING"
    assert manifest_running["session_id"] == session_id
    assert manifest_running["scenario_id"].startswith("scn_")

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

    assert ok is False
    assert detail == "pluto_probe_inconclusive"


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
