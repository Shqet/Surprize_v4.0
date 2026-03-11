from __future__ import annotations

from app.ui.main_window import MainWindow, ReplayState


class _ReplayHarness:
    def __init__(self) -> None:
        self._replay_state = ReplayState.IDLE
        self._replay_timeline = [(0.0, 0.0, 0.0, 0.0, 0.0), (1.0, 1.0, 0.0, 0.0, 1.0)]
        self._replay_t_sec = 0.0
        self._replay_play_started_mono = 0.0
        self._replay_play_started_t_sec = 0.0
        self._replay_rate = 1.0
        self._btn_replay_play_m = None
        self._replay_slider_m = None
        self._errors: list[str] = []

    def _log_error(self, code: str, message: str) -> None:
        self._errors.append(f"{code}:{message}")

    def _render_replay_state(self) -> None:
        return None

    def _set_replay_state(self, new_state: ReplayState) -> None:
        MainWindow._set_replay_state(self, new_state)

    def seek(self, t: float) -> None:
        MainWindow.seek(self, t)

    def _apply_replay_t_sec(self, t_sec: float, *, from_slider: bool) -> None:
        MainWindow._apply_replay_t_sec(self, t_sec, from_slider=from_slider)

    def pause(self) -> None:
        MainWindow.pause(self)


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
