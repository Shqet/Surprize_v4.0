# app/services/gps_sdr_sim/engine.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional
import json
import time
import uuid

from .formats import (
    CsvTrajectorySpec,
    NmeaGgaParams,
    write_nmea_gga_from_local_xyz_csv,
)


@dataclass(frozen=True)
class GpsSdrSimPaths:
    run_id: str
    run_dir: Path

    input_dir: Path
    sim_dir: Path
    pluto_dir: Path
    meta_dir: Path
    logs_dir: Path

    input_trajectory_copy: Path
    nmea_strings_txt: Path

    sim_iq_bin: Path
    gps_sdr_sim_cmdline_txt: Path

    pluto_cmdline_txt: Path

    run_meta_json: Path
    stdout_gps_sdr_sim_log: Path
    stderr_gps_sdr_sim_log: Path
    stdout_pluto_log: Path
    stderr_pluto_log: Path


def make_run_id(prefix: str = "") -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    rand = uuid.uuid4().hex[:8]
    return f"{prefix}{ts}_{rand}" if prefix else f"{ts}_{rand}"


def build_run_paths(out_root: Path, run_id: str) -> GpsSdrSimPaths:
    run_dir = out_root / "gps_sdr_sim" / run_id
    input_dir = run_dir / "input"
    sim_dir = run_dir / "sim"
    pluto_dir = run_dir / "pluto"
    meta_dir = run_dir / "meta"
    logs_dir = run_dir / "logs"

    return GpsSdrSimPaths(
        run_id=run_id,
        run_dir=run_dir,

        input_dir=input_dir,
        sim_dir=sim_dir,
        pluto_dir=pluto_dir,
        meta_dir=meta_dir,
        logs_dir=logs_dir,

        input_trajectory_copy=input_dir / "trajectory.csv",
        nmea_strings_txt=input_dir / "nmea_strings.txt",

        sim_iq_bin=sim_dir / "gpssim_iq.bin",
        gps_sdr_sim_cmdline_txt=sim_dir / "gps_sdr_sim.cmdline.txt",

        pluto_cmdline_txt=pluto_dir / "plutoplayer.cmdline.txt",

        run_meta_json=meta_dir / "run.json",
        stdout_gps_sdr_sim_log=logs_dir / "stdout_gps_sdr_sim.log",
        stderr_gps_sdr_sim_log=logs_dir / "stderr_gps_sdr_sim.log",
        stdout_pluto_log=logs_dir / "stdout_pluto.log",
        stderr_pluto_log=logs_dir / "stderr_pluto.log",
    )


def ensure_dirs(p: GpsSdrSimPaths) -> None:
    p.input_dir.mkdir(parents=True, exist_ok=True)
    p.sim_dir.mkdir(parents=True, exist_ok=True)
    p.pluto_dir.mkdir(parents=True, exist_ok=True)
    p.meta_dir.mkdir(parents=True, exist_ok=True)
    p.logs_dir.mkdir(parents=True, exist_ok=True)


def validate_tool_paths(
    *,
    gps_sdr_sim_exe: Path,
    pluto_player_exe: Path,
    nav_path: Path,
) -> None:
    if not gps_sdr_sim_exe.exists():
        raise FileNotFoundError(f"gps_sdr_sim_exe not found: {gps_sdr_sim_exe}")
    if not pluto_player_exe.exists():
        raise FileNotFoundError(f"pluto_player_exe not found: {pluto_player_exe}")
    if not nav_path.exists():
        raise FileNotFoundError(f"nav_path not found: {nav_path}")


def copy_if_requested(src: Path, dst: Path, *, enable: bool) -> None:
    if not enable:
        return
    if not src.exists():
        raise FileNotFoundError(f"input_trajectory_csv not found: {src}")
    dst.write_bytes(src.read_bytes())


