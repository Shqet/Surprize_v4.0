from __future__ import annotations

import os
import subprocess
from typing import Any


def windows_no_console_kwargs() -> dict[str, Any]:
    """
    Return subprocess kwargs to suppress child console windows on Windows.
    No-op on non-Windows platforms.
    """
    if os.name != "nt":
        return {}

    kwargs: dict[str, Any] = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    kwargs["startupinfo"] = startupinfo
    return kwargs

