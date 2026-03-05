from __future__ import annotations
import os
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import subprocess
import sys

from app.core.event_bus import EventBus
from app.core.events import ProcessOutputEvent, ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import ServiceStatus
from app.services.stop_utils import terminate_process


@dataclass(frozen=True)
class _RunConfig:
    model_root: Path
    python_exe: str
    calc_entry: str
    plots_entry: str
    out_root: Path
    timeout_sec: int
    make_plots: bool
    config_json: dict[str, Any]


def _validate_config_json(cfg: dict[str, Any]) -> None:
    """
    Fail-fast validation for model_ballistics config format (vkr_core.py expects):
      top-level: simulation, projectile, rotation, initial_conditions

    This is intentionally strict on required keys that vkr_core.py reads.
    """
    if not isinstance(cfg, dict):
        raise ValueError("invalid config_json: must be a dict")

    required_top = ("simulation", "projectile", "rotation", "initial_conditions")
    for k in required_top:
        v = cfg.get(k)
        if not isinstance(v, dict):
            raise ValueError(f"invalid config_json: missing top-level dict '{k}'")

    sim = cfg["simulation"]
    if "dt" not in sim or "t_max" not in sim:
        raise ValueError("invalid config_json: simulation requires dt and t_max")

    ic = cfg["initial_conditions"]

    # single supported format for operator-facing config
    # (avoid ambiguity between legacy and new velocity definitions).
    if not all(k in ic for k in ("V0", "theta_deg", "psi_deg")):
        raise ValueError("invalid config_json: initial_conditions requires (V0,theta_deg,psi_deg)")
    if any(k in ic for k in ("Vx0", "Vy0", "Vz0")):
        raise ValueError("invalid config_json: legacy velocity keys (Vx0,Vy0,Vz0) are not supported")


