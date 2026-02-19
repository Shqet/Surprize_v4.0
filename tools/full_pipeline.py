# tools/test_full_pipeline.py
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import time
from pathlib import Path

from app.services.gps_sdr_sim.engine import (
    make_run_id,
    build_run_paths,
    ensure_dirs,
    prepare_nmea_input,
    write_run_meta,
    save_cmdline,
)


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(8):
        if (cur / "app").is_dir() and (cur / "bin").is_dir() and (cur / "tools").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise SystemExit("FAIL: cannot find repo root (expected app/, bin/, tools/)")


def _validate_paths(gps_exe: Path, pluto_exe: Path, nav: Path) -> None:
    if not gps_exe.exists():
        raise SystemExit(f"FAIL: gps-sdr-sim.exe not found: {gps_exe}")
    if not pluto_exe.exists():
        raise SystemExit(f"FAIL: PlutoPlayer.exe not found: {pluto_exe}")
    if not nav.exists():
        raise SystemExit(f"FAIL: ephemeris not found: {nav}")


def _tail(path: Path, n: int = 40) -> str:
    txt = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    lines = txt.splitlines()
    return "\n".join(lines[-n:]) if lines else ""


def _terminate(proc: subprocess.Popen, grace: float) -> int:
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait(timeout=5.0)
    return int(proc.returncode or 0)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Full pipeline: CSV->NMEA->IQ->PlutoPlayer (single run_id)")

    p.add_argument("--out-root", default="outputs")
    p.add_argument("--run-id", default="", help="optional: reuse run_id (otherwise new)")

    # Step1
    p.add_argument("--input", required=True, help="trajectory.csv")
    p.add_argument("--origin-lat", type=float, required=True)
    p.add_argument("--origin-lon", type=float, required=True)
    p.add_argument("--origin-h", type=float, required=True)
    p.add_argument("--static-sec", type=float, default=0.0)

    # Step2
    p.add_argument("--gps-sdr-sim-exe", default="", help="override path to gps-sdr-sim.exe (default bin/gps_sdr_sim/...)")
    p.add_argument("--nav", required=True, help="ephemeris/nav file")
    p.add_argument("--bit-depth", type=int, choices=[8, 16], default=16)
    p.add_argument("--timeout-sec", type=int, default=120)
    p.add_argument("--gps-extra-args", default="", help="extra args for gps-sdr-sim (shlex parsed)")

    # Step3
    p.add_argument("--pluto-exe", default="", help="override path to PlutoPlayer.exe (default bin/pluto/...)")
    p.add_argument("--tx-atten-db", type=float, default=-20.0)
    p.add_argument("--rf-bw-mhz", type=float, default=3.0)
    p.add_argument("--pluto-extra-args", default="", help="extra args for PlutoPlayer (shlex parsed)")
    p.add_argument(
        "--hold-sec",
        type=float,
        default=None,
        help="If set, stop Pluto after N seconds. If omitted, wait until process exits (Ctrl+C to stop).",
    )

    p.add_argument("--grace-sec", type=float, default=5.0)

    return p.parse_args()


