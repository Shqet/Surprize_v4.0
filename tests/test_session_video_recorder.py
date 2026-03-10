from __future__ import annotations

import time
from pathlib import Path

from app.core.event_bus import EventBus
from app.orchestrator.session_runtime import SessionRuntime, SessionStatus
from app.orchestrator.session_video_recorder import SessionVideoRecorder


class _FakeVideoService:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def save_preview(self, path: str) -> bool:
        if self._fail:
            return False
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake-preview")
        return True


def _make_runtime(tmp_path: Path) -> SessionRuntime:
    out_dir = tmp_path / "outputs" / "sessions" / "sess_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    events = out_dir / "events.log"
    manifest = out_dir / "session_manifest.json"
    return SessionRuntime(
        session_id="sess_test",
        scenario_id="scn_test",
        t0_unix=time.time(),
        t0_monotonic=time.monotonic(),
        status=SessionStatus.STARTING,
        paths={"out_dir": str(out_dir), "events_log": str(events), "manifest": str(manifest)},
    )


def test_session_video_recorder_writes_artifacts_for_both_channels(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    bus = EventBus()
    services = {"video_visible": _FakeVideoService(), "video_thermal": _FakeVideoService()}
    recorder = SessionVideoRecorder(bus, lambda: services, frame_period_sec=0.05, degraded_failures=3)

    recorder.record_for_session(runtime)
    time.sleep(0.3)
    recorder.stop_record_for_session(runtime)

    video_dir = Path(runtime.paths["out_dir"]) / "video"
    assert (video_dir / "visible.mp4").exists()
    assert (video_dir / "thermal.mp4").exists()
    vis_csv = video_dir / "visible_frames.csv"
    thr_csv = video_dir / "thermal_frames.csv"
    assert vis_csv.exists()
    assert thr_csv.exists()
    assert len([x for x in vis_csv.read_text(encoding="utf-8").splitlines() if x.strip()]) >= 2
    assert len([x for x in thr_csv.read_text(encoding="utf-8").splitlines() if x.strip()]) >= 2


def test_session_video_recorder_degraded_when_one_channel_fails(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    bus = EventBus()
    services = {"video_visible": _FakeVideoService(), "video_thermal": _FakeVideoService(fail=True)}
    recorder = SessionVideoRecorder(bus, lambda: services, frame_period_sec=0.05, degraded_failures=2)

    recorder.record_for_session(runtime)
    time.sleep(0.3)
    recorder.stop_record_for_session(runtime)

    video_dir = Path(runtime.paths["out_dir"]) / "video"
    vis_lines = [x for x in (video_dir / "visible_frames.csv").read_text(encoding="utf-8").splitlines() if x.strip()]
    thr_lines = [x for x in (video_dir / "thermal_frames.csv").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(vis_lines) >= 2
    assert len(thr_lines) == 1
