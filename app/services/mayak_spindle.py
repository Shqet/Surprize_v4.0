from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Protocol, Tuple

from app.core.events import ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.events.mayak_spindle_events import MayakSpindleCommandEvent, MayakSpindleTelemetryEvent
from app.services.base import ServiceStatus


class EventBus(Protocol):
    def publish(self, event) -> None: ...


class MayakTransport(Protocol):
    """Abstract transport: real (UDP) or fake (unit tests)."""

    def read_cells(self, names: Iterable[str]) -> Dict[str, int]: ...
    def write_cells(self, values: Dict[str, int]) -> None: ...


class DictTransport:
    """In-memory transport for unit-tests (no network)."""

    def __init__(self, initial: Optional[Dict[str, int]] = None):
        self._lock = threading.Lock()
        self._cells: Dict[str, int] = dict(initial or {})

    def read_cells(self, names: Iterable[str]) -> Dict[str, int]:
        with self._lock:
            return {n: int(self._cells.get(n, 0)) for n in names}

    def write_cells(self, values: Dict[str, int]) -> None:
        with self._lock:
            for k, v in values.items():
                self._cells[str(k)] = int(v)

    # test helper
    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._cells)


def _crc16_ones_complement_22b(first_22: bytes) -> int:
    if len(first_22) != 22:
        raise ValueError("CRC computed over 22 bytes")
    total = 0
    for i in range(0, 22, 2):
        word = first_22[i] | (first_22[i + 1] << 8)
        total += word
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _pack_d_packet(machine_size: int, index: int, value: int, name: str) -> bytes:
    name_b = name.encode("ascii", errors="ignore")[:10].ljust(10, b"\x00")
    header = struct.pack("<IIi10s", int(machine_size), int(index), int(value), name_b)
    crc = _crc16_ones_complement_22b(header)
    return header + struct.pack("<H", crc)


class MayakUdpTransport:
    """UDP transport compatible with majak_sim/emulator.py packet format."""

    def __init__(
        self,
        *,
        cnc_host: str = "127.0.0.1",
        cnc_port: int = 12346,
        listen_host: str = "0.0.0.0",
        listen_port: int = 12345,
        machine_size: int = 850592,
        recv_timeout_sec: float = 0.2,
    ) -> None:
        self._lock = threading.Lock()
        self._cells: Dict[str, int] = {}
        self._indices: Dict[str, int] = {}
        self._next_index = 20000
        self._machine_size = int(machine_size)
        self._target = (str(cnc_host), int(cnc_port))

        self._stop_evt = threading.Event()
        self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._rx_sock.bind((str(listen_host), int(listen_port)))
        self._rx_sock.settimeout(float(recv_timeout_sec))

        self._tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._rx_thread = threading.Thread(target=self._rx_loop, name="mayak_udp_rx", daemon=True)
        self._rx_thread.start()

    def _allocate_index(self, name: str) -> int:
        with self._lock:
            idx = self._indices.get(name)
            if idx is not None:
                return idx
            idx = self._next_index
            self._next_index += 4
            self._indices[name] = idx
            return idx

    def _rx_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                data, _addr = self._rx_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if len(data) != 24:
                continue

            try:
                machine_size, index, value, name_b, crc = struct.unpack("<IIi10sH", data)
            except struct.error:
                continue

            if crc != _crc16_ones_complement_22b(data[:22]):
                continue

            name = name_b.rstrip(b"\x00").decode("ascii", errors="ignore")
            if not name:
                continue

            with self._lock:
                self._cells[name] = int(value)
                if name not in self._indices:
                    self._indices[name] = int(index)

    def read_cells(self, names: Iterable[str]) -> Dict[str, int]:
        with self._lock:
            return {n: int(self._cells.get(n, 0)) for n in names}

    def write_cells(self, values: Dict[str, int]) -> None:
        for name, value in values.items():
            n = str(name)
            idx = self._allocate_index(n)
            pkt = _pack_d_packet(self._machine_size, idx, int(value), n)
            self._tx_sock.sendto(pkt, self._target)
            with self._lock:
                self._cells[n] = int(value)

    def close(self) -> None:
        self._stop_evt.set()
        try:
            self._rx_sock.close()
        except Exception:
            pass
        try:
            self._tx_sock.close()
        except Exception:
            pass
        if self._rx_thread.is_alive():
            self._rx_thread.join(timeout=0.5)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _kv(**kwargs) -> str:
    parts = []
    for k, v in kwargs.items():
        if v is None:
            continue
        s = str(v)
        if " " in s:
            s = s.replace(" ", "_")
        parts.append(f"{k}={s}")
    return " ".join(parts)


