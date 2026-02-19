from __future__ import annotations

import os
import shlex
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.core.event_bus import EventBus
from app.core.events import ProcessOutputEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import ServiceStatus
from app.services.stop_utils import terminate_process

from .engine import (
    make_run_id,
    build_run_paths,
    ensure_dirs,
    prepare_nmea_input,
    save_cmdline,
    write_run_meta,
)
from .process import ProcessHandle, join_readers, start_process, wait_for_exit


@dataclass(frozen=True)
class _RunConfig:
    out_root: Path
    input_csv: Path
    origin_lat: float
    origin_lon: float
    origin_h: float
    static_sec: float
    copy_input: bool

    gps_sdr_sim_exe: Path
    pluto_exe: Path
    nav_path: Path
    bit_depth: int
    gps_timeout_sec: int
    gps_extra_args: str

    tx_atten_db: float
    rf_bw_mhz: float
    pluto_extra_args: str
    hold_sec: Optional[float]
    grace_sec: float

    run_id: str


class GpsSdrSimService:
    """
    GPS SDR Simulation service:
      - CSV -> NMEA -> IQ -> Pluto transmission
      - Uses outputs/gps_sdr_sim/<run_id>/...
      - start() is non-blocking
      - stop() terminates current subprocess
    """

    name = "gps_sdr_sim"

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._lock = threading.Lock()
        self._status: ServiceStatus = ServiceStatus.IDLE
        self._stop_requested = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._proc: Optional[ProcessHandle] = None
        self._terminate_thread: Optional[threading.Thread] = None

    def status(self) -> ServiceStatus:
        with self._lock:
            return self._status

    def start(self, profile_section: Optional[dict[str, Any]] = None, **kwargs: Any) -> None:
        with self._lock:
            if self._status in (ServiceStatus.STARTING, ServiceStatus.RUNNING):
                return

        if profile_section is None:
            profile_section = kwargs.get("profile_section")
        if profile_section is None or not isinstance(profile_section, dict):
            self._set_status(ServiceStatus.ERROR)
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", "missing=profile_section")
            return

        try:
            cfg = self._parse_profile(profile_section)
        except Exception as ex:
            self._set_status(ServiceStatus.ERROR)
            emit_log(
                self._bus,
                "ERROR",
                "gps_sdr_sim",
                "SERVICE_ERROR",
                f"stage=parse_profile err={type(ex).__name__} msg={ex}",
            )
            return

        self._stop_requested.clear()
        self._set_status(ServiceStatus.STARTING)

        t = threading.Thread(
            target=self._run_worker,
            name="GpsSdrSimService.worker",
            args=(cfg,),
            daemon=True,
        )
        self._worker = t
        t.start()

    def stop(self) -> None:
        with self._lock:
            if self._status in (ServiceStatus.IDLE, ServiceStatus.STOPPED, ServiceStatus.ERROR):
                return

        self._stop_requested.set()
        if self._terminate_thread is None or not self._terminate_thread.is_alive():
            tt = threading.Thread(target=self._terminate_sequence, name="GpsSdrSimService.stop", daemon=True)
            self._terminate_thread = tt
            tt.start()

    # -------------------- internals --------------------

    def _set_status(self, st: ServiceStatus) -> None:
        with self._lock:
            self._status = st
        self._bus.publish(ServiceStatusEvent(service_name=self.name, status=st.value))

    def _publish_output(self, stream: str, line: str) -> None:
        self._bus.publish(ProcessOutputEvent(service_name=self.name, stream=stream, line=line))

    def _parse_profile(self, section: dict[str, Any]) -> _RunConfig:
        def req_str(k: str) -> str:
            v = section.get(k)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"missing={k}")
            return v

        def opt_str(k: str, default: str = "") -> str:
            v = section.get(k, default)
            if v is None:
                return default
            if not isinstance(v, str):
                raise ValueError(f"invalid={k}")
            return v

        def req_float(k: str) -> float:
            v = section.get(k)
            if not isinstance(v, (int, float)):
                raise ValueError(f"missing={k}")
            return float(v)

        def opt_float(k: str, default: float) -> float:
            v = section.get(k, default)
            if not isinstance(v, (int, float)):
                raise ValueError(f"invalid={k}")
            return float(v)

        def req_int_pos(k: str) -> int:
            v = section.get(k)
            if not isinstance(v, int) or v <= 0:
                raise ValueError(f"missing={k}")
            return v

        def opt_int_pos(k: str, default: int) -> int:
            v = section.get(k, default)
            if not isinstance(v, int) or v <= 0:
                raise ValueError(f"invalid={k}")
            return v

        def opt_bool(k: str, default: bool = False) -> bool:
            v = section.get(k, default)
            if not isinstance(v, bool):
                raise ValueError(f"invalid={k}")
            return v

        out_root = Path(req_str("out_root")).resolve()
        input_csv = Path(req_str("input")).resolve()
        origin_lat = req_float("origin_lat")
        origin_lon = req_float("origin_lon")
        origin_h = req_float("origin_h")
        static_sec = opt_float("static_sec", 0.0)
        copy_input = opt_bool("copy_input", False)

        gps_sdr_sim_exe = Path(req_str("gps_sdr_sim_exe")).resolve()
        pluto_exe = Path(req_str("pluto_exe")).resolve()
        nav_path = Path(req_str("nav")).resolve()
        bit_depth = int(section.get("bit_depth", 16))
        if bit_depth not in (8, 16):
            raise ValueError("invalid=bit_depth")
        gps_timeout_sec = opt_int_pos("gps_timeout_sec", 120)
        gps_extra_args = opt_str("gps_extra_args", "")

        tx_atten_db = opt_float("tx_atten_db", -20.0)
        rf_bw_mhz = opt_float("rf_bw_mhz", 3.0)
        pluto_extra_args = opt_str("pluto_extra_args", "")
        hold_sec_raw = section.get("hold_sec", None)
        hold_sec = float(hold_sec_raw) if isinstance(hold_sec_raw, (int, float)) else None
        grace_sec = opt_float("grace_sec", 5.0)

        run_id = opt_str("run_id", "")
        if not run_id:
            run_id = make_run_id(prefix="gps_")

        # Validate ranges (per PlutoPlayer manual)
        if tx_atten_db > 0.0 or tx_atten_db < -80.0:
            raise ValueError("invalid=tx_atten_db")
        step = 0.25
        k = round(tx_atten_db / step)
        if abs(tx_atten_db - (k * step)) > 1e-6:
            raise ValueError("invalid=tx_atten_db_step")
        if rf_bw_mhz < 1.0 or rf_bw_mhz > 5.0:
            raise ValueError("invalid=rf_bw_mhz")

        return _RunConfig(
            out_root=out_root,
            input_csv=input_csv,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            origin_h=origin_h,
            static_sec=static_sec,
            copy_input=copy_input,
            gps_sdr_sim_exe=gps_sdr_sim_exe,
            pluto_exe=pluto_exe,
            nav_path=nav_path,
            bit_depth=bit_depth,
            gps_timeout_sec=gps_timeout_sec,
            gps_extra_args=gps_extra_args,
            tx_atten_db=tx_atten_db,
            rf_bw_mhz=rf_bw_mhz,
            pluto_extra_args=pluto_extra_args,
            hold_sec=hold_sec,
            grace_sec=grace_sec,
            run_id=run_id,
        )

    def _validate_tool_paths(self, cfg: _RunConfig) -> None:
        if not cfg.gps_sdr_sim_exe.exists():
            raise FileNotFoundError(f"gps_sdr_sim_exe not found: {cfg.gps_sdr_sim_exe}")
        if not cfg.pluto_exe.exists():
            raise FileNotFoundError(f"pluto_exe not found: {cfg.pluto_exe}")
        if not cfg.nav_path.exists():
            raise FileNotFoundError(f"nav not found: {cfg.nav_path}")

    def _run_worker(self, cfg: _RunConfig) -> None:
        try:
            self._validate_tool_paths(cfg)
        except Exception as ex:
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", f"stage=validate err={type(ex).__name__} msg={ex}")
            self._set_status(ServiceStatus.ERROR)
            return

        paths = build_run_paths(cfg.out_root, cfg.run_id)
        ensure_dirs(paths)

        # Step1: CSV -> NMEA
        try:
            emit_log(self._bus, "INFO", "gps_sdr_sim", "PROCESS_START", "stage=prepare_nmea")
            meta = prepare_nmea_input(
                input_trajectory_csv=cfg.input_csv,
                out_nmea_txt=paths.nmea_strings_txt,
                origin_lat_deg=cfg.origin_lat,
                origin_lon_deg=cfg.origin_lon,
                origin_h_m=cfg.origin_h,
                static_sec=cfg.static_sec,
            )
            meta["run_id"] = cfg.run_id
            meta["out_dir"] = str(paths.run_dir)
            write_run_meta(paths.run_meta_json, meta)
            if cfg.copy_input:
                paths.input_trajectory_copy.write_bytes(cfg.input_csv.read_bytes())
        except Exception as ex:
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", f"stage=prepare_nmea err={type(ex).__name__} msg={ex}")
            self._set_status(ServiceStatus.ERROR)
            return

        self._set_status(ServiceStatus.RUNNING)

        # Step2: gps-sdr-sim -> IQ (run from sim dir with relative paths)
        nav_dst = paths.sim_dir / cfg.nav_path.name
        try:
            nav_dst.write_bytes(cfg.nav_path.read_bytes())
        except Exception as ex:
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", f"stage=nav_copy err={type(ex).__name__} msg={ex}")
            self._set_status(ServiceStatus.ERROR)
            return

        cwd_sim = paths.sim_dir
        nav_arg = nav_dst.name
        nmea_arg = os.path.relpath(paths.nmea_strings_txt, start=cwd_sim)
        out_arg = os.path.relpath(paths.sim_iq_bin, start=cwd_sim)

        gps_argv = [
            str(cfg.gps_sdr_sim_exe),
            "-e",
            nav_arg,
            "-g",
            nmea_arg,
            "-b",
            str(int(cfg.bit_depth)),
            "-o",
            out_arg,
        ]
        if cfg.gps_extra_args:
            gps_argv += shlex.split(cfg.gps_extra_args)

        save_cmdline(paths.gps_sdr_sim_cmdline_txt, gps_argv)
        emit_log(
            self._bus,
            "INFO",
            "gps_sdr_sim",
            "PROCESS_START",
            f"stage=gps_sdr_sim cwd={cwd_sim.as_posix()} cmd={paths.gps_sdr_sim_cmdline_txt.as_posix()}",
        )

        handle = start_process(
            cmd=gps_argv,
            cwd=cwd_sim,
            stdout_path=paths.stdout_gps_sdr_sim_log,
            stderr_path=paths.stderr_gps_sdr_sim_log,
            on_stdout=lambda line: self._publish_output("stdout", line),
            on_stderr=lambda line: self._publish_output("stderr", line),
        )
        with self._lock:
            self._proc = handle

        rc, timed_out, stopped = wait_for_exit(handle, timeout_sec=cfg.gps_timeout_sec, stop_event=self._stop_requested)
        if stopped:
            self._set_status(ServiceStatus.STOPPED)
            return
        if timed_out:
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", f"stage=gps_sdr_sim timeout_sec={cfg.gps_timeout_sec}")
            self._terminate_current(cfg.gps_timeout_sec)
            self._set_status(ServiceStatus.ERROR)
            return
        emit_log(self._bus, "INFO", "gps_sdr_sim", "PROCESS_EXIT", f"stage=gps_sdr_sim rc={rc}")
        join_readers(handle)
        with self._lock:
            self._proc = None

        if rc != 0:
            self._set_status(ServiceStatus.ERROR)
            return
        if not paths.sim_iq_bin.exists() or paths.sim_iq_bin.stat().st_size <= 0:
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", "stage=gps_sdr_sim missing=iq")
            self._set_status(ServiceStatus.ERROR)
            return

        # Step3: PlutoPlayer
        iq_dst = paths.pluto_dir / paths.sim_iq_bin.name
        try:
            iq_dst.write_bytes(paths.sim_iq_bin.read_bytes())
        except Exception as ex:
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", f"stage=iq_copy err={type(ex).__name__} msg={ex}")
            self._set_status(ServiceStatus.ERROR)
            return

        cwd_pluto = paths.pluto_dir
        pluto_argv = [
            str(cfg.pluto_exe),
            "-t",
            iq_dst.name,
            "-a",
            f"{cfg.tx_atten_db:.2f}",
            "-b",
            f"{cfg.rf_bw_mhz:.2f}",
        ]
        if cfg.pluto_extra_args:
            pluto_argv += shlex.split(cfg.pluto_extra_args)

        save_cmdline(paths.pluto_cmdline_txt, pluto_argv)
        emit_log(
            self._bus,
            "INFO",
            "gps_sdr_sim",
            "PROCESS_START",
            f"stage=pluto cwd={cwd_pluto.as_posix()} cmd={paths.pluto_cmdline_txt.as_posix()}",
        )

        handle = start_process(
            cmd=pluto_argv,
            cwd=cwd_pluto,
            stdout_path=paths.stdout_pluto_log,
            stderr_path=paths.stderr_pluto_log,
            on_stdout=lambda line: self._publish_output("stdout", line),
            on_stderr=lambda line: self._publish_output("stderr", line),
        )
        with self._lock:
            self._proc = handle

        if cfg.hold_sec is None:
            rc, _timed_out, stopped = wait_for_exit(handle, timeout_sec=None, stop_event=self._stop_requested)
            if stopped:
                self._set_status(ServiceStatus.STOPPED)
                return
        else:
            # timed run; terminate after hold_sec unless already stopped
            start_ts = time.monotonic()
            while True:
                if self._stop_requested.is_set():
                    self._set_status(ServiceStatus.STOPPED)
                    return
                rc = handle.proc.poll()
                if rc is not None:
                    break
                if (time.monotonic() - start_ts) >= float(cfg.hold_sec):
                    break
                time.sleep(0.05)

            if handle.proc.poll() is None:
                self._terminate_current(cfg.grace_sec)
            rc = handle.proc.poll()

        emit_log(self._bus, "INFO", "gps_sdr_sim", "PROCESS_EXIT", f"stage=pluto rc={rc}")
        join_readers(handle)
        with self._lock:
            self._proc = None

        if self._stop_requested.is_set():
            self._set_status(ServiceStatus.STOPPED)
            return

        # Success
        emit_log(
            self._bus,
            "INFO",
            "gps_sdr_sim",
            "SERVICE_STATUS",
            f"run_id={cfg.run_id} out_dir={paths.run_dir.as_posix()} nmea={paths.nmea_strings_txt.as_posix()} iq={paths.sim_iq_bin.as_posix()}",
        )
        self._set_status(ServiceStatus.STOPPED)

    def _terminate_current(self, timeout_sec: float) -> None:
        handle: Optional[ProcessHandle]
        with self._lock:
            handle = self._proc

        if handle is None:
            return

        def _info(msg: str) -> None:
            emit_log(self._bus, "INFO", "gps_sdr_sim", "PROCESS_EXIT", msg)

        def _err(msg: str) -> None:
            emit_log(self._bus, "ERROR", "gps_sdr_sim", "SERVICE_ERROR", msg)

        terminate_process(handle.proc, timeout_sec=timeout_sec, on_info=_info, on_error=_err)
        with self._lock:
            if self._proc is handle:
                self._proc = None

    def _terminate_sequence(self) -> None:
        self._terminate_current(timeout_sec=5.0)
        st = self.status()
        if st != ServiceStatus.ERROR:
            self._set_status(ServiceStatus.STOPPED)
