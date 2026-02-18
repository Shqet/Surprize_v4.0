from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from app.vendor.video_channel.client.stream_worker import StreamWorker


def _now() -> float:
    return time.time()


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _write_jpg_atomic_bytes(final_path: str, jpg_bytes: bytes) -> None:
    p = Path(final_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(jpg_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)  # atomic replace on Windows/Linux


def _encode_jpg(frame_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame_bgr)
    if not ok:
        raise RuntimeError("imencode_failed")
    return buf.tobytes()


def _make_black(width: int, height: int) -> np.ndarray:
    # BGR
    return np.zeros((height, width, 3), dtype=np.uint8)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stream", default="visible")
    p.add_argument("--url", required=True)

    p.add_argument("--heartbeat-seconds", type=float, default=2.0)
    p.add_argument("--no-data-timeout-seconds", type=float, default=7.0)
    p.add_argument("--fps-window-seconds", type=float, default=3.0)

    # for preview fallback (black frame) when no last_frame is available
    p.add_argument("--preview-width", type=int, default=1280)
    p.add_argument("--preview-height", type=int, default=720)

    args = p.parse_args()

    stopping = {"done": False}

    def on_signal(signum, _frame):
        stopping["done"] = True

    signal.signal(signal.SIGINT, on_signal)
    try:
        signal.signal(signal.SIGTERM, on_signal)
    except Exception:
        pass

    def wlog(line: str) -> None:
        _emit({"type": "log", "ts": _now(), "msg": line})

    worker = StreamWorker(
        stream=args.stream,
        url=args.url,
        log=wlog,
        heartbeat_seconds=int(args.heartbeat_seconds),
        fps_window_seconds=float(args.fps_window_seconds),
        no_data_timeout_seconds=float(args.no_data_timeout_seconds),
        jitter=True,
    )

    # ---------- IPC (stdin json lines) ----------
    cmd_q: "queue.Queue[dict]" = queue.Queue()

    def stdin_reader() -> None:
        # Reads JSON lines from parent. If stdin closes -> just exit thread.
        while True:
            line = sys.stdin.readline()
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
                if isinstance(cmd, dict):
                    cmd_q.put(cmd)
            except Exception:
                # ignore malformed
                continue

    t_stdin = threading.Thread(target=stdin_reader, name="stdin_reader", daemon=True)
    t_stdin.start()
    # -------------------------------------------

    worker.start()
    _emit({"type": "started", "ts": _now(), "stream": args.stream, "url": args.url})

    next_hb = _now() + float(args.heartbeat_seconds)

    try:
        while not stopping["done"]:
            time.sleep(0.05)

            # If worker thread died -> exit so parent restarts
            thr = getattr(worker, "_thread", None)
            if thr is not None and not thr.is_alive():
                _emit({"type": "fatal", "ts": _now(), "err": "worker_thread_dead"})
                return 2

            # handle IPC commands
            try:
                while True:
                    cmd = cmd_q.get_nowait()
                    c = cmd.get("cmd")
                    if c == "SAVE_PREVIEW":
                        path = cmd.get("path")
                        if not isinstance(path, str) or not path:
                            continue

                        try:
                            wlog(f"VIDEO_PREVIEW_CMD_RECV path={path}")
                            # Best-effort: try last_frame, else black
                            frame = None
                            try:
                                frame = worker.get_last_frame()
                            except Exception:
                                frame = None
                            if frame is None:
                                frame = _make_black(args.preview_width, args.preview_height)

                            jpg = _encode_jpg(frame)
                            _write_jpg_atomic_bytes(path, jpg)
                            wlog(f"VIDEO_PREVIEW_WRITE_OK path={path}")
                            _emit(
                                {
                                    "type": "evt",
                                    "ts": _now(),
                                    "evt": "PREVIEW_SAVED",
                                    "stream": worker.stream,
                                    "path": path,
                                }
                            )
                        except Exception as ex:
                            wlog(f"VIDEO_PREVIEW_WRITE_FAIL path={path} err={type(ex).__name__}")
                            wlog(f"VIDEO_PREVIEW_IO_ERROR err={type(ex).__name__}")
                            _emit(
                                {
                                    "type": "evt",
                                    "ts": _now(),
                                    "evt": "PREVIEW_SAVE_FAIL",
                                    "stream": worker.stream,
                                    "path": path,
                                    "err": f"{type(ex).__name__}",
                                }
                            )

                    else:
                        # ignore unknown commands for now
                        pass
            except queue.Empty:
                pass

            if _now() >= next_hb:
                st = getattr(worker, "state", "UNKNOWN")
                attempt = int(getattr(worker, "attempt", 0))
                fps = float(getattr(worker, "fps", 0.0))

                last_frame_ts = getattr(worker, "last_frame_ts", None)
                if last_frame_ts is None:
                    age_ms = -1
                else:
                    # worker.last_frame_ts uses time.monotonic()
                    age_ms = int((time.monotonic() - float(last_frame_ts)) * 1000)

                _emit(
                    {
                        "type": "hb",
                        "ts": _now(),
                        "stream": worker.stream,
                        "state": st,
                        "attempt": attempt,
                        "fps": fps,
                        "last_frame_age_ms": age_ms,
                        "url": worker.url,
                    }
                )
                next_hb = _now() + float(args.heartbeat_seconds)

    finally:
        try:
            worker.stop(reason="PROCESS_EXIT")
        except Exception:
            pass
        _emit({"type": "stopped", "ts": _now()})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
