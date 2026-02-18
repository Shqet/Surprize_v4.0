from __future__ import annotations

import subprocess
import threading
from typing import Callable, Optional


def terminate_process(
    proc: subprocess.Popen,
    timeout_sec: float,
    on_info: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Best-effort terminate -> wait(timeout) -> kill.
    on_info/on_error receive short messages (k=v friendly).
    """
    try:
        proc.terminate()
        if on_info:
            on_info("stage=stop terminate=1")
    except Exception as ex:
        if on_error:
            on_error(f"stage=stop_terminate err={type(ex).__name__}")

    try:
        proc.wait(timeout=max(1.0, float(timeout_sec)))
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            if on_info:
                on_info("stage=stop kill=1")
        except Exception as ex:
            if on_error:
                on_error(f"stage=stop_kill err={type(ex).__name__}")
    except Exception as ex:
        if on_error:
            on_error(f"stage=stop_wait err={type(ex).__name__}")


def join_thread(t: Optional[threading.Thread], timeout_sec: float) -> None:
    if t is None:
        return
    try:
        t.join(timeout=max(0.1, float(timeout_sec)))
    except Exception:
        return
