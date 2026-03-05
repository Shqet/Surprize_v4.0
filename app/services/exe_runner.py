from __future__ import annotations

import locale
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Optional

from app.core.event_bus import EventBus
from app.core.events import ProcessOutputEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import ServiceStatus
from app.services.stop_utils import terminate_process


def _trunc_line(s: str, max_len: int = 400) -> str:
    s = s.rstrip("\r\n")
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


@dataclass(frozen=True, slots=True)
class _RunCfg:
    path: str
    args: str
    timeout_sec: int


class ExeRunnerService:
    """
    v0 demo service: runs an external process and streams stdout/stderr line-by-line to EventBus.

    Requirements:
      - start() must be fast (spawn worker thread)
      - no UI blocking (stop uses worker thread too)
      - publish ProcessOutputEvent + LogEvent(PROCESS_STDOUT/ERR) per line
      - publish PROCESS_START/PROCESS_EXIT logs
      - publish ServiceStatusEvent(STOPPED/ERROR) on exit
      - stop(): terminate -> wait(timeout) -> kill
      - start/stop idempotent
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._name = "exe_runner"

        self._lock = threading.Lock()
        self._status: ServiceStatus = ServiceStatus.IDLE

        self._proc: Optional[subprocess.Popen[str]] = None
        self._run_thread: Optional[threading.Thread] = None
        self._stop_thread: Optional[threading.Thread] = None
        self._stop_requested = threading.Event()
        self._cfg: Optional[_RunCfg] = None

    @property
    def name(self) -> str:
        return self._name

    def status(self) -> ServiceStatus:
        with self._lock:
            return self._status

    def start(self, *args: Any, **kwargs: Any) -> None:
        # 1) Принять конфиг из всех допустимых источников
        cfg_dict = kwargs.get("cfg")
        if cfg_dict is None:
            cfg_dict = kwargs.get("profile_section")
        if cfg_dict is None and args and isinstance(args[0], dict):
            cfg_dict = args[0]
        if cfg_dict is None:
            cfg_dict = {}

        # 2) Распарсить cfg только после того, как cfg_dict определён
        cfg = self._parse_cfg(cfg_dict)

        # 3) Idempotent start + установить состояние
        with self._lock:
            if self._status in (ServiceStatus.STARTING, ServiceStatus.RUNNING):
                return
            self._status = ServiceStatus.STARTING
            self._cfg = cfg
            self._stop_requested.clear()

        # 4) Launch in background thread to keep start() fast.
        t = threading.Thread(target=self._run_worker, name="ExeRunnerService.run", daemon=True)
        self._run_thread = t
        t.start()

    def stop(self) -> None:
        # stop() must be idempotent and non-blocking (UI safe).
        with self._lock:
            if self._status in (ServiceStatus.STOPPED, ServiceStatus.IDLE):
                return
            # If already stopping via a stop thread, don't start another.
            if self._stop_thread is not None and self._stop_thread.is_alive():
                self._stop_requested.set()
                return
            self._stop_requested.set()

        t = threading.Thread(target=self._stop_worker, name="ExeRunnerService.stop", daemon=True)
        self._stop_thread = t
        t.start()

    # -------------------- internals --------------------

    def _parse_cfg(self, cfg: Any) -> _RunCfg:
        if not isinstance(cfg, dict):
            raise ValueError("exe_runner cfg must be a dict")

        path = cfg.get("path")
        args = cfg.get("args")
        timeout_sec = cfg.get("timeout_sec")

        if not isinstance(path, str) or not path.strip():
            raise ValueError("exe_runner cfg missing/invalid: path")
        if not isinstance(args, str):
            raise ValueError("exe_runner cfg missing/invalid: args")
        if not isinstance(timeout_sec, int) or timeout_sec <= 0:
            raise ValueError("exe_runner cfg missing/invalid: timeout_sec (int>0)")

        return _RunCfg(path=path, args=args, timeout_sec=timeout_sec)

    def _set_status(self, st: ServiceStatus) -> None:
        with self._lock:
            self._status = st
        self._bus.publish(ServiceStatusEvent(service_name=self._name, status=st.value))

    def _run_worker(self) -> None:
        cfg = self._cfg
        if cfg is None:
            # should never happen
            self._set_status(ServiceStatus.ERROR)
            emit_log(self._bus, "ERROR", "exe_runner", "SERVICE_ERROR", "service=exe_runner err=NoConfig")
            return

        cmd = [cfg.path] + (cfg.args.split() if cfg.args else [])

        # Windows console tools typically output in OEM codepage (cp866).
        enc = "cp866" if os.name == "nt" else locale.getpreferredencoding(False)

        try:
            emit_log(
                self._bus,
                "INFO",
                "exe_runner",
                "PROCESS_START",
                f"service=exe_runner cmd={cfg.path} args={cfg.args}",
            )
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding=enc,
                errors="replace",
                bufsize=1,
            )
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "exe_runner",
                "SERVICE_ERROR",
                f"service=exe_runner err={type(ex).__name__}",
            )
            self._set_status(ServiceStatus.ERROR)
            return

        with self._lock:
            self._proc = proc

        self._set_status(ServiceStatus.RUNNING)

        # Start reader threads for live output.
        threads: list[threading.Thread] = []
        if proc.stdout is not None:
            threads.append(
                threading.Thread(
                    target=self._reader_worker,
                    args=("stdout", proc.stdout),
                    name="ExeRunnerService.stdout",
                    daemon=True,
                )
            )
        if proc.stderr is not None:
            threads.append(
                threading.Thread(
                    target=self._reader_worker,
                    args=("stderr", proc.stderr),
                    name="ExeRunnerService.stderr",
                    daemon=True,
                )
            )

        started_threads: list[threading.Thread] = []
        for th in threads:
            try:
                th.start()
                started_threads.append(th)
            except Exception as ex:
                emit_log(
                    self._bus,
                    "ERROR",
                    "exe_runner",
                    "SERVICE_ERROR",
                    f"service=exe_runner err={type(ex).__name__}",
                )

        # Wait for process exit (in worker thread, never UI).
        rc: Optional[int] = None
        try:
            rc = proc.wait()
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "exe_runner",
                "SERVICE_ERROR",
                f"service=exe_runner err={type(ex).__name__}",
            )

        # Ensure reader threads finish after exit.
        for th in started_threads:
            if th.ident is not None:
                th.join()

        emit_log(
            self._bus,
            "INFO",
            "exe_runner",
            "PROCESS_EXIT",
            f"service=exe_runner rc={rc}",
        )

        # Decide final status.
        stop_req = self._stop_requested.is_set()
        if rc is None:
            self._set_status(ServiceStatus.ERROR)
        elif rc == 0 or stop_req:
            self._set_status(ServiceStatus.STOPPED)
        else:
            # Non-zero exit code without explicit stop -> ERROR
            self._set_status(ServiceStatus.ERROR)

        with self._lock:
            self._proc = None

    def _reader_worker(self, stream_name: str, f) -> None:
        # Read lines until EOF.
        try:
            for line in f:
                if line is None:
                    continue
                line_s = _trunc_line(line)
                self._bus.publish(
                    ProcessOutputEvent(service_name=self._name, stream=stream_name, line=line_s)
                )
                code = "PROCESS_STDOUT" if stream_name == "stdout" else "PROCESS_STDERR"
                emit_log(
                    self._bus,
                    "INFO",
                    "exe_runner",
                    code,
                    f"service=exe_runner stream={stream_name} line={line_s}",
                )
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "exe_runner",
                "SERVICE_ERROR",
                f"service=exe_runner err={type(ex).__name__}",
            )
            self._set_status(ServiceStatus.ERROR)

    def _stop_worker(self) -> None:
        cfg = self._cfg
        timeout = cfg.timeout_sec if cfg is not None else 10

        proc: Optional[subprocess.Popen[str]]
        with self._lock:
            proc = self._proc

        if proc is None:
            # Nothing to stop; consider it stopped.
            self._set_status(ServiceStatus.STOPPED)
            return

        def _err(msg: str) -> None:
            emit_log(self._bus, "ERROR", "exe_runner", "SERVICE_ERROR", f"service=exe_runner {msg}")

        terminate_process(proc, timeout_sec=timeout, on_error=_err)

        # If we made it here, treat as stopped.
        self._set_status(ServiceStatus.STOPPED)
