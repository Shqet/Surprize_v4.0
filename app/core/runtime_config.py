from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.runtime_paths import default_gps_nav_path, resolve_runtime_path

DEFAULT_SESSION_OUTPUT_ROOT = "outputs/sessions"
DEFAULT_AUTO_STOP_AFTER_GPS_SEC = 10.0
DEFAULT_ANIM_WITHOUT_TEST = True
DEFAULT_UI_THEME = "light"
DEFAULT_GPS_TIMEOUT_SEC = 120


@dataclass(frozen=True)
class RuntimeConfig:
    gps_nav_default_path: str
    session_output_root: str
    auto_stop_after_gps_sec: float
    monitor_anim_without_test: bool
    ui_theme: str
    gps_timeout_sec: int

    @staticmethod
    def _as_bool(v: Any, default: bool) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @classmethod
    def defaults(cls) -> "RuntimeConfig":
        nav = default_gps_nav_path()
        return cls(
            gps_nav_default_path=str(nav),
            session_output_root=str(resolve_runtime_path(DEFAULT_SESSION_OUTPUT_ROOT)),
            auto_stop_after_gps_sec=float(DEFAULT_AUTO_STOP_AFTER_GPS_SEC),
            monitor_anim_without_test=bool(DEFAULT_ANIM_WITHOUT_TEST),
            ui_theme=DEFAULT_UI_THEME,
            gps_timeout_sec=int(DEFAULT_GPS_TIMEOUT_SEC),
        )

    @classmethod
    def from_settings(cls, store: Any) -> "RuntimeConfig":
        d = cls.defaults()
        nav = str(store.value("gps_nav_default_path", d.gps_nav_default_path) or "").strip()
        if not nav:
            nav = d.gps_nav_default_path
        out_root = str(store.value("session_output_root", d.session_output_root) or "").strip()
        if not out_root:
            out_root = d.session_output_root
        auto_stop_raw = store.value("auto_stop_after_gps_sec", d.auto_stop_after_gps_sec)
        timeout_raw = store.value("gps_timeout_sec", d.gps_timeout_sec)
        theme = str(store.value("ui_theme", d.ui_theme) or "").strip().lower()
        if theme not in ("light", "dark"):
            theme = d.ui_theme
        try:
            auto_stop = float(auto_stop_raw)
        except Exception:
            auto_stop = d.auto_stop_after_gps_sec
        auto_stop = max(0.0, min(3600.0, auto_stop))
        try:
            gps_timeout_sec = int(timeout_raw)
        except Exception:
            gps_timeout_sec = d.gps_timeout_sec
        gps_timeout_sec = max(10, min(1800, gps_timeout_sec))
        anim = cls._as_bool(store.value("monitor_anim_without_test", d.monitor_anim_without_test), d.monitor_anim_without_test)
        return cls(
            gps_nav_default_path=str(resolve_runtime_path(nav)),
            session_output_root=str(resolve_runtime_path(out_root)),
            auto_stop_after_gps_sec=auto_stop,
            monitor_anim_without_test=anim,
            ui_theme=theme,
            gps_timeout_sec=gps_timeout_sec,
        )

    def to_settings_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_ui_dict(self) -> dict[str, Any]:
        return self.to_settings_dict()

    def with_updates(self, **kwargs: Any) -> "RuntimeConfig":
        d = self.to_settings_dict()
        d.update(kwargs)
        # Re-validate by passing through transient store-like dict wrapper.
        class _Store:
            def __init__(self, data: dict[str, Any]) -> None:
                self._d = data
            def value(self, k: str, default: Any = None) -> Any:
                return self._d.get(k, default)

        return RuntimeConfig.from_settings(_Store(d))

