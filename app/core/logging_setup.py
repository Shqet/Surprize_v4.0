from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from app.core.event_bus import EventBus
from app.core.events import LogEvent


def setup_logging(log_path: str = "./outputs/logs/app.log", level: int = logging.INFO) -> None:
    """
    v0:
      - log to file ./outputs/logs/app.log (create folder if needed)
      - simple format: timestamp/level/logger/message
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Avoid duplicate handlers if setup_logging() is called multiple times.
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Remove existing FileHandlers pointing to the same path.
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler):
            try:
                if Path(getattr(h, "baseFilename", "")).resolve() == path.resolve():
                    root.removeHandler(h)
            except Exception:
                # If any handler is weird, leave it alone.
                pass

    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)


def emit_log(bus: EventBus, level: str, source: str, code: str, message: str) -> None:
    """
    Publish LogEvent + write to python logging.

    Contract:
      - LogEvent.message format: "k=v k2=v2" (caller ensures)
      - log codes must be from CONTRACTS v0 (caller ensures)
    """
    logger = logging.getLogger(source)

    lvl = level.upper().strip()
    if lvl == "DEBUG":
        logger.debug("%s %s", code, message)
    elif lvl == "INFO":
        logger.info("%s %s", code, message)
    elif lvl == "WARNING" or lvl == "WARN":
        logger.warning("%s %s", code, message)
    elif lvl == "ERROR":
        logger.error("%s %s", code, message)
    elif lvl == "CRITICAL":
        logger.critical("%s %s", code, message)
    else:
        # Fallback: keep it visible in file even if user passed unknown level.
        logger.info("%s %s", code, message)
        lvl = "INFO"

    bus.publish(LogEvent(level=lvl, source=source, code=code, message=message))
