# tools/run_rtsp_ingest_only.py
from __future__ import annotations

import time

from app.core.event_bus import EventBus
from app.core.logging_setup import emit_log, setup_logging
from app.core.events import LogEvent, ServiceStatusEvent
from app.profiles.loader import load_profile
from app.services.rtsp_ingest_service import RtspIngestService
from app.services.service_manager import ServiceManager


def _pick_profile_section(profile_cfg: dict, profile_name: str) -> dict:
    """
    load_profile() in this project returns either:
      - {"default": {...}}
    so we normalize into the profile section dict.
    """
    if isinstance(profile_cfg, dict) and isinstance(profile_cfg.get(profile_name), dict):
        return profile_cfg[profile_name]
    if isinstance(profile_cfg, dict):
        return profile_cfg
    return {}


def main() -> int:
    setup_logging("./data/app.log")

    bus = EventBus()

    # Print a few key events to console (optional, but handy for debug)
    def _on_log(e: LogEvent) -> None:
        # LogEvent has: level, message
        try:
            print(e.message)
        except Exception:
            pass

    def _on_status(e: ServiceStatusEvent) -> None:
        try:
            print(f"SERVICE_STATUS service={e.service_name} status={e.status}")
        except Exception:
            pass

    bus.subscribe(LogEvent, _on_log)
    bus.subscribe(ServiceStatusEvent, _on_status)

    sm = ServiceManager(bus)

    ingest = RtspIngestService(bus)
    sm.register(ingest)

    # Load profile + extract rtsp_ingest section
    profile_name = "default"
    prof = load_profile(profile_name)
    root = _pick_profile_section(prof, profile_name)

    services_cfg = root.get("services", {}) if isinstance(root, dict) else {}
    ingest_cfg = services_cfg.get("rtsp_ingest", {}) if isinstance(services_cfg, dict) else {}
    if not isinstance(ingest_cfg, dict):
        ingest_cfg = {}

    emit_log(bus, "INFO", "system", "SYSTEM_START", "mode=ingest_only")

    # Start only rtsp_ingest (no orchestrator, no UI)
    try:
        ingest.start(ingest_cfg)
    except TypeError:
        # some services use kw-only start(profile_section=...)
        ingest.start(profile_section=ingest_cfg)

    print("rtsp_ingest started. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sm.stop_all()
        except Exception:
            pass
        emit_log(bus, "INFO", "system", "SYSTEM_STOP", "req=1")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
