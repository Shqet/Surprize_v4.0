from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.event_bus import EventBus
from app.orchestrator.orchestrator import Orchestrator


@dataclass
class _MayakStub:
    def is_ready(self) -> bool:
        return True

    def start_test(self, **kwargs: Any) -> None:
        return None


class _VideoServiceOk:
    def is_ready(self) -> bool:
        return True

    def save_preview(self, path: str) -> bool:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ok-preview")
        return True


class _VideoServiceDown:
    def is_ready(self) -> bool:
        return False

    def save_preview(self, path: str) -> bool:
        return False


class _ServiceMap:
    def __init__(self, services: dict[str, Any]) -> None:
        self._services = dict(services)

    def get_services(self) -> dict[str, Any]:
        return dict(self._services)


class _GpsTxFake:
    def start(self, session_ctx) -> None:
        session_ctx.handles["gps_tx_proc"] = object()

    def stop(self, session_ctx) -> None:
        session_ctx.handles.pop("gps_tx_proc", None)

    def describe(self, _session_ctx) -> dict[str, Any]:
        return {"state": "running", "pid": 1, "exit_code": None}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Session runtime smoke (start-run-stop + degraded video)")
    p.add_argument("--out-root", default="outputs/smoke/session_runtime", help="where to write smoke report")
    p.add_argument("--run-sec", type=float, default=1.3, help="session run duration for artifacts")
    return p.parse_args()


def _write_fake_inputs(base: Path) -> tuple[Path, Path, Path]:
    base.mkdir(parents=True, exist_ok=True)
    nav = base / "brdc.nav"
    nav.write_text("dummy-nav", encoding="utf-8")
    traj = base / "trajectory.csv"
    traj.write_text("t,X,Y,Z\n0.0,0,0,0\n0.4,5,0,0\n0.8,10,0,0\n1.2,15,0,0\n", encoding="utf-8")
    diag = base / "diagnostics.csv"
    diag.write_text("t,v\n0,0\n", encoding="utf-8")
    return nav, traj, diag


def _collect_checks(session_dir: Path, session_id: str) -> dict[str, Any]:
    manifest = session_dir / "session_manifest.json"
    events = session_dir / "events.log"
    timeline = session_dir / "trajectory_timeline.csv"
    vis_csv = session_dir / "video" / "visible_frames.csv"
    thr_csv = session_dir / "video" / "thermal_frames.csv"

    checks: dict[str, Any] = {}
    checks["manifest_exists"] = manifest.exists()
    checks["events_exists"] = events.exists()
    checks["timeline_exists"] = timeline.exists()
    checks["visible_csv_exists"] = vis_csv.exists()
    checks["thermal_csv_exists"] = thr_csv.exists()

    manifest_json = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    checks["manifest_status_stopped"] = manifest_json.get("status") == "STOPPED"
    checks["manifest_session_id_match"] = manifest_json.get("session_id") == session_id

    event_lines = [x for x in events.read_text(encoding="utf-8").splitlines() if x.strip()] if events.exists() else []
    event_json = [json.loads(x) for x in event_lines]
    event_names = [str(x.get("event", "")) for x in event_json]
    checks["events_start_stop"] = ("SESSION_START" in event_names) and ("SESSION_STOP" in event_names)

    vis_lines = [x for x in vis_csv.read_text(encoding="utf-8").splitlines() if x.strip()] if vis_csv.exists() else []
    thr_lines = [x for x in thr_csv.read_text(encoding="utf-8").splitlines() if x.strip()] if thr_csv.exists() else []
    checks["visible_has_frames"] = len(vis_lines) >= 2
    checks["thermal_degraded"] = len(thr_lines) <= 1
    checks["degraded_event_present"] = any("SESSION_VIDEO_CHANNEL_ERROR" == x for x in event_names)

    checks["all_passed"] = all(bool(v) for v in checks.values() if isinstance(v, bool))
    return checks


def main() -> int:
    args = _parse_args()
    out_root = Path(args.out_root).resolve()
    run_id = f"run_{int(time.time())}"
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    nav, traj, diag = _write_fake_inputs(run_dir / "inputs")

    bus = EventBus()
    services = {
        "mayak_spindle": _MayakStub(),
        "video_visible": _VideoServiceOk(),
        "video_thermal": _VideoServiceDown(),  # degraded channel
    }
    orch = Orchestrator(bus, _ServiceMap(services))
    orch._gps_tx_runner = _GpsTxFake()
    orch._find_latest_trajectory_artifact = lambda: {  # type: ignore[method-assign]
        "run_dir": str(run_dir / "inputs"),
        "trajectory_csv": str(traj),
        "diagnostics_csv": str(diag),
    }
    orch._build_session_gps_tx_config = lambda _prepared: {  # type: ignore[method-assign]
        "pluto_exe": "PlutoPlayer.exe",
        "iq_path": "dummy.bin",
        "tx_atten_db": -20.0,
        "rf_bw_mhz": 3.0,
    }

    scenario_id = orch.prepare_mayak_test(
        head_start_rpm=100,
        head_end_rpm=200,
        tail_start_rpm=300,
        tail_end_rpm=400,
        profile_type="linear",
        duration_sec=3.0,
        sdr_options={"gps_sdr_sim": {"nav": str(nav), "static_sec": 0.0}},
    )
    started = orch.start_test_session()
    session_id = str(started["session_id"])
    time.sleep(max(0.2, float(args.run_sec)))
    stopped = orch.stop_test_session()
    _ = stopped

    session_dir = Path(started["out_dir"]).resolve()
    checks = _collect_checks(session_dir, session_id)
    report = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "session_id": session_id,
        "session_dir": session_dir.as_posix(),
        "checks": checks,
    }
    report_json = run_dir / "smoke_report.json"
    report_md = run_dir / "smoke_report.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Session Runtime Smoke", ""]
    for k, v in checks.items():
        mark = "PASS" if bool(v) else "FAIL"
        lines.append(f"- [{mark}] {k}")
    lines.append("")
    lines.append(f"- scenario_id: `{scenario_id}`")
    lines.append(f"- session_id: `{session_id}`")
    lines.append(f"- session_dir: `{session_dir.as_posix()}`")
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"run_id={run_id}")
    print(f"scenario_id={scenario_id}")
    print(f"session_id={session_id}")
    print(f"session_dir={session_dir.as_posix()}")
    print(f"report_json={report_json.as_posix()}")
    print(f"report_md={report_md.as_posix()}")
    print(f"result={'PASS' if checks['all_passed'] else 'FAIL'}")
    return 0 if checks["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
