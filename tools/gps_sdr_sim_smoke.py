from __future__ import annotations

import argparse
import time
from pathlib import Path

from app.core.event_bus import EventBus
from app.services.base import ServiceStatus
from app.services.gps_sdr_sim.service import GpsSdrSimService


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(8):
        if (cur / "app").is_dir() and (cur / "bin").is_dir() and (cur / "tools").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise SystemExit("FAIL: cannot find repo root (expected app/, bin/, tools/)")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run gps_sdr_sim service end-to-end (long smoke run)")

    p.add_argument("--out-root", default="outputs")
    p.add_argument("--run-id", default="", help="optional: reuse run_id (otherwise new)")

    p.add_argument("--input", required=True, help="trajectory.csv")
    p.add_argument("--origin-lat", type=float, required=True)
    p.add_argument("--origin-lon", type=float, required=True)
    p.add_argument("--origin-h", type=float, required=True)
    p.add_argument("--static-sec", type=float, default=200.0)

    p.add_argument("--gps-sdr-sim-exe", default="", help="override path to gps-sdr-sim.exe")
    p.add_argument("--pluto-exe", default="", help="override path to PlutoPlayer.exe")
    p.add_argument("--nav", required=True, help="ephemeris/nav file")
    p.add_argument("--bit-depth", type=int, choices=[8, 16], default=16)
    p.add_argument("--gps-timeout-sec", type=int, default=300)
    p.add_argument("--gps-extra-args", default="")

    p.add_argument("--tx-atten-db", type=float, default=-20.0)
    p.add_argument("--rf-bw-mhz", type=float, default=3.0)
    p.add_argument("--pluto-extra-args", default="")
    p.add_argument("--hold-sec", type=float, default=None, help="PlutoPlayer run duration (seconds); omit to wait for exit")
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
    nav = Path(args.nav).resolve()
    input_csv = Path(args.input).resolve()

    if not gps_exe.exists():
        raise SystemExit(f"FAIL: gps-sdr-sim.exe not found: {gps_exe}")
    if not pluto_exe.exists():
        raise SystemExit(f"FAIL: PlutoPlayer.exe not found: {pluto_exe}")
    if not nav.exists():
        raise SystemExit(f"FAIL: ephemeris not found: {nav}")
    if not input_csv.exists():
        raise SystemExit(f"FAIL: input csv not found: {input_csv}")

    cfg = {
        "out_root": str(out_root),
        "input": str(input_csv),
        "origin_lat": float(args.origin_lat),
        "origin_lon": float(args.origin_lon),
        "origin_h": float(args.origin_h),
        "static_sec": float(args.static_sec),
        "copy_input": True,
        "gps_sdr_sim_exe": str(gps_exe),
        "pluto_exe": str(pluto_exe),
        "nav": str(nav),
        "bit_depth": int(args.bit_depth),
        "gps_timeout_sec": int(args.gps_timeout_sec),
        "gps_extra_args": args.gps_extra_args,
        "tx_atten_db": float(args.tx_atten_db),
        "rf_bw_mhz": float(args.rf_bw_mhz),
        "pluto_extra_args": args.pluto_extra_args,
        "hold_sec": float(args.hold_sec) if args.hold_sec is not None else None,
        "grace_sec": float(args.grace_sec),
    }
    if args.run_id:
        cfg["run_id"] = args.run_id

    print("STAGE: config")
    print(f"  input_csv={input_csv}")
    print(f"  nav={nav}")
    print(f"  gps_exe={gps_exe}")
    print(f"  pluto_exe={pluto_exe}")
    print(f"  static_sec={args.static_sec}")
    print(f"  hold_sec={'<wait>' if args.hold_sec is None else args.hold_sec}")

    bus = EventBus()
    svc = GpsSdrSimService(bus)
    print("STAGE: start service")
    svc.start(profile_section=cfg)

    deadline = time.monotonic() + 3600.0
    last = None
    while True:
        st = svc.status()
        if st != last:
            print(f"STAGE: status={st.value}")
            last = st
        if st in (ServiceStatus.STOPPED, ServiceStatus.ERROR):
            break
        if time.monotonic() >= deadline:
            svc.stop()
            raise SystemExit("FAIL: timeout waiting for service to finish")
        time.sleep(0.2)

    if svc.status() != ServiceStatus.STOPPED:
        raise SystemExit("FAIL: service ended with ERROR")

    print("OK: gps_sdr_sim smoke finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
