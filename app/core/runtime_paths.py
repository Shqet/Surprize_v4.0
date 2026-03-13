from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def app_root() -> Path:
    """
    Runtime application root.
    - frozen: directory with executable
    - dev: project root
    """
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            pass
    return _project_root()


def bundled_root() -> Optional[Path]:
    """
    Return frozen runtime bundle root when available.
    For PyInstaller this is typically sys._MEIPASS (often dist/.../_internal).
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        try:
            return Path(str(base)).resolve()
        except Exception:
            return None

    if getattr(sys, "frozen", False):
        try:
            exe_dir = Path(sys.executable).resolve().parent
            internal_dir = (exe_dir / "_internal").resolve()
            if internal_dir.exists():
                return internal_dir
        except Exception:
            return None
    return None


def _search_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Optional[Path]) -> None:
        if p is None:
            return
        try:
            r = p.resolve()
        except Exception:
            return
        key = str(r).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(r)

    _add(app_root())
    _add(bundled_root())
    _add(Path.cwd())
    _add(_project_root())
    return roots


def find_existing_path(path_like: str | Path) -> Optional[Path]:
    p = Path(str(path_like)).expanduser()
    if p.is_absolute():
        try:
            rp = p.resolve()
        except Exception:
            return None
        return rp if rp.exists() else None

    for root in _search_roots():
        cand = (root / p)
        try:
            rc = cand.resolve()
        except Exception:
            continue
        if rc.exists():
            return rc
    return None


def resolve_runtime_path(path_like: str | Path) -> Path:
    """
    Resolve path for runtime usage.
    For relative paths tries cwd, frozen bundle root and project root.
    Falls back to app-root-relative absolute path when target does not exist yet.
    """
    found = find_existing_path(path_like)
    if found is not None:
        return found

    p = Path(str(path_like)).expanduser()
    if p.is_absolute():
        try:
            return p.resolve()
        except Exception:
            return p
    return (app_root() / p).resolve()


def default_gps_nav_path() -> Path:
    rel = Path("data") / "ephemerides" / "brdc0430.25n"
    found = find_existing_path(rel)
    if found is not None:
        return found
    return resolve_runtime_path(rel)