def _require(profile: dict, key: str):
    if key not in profile:
        raise ValueError(f"missing required field: {key}")


@dataclass(frozen=True, slots=True)
class _DMap:
    # Commands (OUT)
    sp1_cw: str
    sp1_tgt: str
    sp2_cw: str
    sp2_tgt: str
    global_enable: str

    # Telemetry (IN)
    sp1_sw: str
    sp1_act: str
    sp2_sw: str
    sp2_act: str
    sp1_torque: str
    sp2_torque: str
    sp1_angle: str
    sp1_connected: str
    sp2_connected: str
    sim_time: str
    error_code: str

    @staticmethod
    def from_profile(d_map: Dict[str, str]) -> "_DMap":
        req = [
            "SP1_ControlWord", "SP1_TargetSpeed", "SP1_StatusWord", "SP1_ActualSpeed",
            "SP2_ControlWord", "SP2_TargetSpeed", "SP2_StatusWord", "SP2_ActualSpeed",
            "SP1_ActualTorque", "SP2_ActualTorque", "SP1_Angle",
            "SP1_Connected", "SP2_Connected",
            "Global_Enable", "Sim_Time", "Error_Code",
        ]
        for k in req:
            if k not in d_map:
                raise ValueError(f"d_map missing key: {k}")

        return _DMap(
            sp1_cw=d_map["SP1_ControlWord"],
            sp1_tgt=d_map["SP1_TargetSpeed"],
            sp2_cw=d_map["SP2_ControlWord"],
            sp2_tgt=d_map["SP2_TargetSpeed"],
            global_enable=d_map["Global_Enable"],
            sp1_sw=d_map["SP1_StatusWord"],
            sp1_act=d_map["SP1_ActualSpeed"],
            sp2_sw=d_map["SP2_StatusWord"],
            sp2_act=d_map["SP2_ActualSpeed"],
            sp1_torque=d_map["SP1_ActualTorque"],
            sp2_torque=d_map["SP2_ActualTorque"],
            sp1_angle=d_map["SP1_Angle"],
            sp1_connected=d_map["SP1_Connected"],
            sp2_connected=d_map["SP2_Connected"],
            sim_time=d_map["Sim_Time"],
            error_code=d_map["Error_Code"],
        )