def prepare_nmea_input(
    *,
    input_trajectory_csv: Path,
    out_nmea_txt: Path,
    origin_lat_deg: float,
    origin_lon_deg: float,
    origin_h_m: float,
    static_sec: float,
    sample_hz: float = 10.0,
    csv_spec: CsvTrajectorySpec = CsvTrajectorySpec(),
    nmea_params: NmeaGgaParams = NmeaGgaParams(),
) -> Dict[str, Any]:
    """
    Step 1 of pipeline:
      - convert local trajectory CSV -> nmea_strings.txt
      - prepend 'static' segment by duplicating the first point for static_sec seconds

    IMPORTANT CONTRACT:
      - static_sec is the REAL static duration in seconds (user input)
      - NMEA is 10 Hz, so static_lines = static_sec * 10
    """
    if static_sec < 0:
        raise ValueError(f"static_sec must be >= 0, got {static_sec}")
    if abs(sample_hz - 10.0) > 1e-9:
        raise ValueError(f"gps-sdr-sim expects 10 Hz; got sample_hz={sample_hz}")

    write_nmea_gga_from_local_xyz_csv(
        input_csv=input_trajectory_csv,
        out_nmea_txt=out_nmea_txt,
        origin_lat_deg=origin_lat_deg,
        origin_lon_deg=origin_lon_deg,
        origin_h_m=origin_h_m,
        static_sec=float(static_sec),
        sample_hz=sample_hz,
        csv_spec=csv_spec,
        nmea_params=nmea_params,
    )

    # 1 line per 0.1s at 10Hz
    line_count = sum(1 for _ in out_nmea_txt.open("r", encoding="utf-8", errors="ignore"))
    duration_sec = line_count / sample_hz if line_count > 0 else 0.0
    static_lines = int(round(float(static_sec) * sample_hz))

    return {
        "input_csv": str(input_trajectory_csv),
        "nmea_txt": str(out_nmea_txt),
        "sample_hz": sample_hz,

        "static_sec": float(static_sec),
        "static_lines": static_lines,

        "nmea_lines": line_count,
        "duration_sec": duration_sec,

        "origin": {"lat_deg": origin_lat_deg, "lon_deg": origin_lon_deg, "h_m": origin_h_m},
        "nmea": {
            "start_time_sec": nmea_params.start_time_sec,
            "fix_quality": nmea_params.fix_quality,
            "num_sats": nmea_params.num_sats,
            "hdop": nmea_params.hdop,
            "geoid_sep_m": nmea_params.geoid_sep_m,
        },
        "csv_spec": {
            "time_col": csv_spec.time_col,
            "x_col": csv_spec.x_col,
            "y_col": csv_spec.y_col,
            "z_col": csv_spec.z_col,
        },
    }


def write_run_meta(path: Path, meta: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def build_gps_sdr_sim_cmd(
    *,
    gps_sdr_sim_exe: Path,
    nav_path: Path,
    nmea_txt: Path,
    out_iq_bin: Path,
    duration_sec: Optional[float] = None,
    iq_bit_depth: int = 8,
    extra_args: str = "",
) -> list[str]:
    """
    Returns argv list for subprocess.

    NOTE: For robustness, you may choose to NOT pass -d at all (some builds reject some ranges).
    So duration_sec is optional.
    """
    if iq_bit_depth not in (1, 8, 16):
        raise ValueError(f"iq_bit_depth unexpected: {iq_bit_depth}")

    cmd = [
        str(gps_sdr_sim_exe),
        "-e", str(nav_path),
        "-g", str(nmea_txt),
        "-b", str(iq_bit_depth),
        "-o", str(out_iq_bin),
    ]
    if duration_sec is not None:
        cmd += ["-d", str(int(round(duration_sec)))]
    if extra_args:
        cmd += extra_args.split()
    return cmd


def build_pluto_player_cmd(
    *,
    pluto_player_exe: Path,
    iq_bin: Path,
    extra_args: str = "",
) -> list[str]:
    """
    Returns argv list for subprocess.
    (No opinions about RF args here; caller provides extra_args.)
    """
    cmd = [str(pluto_player_exe), str(iq_bin)]
    if extra_args:
        cmd += extra_args.split()
    return cmd


def save_cmdline(path: Path, argv: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(" ".join(f'"{a}"' if " " in a else a for a in argv), encoding="utf-8")
