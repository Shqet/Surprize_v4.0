# app/services/rtsp_probe.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    error: Optional[str] = None


class RtspProbeFatal(RuntimeError):
    pass


def probe_rtsp_ffprobe(ffprobe_path: str, url: str, timeout_sec: float) -> ProbeResult:
    cmd = [
        ffprobe_path,
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-stimeout", str(int(timeout_sec * 1_000_000)),
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
        "-of", "json",
        url,
    ]
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec + 0.5,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(ok=False, error="timeout")
    except (FileNotFoundError, PermissionError) as ex:
        # Fatal: ffprobe исчез / нет прав / не запускается
        raise RtspProbeFatal(type(ex).__name__) from ex
    except Exception as ex:
        return ProbeResult(ok=False, error=type(ex).__name__)

    if r.returncode == 0:
        return ProbeResult(ok=True)

    err = (r.stderr or "").strip() or f"ffprobe_exit_{r.returncode}"
    return ProbeResult(ok=False, error=err[:200])
