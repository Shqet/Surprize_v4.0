# tools/test_gps_prepare.py
from __future__ import annotations

import argparse
from pathlib import Path

from app.services.gps_sdr_sim.engine import (
    make_run_id,
    build_run_paths,
    ensure_dirs,
    prepare_nmea_input,
    write_run_meta,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Step1 CLI test: convert trajectory.csv -> nmea_strings.txt with static prefix, write run.json"
    )

    p.add_argument("--out-root", default="outputs", help="outputs root (default: outputs)")
    p.add_argument("--input", required=True, help="path to input trajectory.csv")

    p.add_argument("--static-sec", type=float, default=0.0, help="static delay in seconds (real seconds)")
    p.add_argument("--origin-lat", type=float, required=True, help="origin latitude degrees (WGS84)")
    p.add_argument("--origin-lon", type=float, required=True, help="origin longitude degrees (WGS84)")
    p.add_argument("--origin-h", type=float, required=True, help="origin altitude meters (WGS84)")

    p.add_argument("--copy-input", action="store_true", help="copy input trajectory.csv into run_dir/input/")
    return p.parse_args()


def _extract_latlon_from_gga(line: str) -> tuple[str, str, str]:
    parts = line.strip().split(",")
    if len(parts) < 6 or not parts[0].endswith("GPGGA"):
        raise ValueError(f"Not a GPGGA line: {line!r}")
    time_field = parts[1]
    lat_field = parts[2] + "," + parts[3]
    lon_field = parts[4] + "," + parts[5]
    return lat_field, lon_field, time_field


def main() -> int:
    args = _parse_args()

    input_csv = Path(args.input).resolve()
    out_root = Path(args.out_root).resolve()

    run_id = make_run_id(prefix="gps_")
    paths = build_run_paths(out_root, run_id)
    ensure_dirs(paths)

    meta = prepare_nmea_input(
        input_trajectory_csv=input_csv,
        out_nmea_txt=paths.nmea_strings_txt,
        origin_lat_deg=float(args.origin_lat),
        origin_lon_deg=float(args.origin_lon),
        origin_h_m=float(args.origin_h),
        static_sec=float(args.static_sec),
    )
    write_run_meta(paths.run_meta_json, meta)

    if args.copy_input:
        paths.input_trajectory_copy.write_bytes(input_csv.read_bytes())

    print(f"OK: run_id={run_id}")
    print(f"  nmea: {paths.nmea_strings_txt}")
    print(f"  meta: {paths.run_meta_json}")

    # ------------------------
    # Self-checks
    # ------------------------
    lines = paths.nmea_strings_txt.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines:
        raise SystemExit("FAIL: nmea file is empty")

    if not lines[0].startswith("$GPGGA,000000.00,"):
        raise SystemExit(f"FAIL: first line time not 000000.00: {lines[0]!r}")
    if len(lines) > 1 and not lines[1].startswith("$GPGGA,000000.10,"):
        raise SystemExit(f"FAIL: second line time not 000000.10: {lines[1]!r}")

    static_lines = int(round(float(args.static_sec) * 10.0))  # 10Hz contract
    if static_lines > 0:
        if len(lines) < static_lines:
            raise SystemExit(f"FAIL: nmea has {len(lines)} lines < expected static_lines={static_lines}")

        lat0, lon0, _t0 = _extract_latlon_from_gga(lines[0])
        for i in range(static_lines):
            lati, loni, _ti = _extract_latlon_from_gga(lines[i])
            if lati != lat0 or loni != lon0:
                raise SystemExit(
                    f"FAIL: static prefix mismatch at line {i}: "
                    f"expected ({lat0} {lon0}) got ({lati} {loni})"
                )

    print("SELF-CHECK: PASS")
    print(f"  lines_total={len(lines)} static_sec={args.static_sec} static_lines={static_lines}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
