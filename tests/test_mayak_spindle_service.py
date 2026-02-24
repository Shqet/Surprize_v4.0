from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from app.core.events import LogEvent, MayakHealthEvent
from app.services.mayak_spindle import MayakSpindleService, DictTransport
from app.services.base import ServiceStatus


@dataclass
class _Bus:
    events: List[object]

    def publish(self, event) -> None:
        self.events.append(event)


def _profile():
    return {
        "publish_period_ms": 10,
        "global_enable": True,
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


def test_start_stop_idempotent():
    bus = _Bus(events=[])
    tr = DictTransport(initial={
        "D1050": 1, "D1051": 1,
        "D1002": 0, "D1012": 0,
        "D1003": 0, "D1013": 0,
        "D1020": 0, "D1021": 0,
        "D1022": 0,
        "D1091": 0, "D1092": 0,
    })
    svc = MayakSpindleService(bus=bus, transport=tr)

    svc.start(_profile())
    assert svc.status() == ServiceStatus.RUNNING

    svc.start(_profile())
    assert svc.status() == ServiceStatus.RUNNING

    svc.stop()
    assert svc.status() == ServiceStatus.STOPPED

    svc.stop()
    assert svc.status() == ServiceStatus.STOPPED


def test_commands_written_to_cells():
    bus = _Bus(events=[])
    tr = DictTransport(initial={
        "D1050": 1, "D1051": 1,
        "D1002": 0, "D1012": 0,
        "D1003": 0, "D1013": 0,
        "D1020": 0, "D1021": 0,
        "D1022": 0,
        "D1091": 0, "D1092": 0,
    })
    svc = MayakSpindleService(bus=bus, transport=tr)
    svc.start(_profile())

    svc.set_spindle_speed("sp2", direction=1, rpm=123)
    time.sleep(0.05)

    snap = tr.snapshot()
    assert snap["D1090"] == 1
    assert snap["D1010"] == 0x000F
    assert snap["D1011"] == 123

    svc.stop_spindle("sp2")
    time.sleep(0.05)

    snap = tr.snapshot()
    assert snap["D1010"] == 0
    assert snap["D1011"] == 0

    svc.stop()


def test_fail_fast_invalid_config():
    bus = _Bus(events=[])
    tr = DictTransport(initial={})
    svc = MayakSpindleService(bus=bus, transport=tr)

    svc.start({"publish_period_ms": 10})
    assert svc.status() == ServiceStatus.ERROR


def test_readiness_and_state_api():
    bus = _Bus(events=[])
    tr = DictTransport(initial={
        "D1050": 1, "D1051": 1,
        "D1002": 0x0004, "D1012": 0x0004,
        "D1003": 0, "D1013": 0,
        "D1020": 0, "D1021": 0,
        "D1022": 0,
        "D1091": 0, "D1092": 0,
    })
    svc = MayakSpindleService(bus=bus, transport=tr)
    svc.start(_profile())
    time.sleep(0.05)

    assert svc.is_ready() is True
    assert svc.spindle_ready("sp1") is True
    assert svc.get_spindle_state("sp1") in ("READY", "STARTING", "MOVING", "STOPPING")
    snap = svc.get_health_snapshot()
    assert snap["io_degraded"] is False

    tr.write_cells({"D1092": 777})
    time.sleep(0.05)
    assert svc.is_ready() is False
    assert svc.get_spindle_state("sp1") == "FAULT"
    assert svc.get_spindle_state("sp2") == "FAULT"
    svc.stop()


def test_guard_blocks_command_when_global_disabled():
    bus = _Bus(events=[])
    tr = DictTransport(initial={
        "D1050": 1, "D1051": 1,
        "D1002": 0x0004, "D1012": 0x0004,
        "D1003": 0, "D1013": 0,
        "D1020": 0, "D1021": 0,
        "D1022": 0,
        "D1091": 0, "D1092": 0,
    })
    svc = MayakSpindleService(bus=bus, transport=tr)
    prof = _profile()
    prof["global_enable"] = False
    svc.start(prof)
    time.sleep(0.03)

    try:
        svc.set_spindle_speed("sp1", direction=1, rpm=100)
        assert False, "expected RuntimeError for global_enable OFF"
    except RuntimeError:
        pass
    finally:
        svc.stop()


def test_publishes_mayak_health_event():
    bus = _Bus(events=[])
    tr = DictTransport(initial={
        "D1050": 1, "D1051": 1,
        "D1002": 0x0004, "D1012": 0x0004,
        "D1003": 0, "D1013": 0,
        "D1020": 0, "D1021": 0,
        "D1022": 0,
        "D1091": 0, "D1092": 0,
    })
    svc = MayakSpindleService(bus=bus, transport=tr)
    svc.start(_profile())
    time.sleep(0.05)
    svc.stop()

    health = [e for e in bus.events if isinstance(e, MayakHealthEvent)]
    assert health, "expected MayakHealthEvent publication"
    assert health[-1].service_name == "mayak_spindle"


def test_restart_recreates_owned_transport_via_factory():
    bus = _Bus(events=[])
    created: list["_ClosableTransport"] = []

    class _ClosableTransport(DictTransport):
        def __init__(self):
            super().__init__(initial={
                "D1050": 1, "D1051": 1,
                "D1002": 0x0004, "D1012": 0x0004,
                "D1003": 0, "D1013": 0,
                "D1020": 0, "D1021": 0,
                "D1022": 0,
                "D1091": 0, "D1092": 0,
            })
            self.closed = False

        def close(self) -> None:
            self.closed = True

    def _factory(_profile_section: dict):
        tr = _ClosableTransport()
        created.append(tr)
        return tr

    svc = MayakSpindleService(bus=bus, transport_factory=_factory)
    svc.start(_profile())
    time.sleep(0.03)
    svc.stop()

    svc.start(_profile())
    time.sleep(0.03)
    svc.stop()

    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is True


def test_io_degraded_then_recovered_logs():
    bus = _Bus(events=[])

    class _FlakyTransport(DictTransport):
        def __init__(self):
            super().__init__(initial={
                "D1050": 1, "D1051": 1,
                "D1002": 0x0004, "D1012": 0x0004,
                "D1003": 0, "D1013": 0,
                "D1020": 0, "D1021": 0,
                "D1022": 0,
                "D1091": 0, "D1092": 0,
            })
            self.fail_reads = 5

        def read_cells(self, names):
            if self.fail_reads > 0:
                self.fail_reads -= 1
                raise RuntimeError("simulated_rx_error")
            return super().read_cells(names)

    tr = _FlakyTransport()
    svc = MayakSpindleService(bus=bus, transport=tr)
    svc.start(_profile())
    time.sleep(1.8)
    snap = svc.get_health_snapshot()
    svc.stop()

    logs = [e for e in bus.events if isinstance(e, LogEvent)]
    codes = [e.code for e in logs]
    assert "MAYAK_IO_DEGRADED" in codes
    assert "MAYAK_IO_RECOVERED" in codes
    assert snap["io_degraded"] is False
