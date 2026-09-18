"""Start with Windows: one registry value, under the current user.

``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` is the per-user
list Windows runs at sign-in. It needs no administrator rights, affects no
other account, and is exactly what Task Manager's Startup tab shows and lets
the user switch off — so the app never hides how it comes back.

The command uses ``pythonw.exe`` so no console window appears at sign-in.
On other platforms these functions report "unsupported" instead of raising:
the switch is simply not offered there.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LexiTrack"


def supported() -> bool:
    return sys.platform == "win32"


def launch_command() -> str:
    """The command Windows runs at sign-in."""
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if windowless.exists():
        executable = windowless
    return f'"{executable}" -m lexitrack --minimized'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Add or remove the entry. Returns the state actually in effect."""
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        log.exception("Could not change the Start with Windows setting")
    return is_enabled()
