# client/ipc_core.py
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TextIO, Optional


def read_jsonl_lines(stream: TextIO) -> Iterator[dict]:
    """
    Yield dict objects from a JSONL stream. Ignores empty lines.
    Invalid JSON lines are skipped by raising ValueError to caller (IpcServer catches).
    """
    for line in stream:
        s = (line or "").strip()
        if not s:
            continue
        obj = json.loads(s)
        if isinstance(obj, dict):
            yield obj
        else:
            raise ValueError("JSON line is not an object (dict)")


EmitFn = Callable[[dict], None]
HandleCmdFn = Callable[[dict], None]


@dataclass
class IpcServer:
    """
    Simple stdin JSONL -> handle_cmd(dict) in a background thread.

    - in_fp/out_fp are file-like (TextIO) => unit tests can use io.StringIO.
    - on_parse_error/on_cmd_error: write ERROR event to out_fp.
    """
    handle_cmd: HandleCmdFn
    in_fp: TextIO
    out_fp: TextIO

    on_parse_error: Optional[Callable[[Exception, str], None]] = None
    on_cmd_error: Optional[Callable[[Exception, dict], None]] = None

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ipc_stdin_loop", daemon=True)
        self._write_lock = threading.Lock()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def emit(self, obj: dict) -> None:
        with self._write_lock:
            self.out_fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
            try:
                self.out_fp.flush()
            except Exception:
                pass

    def _default_parse_error(self, exc: Exception, raw_line: str) -> None:
        self.emit({"type": "evt", "evt": "ERROR", "error": f"bad_json:{type(exc).__name__}:{exc}", "raw": raw_line})

    def _default_cmd_error(self, exc: Exception, cmd: dict) -> None:
        self.emit({"type": "evt", "evt": "ERROR", "error": f"cmd_failed:{type(exc).__name__}:{exc}", "cmd": cmd})

    def _run(self) -> None:
        # We can't easily "break" a blocking iteration of stdin in Windows reliably;
        # stop() is advisory and used mainly for tests. Normal process exit is the primary stop.
        while not self._stop.is_set():
            try:
                # manual line loop (so we can include raw text for parse error)
                line = self.in_fp.readline()
                if line == "":
                    # EOF
                    return
                raw = (line or "").strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if not isinstance(obj, dict):
                        raise ValueError("JSON line is not an object (dict)")
                except Exception as e:
                    (self.on_parse_error or self._default_parse_error)(e, raw)
                    continue

                try:
                    self.handle_cmd(obj)
                except Exception as e:
                    (self.on_cmd_error or self._default_cmd_error)(e, obj)
            except Exception:
                # last resort: don't spin
                return