class MayakSpindleService:
    """Spindle control + telemetry for 'Маяк' (v1).

    - No UI imports.
    - No Orchestrator imports.
    - No service-to-service calls.
    - Testable without network by injecting DictTransport.
    """

    name = "mayak_spindle"

    def __init__(self, bus: EventBus, transport: MayakTransport):
        self._bus = bus
        self._tr = transport

        self._lock = threading.Lock()
        self._state: ServiceStatus = ServiceStatus.STOPPED

        self._stop_evt = threading.Event()
        self._thr: Optional[threading.Thread] = None

        self._d: Optional[_DMap] = None
        self._publish_period_ms = 50

        # pending commands
        self._global_enable: Optional[bool] = None
        self._sp_cmd: Dict[str, Tuple[Optional[int], Optional[int]]] = {
            "sp1": (None, None),  # (control_word, target_speed_rpm)
            "sp2": (None, None),
        }
        self._sp_stage: Dict[str, int] = {
            "sp1": 0,
            "sp2": 0,
        }

        self._last_tel: Dict[str, Tuple] = {}

    # -----------------
    # Required public API
    # -----------------
    def start(self, profile_section: dict) -> None:
        with self._lock:
            if self._state in (ServiceStatus.RUNNING, ServiceStatus.STARTING):
                return
            self._state = ServiceStatus.STARTING

        self._emit_status(ServiceStatus.STARTING)
        self._emit_log("INFO", "SERVICE_START", _kv(service=self.name))

        try:
            _require(profile_section, "d_map")
            d_map = profile_section["d_map"]
            if not isinstance(d_map, dict):
                raise ValueError("d_map must be dict[str,str]")

            self._d = _DMap.from_profile({str(k): str(v) for k, v in d_map.items()})

            period = int(profile_section.get("publish_period_ms", 50))
            if period <= 0:
                raise ValueError("publish_period_ms must be > 0")
            self._publish_period_ms = period

            if "global_enable" in profile_section:
                self._global_enable = bool(profile_section["global_enable"])

        except Exception as e:
            with self._lock:
                self._state = ServiceStatus.ERROR
            self._emit_status(ServiceStatus.ERROR)
            self._emit_log("ERROR", "SERVICE_ERROR", _kv(service=self.name, error=str(e)))
            return

        self._stop_evt.clear()
        self._thr = threading.Thread(target=self._worker, name="mayak_spindle_worker", daemon=True)
        self._thr.start()

        with self._lock:
            self._state = ServiceStatus.RUNNING
        self._emit_status(ServiceStatus.RUNNING)
        self._emit_log("INFO", "SERVICE_RUNNING", _kv(service=self.name, period_ms=self._publish_period_ms))

    def stop(self) -> None:
        with self._lock:
            if self._state in (ServiceStatus.STOPPED, ServiceStatus.STOPPING):
                return
            self._state = ServiceStatus.STOPPING
        self._emit_status(ServiceStatus.STOPPING)
        self._emit_log("INFO", "SERVICE_STOP", _kv(service=self.name))

        self._stop_evt.set()
        thr = self._thr
        if thr and thr.is_alive():
            thr.join(timeout=2.0)

        with self._lock:
            if self._state != ServiceStatus.ERROR:
                self._state = ServiceStatus.STOPPED
        self._emit_status(ServiceStatus.STOPPED)
        self._emit_log("INFO", "SERVICE_STOPPED", _kv(service=self.name))
        close = getattr(self._tr, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                self._emit_log("ERROR", "SERVICE_ERROR", _kv(service=self.name, error="transport_close_failed"))

    def status(self) -> ServiceStatus:
        with self._lock:
            return self._state

    # -----------------
    # Command API (service-specific)
    # -----------------
    def set_global_enable(self, enabled: bool) -> None:
        with self._lock:
            self._global_enable = bool(enabled)
        self._bus.publish(MayakSpindleCommandEvent(
            service=self.name, spindle="global",
            global_enable=bool(enabled), control_word=None, target_speed_rpm=None, ts=time.time(),
        ))

    def set_spindle_speed(self, spindle: str, *, direction: int, rpm: int) -> None:
        """direction: +1 / -1 / 0 (stop)."""
        sp = spindle.lower().strip()
        if sp not in ("sp1", "sp2"):
            raise ValueError("spindle must be 'sp1' or 'sp2'")
        if direction not in (-1, 0, 1):
            raise ValueError("direction must be -1, 0, or 1")
        if rpm < 0:
            raise ValueError("rpm must be >= 0")

        target = int(rpm * direction)
        cw = 0x0000 if direction == 0 else 0x0006

        with self._lock:
            self._sp_cmd[sp] = (cw, target)
            self._sp_stage[sp] = 0 if direction == 0 else 1

        self._bus.publish(MayakSpindleCommandEvent(
            service=self.name, spindle=sp,
            global_enable=None, control_word=cw, target_speed_rpm=target, ts=time.time(),
        ))

    def stop_spindle(self, spindle: str) -> None:
        self.set_spindle_speed(spindle, direction=0, rpm=0)

    # -----------------
    # Worker loop
    # -----------------
    def _worker(self) -> None:
        assert self._d is not None
        d = self._d
        period_s = self._publish_period_ms / 1000.0

        in_cells = [
            d.sp1_sw, d.sp1_act, d.sp2_sw, d.sp2_act,
            d.sp1_torque, d.sp2_torque, d.sp1_angle,
            d.sp1_connected, d.sp2_connected,
            d.sim_time, d.error_code,
        ]

        while not self._stop_evt.is_set():
            out: Dict[str, int] = {}
            with self._lock:
                ge = self._global_enable
                sp1_cw, sp1_tgt = self._sp_cmd["sp1"]
                sp2_cw, sp2_tgt = self._sp_cmd["sp2"]
                sp1_stage = self._sp_stage["sp1"]
                sp2_stage = self._sp_stage["sp2"]

                if sp1_stage == 1:
                    sp1_cw = 0x0007
                    self._sp_cmd["sp1"] = (sp1_cw, sp1_tgt)
                    self._sp_stage["sp1"] = 2
                elif sp1_stage == 2:
                    sp1_cw = 0x000F
                    self._sp_cmd["sp1"] = (sp1_cw, sp1_tgt)
                    self._sp_stage["sp1"] = 0

                if sp2_stage == 1:
                    sp2_cw = 0x0007
                    self._sp_cmd["sp2"] = (sp2_cw, sp2_tgt)
                    self._sp_stage["sp2"] = 2
                elif sp2_stage == 2:
                    sp2_cw = 0x000F
                    self._sp_cmd["sp2"] = (sp2_cw, sp2_tgt)
                    self._sp_stage["sp2"] = 0

            if ge is not None:
                out[d.global_enable] = 1 if ge else 0
            if sp1_cw is not None:
                out[d.sp1_cw] = int(sp1_cw)
            if sp1_tgt is not None:
                out[d.sp1_tgt] = int(sp1_tgt)
            if sp2_cw is not None:
                out[d.sp2_cw] = int(sp2_cw)
            if sp2_tgt is not None:
                out[d.sp2_tgt] = int(sp2_tgt)

            if out:
                try:
                    self._tr.write_cells(out)
                except Exception as e:
                    self._emit_log("ERROR", "MAYAK_TX_ERROR", _kv(service=self.name, error=str(e)))

            try:
                vals = self._tr.read_cells(in_cells)
            except Exception as e:
                self._emit_log("ERROR", "MAYAK_RX_ERROR", _kv(service=self.name, error=str(e)))
                time.sleep(period_s)
                continue

            sim_time = int(vals.get(d.sim_time, 0))
            err = int(vals.get(d.error_code, 0))

            self._publish_tel(
                spindle="sp1",
                connected=bool(vals.get(d.sp1_connected, 0)),
                status_word=int(vals.get(d.sp1_sw, 0)),
                actual_speed=int(vals.get(d.sp1_act, 0)),
                actual_torque=int(vals.get(d.sp1_torque, 0)),
                angle=int(vals.get(d.sp1_angle, 0)),
                sim_time=sim_time,
                error_code=err,
            )

            self._publish_tel(
                spindle="sp2",
                connected=bool(vals.get(d.sp2_connected, 0)),
                status_word=int(vals.get(d.sp2_sw, 0)),
                actual_speed=int(vals.get(d.sp2_act, 0)),
                actual_torque=int(vals.get(d.sp2_torque, 0)),
                angle=None,
                sim_time=sim_time,
                error_code=err,
            )

            time.sleep(period_s)

    def _publish_tel(
        self,
        *,
        spindle: str,
        connected: bool,
        status_word: int,
        actual_speed: int,
        actual_torque: int,
        angle: Optional[int],
        sim_time: Optional[int],
        error_code: Optional[int],
    ) -> None:
        snap = (connected, status_word, actual_speed, actual_torque, angle, sim_time, error_code)
        self._last_tel[spindle] = snap

        self._bus.publish(MayakSpindleTelemetryEvent(
            service=self.name,
            spindle=spindle,
            connected=connected,
            status_word=status_word,
            actual_speed_rpm=actual_speed,
            actual_torque=actual_torque,
            angle_deg=angle,
            sim_time_ms=sim_time,
            error_code=error_code,
            ts=time.time(),
        ))

    # -----------------
    # Emit helpers
    # -----------------
    def _emit_status(self, status: ServiceStatus) -> None:
        self._bus.publish(ServiceStatusEvent(service_name=self.name, status=status.value))

    def _emit_log(self, level: str, code: str, message: str) -> None:
        emit_log(self._bus, level=level, source=self.name, code=code, message=message)
