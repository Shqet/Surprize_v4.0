# tools/run_ingest_debug.py
from __future__ import annotations

import time
from typing import Any

from app.core.event_bus import EventBus
from app.core.logging_setup import setup_logging
from app.profiles.loader import ProfileError, load_profile
from app.services.rtsp_ingest_service import RtspIngestService


def main() -> int:
    # ВАЖНО: включаем file logging (./data/app.log)
    setup_logging()

    bus = EventBus()

    profile_name = "default"
    try:
        data: dict[str, Any] = load_profile(profile_name)
    except ProfileError as e:
        print(f"[profile] error: {e}")
        return 2

    root = data.get(profile_name)
    if not isinstance(root, dict):
        print(f"[profile] error: missing root key: {profile_name}")
        return 2

    services = root.get("services", {})
    section = services.get("rtsp_ingest")

    if not isinstance(section, dict):
        print("rtsp_ingest section missing in profile")
        return 2

    svc = RtspIngestService(bus)
    svc.start(section)

    st = svc.status().value
    print(f"rtsp_ingest status={st}")

    if st == "ERROR":
        print("rtsp_ingest failed fast (check ffmpeg_path / PATH)")
        return 2

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        svc.stop()
        print("stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
