# scripts/smoke_video_channel.py
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]

VISIBLE_JPG = ROOT / "outputs" / "video_preview" / "visible" / "latest.jpg"
THERMAL_JPG = ROOT / "outputs" / "video_preview" / "thermal" / "latest.jpg"

RUNNING_RE_VISIBLE = re.compile(r"SERVICE_STATUS service=video_visible status=RUNNING")
RUNNING_RE_THERMAL = re.compile(r"SERVICE_STATUS service=video_thermal status=RUNNING")

STOPPED_RE_VISIBLE = re.compile(r"SERVICE_STATUS service=video_visible status=STOPPED")
STOPPED_RE_THERMAL = re.compile(r"SERVICE_STATUS service=video_thermal status=STOPPED")

READER_PROCESS_MARK = "app.vendor.video_channel.client.reader_process"


@dataclass
class SmokeResult:
    ok: bool
    details: str


def _read_lines(proc: subprocess.Popen[str], timeout_sec: float) -> Iterable[str]:
    """
    Yields lines from proc.stdout for up to timeout_sec seconds.
    """
    assert proc.stdout is not None
    end = time.time() + timeout_sec
    while time.time() < end:
        line = proc.stdout.readline()
        if line:
            yield line.rstrip("\n")
        else:
            # if process died, stop early
            if proc.poll() is not None:
                break
            time.sleep(0.05)


def _wait_for_patterns(
    proc: subprocess.Popen[str],
    patterns: Tuple[re.Pattern[str], ...],
    timeout_sec: float,
) -> Tuple[bool, list[str]]:
    """
    Wait until all patterns have been seen in stdout.
    Returns (ok, captured_lines).
    """
    seen = [False] * len(patterns)
    captured: list[str] = []
    for line in _read_lines(proc, timeout_sec=timeout_sec):
        captured.append(line)
        for i, pat in enumerate(patterns):
            if not seen[i] and pat.search(line):
                seen[i] = True
        if all(seen):
            return True, captured
    return False, captured


def _file_exists_and_updates(path: Path, window_sec: float = 2.5) -> SmokeResult:
    if not path.exists():
        return SmokeResult(False, f"missing file: {path}")

    try:
        s1 = path.stat()
    except Exception as e:
        return SmokeResult(False, f"stat failed for {path}: {type(e).__name__}: {e}")

    if s1.st_size <= 0:
        return SmokeResult(False, f"file size is 0: {path}")

    # Wait and check LastWriteTime changes
    time.sleep(window_sec)
    try:
        s2 = path.stat()
    except Exception as e:
        return SmokeResult(False, f"stat2 failed for {path}: {type(e).__name__}: {e}")

    if s2.st_mtime == s1.st_mtime:
        return SmokeResult(False, f"file mtime did not change in {window_sec}s: {path}")

    return SmokeResult(True, f"ok: {path} size={s2.st_size} mtime_changed")


def _list_python_process_cmdlines() -> list[str]:
    """
    Windows-friendly: query python.exe command lines using WMIC-like via powershell.
    Works without admin typically.
    """
    # We avoid importing psutil (not guaranteed installed).
    # Use PowerShell CIM query.
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object -ExpandProperty CommandLine",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return lines
    except Exception:
        # Fallback: return empty => we won't fail hard, just warn.
        return []


def _assert_no_reader_processes() -> SmokeResult:
    cmdlines = _list_python_process_cmdlines()
    if not cmdlines:
        # Can't reliably detect => don't hard-fail, but report.
        return SmokeResult(True, "skip zombie check (cannot query process cmdline)")

    zombies = [c for c in cmdlines if READER_PROCESS_MARK in c]
    if zombies:
        # show up to 3
        sample = "\n".join(zombies[:3])
        return SmokeResult(False, f"found reader_process zombies:\n{sample}")

    return SmokeResult(True, "ok: no reader_process zombies")


def _send_ctrl_c(proc: subprocess.Popen[str]) -> None:
    """
    Send CTRL+C / SIGINT cross-platform.
    On Windows, this works if subprocess is in a new process group.
    """
    try:
        proc.send_signal(signal.CTRL_C_EVENT)  # type: ignore[attr-defined]
    except Exception:
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            proc.terminate()


def run_smoke(timeout_start_sec: float = 20.0, timeout_stop_sec: float = 20.0) -> int:
    # Ensure outputs dirs exist (the service should also do this, но пусть будет)
    VISIBLE_JPG.parent.mkdir(parents=True, exist_ok=True)
    THERMAL_JPG.parent.mkdir(parents=True, exist_ok=True)

    # Remove old files to make the "exists" check meaningful
    for p in (VISIBLE_JPG, THERMAL_JPG):
        try:
            p.unlink(missing_ok=True)  # py3.8+ supports missing_ok
        except TypeError:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    print(f"[smoke] cwd={ROOT}")
    print("[smoke] starting app.main ...")

    env = os.environ.copy()

    # Run module as the same interpreter
    args = [sys.executable, "-u", "-m", "app.main"]

    # CREATE_NEW_PROCESS_GROUP is important on Windows for CTRL_C_EVENT
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        args,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        creationflags=creationflags,
    )

    try:
        ok_run, captured = _wait_for_patterns(
            proc,
            patterns=(RUNNING_RE_VISIBLE, RUNNING_RE_THERMAL),
            timeout_sec=timeout_start_sec,
        )
        for l in captured[-30:]:
            print(l)

        if not ok_run:
            print("[smoke][FAIL] did not reach RUNNING for both daemons")
            _send_ctrl_c(proc)
            proc.wait(timeout=5)
            return 2

        print("[smoke] both daemons RUNNING (by logs)")

        r1 = _file_exists_and_updates(VISIBLE_JPG)
        print(f"[smoke] visible preview: {r1.details}")
        if not r1.ok:
            print("[smoke][FAIL] visible preview check failed")
            return 3

        r2 = _file_exists_and_updates(THERMAL_JPG)
        print(f"[smoke] thermal preview: {r2.details}")
        if not r2.ok:
            print("[smoke][FAIL] thermal preview check failed")
            return 4

        print("[smoke] requesting shutdown (CTRL+C) ...")
        _send_ctrl_c(proc)

        ok_stop, captured_stop = _wait_for_patterns(
            proc,
            patterns=(STOPPED_RE_VISIBLE, STOPPED_RE_THERMAL),
            timeout_sec=timeout_stop_sec,
        )
        for l in captured_stop[-50:]:
            print(l)

        if not ok_stop:
            print("[smoke][FAIL] did not see STOPPED for both daemons")
            # hard kill to avoid leaving processes
            proc.kill()
            return 5

        # Ensure process exits
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        rz = _assert_no_reader_processes()
        print(f"[smoke] zombies: {rz.details}")
        if not rz.ok:
            print("[smoke][FAIL] zombie check failed")
            return 6

        print("[smoke][OK] smoke passed")
        return 0

    finally:
        if proc.poll() is None:
            try:
                _send_ctrl_c(proc)
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(run_smoke())
