from __future__ import annotations

import locale
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.core.subprocess_utils import windows_no_console_kwargs


LineCallback = Callable[[str], None]


def _proc_encoding() -> str:
    # Windows console tools typically output in OEM codepage (cp866).
    if os.name == "nt":
        return "cp866"
    return locale.getpreferredencoding(False)


def _reader_worker(pipe, out_path: Path, on_line: Optional[LineCallback]) -> None:
    try:
        with out_path.open("a", encoding="utf-8", errors="replace") as f_out:
            for raw in pipe:
                if raw is None:
                    continue
                line = str(raw)
                f_out.write(line)
                f_out.flush()
                if on_line:
                    on_line(line.rstrip("\r\n"))
    except Exception:
        # Best-effort: swallow to avoid crashing service thread.
        return


@dataclass
class ProcessHandle:
    proc: subprocess.Popen[str]
    stdout_path: Path
    stderr_path: Path
    threads: list[threading.Thread]


def start_process(
    *,
    cmd: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    on_stdout: Optional[LineCallback] = None,
    on_stderr: Optional[LineCallback] = None,
    thread_label: str = "gps_sdr_sim",
) -> ProcessHandle:
    """
    Start process with live line readers, streaming to files and optional callbacks.
    """
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    enc = _proc_encoding()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=enc,
        errors="replace",
        bufsize=1,
        **windows_no_console_kwargs(),
    )

    threads: list[threading.Thread] = []
    if proc.stdout is not None:
        t = threading.Thread(
            target=_reader_worker,
            name=f"{thread_label}.stdout",
            args=(proc.stdout, stdout_path, on_stdout),
            daemon=True,
        )
        threads.append(t)
        t.start()
    if proc.stderr is not None:
        t = threading.Thread(
            target=_reader_worker,
            name=f"{thread_label}.stderr",
            args=(proc.stderr, stderr_path, on_stderr),
            daemon=True,
        )
        threads.append(t)
        t.start()

    return ProcessHandle(proc=proc, stdout_path=stdout_path, stderr_path=stderr_path, threads=threads)


def wait_for_exit(
    handle: ProcessHandle,
    *,
    timeout_sec: Optional[float] = None,
    stop_event: Optional[threading.Event] = None,
) -> tuple[Optional[int], bool, bool]:
    """
    Wait until process exits, timeout, or stop_event is set.

    Returns: (rc, timed_out, stopped)
      - rc: return code or None if still running
      - timed_out: True if timeout occurred
      - stopped: True if stop_event was set while waiting
    """
    deadline = time.monotonic() + float(timeout_sec) if timeout_sec is not None else None
    while True:
        if stop_event is not None and stop_event.is_set():
            return None, False, True

        rc = handle.proc.poll()
        if rc is not None:
            return int(rc), False, False

        if deadline is not None and time.monotonic() >= deadline:
            return None, True, False

        time.sleep(0.05)


def join_readers(handle: ProcessHandle, timeout_sec: float = 0.5) -> None:
    for t in handle.threads:
        try:
            t.join(timeout=timeout_sec)
        except Exception:
            pass
