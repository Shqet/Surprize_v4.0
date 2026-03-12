from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from app.core.subprocess_utils import windows_no_console_kwargs


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    error: Optional[str] = None


class RtspProbeFatal(RuntimeError):
    pass


def _run_ffprobe(cmd: list[str], timeout_sec: float) -> ProbeResult:
    """
    Low-level ffprobe runner.

    - Timeout -> ok=False, error="timeout"
    - ffprobe missing / permission -> raise RtspProbeFatal
    - other errors -> ok=False with stderr
    """
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec + 0.5,
            **windows_no_console_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(ok=False, error="timeout")
    except (FileNotFoundError, PermissionError) as ex:
        raise RtspProbeFatal(type(ex).__name__) from ex
    except Exception as ex:
        return ProbeResult(ok=False, error=type(ex).__name__)

    if r.returncode == 0:
        return ProbeResult(ok=True)

    err = (r.stderr or "").strip() or f"ffprobe_exit_{r.returncode}"
    return ProbeResult(ok=False, error=err[:200])


def probe_rtsp_ffprobe(bus, url: str, timeout_sec: float, source: str) -> ProbeResult:
    """
    Public probe API used by RtspHealthService.

    Signature MUST stay compatible with tests:
        probe_rtsp_ffprobe(bus, url, timeout_sec, source)

    'bus' and 'source' currently unused here (kept for contract compatibility).
    """

    ffprobe_path = shutil.which("ffprobe") or "ffprobe"

    stimeout_us = int(float(timeout_sec) * 1_000_000)

    cmd_with_timeout = [
        ffprobe_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-stimeout",
        str(stimeout_us),
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate",
        "-of",
        "json",
        url,
    ]

    r = _run_ffprobe(cmd_with_timeout, timeout_sec=timeout_sec)

    # Fallback: some ffprobe builds don't support -stimeout
    if (
        not r.ok
        and r.error
        and "Option not found" in r.error
        and "stimeout" in r.error
    ):
        cmd_no_timeout = [
            ffprobe_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate",
            "-of",
            "json",
            url,
        ]
        return _run_ffprobe(cmd_no_timeout, timeout_sec=timeout_sec)

    return r
