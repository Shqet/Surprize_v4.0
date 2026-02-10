from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass()
class ProfileError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def _profiles_dir() -> Path:
    # app/profiles/loader.py -> app/profiles
    return Path(__file__).resolve().parent


def _require_path(d: dict[str, Any], path: str) -> Any:
    """
    Minimal presence check: "a.b.c" must exist.
    Returns value or raises ProfileError.
    """
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ProfileError(f"missing={path}")
        cur = cur[part]
    return cur


def load_profile(profile_name: str) -> dict[str, Any]:
    """
    v2 loader:
      - Reads app/profiles/{profile_name}.yaml
      - Validates minimal presence of required keys
      - No logging here (per contract): caller (Orchestrator) handles logs
    """
    p = _profiles_dir() / f"{profile_name}.yaml"
    if not p.exists():
        raise ProfileError(f"not_found={p.as_posix()}")

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as ex:
        raise ProfileError(f"yaml_error={type(ex).__name__}") from ex

    if not isinstance(data, dict):
        raise ProfileError("yaml_root_not_mapping=1")

    if profile_name not in data or not isinstance(data[profile_name], dict):
        raise ProfileError(f"missing_root_key={profile_name}")

    root = data[profile_name]

    # v0/v1 required service: exe_runner (kept)
    _require_path(root, "services.exe_runner.path")
    _require_path(root, "services.exe_runner.args")
    _require_path(root, "services.exe_runner.timeout_sec")

    # v2: new service ballistics_model (required for v2 stage)
    _require_path(root, "services.ballistics_model.model_root")
    _require_path(root, "services.ballistics_model.python_exe")
    _require_path(root, "services.ballistics_model.calc_entry")
    _require_path(root, "services.ballistics_model.plots_entry")
    _require_path(root, "services.ballistics_model.out_root")
    _require_path(root, "services.ballistics_model.timeout_sec")
    _require_path(root, "services.ballistics_model.make_plots")

    cfg = _require_path(root, "services.ballistics_model.config_json")
    if not isinstance(cfg, dict):
        raise ProfileError("config_json_not_mapping=1")

    # v1/v2 orchestrator section is optional; defaulting+WARNING is done in Orchestrator
    # (do not enforce here)

    return data
