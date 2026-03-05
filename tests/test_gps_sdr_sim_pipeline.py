from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

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
    raise RuntimeError("cannot find repo root")


@pytest.mark.smoke
def test_gps_sdr_sim_pipeline(tmp_path: Path) -> None:
    # Explicit opt-in: this test runs external binaries and may require hardware.
    if os.getenv("GPS_SDR_SIM_SMOKE") != "1":
        pytest.skip("set GPS_SDR_SIM_SMOKE=1 to run this smoke test")

    repo_root = _find_repo_root(Path(__file__).resolve().parent)
    gps_exe = repo_root / "bin" / "gps_sdr_sim" / "gps-sdr-sim.exe"
    pluto_exe = repo_root / "bin" / "pluto" / "PlutoPlayer.exe"
    nav = repo_root / "data" / "ephemerides" / "brdc0430.25n"
    input_csv = repo_root / "app" / "trajectory.csv"

    if not gps_exe.exists():
        pytest.skip(f"missing gps-sdr-sim.exe: {gps_exe}")
    if not pluto_exe.exists():
        pytest.skip(f"missing PlutoPlayer.exe: {pluto_exe}")
    if not nav.exists():
        pytest.skip(f"missing nav file: {nav}")
    if not input_csv.exists():
        pytest.skip(f"missing input trajectory: {input_csv}")

    bus = EventBus()
    svc = GpsSdrSimService(bus)

    cfg = {
        "out_root": str(tmp_path),
        "input": str(input_csv),
        "origin_lat": 55.0,
        "origin_lon": 37.0,
        "origin_h": 0.0,
        "static_sec": 2.0,
        "copy_input": True,
        "gps_sdr_sim_exe": str(gps_exe),
        "pluto_exe": str(pluto_exe),
        "nav": str(nav),
        "bit_depth": 16,
        "gps_timeout_sec": 120,
        "gps_extra_args": "",
        "tx_atten_db": -20.0,
        "rf_bw_mhz": 3.0,
        "pluto_extra_args": "",
        "hold_sec": 2.0,
        "grace_sec": 5.0,
    }

    svc.start(profile_section=cfg)

    deadline = time.monotonic() + 300.0
    while True:
        st = svc.status()
        if st in (ServiceStatus.STOPPED, ServiceStatus.ERROR):
            break
        if time.monotonic() >= deadline:
            svc.stop()
            pytest.fail("timeout waiting for gps_sdr_sim pipeline to finish")
        time.sleep(0.2)

    assert svc.status() == ServiceStatus.STOPPED

    # Artifacts check (all 3 stages)
    run_root = tmp_path / "gps_sdr_sim"
    assert run_root.exists()
    run_dirs = [p for p in run_root.iterdir() if p.is_dir()]
    assert run_dirs, "no run directories created"
    run_dir = run_dirs[0]

    nmea_txt = run_dir / "input" / "nmea_strings.txt"
    iq_bin = run_dir / "sim" / "gpssim_iq.bin"
    pluto_log = run_dir / "logs" / "stderr_pluto.log"

    assert nmea_txt.exists() and nmea_txt.stat().st_size > 0
    assert iq_bin.exists() and iq_bin.stat().st_size > 0
    assert pluto_log.exists()
