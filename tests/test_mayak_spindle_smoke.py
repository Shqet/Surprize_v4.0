from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List

import pytest

from app.services.mayak_spindle import MayakSpindleService
from app.services.base import ServiceStatus


@dataclass
class _Bus:
    events: List[object]

    def publish(self, event) -> None:
        self.events.append(event)


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return int(port)


def _profile(bind_port: int, listen_port: int) -> dict:
    return {
        "publish_period_ms": 20,
        "global_enable": True,
        "runtime": {"command_timeout_ms": 2000},
        "transport": {
            "cnc_host": "127.0.0.1",
            "cnc_port": int(bind_port),
            "listen_host": "127.0.0.1",
            "listen_port": int(listen_port),
            "machine_size": 850592,
            "recv_timeout_sec": 0.2,
        },
        "d_map": {
            "SP1_ControlWord": "D1000",
            "SP1_TargetSpeed": "D1001",
            "SP1_StatusWord": "D1002",
            "SP1_ActualSpeed": "D1003",
            "SP2_ControlWord": "D1010",
            "SP2_TargetSpeed": "D1011",
            "SP2_StatusWord": "D1012",
            "SP2_ActualSpeed": "D1013",
            "SP1_ActualTorque": "D1020",
            "SP2_ActualTorque": "D1021",
            "SP1_Angle": "D1022",
            "SP1_Connected": "D1050",
            "SP2_Connected": "D1051",
            "Global_Enable": "D1090",
            "Sim_Time": "D1091",
            "Error_Code": "D1092",
        },
    }


@pytest.mark.smoke
def test_mayak_spindle_with_real_emulator():
    bind_port = _free_udp_port()
    listen_port = _free_udp_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "majak_sim",
            "--bind-host",
            "127.0.0.1",
            "--bind-port",
            str(bind_port),
            "--target-host",
            "127.0.0.1",
            "--target-port",
            str(listen_port),
            "--tx-interval",
            "0.01",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    bus = _Bus(events=[])
    svc = MayakSpindleService(bus=bus)
    try:
        time.sleep(0.2)
        svc.start(_profile(bind_port, listen_port))
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            hs = svc.get_health_snapshot()
            if hs.get("sp1_connected") is True and hs.get("sp2_connected") is True:
                break
            time.sleep(0.05)
        assert svc.status() == ServiceStatus.RUNNING

        svc.set_spindle_speed("sp2", direction=1, rpm=300)
        deadline = time.monotonic() + 3.0
        moving = False
        while time.monotonic() < deadline:
            if svc.get_spindle_state("sp2") in ("STARTING", "MOVING"):
                moving = True
                break
            time.sleep(0.05)
        assert moving, f"unexpected state={svc.get_spindle_state('sp2')}"
    finally:
        try:
            svc.stop()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
