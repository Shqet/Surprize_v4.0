from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Protocol, Tuple

from app.core.events import MayakHealthEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.events.mayak_spindle_events import MayakSpindleCommandEvent, MayakSpindleTelemetryEvent
from app.services.base import ServiceStatus


class EventBus(Protocol):
    def publish(self, event) -> None: ...


class MayakTransport(Protocol):
    """Abstract transport: real (UDP) or fake (unit tests)."""

    def read_cells(self, names: Iterable[str]) -> Dict[str, int]: ...
    def write_cells(self, values: Dict[str, int]) -> None: ...
    def last_packet_age_sec(self) -> float: ...


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

    def last_packet_age_sec(self) -> float:
        return 0.0


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
        self._last_rx_ts = time.monotonic()

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
                self._last_rx_ts = time.monotonic()

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

    def last_packet_age_sec(self) -> float:
        with self._lock:
            return max(0.0, time.monotonic() - self._last_rx_ts)

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


def _as_dict(v: object) -> dict:
    return v if isinstance(v, dict) else {}


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


def _transport_from_profile(profile_section: dict) -> MayakTransport:
    tcfg = _as_dict(profile_section.get("transport"))
    return MayakUdpTransport(
        cnc_host=str(tcfg.get("cnc_host", "127.0.0.1")),
        cnc_port=int(tcfg.get("cnc_port", 12346)),
        listen_host=str(tcfg.get("listen_host", "0.0.0.0")),
        listen_port=int(tcfg.get("listen_port", 12345)),
        machine_size=int(tcfg.get("machine_size", 850592)),
        recv_timeout_sec=float(tcfg.get("recv_timeout_sec", 0.2)),
    )


