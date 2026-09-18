"""Filesystem locations used by LexiTrack.

All paths are derived at runtime. Nothing in the code base may hard-code a
machine-specific directory: the application must work from any clone location
and for any user account.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "LexiTrack"

#: Repository / installation root (the directory that contains ``lexitrack/``).
#: In a source checkout this is the clone; in a regular ``pip install`` it is
#: ``site-packages``, which is why nothing is stored here unless
#: :func:`is_source_checkout` says so.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENV_DATA_DIR = "LEXITRACK_DATA_DIR"


def is_source_checkout() -> bool:
    """True when LexiTrack runs from a clone of the repository.

    That is a clone used directly or installed with ``pip install -e .``; the
    marker is the ``pyproject.toml`` beside the package. A regular
    ``pip install`` copies only the package into ``site-packages``, where
    there is no ``pyproject.toml`` — and where the previous rule, "the folder
    is writable", was also true, so the database silently ended up inside
    ``site-packages`` and was lost on the next upgrade.
    """
    return (PROJECT_ROOT / "pyproject.toml").is_file() and _is_writable(PROJECT_ROOT)


def data_dir() -> Path:
    """Return the writable directory that holds the database and exports.

    Resolution order:

    1. ``LEXITRACK_DATA_DIR`` when set (tests, portable installs).
    2. ``<clone>/data`` in a source checkout, which keeps a development clone
       self-contained.
    3. The per-user application data folder otherwise — on Windows
       ``%LOCALAPPDATA%\\LexiTrack``, the standard place for an app's own
       data, and the same folder Qt calls AppLocalDataLocation.
    """
    override = os.environ.get(_ENV_DATA_DIR)
    if override:
        return Path(override).expanduser().resolve()
    if is_source_checkout():
        return PROJECT_ROOT / "data"
    return user_data_dir()


def user_data_dir() -> Path:
    """Return the per-user application data directory for the current OS."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


def database_path() -> Path:
    return data_dir() / "vocabulary.db"


def exports_dir() -> Path:
    return data_dir() / "exports"


def logs_dir() -> Path:
    """Where the daily log files go, when debug logging is on.

    Beside ``data`` rather than inside it: logs describe the program, not the
    vocabulary, and must never end up in a backup. ``LEXITRACK_LOG_DIR``
    overrides it; with ``LEXITRACK_DATA_DIR`` set (tests, portable installs)
    the logs stay inside that folder so nothing leaks into the clone.
    """
    override = os.environ.get("LEXITRACK_LOG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.environ.get(_ENV_DATA_DIR):
        return data_dir() / "logs"
    if is_source_checkout():
        return PROJECT_ROOT / "logs"
    return user_data_dir() / "logs"


def log_file(day: str, directory: Path | None = None) -> Path:
    """The log file for one day, e.g. ``logs/lexitrack-2026-09-18.log``."""
    return (directory or logs_dir()) / f"lexitrack-{day}.log"


def ensure_data_dirs() -> Path:
    """Create the data directories if they do not exist and return the data dir."""
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "exports").mkdir(parents=True, exist_ok=True)
    return root


def _is_writable(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False
