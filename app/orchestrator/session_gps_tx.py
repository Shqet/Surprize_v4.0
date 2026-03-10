from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log

from .session_runtime import SessionRuntime


class SessionGpsTxRunner:
    """
    Runtime GPS TX component for test sessions.
    Starts/stops PlutoPlayer using prepared IQ artifact.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def start(self, session_ctx: SessionRuntime) -> None:
        cfg = session_ctx.handles.get("gps_tx_cfg")
        if not isinstance(cfg, dict):
            raise RuntimeError("gps_tx_cfg_missing")

        pluto_exe = Path(str(cfg.get("pluto_exe", ""))).resolve()
        iq_path = Path(str(cfg.get("iq_path", ""))).resolve()
        tx_atten_db = float(cfg.get("tx_atten_db", -20.0))
        rf_bw_mhz = float(cfg.get("rf_bw_mhz", 3.0))
        host = str(cfg.get("host", "") or "").strip()
        if not pluto_exe.exists():
            raise FileNotFoundError(f"pluto_exe_not_found={pluto_exe.as_posix()}")
        if not iq_path.exists():
            raise FileNotFoundError(f"gps_iq_not_found={iq_path.as_posix()}")

        gps_dir = Path(session_ctx.paths["out_dir"]) / "gps"
        gps_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = gps_dir / "pluto_stdout.log"
        stderr_path = gps_dir / "pluto_stderr.log"
        cmdline_path = gps_dir / "plutoplayer.cmdline.txt"

        cmd = [
            str(pluto_exe),
            "-t",
            str(iq_path),
            "-a",
            f"{tx_atten_db:.2f}",
            "-b",
            f"{rf_bw_mhz:.2f}",
        ]
        if host:
            cmd += ["-n", host]
        cmdline_path.write_text(" ".join(cmd), encoding="utf-8")

        proc = subprocess.Popen(
            cmd,
            cwd=str(pluto_exe.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        session_ctx.handles["gps_tx_proc"] = proc
        session_ctx.paths["gps_stdout"] = str(stdout_path)
        session_ctx.paths["gps_stderr"] = str(stderr_path)
        session_ctx.paths["gps_cmdline"] = str(cmdline_path)

        self._emit_session_event(session_ctx, "SESSION_GPS_TX_START", "status=started")

        # Early-exit detection: process should stay alive shortly after start.
        time.sleep(0.35)
        rc = proc.poll()
        if rc is not None:
            out, err = proc.communicate(timeout=1.0)
            stdout_path.write_text(out or "", encoding="utf-8")
            stderr_path.write_text(err or "", encoding="utf-8")
            session_ctx.handles.pop("gps_tx_proc", None)
            detail = self._summarize_process_error(rc=int(rc), stderr=err or "", stdout=out or "")
            self._emit_session_event(session_ctx, "SESSION_GPS_TX_ERROR", f"stage=start detail={detail}")
            raise RuntimeError(f"gps_tx_early_exit:{detail}")

    def stop(self, session_ctx: SessionRuntime) -> None:
        proc = session_ctx.handles.get("gps_tx_proc")
        if not isinstance(proc, subprocess.Popen):
            self._emit_session_event(session_ctx, "SESSION_GPS_TX_STOP", "status=not_running")
            return

        timeout_sec = float(session_ctx.handles.get("gps_tx_cfg", {}).get("graceful_stop_timeout_sec", 3.0))
        stop_mode = "already_exited"
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=max(0.1, timeout_sec))
                stop_mode = "graceful"
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
                stop_mode = "kill"

        try:
            out, err = proc.communicate(timeout=1.0)
        except Exception:
            out, err = "", ""

        stdout_path = Path(session_ctx.paths.get("gps_stdout", Path(session_ctx.paths["out_dir"]) / "gps" / "pluto_stdout.log"))
        stderr_path = Path(session_ctx.paths.get("gps_stderr", Path(session_ctx.paths["out_dir"]) / "gps" / "pluto_stderr.log"))
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(out or "", encoding="utf-8")
        stderr_path.write_text(err or "", encoding="utf-8")
        session_ctx.handles.pop("gps_tx_proc", None)

        rc = proc.poll()
        self._emit_session_event(
            session_ctx,
            "SESSION_GPS_TX_STOP",
            f"status=stopped mode={stop_mode} rc={int(rc) if rc is not None else 'none'}",
        )

    def describe(self, session_ctx: SessionRuntime) -> dict[str, Any]:
        proc = session_ctx.handles.get("gps_tx_proc")
        if not isinstance(proc, subprocess.Popen):
            return {"state": "not_running", "pid": None, "exit_code": None}
        rc = proc.poll()
        if rc is None:
            return {"state": "running", "pid": int(proc.pid) if proc.pid is not None else None, "exit_code": None}
        return {"state": "exited", "pid": int(proc.pid) if proc.pid is not None else None, "exit_code": int(rc)}

    def _emit_session_event(self, session_ctx: SessionRuntime, event: str, details: str) -> None:
        t_rel = max(0.0, time.monotonic() - float(session_ctx.t0_monotonic))
        msg = f"session_id={session_ctx.session_id} t_rel_sec={t_rel:.3f} {details}".strip()
        level = "ERROR" if event.endswith("_ERROR") else "INFO"
        emit_log(self._bus, level, "orchestrator", event, msg)

        events_path = Path(session_ctx.paths["events_log"])
        payload = {
            "event": event,
            "session_id": session_ctx.session_id,
            "scenario_id": session_ctx.scenario_id,
            "unix_ts": time.time(),
            "t_rel_sec": t_rel,
            "details": details,
        }
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _summarize_process_error(*, rc: int, stderr: str, stdout: str) -> str:
        text = (stderr or stdout or "").strip().splitlines()
        tail = text[-1] if text else "no_output"
        return f"rc={rc} msg={tail}"
