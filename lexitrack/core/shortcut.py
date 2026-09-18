"""A desktop shortcut to LexiTrack, with its own icon.

``pip`` creates ``lexitrack-gui.exe`` in the environment's ``Scripts`` folder,
but that launcher has no icon of its own and lives somewhere nobody looks.
``lexitrack --create-shortcut`` puts a proper ``LexiTrack`` shortcut on the
desktop instead: it starts ``pythonw.exe -m lexitrack`` from the same Python
that is running now (so a virtual environment is honoured), without a console
window, and shows the L icon from ``app.ico``.

A ``.lnk`` file is written by Windows' own shell object (``WScript.Shell``)
through PowerShell, which every Windows install has, rather than by adding a
Windows-only dependency to the project. Values reach PowerShell as environment
variables, never spliced into the script, so a path with spaces or quotes
cannot break it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import paths

SHORTCUT_NAME = "LexiTrack.lnk"
ICON = Path(__file__).resolve().parents[1] / "ui" / "theme" / "icons" / "app.ico"

_SCRIPT = r"""
$desktop = [Environment]::GetFolderPath('Desktop')
$path = Join-Path $desktop $env:LEXI_NAME
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = $env:LEXI_TARGET
$link.Arguments = $env:LEXI_ARGS
$link.WorkingDirectory = $env:LEXI_WORKDIR
$link.IconLocation = "$($env:LEXI_ICON),0"
$link.Description = 'LexiTrack - Vocabulary Learning & Review'
$link.Save()
Write-Output $path
"""


def supported() -> bool:
    return sys.platform == "win32"


def launcher() -> tuple[Path, str]:
    """The program and arguments the shortcut runs."""
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    return (windowless if windowless.exists() else executable), "-m lexitrack"


def create_desktop_shortcut() -> Path:
    """Create or replace ``LexiTrack.lnk`` on the desktop. Returns its path."""
    if not supported():
        raise OSError("Desktop shortcuts are only created on Windows.")
    target, arguments = launcher()
    workdir = paths.PROJECT_ROOT if paths.is_source_checkout() else Path.home()
    env = {
        **os.environ,
        "LEXI_NAME": SHORTCUT_NAME,
        "LEXI_TARGET": str(target),
        "LEXI_ARGS": arguments,
        "LEXI_WORKDIR": str(workdir),
        "LEXI_ICON": str(ICON),
    }
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "PowerShell could not create the shortcut.")
    return Path(result.stdout.strip().splitlines()[-1])
