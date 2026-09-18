"""Logging setup.

Nothing is written to disk unless the user asks for it. The log file exists
to explain a problem after the fact, and most days there is no problem, so a
log that grows every ten seconds while the bot polls is disk space spent on
nothing. Debug logging is one switch under Settings → Advanced, available in
Developer mode, and it can be turned on and off while the app runs.

When it is on:

* each day gets its own file, ``logs/lexitrack-YYYY-MM-DD.log``, so the file
  for the day something went wrong is easy to find and send;
* files older than :data:`KEEP_DAYS` days are deleted;
* the ``logs`` folder is next to ``data``, not inside it: logs describe the
  program, not the vocabulary, and are never part of a backup.

Whether it is on or not, messages still go to the console of a development
run, and the Telegram token never appears anywhere. The Bot API carries it in
every request URL, so the HTTP libraries that log each request are held at
WARNING, and every formatted line passes through :func:`redact` as a last line
of defence — a traceback can still quote a URL.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import paths

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

#: How many days of log files are kept.
KEEP_DAYS = 7

#: A Telegram bot token: the bot id, a colon, and at least 30 characters of
#: secret. No word boundary at the start: in a URL the id follows "bot".
_TOKEN = re.compile(r"(?<!\d)(\d{6,12}):[A-Za-z0-9_-]{30,}")

#: Libraries that log every HTTP request in full, token included.
_CHATTY = ("httpx", "httpcore", "telegram.ext")

#: The file handler while debug logging is on; ``None`` while it is off.
_file_handler: DailyFileHandler | None = None


def redact(text: str) -> str:
    """Replace the secret half of any bot token with ``[redacted]``."""
    return _TOKEN.sub(lambda match: f"{match.group(1)}:[redacted]", text)


class RedactingFormatter(logging.Formatter):
    """A formatter whose every line has passed through :func:`redact`."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class DailyFileHandler(logging.Handler):
    """Writes to ``lexitrack-<today>.log`` and moves to a new file at midnight.

    The date is checked on every record rather than by a timer, so a laptop
    that sleeps through midnight still starts a new file with the first line
    it writes the next morning.
    """

    def __init__(self, directory: Path) -> None:
        super().__init__(logging.DEBUG)
        self.directory = directory
        self._day: str | None = None
        self._stream = None

    @property
    def current_path(self) -> Path:
        return paths.log_file(self._today(), self.directory)

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = self._today()
            if today != self._day or self._stream is None:
                self._open(today)
            assert self._stream is not None
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)

    def _open(self, today: str) -> None:
        self._close_stream()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._stream = paths.log_file(today, self.directory).open("a", encoding="utf-8")
        self._day = today
        prune_logs(self.directory)

    def _close_stream(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def close(self) -> None:
        self.acquire()
        try:
            self._close_stream()
        finally:
            self.release()
        super().close()


def prune_logs(directory: Path, keep_days: int = KEEP_DAYS) -> list[Path]:
    """Delete daily log files older than ``keep_days`` days."""
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    removed = []
    for path in directory.glob("lexitrack-*.log"):
        day = path.stem.removeprefix("lexitrack-")
        if day < cutoff:
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass
    return removed


def remove_legacy_logs() -> list[Path]:
    """Delete the always-on log files older versions kept in ``data``.

    They were written whether or not anyone wanted them, and the ones from
    the first Telegram test contain the bot token.
    """
    removed = []
    for name in ("lexitrack.log", "lexitrack.log.1", "lexitrack.log.2"):
        path = paths.data_dir() / name
        try:
            if path.exists():
                path.unlink()
                removed.append(path)
        except OSError:
            pass
    return removed


def configure_logging(level: int | None = None) -> None:
    """Set up console logging once per process. No file is opened here."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    if level is None:
        level = logging.DEBUG if os.environ.get("LEXITRACK_DEBUG") else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    for name in _CHATTY:
        logging.getLogger(name).setLevel(logging.WARNING)

    # pythonw has no console; sys.stderr is None there and nothing is shown.
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(RedactingFormatter(_FORMAT))
        stream.setLevel(level)
        root.addHandler(stream)

    _CONFIGURED = True


def set_file_logging(enabled: bool, directory: Path | None = None) -> Path | None:
    """Start or stop writing the daily log file. Returns today's file when on.

    Safe to call repeatedly, and from the Settings window while the app runs:
    turning it on starts today's file, turning it off closes it.
    """
    global _file_handler
    root = logging.getLogger()
    if not enabled:
        if _file_handler is not None:
            root.removeHandler(_file_handler)
            _file_handler.close()
            _file_handler = None
            root.setLevel(logging.INFO)
        return None

    target = directory or paths.logs_dir()
    if _file_handler is not None and _file_handler.directory == target:
        return _file_handler.current_path
    if _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()
    handler = DailyFileHandler(target)
    handler.setFormatter(RedactingFormatter(_FORMAT))
    root.addHandler(handler)
    # Debug logging means debug: the scheduler's decisions are logged at DEBUG.
    root.setLevel(logging.DEBUG)
    _file_handler = handler
    return handler.current_path


def file_logging_enabled() -> bool:
    return _file_handler is not None
