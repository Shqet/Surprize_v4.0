from __future__ import annotations

import pytest

from app.ui.main_window import GraphSyncAdapter, MainWindow, ReplayState


class _ReplayHarness:
    def __init__(self) -> None:
        self._replay_state = ReplayState.IDLE
        self._replay_timeline = [(0.0, 0.0, 0.0, 0.0, 0.0), (1.0, 1.0, 0.0, 0.0, 1.0)]
        self._replay_timeline_times = [0.0, 1.0]
        self._replay_visible_frames = []
        self._replay_visible_times = []
        self._replay_thermal_frames = []
        self._replay_thermal_times = []
        self._replay_t_min_sec = 0.0
        self._replay_t_max_sec = 1.0
        self._replay_duration_sec = 1.0
        self._replay_t_sec = 0.0
        self._replay_play_started_mono = 0.0
        self._replay_play_started_t_sec = 0.0
        self._replay_rate = 1.0
        self._btn_replay_play_m = None
        self._btn_replay_stop_m = None
        self._btn_replay_back_m = None
        self._btn_replay_fwd_m = None
        self._btn_replay_step_back_m = None
        self._btn_replay_step_fwd_m = None
        self._replay_slider_m = None
        self._replay_t_spin_m = None
        self._replay_rate_combo_m = None
        self._errors: list[str] = []
        self._published_events: list[str] = []

    def _log_error(self, code: str, message: str) -> None:
        self._errors.append(f"{code}:{message}")

    def _render_replay_state(self) -> None:
        return None

    def _sync_replay_controls(self) -> None:
        return None

    def _publish_graph_sync_event(self, *, event: str, payload=None) -> None:
        self._published_events.append(str(event))

    def _publish_graph_sync_time(self, *, source: str) -> None:
        return None

    def _set_replay_state(self, new_state: ReplayState) -> None:
        MainWindow._set_replay_state(self, new_state)

    def seek(self, t: float) -> None:
        MainWindow.seek(self, t)

    def _apply_replay_t_sec(self, t_sec: float, *, from_slider: bool) -> None:
        MainWindow._apply_replay_t_sec(self, t_sec, from_slider=from_slider)

    def pause(self) -> None:
        MainWindow.pause(self)

    def _current_playback_t_sec(self) -> float:
        return MainWindow._current_playback_t_sec(self)


def test_replay_state_invalid_transition_sets_error() -> None:
    h = _ReplayHarness()
    MainWindow._set_replay_state(h, ReplayState.EOF)
    assert h._replay_state == ReplayState.ERROR
    assert h._errors


def test_replay_state_play_pause_flow_is_deterministic() -> None:
    h = _ReplayHarness()

    MainWindow._set_replay_state(h, ReplayState.LOADED)
    assert h._replay_state == ReplayState.LOADED

    MainWindow.play(h)
    assert h._replay_state == ReplayState.PLAYING

    MainWindow.pause(h)
    assert h._replay_state == ReplayState.PAUSED

    MainWindow.seek(h, 0.6)
    assert abs(h._replay_t_sec - 0.6) < 1e-9
    assert h._replay_state == ReplayState.PAUSED

    MainWindow.step(h, 0.5)
    assert abs(h._replay_t_sec - 1.0) < 1e-9
    assert h._replay_state == ReplayState.PAUSED


def test_replay_build_indices_normalizes_time_bounds() -> None:
    h = _ReplayHarness()
    h._replay_timeline = [(2.0, 0.0, 0.0, 0.0, 0.0), (5.5, 0.0, 0.0, 0.0, 0.0)]
    h._replay_visible_frames = [(2.1, 1), (4.9, 2)]
    h._replay_thermal_frames = [(2.0, 10), (5.4, 11)]

    MainWindow._replay_build_indices(h)

    assert h._replay_timeline_times == [2.0, 5.5]
    assert h._replay_visible_times == [2.1, 4.9]
    assert h._replay_thermal_times == [2.0, 5.4]
    assert abs(h._replay_t_min_sec - 2.0) < 1e-9
    assert abs(h._replay_t_max_sec - 5.5) < 1e-9
    assert abs(h._replay_duration_sec - 3.5) < 1e-9


