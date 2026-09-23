"""Persistence for ``app_settings`` and ``runtime_state``.

Two key/value tables that look alike and mean different things:

* ``app_settings`` is what the user decided: 25 new words a day, notify at
  06:00, Telegram on. It is backed up with the database and survives a
  reinstall. Because the Telegram thread reads the same rows as the desktop
  UI, a change made in Settings takes effect for the bot without a restart.
* ``runtime_state`` is what the program knows about its own last run: when it
  last sent the morning message, when it last saw the clock. It is a cache of
  facts about this machine, and losing it costs at most one duplicate
  notification.

Settings are read as a whole snapshot (:class:`LearningSettings`) rather than
one key at a time, so the queue builder cannot see 25 for one card and 10 for
the next.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..core.errors import StorageError
from ..database.connection import Database
from ..models.settings import DEFAULT_SETTINGS, LearningSettings, Setting, serialize


class SettingsRepository:
    """Reads and writes the settings the learning engine understands."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def all_values(self) -> dict[str, str]:
        """Every stored setting, with defaults filled in for missing keys."""
        rows = self._db.connection.execute("SELECT key, value FROM app_settings").fetchall()
        stored = {row["key"]: row["value"] for row in rows}
        return {**DEFAULT_SETTINGS, **stored}

    def load(self) -> LearningSettings:
        """The current settings as a typed snapshot."""
        return LearningSettings.from_values(self.all_values())

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._db.connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (str(key),)
        ).fetchone()
        if row is not None:
            return row["value"]
        if default is not None:
            return default
        return DEFAULT_SETTINGS.get(str(key))

    def set(self, key: str, value: object) -> None:
        self.set_many({str(key): value})

    def set_many(self, values: dict[str, object]) -> None:
        """Write several settings in one transaction.

        All or nothing: a Settings window that changes the daily count and the
        review capacity together must never leave the pair half applied.
        """
        if not values:
            return
        try:
            with self._db.transaction() as conn:
                for key, value in values.items():
                    conn.execute(
                        """
                        INSERT INTO app_settings (key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE
                           SET value = excluded.value, updated_at = datetime('now')
                        """,
                        (str(key), serialize(value)),
                    )
        except sqlite3.Error as exc:
            raise StorageError("Those settings could not be saved.") from exc

    def reset(self, keys: list[str] | None = None) -> None:
        """Put settings back to their defaults.

        Passing ``None`` resets everything the engine understands except the
        active plan, which is a fact about the user's data rather than a
        preference and would otherwise be silently unlinked.
        """
        targets = keys if keys is not None else [
            key for key in DEFAULT_SETTINGS if key != Setting.ACTIVE_PLAN_ID
        ]
        self.set_many({key: DEFAULT_SETTINGS[key] for key in targets if key in DEFAULT_SETTINGS})

    def unknown_keys(self) -> list[str]:
        """Stored keys the current version does not understand.

        Reported in Developer Mode rather than deleted: a key left by a newer
        version is not rubbish, and downgrading should not destroy it.
        """
        rows = self._db.connection.execute("SELECT key FROM app_settings").fetchall()
        return sorted(row["key"] for row in rows if row["key"] not in DEFAULT_SETTINGS)


class RuntimeRepository:
    """Reads and writes ``runtime_state``: what the last run got as far as."""

    #: The morning message has been sent for this local date.
    LAST_NOTIFIED_ON = "last_notified_on"
    #: The evening reminder has been sent for this local date.
    LAST_REMINDER_ON = "last_reminder_on"
    #: The Sunday the weekly summary was last sent on.
    LAST_WEEKLY_ON = "last_weekly_on"
    #: New words have been offered for this local date.
    LAST_INTAKE_ON = "last_intake_on"
    #: The last moment the application was known to be running.
    LAST_SEEN_AT = "last_seen_at"
    #: The chat that talked to the bot last, so the app knows where to write.
    TELEGRAM_CHAT_ID = "telegram_chat_id"
    #: The highest Telegram update id already consumed.
    TELEGRAM_OFFSET = "telegram_offset"

    def __init__(self, database: Database) -> None:
        self._db = database

    def get(self, key: str) -> str | None:
        row = self._db.connection.execute(
            "SELECT value FROM runtime_state WHERE key = ?", (str(key),)
        ).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: object) -> None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO runtime_state (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE
                       SET value = excluded.value, updated_at = datetime('now')
                    """,
                    (str(key), serialize(value)),
                )
        except sqlite3.Error as exc:
            raise StorageError("The application state could not be saved.") from exc

    def clear(self, key: str) -> None:
        try:
            with self._db.transaction() as conn:
                conn.execute("DELETE FROM runtime_state WHERE key = ?", (str(key),))
        except sqlite3.Error as exc:
            raise StorageError("The application state could not be cleared.") from exc

    # -- day marks ---------------------------------------------------------

    def mark_done(self, key: str, local_date: str) -> bool:
        """Record that a once-a-day action happened, if it has not already.

        Returns ``True`` when this call is the one that claimed the day. Both
        the scheduler and a manual "send now" go through here, so a missed day
        caught up at 09:00 does not also fire when the timer next ticks.
        """
        if self.get(key) == local_date:
            return False
        self.set(key, local_date)
        return True

    def was_done(self, key: str, local_date: str) -> bool:
        return self.get(key) == local_date

    # -- downtime ----------------------------------------------------------

    def touch(self, now: datetime) -> None:
        """Note that the application is alive, for downtime detection."""
        self.set(self.LAST_SEEN_AT, now.isoformat(timespec="seconds"))

    def last_seen(self) -> datetime | None:
        raw = self.get(self.LAST_SEEN_AT)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:  # pragma: no cover - only hand-edited data gets here
            return None

    def missed_days(self, now: datetime) -> int:
        """Whole local days between the last run and now.

        Zero on a first run: a fresh install has not missed anything, and
        reporting otherwise would open the app with an apology.
        """
        seen = self.last_seen()
        if seen is None:
            return 0
        return max((now.date() - seen.date()).days, 0)

    # -- Telegram ----------------------------------------------------------

    def telegram_offset(self) -> int | None:
        raw = self.get(self.TELEGRAM_OFFSET)
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    def set_telegram_offset(self, offset: int) -> None:
        self.set(self.TELEGRAM_OFFSET, int(offset))

    def chat_id(self) -> str | None:
        value = self.get(self.TELEGRAM_CHAT_ID)
        return value or None

    def set_chat_id(self, chat_id: str | int) -> None:
        self.set(self.TELEGRAM_CHAT_ID, str(chat_id))
