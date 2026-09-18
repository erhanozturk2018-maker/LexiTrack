"""Logging setup.

LexiTrack logs to a rotating file next to the database plus, during
development, to stderr. Logs exist to explain parser, import, database and
export failures after the fact; they are deliberately quiet during normal use.

The Telegram token must never reach the log. The Bot API carries it in every
request URL, so the HTTP libraries that log each request are held at WARNING,
and every formatted line is passed through :func:`redact` as a last line of
defence — a traceback can still quote a URL.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

from . import paths

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

#: A Telegram bot token: the bot id, a colon, and at least 30 characters of
#: secret. No word boundary at the start: in a URL the id follows "bot".
_TOKEN = re.compile(r"(?<!\d)(\d{6,12}):[A-Za-z0-9_-]{30,}")

#: Libraries that log every HTTP request in full, token included.
_CHATTY = ("httpx", "httpcore", "telegram.ext")


def redact(text: str) -> str:
    """Replace the secret half of any bot token with ``[redacted]``."""
    return _TOKEN.sub(lambda match: f"{match.group(1)}:[redacted]", text)


class RedactingFormatter(logging.Formatter):
    """A formatter whose every line has passed through :func:`redact`."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: int | None = None, log_file: Path | None = None) -> None:
    """Configure root logging once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    if level is None:
        level = logging.DEBUG if os.environ.get("LEXITRACK_DEBUG") else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    formatter = RedactingFormatter(_FORMAT)
    for name in _CHATTY:
        logging.getLogger(name).setLevel(logging.WARNING)

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
