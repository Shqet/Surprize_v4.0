# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Surprize (Windows desktop build, onedir).

Build:
    pyinstaller --clean Surprize.spec
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


spec_path = Path(globals().get("SPEC", "Surprize.spec"))
if not spec_path.is_absolute():
    spec_path = Path(globals().get("SPECPATH", os.getcwd())) / spec_path
project_root = spec_path.resolve().parent
app_icon_path = project_root / "app" / "ui" / "assets" / "icons" / "main_app.ico"

block_cipher = None


def _data_if_exists(src: Path, dst: str) -> list[tuple[str, str]]:
    if src.exists():
        return [(str(src), dst)]
    return []


hiddenimports = []
hiddenimports += [
    "cv2",
    "numpy",
    "pyqtgraph",
    "pyqtgraph.opengl",
    "pyqtgraph.Vector",
    "OpenGL",
    "OpenGL.GL",
    "OpenGL.GLU",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]

datas: list[tuple[str, str]] = []
datas += collect_data_files("pyqtgraph", include_py_files=False)
datas += _data_if_exists(project_root / "app" / "profiles", "app/profiles")
datas += _data_if_exists(project_root / "app" / "ui" / "assets", "app/ui/assets")
datas += _data_if_exists(project_root / "data", "data")
datas += _data_if_exists(project_root / "bin", "bin")

binaries: list[tuple[str, str, str]] = []
binaries += collect_dynamic_libs("cv2")


a = Analysis(
    ["app/main.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(project_root / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Surprize",
    icon=str(app_icon_path) if app_icon_path.exists() else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Surprize",
)