class BallisticsModelSubprocessService:
    """
    v2 real service: runs external ballistics model as subprocess.

    Contract:
      - name = "ballistics_model"
      - start(profile_section) returns fast (worker thread)
      - stop(): idempotent terminate -> wait(timeout) -> kill (NOT in UI thread)
      - publishes ServiceStatusEvent STARTING/RUNNING/STOPPED/ERROR
      - publishes ProcessOutputEvent for stdout/stderr lines
      - artifacts check: trajectory.csv, diagnostics.csv in run_dir
      - optional plots stage if make_plots=true
    """

    name = "ballistics_model"

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

        self._lock = threading.Lock()
        self._status: ServiceStatus = ServiceStatus.IDLE

        self._worker: Optional[threading.Thread] = None
        self._stop_requested = threading.Event()

        self._proc: Optional[subprocess.Popen[str]] = None
        self._terminate_thread: Optional[threading.Thread] = None

        self._current_run_dir: Optional[Path] = None
        self._current_timeout_sec: int = 10

    def status(self) -> ServiceStatus:
        with self._lock:
            return self._status

    # NOTE: keep signature tolerant; ServiceManager may pass positional or kw arg.
    def start(self, profile_section: Optional[dict[str, Any]] = None, **kwargs: Any) -> None:
        # Idempotent: if already running/starting, ignore
        with self._lock:
            if self._status in (ServiceStatus.STARTING, ServiceStatus.RUNNING):
                return

        if profile_section is None:
            # allow passing as kwargs: start(profile_section=...)
            profile_section = kwargs.get("profile_section")
        if profile_section is None or not isinstance(profile_section, dict):
            self._set_status(ServiceStatus.ERROR)
            emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", "missing=profile_section")
            return

        try:
            rcfg = self._parse_profile(profile_section)
        except Exception as ex:
            self._set_status(ServiceStatus.ERROR)
            emit_log(
                self._bus,
                "ERROR",
                "ballistics_model",
                "SERVICE_ERROR",
                f"stage=parse_profile err={type(ex).__name__} msg={ex}",
            )
            return

        self._stop_requested.clear()
        self._set_status(ServiceStatus.STARTING)

        t = threading.Thread(
            target=self._run_worker,
            name="BallisticsModelSubprocessService.worker",
            args=(rcfg,),
            daemon=True,
        )
        self._worker = t
        t.start()

    def stop(self) -> None:
        # Idempotent: if already stopped/idle/error, ignore
        with self._lock:
            if self._status in (ServiceStatus.IDLE, ServiceStatus.STOPPED, ServiceStatus.ERROR):
                return

        self._stop_requested.set()

        # terminate/wait/kill must not block caller thread
        if self._terminate_thread is None or not self._terminate_thread.is_alive():
            tt = threading.Thread(target=self._terminate_sequence, name="BallisticsModelSubprocessService.stop", daemon=True)
            self._terminate_thread = tt
            tt.start()

    # ------------------------- internals -------------------------

    def _parse_profile(self, section: dict[str, Any]) -> _RunConfig:
        def req_str(k: str) -> str:
            v = section.get(k)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"missing={k}")
            return v

        def req_int_pos(k: str) -> int:
            v = section.get(k)
            if not isinstance(v, int) or v <= 0:
                raise ValueError(f"missing={k}")
            return v

        def req_bool(k: str) -> bool:
            v = section.get(k)
            if not isinstance(v, bool):
                raise ValueError(f"missing={k}")
            return v

        model_root = Path(req_str("model_root"))
        out_root = Path(req_str("out_root"))

        python_exe = req_str("python_exe")
        if python_exe.lower() in ("python", "python.exe", "py", "py.exe"):
            python_exe = sys.executable
        calc_entry = req_str("calc_entry")
        plots_entry = req_str("plots_entry")

        timeout_sec = req_int_pos("timeout_sec")
        make_plots = req_bool("make_plots")

        cfg = section.get("config_json")
        if not isinstance(cfg, dict):
            raise ValueError("missing=config_json")

        return _RunConfig(
            model_root=model_root,
            python_exe=python_exe,
            calc_entry=calc_entry,
            plots_entry=plots_entry,
            out_root=out_root,
            timeout_sec=timeout_sec,
            make_plots=make_plots,
            config_json=cfg,
        )

    def _set_status(self, st: ServiceStatus) -> None:
        with self._lock:
            self._status = st
        self._bus.publish(ServiceStatusEvent(service_name=self.name, status=st.value))

    def _publish_output(self, stream: str, line: str) -> None:
        self._bus.publish(ProcessOutputEvent(service_name=self.name, stream=stream, line=line))

    def _run_worker(self, cfg: _RunConfig) -> None:
        # run_id: timestamp-based (safe + readable)
        run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"

        # NOTE (per v2 step): run_dir = <out_root>/ballistics/<run_id>/
        run_dir = (cfg.out_root / "ballistics" / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        self._current_run_dir = run_dir
        self._current_timeout_sec = int(cfg.timeout_sec)

        # ---- FAIL-FAST: validate config before writing and before subprocess ----
        try:
            _validate_config_json(cfg.config_json)
        except Exception as ex:
            emit_log(
                self._bus,
                "ERROR",
                "ballistics_model",
                "SERVICE_ERROR",
                f"stage=validate_config err={type(ex).__name__} msg={ex}",
            )
            self._set_status(ServiceStatus.ERROR)
            return

        config_path = run_dir / "vkr_config.json"
        try:
            config_path.write_text(json.dumps(cfg.config_json, ensure_ascii=False, indent=2), encoding="utf-8")
            emit_log(
                self._bus,
                "INFO",
                "ballistics_model",
                "PROCESS_START",
                f"stage=config_write run_id={run_id} run_dir={run_dir.as_posix()}",
            )
        except Exception as ex:
            emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", f"stage=config_write err={type(ex).__name__} msg={ex}")
            self._set_status(ServiceStatus.ERROR)
            return

        # Start calc
        self._set_status(ServiceStatus.RUNNING)
        ok = self._run_stage(
            stage="calc",
            cwd=cfg.model_root,
            python_exe=cfg.python_exe,
            entry=cfg.calc_entry,
            config_path=config_path,
            out_dir=run_dir,
            timeout_sec=cfg.timeout_sec,
        )
        # Если нажали Stop во время расчёта — не проверяем артефакты и не считаем это ошибкой.
        if self._stop_requested.is_set():
            self._set_status(ServiceStatus.STOPPED)
            return

        if not ok:
            # status already moved to ERROR by stage (or STOPPED if stop requested)
            return

        # Artifacts check (required)
        traj = run_dir / "trajectory.csv"
        diag = run_dir / "diagnostics.csv"
        if not traj.exists() or not diag.exists():
            missing = []
            if not traj.exists():
                missing.append("trajectory.csv")
            if not diag.exists():
                missing.append("diagnostics.csv")
            emit_log(
                self._bus,
                "ERROR",
                "ballistics_model",
                "SERVICE_ERROR",
                f"missing={','.join(missing)} run_dir={run_dir.as_posix()}",
            )
            self._set_status(ServiceStatus.ERROR)
            return

        emit_log(
            self._bus,
            "INFO",
            "ballistics_model",
            "PROCESS_EXIT",
            f"stage=calc rc=0 trajectory={traj.as_posix()} diagnostics={diag.as_posix()}",
        )

        # Optional plots
        plots_flag = "0"
        if cfg.make_plots and not self._stop_requested.is_set():
            plots_ok = self._run_plots(
                cwd=cfg.model_root,
                python_exe=cfg.python_exe,
                entry=cfg.plots_entry,
                out_dir=run_dir,
                timeout_sec=cfg.timeout_sec,
            )
            if not plots_ok:
                # Рекомендация: plots best-effort — НЕ валим сервис в ERROR
                emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", "stage=plots failed=1")
                # продолжаем к STOPPED

            # Best-effort check: plots dir or any png
            try:
                if (run_dir / "plots").exists() or any(run_dir.glob("*.png")):
                    plots_flag = "1"
            except Exception:
                plots_flag = "1"

        # Final status evidence (required by SERVICES.md)
        emit_log(
            self._bus,
            "INFO",
            "ballistics_model",
            "SERVICE_STATUS",
            f"run_id={run_id} out_dir={run_dir.as_posix()} trajectory={traj.as_posix()} diagnostics={diag.as_posix()} plots={plots_flag}",
        )

        # If stop was requested at any time, treat as STOPPED if process ended cleanly
        if self._stop_requested.is_set():
            self._set_status(ServiceStatus.STOPPED)
            return

        # Success
        self._set_status(ServiceStatus.STOPPED)

    def _run_stage(
        self,
        stage: str,
        cwd: Path,
        python_exe: str,
        entry: str,
        config_path: Path,
        out_dir: Path,
        timeout_sec: int,
    ) -> bool:
        if self._stop_requested.is_set():
            self._set_status(ServiceStatus.STOPPED)
            return False

        cwd = Path(cwd).resolve()
        cfg_arg = str(config_path.resolve())
        out_arg = str(out_dir.resolve())

        cmd = [python_exe, entry, "--config", cfg_arg, "--out", out_arg]

        emit_log(
            self._bus,
            "INFO",
            "ballistics_model",
            "PROCESS_START",
            f"stage={stage} cwd={cwd.as_posix()} cmd={python_exe} entry={entry}",
        )
        env = os.environ.copy()
        model_root = str(cwd.resolve())
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = model_root if not prev else (model_root + os.pathsep + prev)
        try:
            # text=True for line-by-line reading; utf-8 with replacement for safety on Windows
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as ex:
            emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", f"stage={stage} err={type(ex).__name__} msg={ex}")
            self._set_status(ServiceStatus.ERROR)
            return False

        with self._lock:
            self._proc = proc

        # Reader threads
        out_t = threading.Thread(
            target=self._pipe_reader,
            name=f"ballistics_model.{stage}.stdout",
            args=(proc.stdout, "stdout"),
            daemon=True,
        )
        err_t = threading.Thread(
            target=self._pipe_reader,
            name=f"ballistics_model.{stage}.stderr",
            args=(proc.stderr, "stderr"),
            daemon=True,
        )
        out_t.start()
        err_t.start()

        rc: Optional[int] = None
        deadline = time.monotonic() + max(1, int(timeout_sec))
        try:
            while True:
                if self._stop_requested.is_set():
                    # stop() will do terminate/kill in its own thread; we just break and let rc be handled
                    break

                rc = proc.poll()
                if rc is not None:
                    break

                if time.monotonic() >= deadline:
                    emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", f"pending={self.name} timeout_sec={int(timeout_sec)}")
                    self._set_status(ServiceStatus.ERROR)
                    # trigger termination sequence (non-blocking)
                    self.stop()
                    break

                time.sleep(0.05)
        finally:
            # Ensure streams are closed so reader threads can finish
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr:
                    proc.stderr.close()
            except Exception:
                pass

        # Try to join reader threads shortly (daemon threads anyway)
        for t in (out_t, err_t):
            try:
                t.join(timeout=0.5)
            except Exception:
                pass

        rc = proc.poll()
        if rc is None:
            # still running -> stop will handle; do not mark success
            return False

        emit_log(self._bus, "INFO", "ballistics_model", "PROCESS_EXIT", f"stage={stage} rc={int(rc)}")
        # ВАЖНО: если был Stop — считаем стадию отменённой, не "успешной"
        if self._stop_requested.is_set():
            self._set_status(ServiceStatus.STOPPED)
            return False

        if rc != 0 and not self._stop_requested.is_set():
            self._set_status(ServiceStatus.ERROR)
            return False

        return True

    def _pipe_reader(self, pipe: Optional[Any], stream: str) -> None:
        if pipe is None:
            return
        try:
            for raw in pipe:
                if raw is None:
                    continue
                line = str(raw).rstrip("\r\n")
                self._publish_output(stream, line)
                code = "PROCESS_STDOUT" if stream == "stdout" else "PROCESS_STDERR"
                # Keep log short
                msg_line = line
                if len(msg_line) > 400:
                    msg_line = msg_line[:400] + "...len=trunc"
                emit_log(self._bus, "INFO", "ballistics_model", code, f"stage=io stream={stream} line={msg_line}")
                if self._stop_requested.is_set():
                    break
        except Exception as ex:
            emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", f"stage=pipe_reader err={type(ex).__name__} msg={ex}")

    def _terminate_sequence(self) -> None:
        # terminate -> wait(timeout) -> kill (idempotent)
        proc: Optional[subprocess.Popen[str]]
        with self._lock:
            proc = self._proc
            timeout_sec = int(self._current_timeout_sec)

        if proc is None:
            # no active process
            self._set_status(ServiceStatus.STOPPED)
            return

        def _info(msg: str) -> None:
            emit_log(self._bus, "INFO", "ballistics_model", "PROCESS_EXIT", msg)

        def _err(msg: str) -> None:
            emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", msg)

        terminate_process(proc, timeout_sec=timeout_sec, on_info=_info, on_error=_err)

        # Finalize status (if not already ERROR)
        st = self.status()
        if st != ServiceStatus.ERROR:
            self._set_status(ServiceStatus.STOPPED)

        with self._lock:
            self._proc = None
    def _run_plots(
        self,
        cwd: Path,
        python_exe: str,
        entry: str,
        out_dir: Path,
        timeout_sec: int,
    ) -> bool:
        if self._stop_requested.is_set():
            self._set_status(ServiceStatus.STOPPED)
            return False

        cwd = Path(cwd).resolve()
        out_arg = str(out_dir.resolve())

        traj = out_dir / "trajectory.csv"
        diag = out_dir / "diagnostics.csv"

        # visualization.py ожидает пути относительно --out, но умеет и явные имена файлов;
        # у нас удобнее дать базовые имена, т.к. --out уже run_dir.
        cmd = [
            python_exe,
            entry,
            "--out",
            out_arg,
            "--trajectory",
            "trajectory.csv",
            "--diagnostics",
            "diagnostics.csv",
        ]

        emit_log(
            self._bus,
            "INFO",
            "ballistics_model",
            "PROCESS_START",
            f"stage=plots cwd={cwd.as_posix()} cmd={python_exe} entry={entry}",
        )

        try:
            import os
            env = os.environ.copy()
            model_root = str(cwd.resolve())
            prev = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = model_root if not prev else (model_root + os.pathsep + prev)

            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as ex:
            emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", f"stage=plots err={type(ex).__name__} msg={ex}")
            return False

        with self._lock:
            self._proc = proc

        out_t = threading.Thread(target=self._pipe_reader, name="ballistics_model.plots.stdout", args=(proc.stdout, "stdout"), daemon=True)
        err_t = threading.Thread(target=self._pipe_reader, name="ballistics_model.plots.stderr", args=(proc.stderr, "stderr"), daemon=True)
        out_t.start()
        err_t.start()

        rc: Optional[int] = None
        deadline = time.monotonic() + max(1, int(timeout_sec))
        try:
            while True:
                if self._stop_requested.is_set():
                    break

                rc = proc.poll()
                if rc is not None:
                    break

                if time.monotonic() >= deadline:
                    emit_log(self._bus, "ERROR", "ballistics_model", "SERVICE_ERROR", f"stage=plots timeout_sec={int(timeout_sec)}")
                    self.stop()
                    return False

                time.sleep(0.05)
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr:
                    proc.stderr.close()
            except Exception:
                pass

            for t in (out_t, err_t):
                try:
                    t.join(timeout=0.5)
                except Exception:
                    pass

        rc = proc.poll()
        if rc is None:
            return False

        emit_log(self._bus, "INFO", "ballistics_model", "PROCESS_EXIT", f"stage=plots rc={int(rc)}")

        # если stop -> это не ошибка
        if self._stop_requested.is_set():
            return False

        return rc == 0
