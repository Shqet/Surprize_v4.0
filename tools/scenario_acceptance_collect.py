from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (?P<level>[A-Z]+) \| (?P<source>[^|]+) \| (?P<code>\S+)(?: (?P<msg>.*))?$"
)
_KV_RE = re.compile(r"(?P<k>[A-Za-z0-9_]+)=(?P<v>[^\s]+)")


@dataclass(frozen=True)
class LogRow:
    ts: datetime
    ts_raw: str
    level: str
    source: str
    code: str
    message: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Collect acceptance artifacts for scenario e2e")
    p.add_argument("--log-path", default="data/app.log")
    p.add_argument("--scenario-id", default="", help="optional fixed scenario_id; default: latest from SCENARIO_ID logs")
    p.add_argument("--out-root", default="outputs/acceptance")
    p.add_argument("--visible-preview", default="outputs/video_preview/visible/latest.jpg")
    p.add_argument("--thermal-preview", default="outputs/video_preview/thermal/latest.jpg")
    p.add_argument("--copy-full-log", action="store_true", help="copy full app log into artifacts")
    return p.parse_args()


def _parse_log(path: Path) -> list[LogRow]:
    if not path.exists():
        raise SystemExit(f"FAIL: log file not found: {path}")
    rows: list[LogRow] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE_RE.match(raw.strip())
        if not m:
            continue
        ts_raw = m.group("ts")
        rows.append(
            LogRow(
                ts=datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S"),
                ts_raw=ts_raw,
                level=(m.group("level") or "").strip(),
                source=(m.group("source") or "").strip(),
                code=(m.group("code") or "").strip(),
                message=(m.group("msg") or "").strip(),
            )
        )
    if not rows:
        raise SystemExit(f"FAIL: no parseable log lines in: {path}")
    return rows


