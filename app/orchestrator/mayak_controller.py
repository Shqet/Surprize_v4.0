from __future__ import annotations

from typing import Any


class MayakStubController:
    """Temporary no-op controller used while real Mayak integration is undefined."""

    name = "mayak_spindle_stub"

    def is_ready(self) -> bool:
        return True

    def set_spindle_speed(self, spindle: str, *, direction: int, rpm: int) -> None:
        return

    def stop_spindle(self, spindle: str) -> None:
        return

    def emergency_stop(self) -> None:
        return

    def apply_profile_linear(self, spindle: str, *, from_rpm: int, to_rpm: int, duration_sec: float) -> None:
        return

    def start_test(
        self,
        *,
        head_start_rpm: int,
        head_end_rpm: int,
        tail_start_rpm: int,
        tail_end_rpm: int,
        profile_type: str,
        duration_sec: float,
    ) -> None:
        return

    def stop_test(self) -> None:
        return


def read_mayak_mode(profile_cfg: dict, profile_name: str) -> str:
    root = profile_cfg.get(profile_name) if isinstance(profile_cfg, dict) else None
    root = root if isinstance(root, dict) else (profile_cfg if isinstance(profile_cfg, dict) else {})
    services = root.get("services", {}) if isinstance(root, dict) else {}
    mayak = services.get("mayak_spindle", {}) if isinstance(services, dict) else {}
    mode_val = mayak.get("mode", "real") if isinstance(mayak, dict) else "real"
    mode = str(mode_val).strip().lower()
    if mode not in ("real", "stub"):
        mode = "real"
    return mode


def is_stub_mode(mode: str) -> bool:
    return str(mode).strip().lower() == "stub"


def resolve_mayak_controller(*, mode: str, services_map: dict[str, Any], stub: MayakStubController) -> Any:
    if is_stub_mode(mode):
        return stub
    return services_map.get("mayak_spindle")
