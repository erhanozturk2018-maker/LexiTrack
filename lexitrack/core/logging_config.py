"""Logging setup.

LexiTrack logs to a rotating file next to the database plus, during
development, to stderr. Logs exist to explain parser, import, database and
export failures after the fact; they are deliberately quiet during normal use.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from . import paths

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: int | None = None, log_file: Path | None = None) -> None:
    """Configure root logging once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    if level is None:
        level = logging.DEBUG if os.environ.get("LEXITRACK_DEBUG") else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    try:
        target = log_file or paths.log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # A read-only install location must not prevent the app from starting.
        pass

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.setLevel(level)
    root.addHandler(stream)

    _CONFIGURED = True
