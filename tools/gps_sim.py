# tools/test_gps_sim.py
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from app.services.gps_sdr_sim.engine import (
    make_run_id,
    build_run_paths,
    ensure_dirs,
    prepare_nmea_input,
    write_run_meta,
    save_cmdline,
)


def _validate_step2_paths(gps_sdr_sim_exe: Path, nav_path: Path) -> None:
    if not gps_sdr_sim_exe.exists():
        raise FileNotFoundError(f"gps_sdr_sim_exe not found: {gps_sdr_sim_exe}")
    if not nav_path.exists():
        raise FileNotFoundError(f"nav_path not found: {nav_path}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Step2 CLI test: run GPS-SDR-SIM.exe -> IQ bin (no Pluto)")

    p.add_argument("--out-root", default="outputs")

    # reuse run_id OR generate NMEA inside
    p.add_argument("--run-id", default="")
    p.add_argument("--input", default="")
    p.add_argument("--origin-lat", type=float, default=None)
    p.add_argument("--origin-lon", type=float, default=None)
    p.add_argument("--origin-h", type=float, default=None)
    p.add_argument("--static-sec", type=float, default=0.0)

    # tool inputs
    p.add_argument("--gps-sdr-sim-exe", required=True)
    p.add_argument("--nav", required=True)

    # IMPORTANT: allow only 8 or 16, selectable by flag
    p.add_argument(
        "--bit-depth",
        type=int,
        choices=[8, 16],
        default=16,
        help="IQ bit depth (8 or 16). Default: 16.",
    )

    # duration: only if explicitly set; default is inferred from -g
    p.add_argument("--duration", type=int, default=0, help="explicit -d value (0 = do not pass -d)")
    p.add_argument("--extra-args", default="")
    p.add_argument("--timeout-sec", type=int, default=120)

    return p.parse_args()


def _tail(text: str, n: int = 40) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:]) if lines else ""


def main() -> int:
    args = _parse_args()

    out_root = Path(args.out_root).resolve()
    gps_exe = Path(args.gps_sdr_sim_exe).resolve()
    nav_src = Path(args.nav).resolve()

    # Prepare or reuse run
    if args.run_id:
        run_id = args.run_id
        paths = build_run_paths(out_root, run_id)
        if not paths.nmea_strings_txt.exists():
            raise SystemExit(f"FAIL: run_id provided but NMEA missing: {paths.nmea_strings_txt}")
        ensure_dirs(paths)
    else:
        run_id = make_run_id(prefix="gps_")
        paths = build_run_paths(out_root, run_id)
        ensure_dirs(paths)

        if not args.input:
            raise SystemExit("FAIL: either --run-id or --input required")
        if args.origin_lat is None or args.origin_lon is None or args.origin_h is None:
            raise SystemExit("FAIL: origin params required")

        input_csv = Path(args.input).resolve()
        meta = prepare_nmea_input(
            input_trajectory_csv=input_csv,
            out_nmea_txt=paths.nmea_strings_txt,
            origin_lat_deg=float(args.origin_lat),
            origin_lon_deg=float(args.origin_lon),
            origin_h_m=float(args.origin_h),
            static_sec=float(args.static_sec),
        )
        write_run_meta(paths.run_meta_json, meta)

    _validate_step2_paths(gps_exe, nav_src)

    # Copy nav into run_dir/sim and run with cwd=sim_dir and RELATIVE paths
    nav_dst = paths.sim_dir / nav_src.name
    nav_dst.write_bytes(nav_src.read_bytes())

    cwd = paths.sim_dir
    nav_arg = nav_dst.name
    nmea_arg = os.path.relpath(paths.nmea_strings_txt, start=cwd)
    out_arg = os.path.relpath(paths.sim_iq_bin, start=cwd)

    argv = [
        str(gps_exe),
        "-e",
        nav_arg,
        "-g",
        nmea_arg,
        "-b",
        str(int(args.bit_depth)),
        "-o",
        out_arg,
    ]

    if args.duration and args.duration > 0:
        argv += ["-d", str(int(args.duration))]
        print(f"INFO: passing explicit -d {args.duration}")
    else:
        print("INFO: -d not passed (duration inferred from NMEA)")

    if args.extra_args:
        argv += args.extra_args.split()

    save_cmdline(paths.gps_sdr_sim_cmdline_txt, argv)

    print(f"RUN: run_id={run_id}")
    print(f"  cwd:  {cwd}")
    print(f"  nav:  {nav_arg}")
    print(f"  nmea: {nmea_arg}")
    print(f"  out:  {out_arg}")
    print(f"  bit_depth: {args.bit_depth}")
    print(f"  cmd:  {paths.gps_sdr_sim_cmdline_txt}")

    try:
        cp = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=int(args.timeout_sec),
        )
    except subprocess.TimeoutExpired as e:
        paths.stderr_gps_sdr_sim_log.write_text(f"TIMEOUT: {e}\n", encoding="utf-8")
        raise SystemExit(f"FAIL: timeout after {args.timeout_sec}s")

    paths.stdout_gps_sdr_sim_log.write_text(cp.stdout or "", encoding="utf-8")
    paths.stderr_gps_sdr_sim_log.write_text(cp.stderr or "", encoding="utf-8")

    print(f"EXIT: rc={cp.returncode}")

    if cp.returncode != 0:
        print("---- STDERR (tail) ----")
        print(_tail(cp.stderr) or "<empty>")
        print("---- STDOUT (tail) ----")
        print(_tail(cp.stdout) or "<empty>")
        raise SystemExit("FAIL: rc!=0")

    if not paths.sim_iq_bin.exists():
        raise SystemExit("FAIL: IQ file not created")

    size = paths.sim_iq_bin.stat().st_size
    if size <= 0:
        raise SystemExit("FAIL: IQ file size is 0")

    print("SELF-CHECK: PASS")
    print(f"  iq_size_bytes={size}")
    print(f"  stdout_log={paths.stdout_gps_sdr_sim_log}")
    print(f"  stderr_log={paths.stderr_gps_sdr_sim_log}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
