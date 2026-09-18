"""Where the Telegram bot's token and chat come from.

The token is the bot's password: whoever holds it can read and send messages
as the bot. It is therefore never written to the database, which is the file
that gets copied into backups, attached to bug reports and moved between
machines. It comes from, in order:

1. the environment variable ``LEXITRACK_TELEGRAM_TOKEN``;
2. a ``.env`` file in the data folder (``%LOCALAPPDATA%\\LexiTrack`` for an
   installed copy);
3. a ``.env`` file in the project folder, when running from a clone.

Both ``.env`` locations are ignored by git. If the token ever leaks it is
revoked in BotFather and replaced in ``.env`` — nothing in the database
changes.

The optional ``LEXITRACK_TELEGRAM_CHAT_ID`` pins the bot to one chat. Without
it the first chat to send ``/start`` is remembered and every other chat is
ignored, so a stranger who finds the bot's name cannot answer your reviews.

A tiny parser reads the ``.env`` file rather than a dependency: the format
needed here is ``KEY=value`` lines, comments and optional quotes, and a
library would be more code than that.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core import paths

TOKEN_VARIABLE = "LEXITRACK_TELEGRAM_TOKEN"
CHAT_VARIABLE = "LEXITRACK_TELEGRAM_CHAT_ID"
ENV_FILE = ".env"


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """What the bot needs to connect, and where it was found."""

    token: str | None = None
    allowed_chat_id: str | None = None
    #: The file the token came from, or ``None`` for the environment. Shown in
    #: Settings so "which .env is it reading?" has an answer.
    source: Path | None = None

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    @property
    def masked_token(self) -> str:
        """The token as it can safely appear on screen or in a log."""
        if not self.token:
            return ""
        head, _, tail = self.token.partition(":")
        return f"{head}:…{tail[-4:]}" if tail else f"{self.token[:4]}…"


def env_file_candidates() -> list[Path]:
    """The ``.env`` files that are read, most specific first."""
    candidates = [paths.data_dir() / ENV_FILE]
    # The clone's own .env only means something in a source checkout; in an
    # installed copy PROJECT_ROOT is site-packages.
    if paths.is_source_checkout():
        candidates.append(paths.PROJECT_ROOT / ENV_FILE)
    return candidates


def load_config() -> TelegramConfig:
    """Read the token and chat from the environment or a ``.env`` file.

    The environment wins for each key separately, so a machine can keep the
    token in ``.env`` and override only the chat for a test.
    """
    token = _clean(os.environ.get(TOKEN_VARIABLE))
    chat = _clean(os.environ.get(CHAT_VARIABLE))
    source: Path | None = None
    for candidate in env_file_candidates():
        if token and chat:
            break
        values = read_env_file(candidate)
        if not values:
            continue
        if not token and _clean(values.get(TOKEN_VARIABLE)):
            token = _clean(values.get(TOKEN_VARIABLE))
            source = candidate
        if not chat and _clean(values.get(CHAT_VARIABLE)):
            chat = _clean(values.get(CHAT_VARIABLE))
    return TelegramConfig(token=token, allowed_chat_id=chat, source=source)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines. Missing or unreadable files read as empty."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # An unquoted value ends at an inline comment.
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


def _clean(value: str | None) -> str | None:
    """Treat the template's placeholder and blank values as "not set"."""
    if value is None:
        return None
    value = value.strip()
    if not value or value.startswith("<") or value.lower() in {"your-token-here", "changeme"}:
        return None
    return value