def _kv(message: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _KV_RE.finditer(message or ""):
        out[m.group("k")] = m.group("v")
    return out


def _pick_scenario_id(rows: list[LogRow], forced: str) -> str:
    if forced:
        return forced
    candidates: list[str] = []
    for r in rows:
        if r.code != "SCENARIO_ID":
            continue
        sid = _kv(r.message).get("scenario_id")
        if sid:
            candidates.append(sid)
    if not candidates:
        raise SystemExit("FAIL: scenario_id not found in logs (SCENARIO_ID missing)")
    return candidates[-1]


def _scenario_start_time(rows: list[LogRow], scenario_id: str) -> datetime:
    for r in rows:
        if r.code == "SCENARIO_ID" and _kv(r.message).get("scenario_id") == scenario_id:
            return r.ts
    raise SystemExit(f"FAIL: SCENARIO_ID not found for scenario_id={scenario_id}")


def _extract_timeline(rows: list[LogRow], scenario_id: str, start_ts: datetime) -> list[LogRow]:
    # Keep key lines after scenario start. We keep full time-ordered context needed for acceptance.
    keep_codes = {
        "SCENARIO_ID",
        "MAYAK_TEST_START",
        "MAYAK_TEST_STOP",
        "MAYAK_TEST_ABORT",
        "SCENARIO_STATUS",
        "SERVICE_STATUS",
        "ORCH_STATE_CHANGE",
        "ORCH_PRECHECK_OK",
        "MAYAK_READY_STATE",
        "MAYAK_SPINDLE_STATE",
    }
    out: list[LogRow] = []
    for r in rows:
        if r.ts < start_ts:
            continue
        if r.code not in keep_codes:
            continue
        msg_kv = _kv(r.message)
        sid = msg_kv.get("scenario_id")
        if sid is not None and sid != scenario_id:
            continue
        out.append(r)
    return out


def _has(rows: list[LogRow], pred) -> bool:
    return any(pred(r) for r in rows)


def _find_last_trajectory_dir(rows: list[LogRow]) -> Optional[Path]:
    out_dir: Optional[str] = None
    for r in rows:
        if r.code == "SERVICE_STATUS" and r.source == "ballistics_model":
            v = _kv(r.message).get("out_dir")
            if v:
                out_dir = v
    return Path(out_dir).resolve() if out_dir else None


def _checklist(timeline: list[LogRow]) -> dict[str, bool]:
    daemon_up = _has(
        timeline,
        lambda r: r.code == "SERVICE_STATUS"
        and "service=mayak_spindle" in r.message
        and "status=RUNNING" in r.message,
    )
    mayak_ready = _has(
        timeline,
        lambda r: (r.code == "MAYAK_READY_STATE" and "ready=1" in r.message)
        or (r.code == "ORCH_PRECHECK_OK" and "service=mayak_spindle" in r.message),
    )
    test_start = _has(timeline, lambda r: r.code == "MAYAK_TEST_START")
    telemetry_changes = _has(
        timeline,
        lambda r: r.code == "MAYAK_SPINDLE_STATE" and ("new=STARTING" in r.message or "new=MOVING" in r.message),
    )
    test_stop = _has(timeline, lambda r: r.code == "MAYAK_TEST_STOP")
    trajectory_run = _has(
        timeline,
        lambda r: r.code == "SERVICE_STATUS"
        and "service=ballistics_model" in r.message
        and ("status=RUNNING" in r.message or "status=STOPPED" in r.message),
    )
    idle = _has(timeline, lambda r: r.code == "ORCH_STATE_CHANGE" and "to=IDLE" in r.message)
    return {
        "daemon_up": daemon_up,
        "mayak_ready": mayak_ready,
        "start_test": test_start,
        "telemetry_changes": telemetry_changes,
        "stop_test": test_stop,
        "trajectory_run": trajectory_run,
        "idle": idle,
    }


def _write_timeline(path: Path, timeline: list[LogRow]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in timeline:
            payload = {
                "ts": r.ts_raw,
                "level": r.level,
                "source": r.source,
                "code": r.code,
                "message": r.message,
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_checklist(path: Path, checks: dict[str, bool]) -> None:
    order = [
        ("daemon_up", "daemon up"),
        ("mayak_ready", "mayak ready"),
        ("start_test", "start test"),
        ("telemetry_changes", "telemetry changes"),
        ("stop_test", "stop test"),
        ("trajectory_run", "trajectory run"),
        ("idle", "orchestrator idle"),
    ]
    lines = ["# Acceptance Checklist", ""]
    for key, title in order:
        mark = "PASS" if checks.get(key, False) else "FAIL"
        lines.append(f"- [{mark}] {title}")
    lines.append("")
    lines.append("Legend: PASS means evidence found in timeline/log.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def main() -> int:
    args = _parse_args()
    log_path = Path(args.log_path).resolve()
    out_root = Path(args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    rows = _parse_log(log_path)
    scenario_id = _pick_scenario_id(rows, args.scenario_id.strip())
    start_ts = _scenario_start_time(rows, scenario_id)
    timeline = _extract_timeline(rows, scenario_id, start_ts)
    checks = _checklist(timeline)
    result = "PASS" if all(checks.values()) else "FAIL"

    out_dir = out_root / scenario_id
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_timeline(out_dir / "timeline.jsonl", timeline)
    _write_checklist(out_dir / "checklist.md", checks)

    trj_dir = _find_last_trajectory_dir(rows)
    copied_traj = False
    copied_diag = False
    if trj_dir is not None:
        copied_traj = _copy_if_exists(trj_dir / "trajectory.csv", out_dir / "trajectory" / "trajectory.csv")
        copied_diag = _copy_if_exists(trj_dir / "diagnostics.csv", out_dir / "trajectory" / "diagnostics.csv")

    copied_visible = _copy_if_exists(Path(args.visible_preview).resolve(), out_dir / "video_preview" / "visible" / "latest.jpg")
    copied_thermal = _copy_if_exists(Path(args.thermal_preview).resolve(), out_dir / "video_preview" / "thermal" / "latest.jpg")

    if args.copy_full_log:
        _copy_if_exists(log_path, out_dir / "app.log")

    summary = {
        "scenario_id": scenario_id,
        "result": result,
        "checks": checks,
        "timeline_rows": len(timeline),
        "artifacts": {
            "timeline": str((out_dir / "timeline.jsonl").as_posix()),
            "checklist": str((out_dir / "checklist.md").as_posix()),
            "trajectory_csv": copied_traj,
            "diagnostics_csv": copied_diag,
            "preview_visible": copied_visible,
            "preview_thermal": copied_thermal,
        },
        "log_path": str(log_path.as_posix()),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"scenario_id={scenario_id}")
    print(f"result={result}")
    print(f"out_dir={out_dir.as_posix()}")
    return 0 if result == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