class MayakSpindleService:
    """Spindle control + telemetry for 'Маяк' (v1).

    - No UI imports.
    - No Orchestrator imports.
    - No service-to-service calls.
    - Testable without network by injecting DictTransport.
    """

    name = "mayak_spindle"

    def __init__(
        self,
        bus: EventBus,
        transport: Optional[MayakTransport] = None,
        transport_factory: Optional[Callable[[dict], MayakTransport]] = None,
    ):
        self._bus = bus
        self._tr: Optional[MayakTransport] = transport
        self._transport_factory = transport_factory
        self._owns_transport = transport is None

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
        self._spindle_state: Dict[str, str] = {"sp1": "UNKNOWN", "sp2": "UNKNOWN"}
        self._spindle_fault: Dict[str, bool] = {"sp1": False, "sp2": False}
        self._spindle_connected: Dict[str, Optional[bool]] = {"sp1": None, "sp2": None}
        self._last_error_code: int = 0
        self._rpm_moving_threshold = 5

        self._io_error_streak = 0
        self._io_error_threshold = 5
        self._io_backoff_s = 0.0
        self._last_health_event_key: Optional[Tuple[object, ...]] = None
        self._last_ready: Optional[bool] = None
        self._last_packet_age_sec: float = 0.0

        self._max_rpm: Dict[str, int] = {"sp1": 6000, "sp2": 6000}
        self._max_accel_rpm_s: float = 0.0
        self._max_torque: int = 100000
        self._command_timeout_ms: int = 1500
        self._pending_deadline: Dict[str, Optional[float]] = {"sp1": None, "sp2": None}
        self._pending_expect: Dict[str, str] = {"sp1": "", "sp2": ""}
        self._last_cmd_ts: Dict[str, Optional[float]] = {"sp1": None, "sp2": None}
        self._last_cmd_target: Dict[str, int] = {"sp1": 0, "sp2": 0}
        self._sp_force_cw: Dict[str, Optional[int]] = {"sp1": None, "sp2": None}

        self._watchdog_cell: Optional[str] = None
        self._watchdog_counter: int = 0
        self._watchdog_max_packet_age_sec: float = 1.0

        self._metrics_log_period_sec: float = 2.0
        self._metrics_last_log_ts: float = time.monotonic()

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

        created_transport = False
        try:
            if self._tr is None:
                if self._transport_factory is not None:
                    self._tr = self._transport_factory(profile_section)
                else:
                    self._tr = _transport_from_profile(profile_section)
                self._owns_transport = True
                created_transport = True

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

            limits = _as_dict(profile_section.get("limits"))
            self._max_rpm["sp1"] = int(limits.get("max_rpm_sp1", 6000))
            self._max_rpm["sp2"] = int(limits.get("max_rpm_sp2", 6000))
            self._max_accel_rpm_s = float(limits.get("max_accel_rpm_s", 0.0))
            self._max_torque = int(limits.get("max_torque", 100000))
            if self._max_rpm["sp1"] <= 0 or self._max_rpm["sp2"] <= 0:
                raise ValueError("max_rpm must be > 0")
            if self._max_accel_rpm_s < 0:
                raise ValueError("max_accel_rpm_s must be >= 0")
            if self._max_torque <= 0:
                raise ValueError("max_torque must be > 0")

            runtime = _as_dict(profile_section.get("runtime"))
            self._command_timeout_ms = int(runtime.get("command_timeout_ms", 1500))
            if self._command_timeout_ms <= 0:
                raise ValueError("command_timeout_ms must be > 0")

            watchdog = _as_dict(profile_section.get("watchdog"))
            wcell = watchdog.get("cell")
            self._watchdog_cell = str(wcell) if isinstance(wcell, str) and wcell.strip() else None
            self._watchdog_max_packet_age_sec = float(watchdog.get("max_packet_age_sec", 1.0))
            if self._watchdog_max_packet_age_sec <= 0:
                raise ValueError("watchdog.max_packet_age_sec must be > 0")

            metrics = _as_dict(profile_section.get("metrics"))
            self._metrics_log_period_sec = float(metrics.get("log_period_sec", 2.0))
            if self._metrics_log_period_sec <= 0:
                raise ValueError("metrics.log_period_sec must be > 0")

        except Exception as e:
            if created_transport and self._tr is not None:
                close = getattr(self._tr, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                self._tr = None
            with self._lock:
                self._state = ServiceStatus.ERROR
            self._emit_status(ServiceStatus.ERROR)
            self._emit_log("ERROR", "SERVICE_ERROR", _kv(service=self.name, error=str(e)))
            return

        self._stop_evt.clear()
        self._pending_deadline = {"sp1": None, "sp2": None}
        self._pending_expect = {"sp1": "", "sp2": ""}
        self._sp_force_cw = {"sp1": None, "sp2": None}
        self._thr = threading.Thread(target=self._worker, name="mayak_spindle_worker", daemon=True)
        self._thr.start()

        with self._lock:
            self._state = ServiceStatus.RUNNING
        self._emit_status(ServiceStatus.RUNNING)
        self._emit_log("INFO", "SERVICE_RUNNING", _kv(service=self.name, period_ms=self._publish_period_ms))
        self._publish_health_event()

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
        if self._owns_transport and self._tr is not None:
            close = getattr(self._tr, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    self._emit_log("ERROR", "SERVICE_ERROR", _kv(service=self.name, error="transport_close_failed"))
            self._tr = None
        self._publish_health_event()

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
        if rpm > self._max_rpm[sp]:
            raise ValueError(f"rpm exceeds limit for {sp}: {self._max_rpm[sp]}")
        if self.status() != ServiceStatus.RUNNING:
            raise RuntimeError("service is not RUNNING")

        with self._lock:
            ge = self._global_enable
            connected = self._spindle_connected.get(sp)
            fault = self._spindle_fault.get(sp, False)
            err_code = self._last_error_code
            last_cmd_ts = self._last_cmd_ts.get(sp)
            last_target = self._last_cmd_target.get(sp, 0)
            last_tel = self._last_tel.get(sp)
        if ge is False:
            raise RuntimeError("global_enable is OFF")
        if connected is False:
            raise RuntimeError(f"{sp} is not connected")
        if fault or err_code != 0:
            raise RuntimeError(f"{sp} is in FAULT")
        if last_tel is not None:
            torque = int(last_tel[3])
            if abs(torque) > self._max_torque:
                raise RuntimeError(f"{sp} torque limit exceeded")

        target = int(rpm * direction)
        now = time.monotonic()
        if self._max_accel_rpm_s > 0 and last_cmd_ts is not None:
            dt = max(1e-3, now - last_cmd_ts)
            accel = abs(target - int(last_target)) / dt
            if accel > self._max_accel_rpm_s:
                raise ValueError("command acceleration limit exceeded")
        cw = 0x0000 if direction == 0 else 0x0006

        with self._lock:
            self._sp_cmd[sp] = (cw, target)
            self._sp_stage[sp] = 0 if direction == 0 else 1
            self._sp_force_cw[sp] = None
            self._last_cmd_ts[sp] = now
            self._last_cmd_target[sp] = target
            self._pending_deadline[sp] = time.monotonic() + (float(self._command_timeout_ms) / 1000.0)
            self._pending_expect[sp] = "STOP" if direction == 0 else "MOVE"

        self._bus.publish(MayakSpindleCommandEvent(
            service=self.name, spindle=sp,
            global_enable=None, control_word=cw, target_speed_rpm=target, ts=time.time(),
        ))

    def stop_spindle(self, spindle: str) -> None:
        self.set_spindle_speed(spindle, direction=0, rpm=0)

    def fault_reset(self, spindle: str) -> None:
        sp = spindle.lower().strip()
        if sp not in ("sp1", "sp2"):
            raise ValueError("spindle must be 'sp1' or 'sp2'")
        if self.status() != ServiceStatus.RUNNING:
            raise RuntimeError("service is not RUNNING")
        with self._lock:
            self._sp_cmd[sp] = (0x0000, 0)
            self._sp_force_cw[sp] = 0x0080
            self._sp_stage[sp] = 1
            self._spindle_fault[sp] = False
            if self._last_error_code >= 9000:
                self._last_error_code = 0
            self._last_cmd_ts[sp] = time.monotonic()
            self._last_cmd_target[sp] = 0
            self._pending_deadline[sp] = time.monotonic() + (float(self._command_timeout_ms) / 1000.0)
            self._pending_expect[sp] = "RESET"
        self._bus.publish(
            MayakSpindleCommandEvent(
                service=self.name,
                spindle=sp,
                global_enable=None,
                control_word=0x0080,
                target_speed_rpm=0,
                ts=time.time(),
            )
        )

    # -----------------
    # Readiness API (v2)
    # -----------------
    def get_spindle_state(self, spindle: str) -> str:
        sp = spindle.lower().strip()
        if sp not in ("sp1", "sp2"):
            raise ValueError("spindle must be 'sp1' or 'sp2'")
        with self._lock:
            return self._spindle_state.get(sp, "UNKNOWN")

    def spindle_ready(self, spindle: str) -> bool:
        st = self.get_spindle_state(spindle)
        return st in ("READY", "MOVING", "STARTING", "STOPPING")

    def is_ready(self) -> bool:
        with self._lock:
            if self._state != ServiceStatus.RUNNING:
                return False
            if self._global_enable is False:
                return False
            if self._last_error_code != 0:
                return False
            io_bad = self._io_error_streak >= self._io_error_threshold
            sp1 = self._spindle_state.get("sp1", "UNKNOWN")
            sp2 = self._spindle_state.get("sp2", "UNKNOWN")
        if io_bad:
            return False
        return sp1 in ("READY", "MOVING", "STARTING", "STOPPING") and sp2 in (
            "READY",
            "MOVING",
            "STARTING",
            "STOPPING",
        )

    def get_health_snapshot(self) -> Dict[str, object]:
        with self._lock:
            degraded_reason = self._degraded_reason_locked()
            return {
                "service_status": self._state.value,
                "global_enable": self._global_enable,
                "error_code": self._last_error_code,
                "io_error_streak": self._io_error_streak,
                "io_degraded": self._io_error_streak >= self._io_error_threshold,
                "degraded_reason": degraded_reason,
                "sp1_state": self._spindle_state.get("sp1", "UNKNOWN"),
                "sp2_state": self._spindle_state.get("sp2", "UNKNOWN"),
                "sp1_connected": self._spindle_connected.get("sp1"),
                "sp2_connected": self._spindle_connected.get("sp2"),
                "last_packet_age_ms": int(self._last_packet_age_sec * 1000.0),
            }

    # -----------------
    # Worker loop
    # -----------------
    def _worker(self) -> None:
        assert self._d is not None
        d = self._d
        tr = self._tr
        if tr is None:
            self._emit_log("ERROR", "SERVICE_ERROR", _kv(service=self.name, error="transport_none"))
            with self._lock:
                self._state = ServiceStatus.ERROR
            self._emit_status(ServiceStatus.ERROR)
            return
        period_s = self._publish_period_ms / 1000.0

        in_cells = [
            d.sp1_sw, d.sp1_act, d.sp2_sw, d.sp2_act,
            d.sp1_torque, d.sp2_torque, d.sp1_angle,
            d.sp1_connected, d.sp2_connected,
            d.sim_time, d.error_code,
        ]
        if self._watchdog_cell is not None:
            in_cells.append(self._watchdog_cell)

        last_loop_ts = time.monotonic()
        while not self._stop_evt.is_set():
            loop_start = time.monotonic()
            if self._io_backoff_s > 0:
                time.sleep(self._io_backoff_s)
            out: Dict[str, int] = {}
            with self._lock:
                ge = self._global_enable
                sp1_cw, sp1_tgt = self._sp_cmd["sp1"]
                sp2_cw, sp2_tgt = self._sp_cmd["sp2"]
                sp1_stage = self._sp_stage["sp1"]
                sp2_stage = self._sp_stage["sp2"]
                sp1_force = self._sp_force_cw["sp1"]
                sp2_force = self._sp_force_cw["sp2"]

                if sp1_force is not None:
                    sp1_cw = sp1_force
                    self._sp_force_cw["sp1"] = None

                if sp1_force is None and sp1_stage == 1:
                    self._sp_cmd["sp1"] = (0x0007, sp1_tgt)
                    self._sp_stage["sp1"] = 2
                elif sp1_force is None and sp1_stage == 2:
                    self._sp_cmd["sp1"] = (0x000F, sp1_tgt)
                    self._sp_stage["sp1"] = 0

                if sp2_force is not None:
                    sp2_cw = sp2_force
                    self._sp_force_cw["sp2"] = None

                if sp2_force is None and sp2_stage == 1:
                    self._sp_cmd["sp2"] = (0x0007, sp2_tgt)
                    self._sp_stage["sp2"] = 2
                elif sp2_force is None and sp2_stage == 2:
                    self._sp_cmd["sp2"] = (0x000F, sp2_tgt)
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
            if self._watchdog_cell is not None:
                self._watchdog_counter = (self._watchdog_counter + 1) & 0x7FFFFFFF
                out[self._watchdog_cell] = int(self._watchdog_counter)

            if out:
                try:
                    tr.write_cells(out)
                except Exception as e:
                    self._emit_log("ERROR", "MAYAK_TX_ERROR", _kv(service=self.name, error=str(e)))
                    self._on_io_error()

            try:
                vals = tr.read_cells(in_cells)
            except Exception as e:
                self._emit_log("ERROR", "MAYAK_RX_ERROR", _kv(service=self.name, error=str(e)))
                self._on_io_error()
                time.sleep(period_s)
                continue
            self._on_io_success()
            self._last_packet_age_sec = float(getattr(tr, "last_packet_age_sec", lambda: 0.0)())
            if self._last_packet_age_sec > self._watchdog_max_packet_age_sec:
                self._on_io_error()

            sim_time = int(vals.get(d.sim_time, 0))
            err = int(vals.get(d.error_code, 0))
            with self._lock:
                if err != 0:
                    self._last_error_code = err
                elif self._last_error_code < 9000:
                    self._last_error_code = 0

            sp1_connected = bool(vals.get(d.sp1_connected, 0))
            sp2_connected = bool(vals.get(d.sp2_connected, 0))
            sp1_sw = int(vals.get(d.sp1_sw, 0))
            sp2_sw = int(vals.get(d.sp2_sw, 0))
            sp1_act = int(vals.get(d.sp1_act, 0))
            sp2_act = int(vals.get(d.sp2_act, 0))
            self._publish_tel(
                spindle="sp1",
                connected=sp1_connected,
                status_word=sp1_sw,
                actual_speed=sp1_act,
                actual_torque=int(vals.get(d.sp1_torque, 0)),
                angle=int(vals.get(d.sp1_angle, 0)),
                sim_time=sim_time,
                error_code=err,
            )

            self._publish_tel(
                spindle="sp2",
                connected=sp2_connected,
                status_word=sp2_sw,
                actual_speed=sp2_act,
                actual_torque=int(vals.get(d.sp2_torque, 0)),
                angle=None,
                sim_time=sim_time,
                error_code=err,
            )
            with self._lock:
                sp1_tgt = self._sp_cmd["sp1"][1] or 0
                sp2_tgt = self._sp_cmd["sp2"][1] or 0
                self._spindle_connected["sp1"] = sp1_connected
                self._spindle_connected["sp2"] = sp2_connected
                self._spindle_fault["sp1"] = bool(err != 0 or (sp1_sw & 0x0008))
                self._spindle_fault["sp2"] = bool(err != 0 or (sp2_sw & 0x0008))
                self._set_spindle_state(
                    "sp1",
                    self._derive_spindle_state(
                        connected=sp1_connected,
                        status_word=sp1_sw,
                        actual_speed=sp1_act,
                        target_speed=sp1_tgt,
                        error_code=err,
                    ),
                )
                self._set_spindle_state(
                    "sp2",
                    self._derive_spindle_state(
                        connected=sp2_connected,
                        status_word=sp2_sw,
                        actual_speed=sp2_act,
                        target_speed=sp2_tgt,
                        error_code=err,
                    ),
                )
            self._publish_health_event()
            self._evaluate_command_deadlines()
            self._log_metrics(loop_start=loop_start, last_loop_ts=last_loop_ts)
            last_loop_ts = loop_start

            time.sleep(period_s)

    def _on_io_error(self) -> None:
        with self._lock:
            self._io_error_streak += 1
            streak = self._io_error_streak
            self._io_backoff_s = min(1.0, max(0.05, self._io_backoff_s * 2 if self._io_backoff_s > 0 else 0.05))
        if streak == self._io_error_threshold:
            self._emit_log("ERROR", "MAYAK_IO_DEGRADED", _kv(service=self.name, streak=streak))
        self._publish_health_event()

    def _on_io_success(self) -> None:
        with self._lock:
            had_errors = self._io_error_streak >= self._io_error_threshold
            self._io_error_streak = 0
            self._io_backoff_s = 0.0
        if had_errors:
            self._emit_log("INFO", "MAYAK_IO_RECOVERED", _kv(service=self.name))
        self._publish_health_event()

    def _derive_spindle_state(
        self,
        *,
        connected: bool,
        status_word: int,
        actual_speed: int,
        target_speed: int,
        error_code: int,
    ) -> str:
        if not connected:
            return "OFFLINE"
        if error_code != 0 or (status_word & 0x0008):
            return "FAULT"

        op_enabled = bool(status_word & 0x0004)
        moving = abs(int(actual_speed)) >= self._rpm_moving_threshold
        wants_move = abs(int(target_speed)) > 0

        if not op_enabled:
            return "ENABLING" if wants_move else "DISABLED"
        if wants_move and not moving:
            return "STARTING"
        if not wants_move and moving:
            return "STOPPING"
        if moving:
            return "MOVING"
        return "READY"

    def _degraded_reason_locked(self) -> str:
        if self._io_error_streak >= self._io_error_threshold:
            return "io_errors"
        if self._last_packet_age_sec > self._watchdog_max_packet_age_sec:
            return "packet_age"
        if self._last_error_code != 0:
            return "fault_code"
        if self._spindle_connected.get("sp1") is False or self._spindle_connected.get("sp2") is False:
            return "offline_spindle"
        return "none"

    def _evaluate_command_deadlines(self) -> None:
        now = time.monotonic()
        for sp in ("sp1", "sp2"):
            with self._lock:
                deadline = self._pending_deadline.get(sp)
                expect = self._pending_expect.get(sp, "")
                state = self._spindle_state.get(sp, "UNKNOWN")
                err = self._last_error_code

            if deadline is None:
                continue

            success = False
            if expect == "MOVE":
                success = state in ("STARTING", "MOVING")
            elif expect == "STOP":
                success = state in ("READY", "STOPPING", "DISABLED")
            elif expect == "RESET":
                success = state in ("READY", "DISABLED") and err == 0

            if success:
                with self._lock:
                    self._pending_deadline[sp] = None
                    self._pending_expect[sp] = ""
                continue

            if now > deadline:
                should_state_fault = False
                with self._lock:
                    self._pending_deadline[sp] = None
                    self._pending_expect[sp] = ""
                    if self._last_error_code == 0:
                        self._last_error_code = 9001
                    self._spindle_fault[sp] = True
                    should_state_fault = True
                if should_state_fault:
                    self._set_spindle_state(sp, "FAULT")
                self._emit_log("ERROR", "MAYAK_CMD_TIMEOUT", _kv(service=self.name, spindle=sp, expect=expect))
                self._publish_health_event()

    def _log_metrics(self, *, loop_start: float, last_loop_ts: float) -> None:
        if (loop_start - self._metrics_last_log_ts) < self._metrics_log_period_sec:
            return
        self._metrics_last_log_ts = loop_start
        loop_period_ms = (loop_start - last_loop_ts) * 1000.0
        target_ms = float(self._publish_period_ms)
        rx_jitter_ms = abs(loop_period_ms - target_ms)
        last_packet_age_ms = self._last_packet_age_sec * 1000.0
        self._emit_log(
            "INFO",
            "MAYAK_METRICS",
            _kv(
                service=self.name,
                loop_period_ms=f"{loop_period_ms:.1f}",
                rx_jitter_ms=f"{rx_jitter_ms:.1f}",
                last_packet_age_ms=f"{last_packet_age_ms:.1f}",
            ),
        )

    def _set_spindle_state(self, spindle: str, state: str) -> None:
        prev = self._spindle_state.get(spindle)
        if prev == state:
            return
        self._spindle_state[spindle] = state
        self._emit_log("INFO", "MAYAK_SPINDLE_STATE", _kv(spindle=spindle, prev=prev, new=state))

    def _publish_health_event(self) -> None:
        snapshot = self.get_health_snapshot()
        key: Tuple[object, ...] = (
            snapshot["service_status"],
            snapshot["global_enable"],
            snapshot["error_code"],
            snapshot["io_error_streak"],
            snapshot["io_degraded"],
            snapshot["degraded_reason"],
            snapshot["sp1_state"],
            snapshot["sp2_state"],
            snapshot["sp1_connected"],
            snapshot["sp2_connected"],
        )
        if key == self._last_health_event_key:
            return
        self._last_health_event_key = key
        ready = bool(self.is_ready())
        if self._last_ready is None or self._last_ready != ready:
            self._last_ready = ready
            self._emit_log("INFO", "MAYAK_READY_STATE", _kv(service=self.name, ready=1 if ready else 0))
        self._bus.publish(
            MayakHealthEvent(
                service_name=self.name,
                ready=ready,
                global_enable=snapshot["global_enable"],  # type: ignore[arg-type]
                error_code=int(snapshot["error_code"]),
                io_error_streak=int(snapshot["io_error_streak"]),
                io_degraded=bool(snapshot["io_degraded"]),
                degraded_reason=str(snapshot["degraded_reason"]),
                sp1_state=str(snapshot["sp1_state"]),
                sp2_state=str(snapshot["sp2_state"]),
                sp1_connected=snapshot["sp1_connected"],  # type: ignore[arg-type]
                sp2_connected=snapshot["sp2_connected"],  # type: ignore[arg-type]
                ts=time.time(),
            )
        )

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
