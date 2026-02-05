from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ProfileError(ValueError):
    """Raised when a profile YAML is missing required keys/structure."""


def load_profile(profile_name: str) -> dict[str, Any]:
    """
    Load and minimally validate a profile.

    Contract v0:
      - file: app/profiles/{profile_name}.yaml
      - YAML structure:
          <profile_name>:
            services:
              exe_runner:
                path: str
                args: str
                timeout_sec: int
      - loader raises exceptions; orchestrator logs (SERVICE_ERROR / ORCH_STATE_CHANGE)
    """
    if not profile_name or not profile_name.strip():
        raise ProfileError("profile_name must be non-empty")

    path = Path(__file__).resolve().parent / f"{profile_name}.yaml"
    if not path.exists():
        raise ProfileError(f"profile file not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as ex:
        raise ProfileError(f"failed to parse YAML: {type(ex).__name__}") from ex

    if not isinstance(data, dict):
        raise ProfileError("profile root must be a mapping (dict)")

    if profile_name not in data:
        raise ProfileError(f"missing root key: {profile_name}")

    root = data.get(profile_name)
    if not isinstance(root, dict):
        raise ProfileError(f"root '{profile_name}' must be a mapping (dict)")

    services = root.get("services")
    if not isinstance(services, dict):
        raise ProfileError("missing or invalid key: services (must be mapping)")

    exe_runner = services.get("exe_runner")
    if not isinstance(exe_runner, dict):
        raise ProfileError("missing or invalid key: services.exe_runner (must be mapping)")

    _require_str(exe_runner, "path", "services.exe_runner.path")
    _require_str(exe_runner, "args", "services.exe_runner.args")
    _require_int(exe_runner, "timeout_sec", "services.exe_runner.timeout_sec")

    return data


def _require_str(d: dict[str, Any], key: str, path: str) -> None:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ProfileError(f"missing or invalid key: {path} (must be non-empty string)")


def _require_int(d: dict[str, Any], key: str, path: str) -> None:
    v = d.get(key)
    if not isinstance(v, int):
        raise ProfileError(f"missing or invalid key: {path} (must be int)")
