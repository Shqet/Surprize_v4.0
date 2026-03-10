from __future__ import annotations

from pathlib import Path

import pytest

from app.ui.main_window import MainWindow


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeOrchestrator:
    def __init__(self) -> None:
        self._states = [
            {
                "active": True,
                "phase": "TEST_RUNNING",
                "session_id": "sess_ui_smoke",
                "status": "RUNNING",
                "elapsed_sec": 0.5,
                "video": {"state": "running", "degraded": False, "channels": []},
                "gps_tx": {"state": "running", "pid": 1234, "exit_code": None},
                "trajectory_ticker": {"state": "running"},
                "degraded": False,
                "error": False,
            },
            {
                "active": False,
                "phase": "PREPARED",
                "session_id": None,
                "status": "STOPPED",
                "elapsed_sec": 0.0,
                "video": {"state": "not_running", "degraded": False, "channels": []},
                "gps_tx": {"state": "not_running", "pid": None, "exit_code": None},
                "trajectory_ticker": {"state": "not_running"},
                "degraded": False,
                "error": False,
            },
        ]
        self._idx = 0

    def get_test_session_runtime_state(self):
        i = min(self._idx, len(self._states) - 1)
        out = dict(self._states[i])
        self._idx += 1
        return out


@pytest.mark.smoke
def test_ui_runtime_tick_notifies_when_session_finishes() -> None:
    notifications: list[tuple[str, str]] = []
    calls = {"start_anim": 0}

    class _Harness:
        pass

    h = _Harness()
    h._orch = _FakeOrchestrator()
    h._session_runtime_last = {}
    h._runtime_prev_active = False
    h._anim_without_test_enabled = False
    h._monitor_timer = _FakeTimer()
    h._last_finished_session_notified = None
    h._session_out_dir_hints = {"sess_ui_smoke": str(Path("outputs") / "sessions" / "sess_ui_smoke")}
    h._show_test_finished_notification = lambda sid, out: notifications.append((str(sid), str(out)))
    h._start_monitor_trajectory_animation = lambda force=False: calls.__setitem__("start_anim", calls["start_anim"] + 1)
    h._render_runtime_state = lambda _state: None
    h._refresh_monitor_flow_controls = lambda _state: None
    h._log_info = lambda _code, _msg: None

    MainWindow._on_runtime_ui_tick(h)  # RUNNING snapshot
    MainWindow._on_runtime_ui_tick(h)  # STOPPED snapshot

    assert calls["start_anim"] == 1
    assert h._monitor_timer.stopped is True
    assert notifications == [("sess_ui_smoke", str(Path("outputs") / "sessions" / "sess_ui_smoke"))]