def main() -> int:
    args = _parse_args()

    repo_root = _find_repo_root(Path(__file__).resolve().parent)
    out_root = Path(args.out_root)
    out_root = out_root if out_root.is_absolute() else (repo_root / out_root)
    out_root = out_root.resolve()

    gps_exe = Path(args.gps_sdr_sim_exe).resolve() if args.gps_sdr_sim_exe else (repo_root / "bin/gps_sdr_sim/gps-sdr-sim.exe").resolve()
    pluto_exe = Path(args.pluto_exe).resolve() if args.pluto_exe else (repo_root / "bin/pluto/PlutoPlayer.exe").resolve()
    nav_src = Path(args.nav).resolve()
    _validate_paths(gps_exe, pluto_exe, nav_src)

    run_id = args.run_id or make_run_id(prefix="gps_")
    paths = build_run_paths(out_root, run_id)
    ensure_dirs(paths)

    # ----------------
    # Step1: CSV -> NMEA
    # ----------------
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

    # ----------------
    # Step2: gps-sdr-sim -> IQ
    # ----------------
    # Copy nav to sim dir and use REL paths + cwd=sim_dir (Windows-safe)
    nav_dst = paths.sim_dir / nav_src.name
    nav_dst.write_bytes(nav_src.read_bytes())

    cwd_sim = paths.sim_dir
    nav_arg = nav_dst.name
    nmea_arg = os.path.relpath(paths.nmea_strings_txt, start=cwd_sim)
    out_arg = os.path.relpath(paths.sim_iq_bin, start=cwd_sim)

    gps_argv = [
        str(gps_exe),
        "-e", nav_arg,
        "-g", nmea_arg,
        "-b", str(int(args.bit_depth)),
        "-o", out_arg,
    ]
    if args.gps_extra_args:
        gps_argv += shlex.split(args.gps_extra_args)

    save_cmdline(paths.gps_sdr_sim_cmdline_txt, gps_argv)

    cp = subprocess.run(
        gps_argv,
        cwd=str(cwd_sim),
        capture_output=True,
        text=True,
        timeout=int(args.timeout_sec),
    )
    paths.stdout_gps_sdr_sim_log.write_text(cp.stdout or "", encoding="utf-8")
    paths.stderr_gps_sdr_sim_log.write_text(cp.stderr or "", encoding="utf-8")
    if cp.returncode != 0:
        print("FAIL: gps-sdr-sim rc!=0")
        print("---- STDERR (tail) ----")
        print(_tail(paths.stderr_gps_sdr_sim_log) or "<empty>")
        return 1
    if not paths.sim_iq_bin.exists() or paths.sim_iq_bin.stat().st_size <= 0:
        print("FAIL: IQ not created")
        return 1

    # ----------------
    # Step3: PlutoPlayer (long process)
    # ----------------
    iq_dst = paths.pluto_dir / paths.sim_iq_bin.name
    iq_dst.write_bytes(paths.sim_iq_bin.read_bytes())

    cwd_pluto = paths.pluto_dir
    iq_arg = iq_dst.name

    pluto_argv = [
        str(pluto_exe),
        "-t", iq_arg,
        "-a", f"{args.tx_atten_db:.2f}",
        "-b", f"{args.rf_bw_mhz:.2f}",
    ]
    if args.pluto_extra_args:
        pluto_argv += shlex.split(args.pluto_extra_args)

    save_cmdline(paths.pluto_cmdline_txt, pluto_argv)

    print(f"RUN: run_id={run_id}")
    print(f"  out_dir: {paths.run_dir}")
    print(f"  nmea:    {paths.nmea_strings_txt}")
    print(f"  iq:      {paths.sim_iq_bin}")
    print(f"  pluto:   hold_sec={args.hold_sec}")

    start_ts = time.time()

    with paths.stdout_pluto_log.open("w", encoding="utf-8", errors="ignore") as f_out, \
            paths.stderr_pluto_log.open("w", encoding="utf-8", errors="ignore") as f_err:

        p = subprocess.Popen(pluto_argv, cwd=str(cwd_pluto), stdout=f_out, stderr=f_err, text=True)
        print(f"PID: {p.pid}")

        try:
            if args.hold_sec is None:
                print("MODE: wait-until-exit (Ctrl+C to stop)")
                while True:
                    rc = p.poll()
                    if rc is not None:
                        print(f"PLUTO_EXIT: rc={rc}")
                        break
                    time.sleep(0.5)
            else:
                print(f"MODE: timed run ({args.hold_sec} sec)")
                time.sleep(max(0.0, float(args.hold_sec)))
                if p.poll() is None:
                    rc = _terminate(p, float(args.grace_sec))
                    print(f"PLUTO_EXIT: rc={rc}")
                else:
                    print(f"NOTE: process already exited rc={p.returncode}")

        except KeyboardInterrupt:
            print("INTERRUPT: stopping Pluto...")
            if p.poll() is None:
                rc = _terminate(p, float(args.grace_sec))
                print(f"PLUTO_EXIT: rc={rc}")
            else:
                print(f"NOTE: process already exited rc={p.returncode}")

    elapsed = time.time() - start_ts
    print(f"elapsed_sec={elapsed:.1f}")

    print("SELF-CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