def test_replay_rate_clamped_to_supported_range() -> None:
    h = _ReplayHarness()
    MainWindow.set_rate(h, 0.01)
    assert abs(h._replay_rate - 0.25) < 1e-9
    MainWindow.set_rate(h, 99.0)
    assert abs(h._replay_rate - 4.0) < 1e-9


def test_replay_pause_resume_does_not_accelerate_clock(monkeypatch) -> None:
    now = {"t": 100.0}
    monkeypatch.setattr("app.ui.main_window.time.monotonic", lambda: float(now["t"]))

    h = _ReplayHarness()
    h._replay_timeline = [(0.0, 0.0, 0.0, 0.0, 0.0), (10.0, 0.0, 0.0, 0.0, 0.0)]
    MainWindow._replay_build_indices(h)
    MainWindow._set_replay_state(h, ReplayState.LOADED)

    MainWindow.play(h)  # anchor at t=0, mono=100
    now["t"] = 102.0
    MainWindow._on_replay_timer_tick(h)
    assert abs(h._replay_t_sec - 2.0) < 1e-9

    now["t"] = 103.0
    MainWindow.pause(h)
    assert h._replay_state == ReplayState.PAUSED
    assert abs(h._replay_t_sec - 3.0) < 1e-9

    now["t"] = 110.0
    MainWindow.play(h)
    now["t"] = 111.0
    MainWindow._on_replay_timer_tick(h)
    assert abs(h._replay_t_sec - 4.0) < 1e-9
    assert h._replay_state == ReplayState.PLAYING


def test_replay_seek_from_eof_allows_replay_again() -> None:
    h = _ReplayHarness()
    h._replay_timeline = [(0.0, 0.0, 0.0, 0.0, 0.0), (5.0, 0.0, 0.0, 0.0, 0.0)]
    MainWindow._replay_build_indices(h)
    h._replay_t_sec = 5.0
    MainWindow._set_replay_state(h, ReplayState.LOADED)
    MainWindow._set_replay_state(h, ReplayState.PLAYING)
    MainWindow._set_replay_state(h, ReplayState.EOF)

    MainWindow.seek(h, 2.0)
    assert h._replay_state == ReplayState.PAUSED
    MainWindow.play(h)
    assert h._replay_state == ReplayState.PLAYING


def test_replay_channel_status_ok() -> None:
    st, reason = MainWindow._replay_channel_status(
        t_master=10.0,
        frame_info=(10.2, 100),
        has_stream=True,
        has_video=True,
    )
    assert st == "OK"
    assert "норме" in reason


def test_replay_channel_status_gap_and_na() -> None:
    st1, _ = MainWindow._replay_channel_status(
        t_master=10.0,
        frame_info=(12.0, 100),
        has_stream=True,
        has_video=True,
    )
    assert st1 == "GAP"

    st2, _ = MainWindow._replay_channel_status(
        t_master=10.0,
        frame_info=(10.0, 100),
        has_stream=True,
        has_video=False,
    )
    assert st2 == "N/A"

    st3, _ = MainWindow._replay_channel_status(
        t_master=10.0,
        frame_info=None,
        has_stream=False,
        has_video=False,
    )
    assert st3 == "N/A"


def test_format_replay_3d_overlay_contains_time_and_index() -> None:
    text = MainWindow._format_replay_3d_overlay(
        t_sec=12.3456,
        idx=4,
        pt=(12.3, 100.0, 200.0, 50.0, 88.5),
        total=120,
    )
    assert "t=12.346" in text
    assert "точка=5/120" in text
    assert "x=100.0" in text


