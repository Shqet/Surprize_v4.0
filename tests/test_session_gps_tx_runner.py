from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from app.core.event_bus import EventBus
from app.orchestrator.session_gps_tx import SessionGpsTxRunner
from app.orchestrator.session_runtime import SessionRuntime, SessionStatus


def _make_runtime(tmp_path: Path, *, exe_path: Path, iq_path: Path) -> SessionRuntime:
    out_dir = tmp_path / "outputs" / "sessions" / "sess_tx"
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime = SessionRuntime(
        session_id="sess_tx",
        scenario_id="scn_tx",
        t0_unix=time.time(),
        t0_monotonic=time.monotonic(),
        status=SessionStatus.STARTING,
        paths={
            "out_dir": str(out_dir),
            "events_log": str(out_dir / "events.log"),
            "manifest": str(out_dir / "session_manifest.json"),
        },
    )
    runtime.handles["gps_tx_cfg"] = {
        "pluto_exe": str(exe_path),
        "iq_path": str(iq_path),
        "tx_atten_db": -20.0,
        "rf_bw_mhz": 3.0,
        "graceful_stop_timeout_sec": 0.1,
    }
    return runtime


def test_session_gps_tx_start_and_stop_graceful(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "PlutoPlayer.exe"
    exe.write_bytes(b"MZ")
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")
    rt = _make_runtime(tmp_path, exe_path=exe, iq_path=iq)

    class _FakePopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 123
            self._rc = None

        def poll(self):
            return self._rc

        def terminate(self):
            self._rc = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self._rc = -9

        def communicate(self, timeout=None):
            return ("ok", "")

    monkeypatch.setattr("app.orchestrator.session_gps_tx.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("app.orchestrator.session_gps_tx.time.sleep", lambda _sec: None)

    runner = SessionGpsTxRunner(EventBus())
    runner.start(rt)
    d_running = runner.describe(rt)
    assert d_running["state"] == "running"
    assert d_running["pid"] == 123
    assert rt.handles.get("gps_tx_proc") is not None

    runner.stop(rt)
    d_stopped = runner.describe(rt)
    assert d_stopped["state"] == "not_running"
    assert rt.handles.get("gps_tx_proc") is None
    assert Path(rt.paths["gps_stdout"]).exists()
    assert Path(rt.paths["gps_stderr"]).exists()


def test_session_gps_tx_start_early_exit_writes_logs_and_raises(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "PlutoPlayer.exe"
    exe.write_bytes(b"MZ")
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")
    rt = _make_runtime(tmp_path, exe_path=exe, iq_path=iq)

    class _EarlyExitPopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 456

        def poll(self):
            return 2

        def communicate(self, timeout=None):
            return ("", "Failed creating IIO context")

    monkeypatch.setattr("app.orchestrator.session_gps_tx.subprocess.Popen", _EarlyExitPopen)
    monkeypatch.setattr("app.orchestrator.session_gps_tx.time.sleep", lambda _sec: None)

    runner = SessionGpsTxRunner(EventBus())
    try:
        runner.start(rt)
        assert False, "expected early exit error"
    except RuntimeError as ex:
        assert "gps_tx_early_exit" in str(ex)

    assert rt.handles.get("gps_tx_proc") is None
    stderr_txt = Path(rt.paths["gps_stderr"]).read_text(encoding="utf-8")
    assert "IIO context" in stderr_txt
    events = [x for x in Path(rt.paths["events_log"]).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert events
    last = json.loads(events[-1])
    assert last["event"] == "SESSION_GPS_TX_ERROR"


def test_session_gps_tx_stop_kill_fallback(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "PlutoPlayer.exe"
    exe.write_bytes(b"MZ")
    iq = tmp_path / "probe_iq.bin"
    iq.write_bytes(b"\x00\x01")
    rt = _make_runtime(tmp_path, exe_path=exe, iq_path=iq)

    class _KillPopen:
        def __init__(self, *args, **kwargs) -> None:
            self.pid = 789
            self._rc = None
            self.killed = False
            self.wait_calls = 0

        def poll(self):
            return self._rc

        def terminate(self):
            return None

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.killed:
                return -9
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 0)

        def kill(self):
            self.killed = True
            self._rc = -9

        def communicate(self, timeout=None):
            return ("", "killed")

    monkeypatch.setattr("app.orchestrator.session_gps_tx.subprocess.Popen", _KillPopen)
    monkeypatch.setattr("app.orchestrator.session_gps_tx.time.sleep", lambda _sec: None)

    runner = SessionGpsTxRunner(EventBus())
    runner.start(rt)
    proc = rt.handles["gps_tx_proc"]
    runner.stop(rt)
    assert proc.killed is True
    assert rt.handles.get("gps_tx_proc") is None
