from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ProcHealth:
    state: str
    attempt: int
    fps: float
    last_frame_age_ms: int


class ProcessStreamWorker:
    def __init__(
        self,
        stream: str,
        url: str,
        log: Callable[[str], None],
        heartbeat_seconds: float = 2.0,
        preview_width: int = 1280,
        preview_height: int = 720,
    ):
        self.stream = stream
        self.url = url
        self._log = log
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._preview_width = int(preview_width)
        self._preview_height = int(preview_height)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._proc: Optional[subprocess.Popen[str]] = None
        self._reader_thread: Optional[threading.Thread] = None

        self._health = ProcHealth(state="IDLE", attempt=0, fps=0.0, last_frame_age_ms=-1)

    def start(self) -> None:
        if self._reader_thread and self._reader_thread.is_alive():
            return

        self._stop_event.clear()
        self._spawn(reason="START")

        self._reader_thread = threading.Thread(target=self._reader_loop, name=f"proc_reader.{self.stream}", daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._terminate(reason="SERVICE_STOP:STOP")

        t = self._reader_thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._reader_thread = None

    def _spawn(self, reason: str) -> None:
        # ВАЖНО: sys.executable гарантирует venv python (если parent в venv)
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "app.vendor.video_channel.client.reader_process",
            "--stream",
            self.stream,
            "--url",
            self.url,
            "--heartbeat-seconds",
            str(self._heartbeat_seconds),
            "--preview-width",
            str(self._preview_width),
            "--preview-height",
            str(self._preview_height),
        ]

        cwd = os.getcwd()
        env = os.environ.copy()
        env["PYTHONPATH"] = cwd + os.pathsep + env.get("PYTHONPATH", "")

        self._log(f"PROC_SPAWN stream={self.stream} reason={reason} cmd={' '.join(cmd)}")

        with self._lock:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                env=env,
            )
            self._health.state = "CONNECTING"
            self._health.attempt = 0

    def _terminate(self, reason: str) -> None:
        self._log(f"PROC_TERMINATE stream={self.stream} reason={reason}")
        with self._lock:
            proc = self._proc

        if proc is None:
            return

        try:
            proc.terminate()
        except Exception:
            pass

        try:
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        with self._lock:
            # критично: обнуляем под lock
            if self._proc is proc:
                self._proc = None
            self._health.state = "STOPPED"

    def _reader_loop(self) -> None:
        # читаем stdout child, но безопасно переживаем shutdown race
        while not self._stop_event.is_set():
            with self._lock:
                proc = self._proc

            if proc is None:
                # могло случиться на stop() между итерациями
                break

            try:
                line = proc.stdout.readline() if proc.stdout else ""
            except Exception:
                line = ""

            if not line:
                # процесс мог завершиться
                try:
                    rc = proc.poll()
                except Exception:
                    rc = None

                if rc is not None:
                    self._log(f"PROC_EXIT stream={self.stream} rc={rc}")
                    if not self._stop_event.is_set():
                        # перезапуск
                        self._terminate(reason="RESTART:PROC_EXIT")
                        self._spawn(reason="RESTART:PROC_EXIT")
                        continue
                    break

                time.sleep(0.01)
                continue

            line = line.rstrip("\n")
            self._handle_child_line(line)

        # выход без исключений

    def _handle_child_line(self, line: str) -> None:
        # протокол: либо JSON event, либо plain log
        try:
            ev = json.loads(line)
        except Exception:
            self._log(f"CHILD_OUT stream={self.stream} msg={line}")
            return

        t = ev.get("type")
        if t == "log":
            self._log(f"CHILD_LOG stream={self.stream} msg={ev.get('msg','')}")
        else:
            self._log(f"CHILD_EVENT stream={self.stream} ev={json.dumps(ev)}")