def test_graph_sync_adapter_publishes_time_and_events() -> None:
    adapter = GraphSyncAdapter()
    seen_time = []
    seen_event = []

    adapter.subscribe_time(lambda t, state, rate, source: seen_time.append((t, state, rate, source)))
    adapter.subscribe_event(lambda e, t, state, rate, payload: seen_event.append((e, t, state, rate, payload)))

    adapter.publish_time(t_sec=1.25, state=ReplayState.PLAYING, rate=2.0, source="runtime")
    adapter.publish_event(
        event="seek",
        t_sec=1.25,
        state=ReplayState.PLAYING,
        rate=2.0,
        payload={"source": "slider"},
    )

    assert len(seen_time) == 1
    assert seen_time[0][0] == 1.25
    assert seen_time[0][1] == ReplayState.PLAYING
    assert seen_time[0][3] == "runtime"
    assert len(seen_event) == 1
    assert seen_event[0][0] == "seek"
    assert seen_event[0][4]["source"] == "slider"


def test_replay_seek_and_step_clamp_to_time_bounds() -> None:
    h = _ReplayHarness()
    h._replay_timeline = [(2.0, 0.0, 0.0, 0.0, 0.0), (5.0, 0.0, 0.0, 0.0, 0.0)]
    MainWindow._replay_build_indices(h)
    MainWindow._set_replay_state(h, ReplayState.LOADED)

    MainWindow.seek(h, -100.0)
    assert abs(h._replay_t_sec - 2.0) < 1e-9

    MainWindow.step(h, 100.0)
    assert abs(h._replay_t_sec - 5.0) < 1e-9


def test_replay_timer_moves_to_eof(monkeypatch) -> None:
    now = {"t": 50.0}
    monkeypatch.setattr("app.ui.main_window.time.monotonic", lambda: float(now["t"]))

    h = _ReplayHarness()
    h._replay_timeline = [(0.0, 0.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0, 0.0)]
    MainWindow._replay_build_indices(h)
    MainWindow._set_replay_state(h, ReplayState.LOADED)
    h._replay_t_sec = 0.0
    MainWindow.play(h)

    now["t"] = 50.5
    MainWindow._on_replay_timer_tick(h)
    assert h._replay_state in {ReplayState.PLAYING, ReplayState.EOF}

    now["t"] = 51.2
    MainWindow._on_replay_timer_tick(h)
    assert h._replay_state == ReplayState.EOF
    assert abs(h._replay_t_sec - 1.0) < 1e-9


@pytest.mark.smoke
def test_replay_smoke_sequence_load_play_seek_pause_step_stop() -> None:
    h = _ReplayHarness()
    h._replay_timeline = [
        (0.0, 0.0, 0.0, 0.0, 0.0),
        (1.0, 1.0, 0.0, 0.0, 1.0),
        (2.0, 2.0, 0.0, 0.0, 1.0),
    ]
    MainWindow._replay_build_indices(h)
    MainWindow._set_replay_state(h, ReplayState.LOADED)

    MainWindow.play(h)
    assert h._replay_state == ReplayState.PLAYING

    MainWindow.seek(h, 1.4)
    assert 0.0 <= h._replay_t_sec <= 2.0

    MainWindow.pause(h)
    assert h._replay_state == ReplayState.PAUSED

    MainWindow.step(h, -1.0)
    assert 0.0 <= h._replay_t_sec <= 2.0

    MainWindow.stop(h)
    assert abs(h._replay_t_sec - 0.0) < 1e-9
    assert h._replay_state == ReplayState.LOADED
    assert {"play", "seek", "pause", "stop"}.issubset(set(h._published_events))


@pytest.mark.smoke
def test_replay_smoke_missing_channel_does_not_break_other_channel() -> None:
    # Visible channel missing entirely.
    vis_state = MainWindow._replay_channel_status(
        t_master=1.0,
        frame_info=None,
        has_stream=False,
        has_video=False,
    )
    # Thermal channel still valid.
    thr_state = MainWindow._replay_channel_status(
        t_master=1.0,
        frame_info=(1.1, 42),
        has_stream=True,
        has_video=True,
    )

    assert vis_state[0] == "N/A"
    assert thr_state[0] == "OK"
