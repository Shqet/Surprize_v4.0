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
        "hard_limits": {
            "max_rpm_sp1": 6000,
            "max_rpm_sp2": 6000,
            "max_accel_rpm_s": 0.0,
            "max_torque": 100000,
        },
        "operator_limits": {
            "max_rpm_sp1": 6000,
            "max_rpm_sp2": 6000,
            "max_accel_rpm_s": 0.0,
            "max_torque": 100000,
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
            "Test_Start": "D1200",
            "Test_ProfileType": "D1201",
            "Test_Head_StartRpm": "D1202",
            "Test_Head_EndRpm": "D1203",
            "Test_Tail_StartRpm": "D1204",
            "Test_Tail_EndRpm": "D1205",
            "Test_DurationSec": "D1206",
            "Limit_MaxRpm_SP1": "D1210",
            "Limit_MaxRpm_SP2": "D1211",
            "Limit_MaxTorque": "D1212",
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


def test_start_test_writes_program_params_and_pulses_start():
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
    time.sleep(0.02)

    svc.start_test(
        head_start_rpm=120,
        head_end_rpm=900,
        tail_start_rpm=130,
        tail_end_rpm=950,
        profile_type="linear",
        duration_sec=12.0,
    )
    time.sleep(0.06)

    snap = tr.snapshot()
    assert snap["D1201"] == 1
    assert snap["D1202"] == 120
    assert snap["D1203"] == 900
    assert snap["D1204"] == 130
    assert snap["D1205"] == 950
    assert snap["D1206"] == 12
    assert snap["D1200"] == 0  # pulse must return to 0
    assert snap["D1210"] == 6000
    assert snap["D1211"] == 6000
    assert snap["D1212"] == 100000

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
    assert isinstance(health[-1].effective_max_rpm_sp1, int)
    assert isinstance(health[-1].effective_max_rpm_sp2, int)


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


def test_fault_reset_writes_reset_controlword():
    bus = _Bus(events=[])
    tr = DictTransport(initial={
        "D1050": 1, "D1051": 1,
        "D1002": 0x0008, "D1012": 0x0004,  # sp1 fault
        "D1003": 0, "D1013": 0,
        "D1020": 0, "D1021": 0,
        "D1022": 0,
        "D1091": 0, "D1092": 0,
    })
    svc = MayakSpindleService(bus=bus, transport=tr)
    svc.start(_profile())
    time.sleep(0.03)
    svc.fault_reset("sp1")
    time.sleep(0.03)
    snap = tr.snapshot()
    svc.stop()
    assert snap["D1000"] in (0x0080, 0x0007, 0x000F)


def test_rpm_limit_enforced():
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
    prof["hard_limits"] = {"max_rpm_sp1": 200, "max_rpm_sp2": 200, "max_accel_rpm_s": 0.0, "max_torque": 100000}
    prof["operator_limits"] = {"max_rpm_sp1": 200, "max_rpm_sp2": 200, "max_accel_rpm_s": 0.0, "max_torque": 100000}
    svc.start(prof)
    time.sleep(0.03)
    try:
        svc.set_spindle_speed("sp1", direction=1, rpm=500)
        assert False, "expected ValueError for rpm limit"
    except ValueError:
        pass
    finally:
        svc.stop()


def test_command_timeout_sets_fault_and_logs():
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
    prof["runtime"] = {"command_timeout_ms": 150}
    svc.start(prof)
    time.sleep(0.03)
    svc.set_spindle_speed("sp2", direction=1, rpm=100)
    # Force no movement so command cannot satisfy MOVE expectation.
    tr.write_cells({"D1013": 0, "D1012": 0x0000})
    time.sleep(0.35)
    hs = svc.get_health_snapshot()
    svc.stop()
    logs = [e for e in bus.events if isinstance(e, LogEvent)]
    assert any(e.code == "MAYAK_CMD_TIMEOUT" for e in logs)
    assert hs["error_code"] != 0


def test_metrics_log_emitted():
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
    prof["metrics"] = {"log_period_sec": 0.1}
    svc.start(prof)
    time.sleep(0.25)
    svc.stop()
    logs = [e for e in bus.events if isinstance(e, LogEvent)]
    assert any(e.code == "MAYAK_METRICS" for e in logs)


def test_operator_limits_cannot_exceed_hard():
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
    time.sleep(0.03)
    try:
        svc.set_operator_limits(max_rpm_sp1=7000)
        assert False, "expected ValueError"
    except ValueError:
        pass
    finally:
        svc.stop()


def test_hard_limits_require_privileged():
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
    time.sleep(0.03)
    try:
        svc.set_hard_limits(max_rpm_sp1=2000)
        assert False, "expected PermissionError"
    except PermissionError:
        pass
    finally:
        svc.stop()


def test_operator_limits_update_effective_limit():
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
    time.sleep(0.03)
    svc.set_operator_limits(max_rpm_sp1=100)
    hs = svc.get_health_snapshot()
    assert int(hs["effective_max_rpm_sp1"]) == 100
    try:
        svc.set_spindle_speed("sp1", direction=1, rpm=120)
        assert False, "expected ValueError for effective operator limit"
    except ValueError:
        pass
    finally:
        svc.stop()


def test_packet_age_degrades_readiness_until_recovered():
    bus = _Bus(events=[])

    class _AgingTransport(DictTransport):
        def __init__(self):
            super().__init__(initial={
                "D1050": 1, "D1051": 1,
                "D1002": 0x0004, "D1012": 0x0004,
                "D1003": 0, "D1013": 0,
                "D1020": 0, "D1021": 0,
                "D1022": 0,
                "D1091": 0, "D1092": 0,
            })
            self.stale = True

        def last_packet_age_sec(self) -> float:
            return 10.0 if self.stale else 0.0

    tr = _AgingTransport()
    svc = MayakSpindleService(bus=bus, transport=tr)
    svc.start(_profile())
    time.sleep(0.06)
    hs = svc.get_health_snapshot()
    assert hs["degraded_reason"] == "packet_age"
    assert hs["io_degraded"] is True
    assert svc.is_ready() is False

    tr.stale = False
    deadline = time.monotonic() + 0.6
    hs2 = svc.get_health_snapshot()
    while hs2["degraded_reason"] != "none" and time.monotonic() < deadline:
        time.sleep(0.02)
        hs2 = svc.get_health_snapshot()
    assert hs2["degraded_reason"] == "none"
    assert svc.is_ready() is True
    svc.stop()


def test_health_event_published_on_effective_limits_change():
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
    time.sleep(0.03)

    before = len([e for e in bus.events if isinstance(e, MayakHealthEvent)])
    svc.set_operator_limits(max_rpm_sp1=111)
    after_events = [e for e in bus.events if isinstance(e, MayakHealthEvent)]
    assert len(after_events) > before
    assert after_events[-1].effective_max_rpm_sp1 == 111
    svc.stop()
