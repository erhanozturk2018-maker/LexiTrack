"""Persistence for review sessions and Telegram idempotency keys.

A *session* is one sitting: the desktop Review page opening, or the Telegram
message the user taps through in the evening. It exists for two reasons that
have nothing to do with statistics:

1. **Resumption.** A Telegram session records its ``chat_id``, ``message_id``
   and the card currently being asked, so that after a restart the running
   message can be edited again instead of a new one being sent.
2. **Attribution.** Every row in ``review_logs`` points at the session it came
   from, which is what lets "answered on the phone" be told apart from
   "answered at the desk" later.

``telegram_updates`` is smaller and blunter: a set of keys that have already
been handled. Telegram re-delivers a callback whenever it is not sure the
answer arrived, and the same tap must not rate the same card twice. The keys
are pruned; the permanent record is ``review_logs``.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from ..core.clock import from_storage as _parse
from ..core.clock import to_storage as _stamp
from ..core.errors import StorageError
from ..database.connection import Database
from ..models.srs import Channel


@dataclass(frozen=True, slots=True)
class ReviewSession:
    """One sitting of reviews."""

    id: str
    channel: Channel
    started_at: datetime | None
    started_on: str
    plan_id: int | None = None
    finished_at: datetime | None = None
    chat_id: str | None = None
    message_id: str | None = None
    current_word_id: int | None = None
    planned_count: int = 0
    done_count: int = 0
    #: Versioned JSON describing where the flow stands, or None.
    flow_state: str | None = None

    @property
    def is_open(self) -> bool:
        return self.finished_at is None

    @property
    def remaining(self) -> int:
        return max(self.planned_count - self.done_count, 0)


class SessionRepository:
    """Reads and writes ``review_sessions`` and ``telegram_updates``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- sessions ----------------------------------------------------------

    def start(
        self,
        *,
        channel: Channel,
        now: datetime,
        local_date: str,
        plan_id: int | None = None,
        planned_count: int = 0,
        chat_id: str | None = None,
    ) -> ReviewSession:
        session_id = uuid.uuid4().hex
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO review_sessions
                        (id, channel, plan_id, started_at, started_on, planned_count, chat_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        Channel(channel).value,
                        plan_id,
                        _stamp(now),
                        local_date,
                        max(int(planned_count), 0),
                        chat_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError("The review session could not be started.") from exc
        return self.require(session_id)

    def get(self, session_id: str) -> ReviewSession | None:
        row = self._db.connection.execute(
            "SELECT * FROM review_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _to_session(row) if row else None

    def require(self, session_id: str) -> ReviewSession:
        session = self.get(session_id)
        if session is None:
            raise StorageError("That review session no longer exists.")
        return session

    def open_session(self, channel: Channel) -> ReviewSession | None:
        """The newest unfinished session of a channel, if any.

        Used on startup: an open Telegram session means a message out there is
        still waiting for taps, and the app should keep serving it rather than
        starting a second one.
        """
        row = self._db.connection.execute(
            """
            SELECT * FROM review_sessions
            WHERE channel = ? AND finished_at IS NULL
            ORDER BY started_at DESC, rowid DESC LIMIT 1
            """,
            (Channel(channel).value,),
        ).fetchone()
        return _to_session(row) if row else None

    def update(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
        current_word_id: int | None = None,
        clear_current: bool = False,
        planned_count: int | None = None,
        done_increment: int = 0,
    ) -> ReviewSession:
        """Move a session forward. Only the fields given are touched."""
        sets: list[str] = []
        params: list[object] = []
        if message_id is not None:
            sets.append("message_id = ?")
            params.append(message_id)
        if clear_current:
            sets.append("current_word_id = NULL")
        elif current_word_id is not None:
            sets.append("current_word_id = ?")
            params.append(current_word_id)
        if planned_count is not None:
            sets.append("planned_count = ?")
            params.append(max(int(planned_count), 0))
        if done_increment:
            sets.append("done_count = done_count + ?")
            params.append(int(done_increment))
        if not sets:
            return self.require(session_id)
        params.append(session_id)
        assignments = ", ".join(sets)
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    f"UPDATE review_sessions SET {assignments} WHERE id = ?", params
                )
        except sqlite3.Error as exc:
            raise StorageError("The review session could not be saved.") from exc
        return self.require(session_id)

    def finish(self, session_id: str, now: datetime) -> ReviewSession:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "UPDATE review_sessions SET finished_at = ?, current_word_id = NULL "
                    "WHERE id = ? AND finished_at IS NULL",
                    (_stamp(now), session_id),
                )
        except sqlite3.Error as exc:
            raise StorageError("The review session could not be closed.") from exc
        return self.require(session_id)

    def finish_stale(self, before_local_date: str, now: datetime) -> int:
        """Close sessions left open on an earlier day.

        A session belongs to its day. One still open from yesterday is not a
        session to resume — the queue it was built from no longer exists — so
        startup closes it instead of carrying it over.
        """
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "UPDATE review_sessions SET finished_at = ?, current_word_id = NULL "
                    "WHERE finished_at IS NULL AND started_on < ?",
                    (_stamp(now), before_local_date),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Old review sessions could not be closed.") from exc

    def sessions_on(self, local_date: str) -> list[ReviewSession]:
        rows = self._db.connection.execute(
            "SELECT * FROM review_sessions WHERE started_on = ? ORDER BY started_at, rowid",
            (local_date,),
        ).fetchall()
        return [_to_session(row) for row in rows]

    # -- Telegram idempotency ---------------------------------------------

    def set_flow_state(self, session_id: str, state: str | None) -> None:
        """Store where the session's flow stands, as JSON text, or clear it."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE review_sessions SET flow_state = ? WHERE id = ?", (state, session_id)
            )

    def release_update(self, update_key: str) -> None:
        """Forget a claimed key, so the same answer can be given again.

        Used by Undo: the word goes back on screen, and without this its next
        answer would be taken for a re-delivery of the one just undone.
        """
        key = (update_key or "").strip()
        if not key:
            return
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM telegram_updates WHERE update_key = ?", (key,))

    def claim_update(self, update_key: str) -> bool:
        """Record a Telegram update key, returning whether it is new.

        The insert *is* the lock: two threads racing on the same callback both
        try to insert, and exactly one gets a row. The caller acts only when
        this returns ``True``.
        """
        key = (update_key or "").strip()
        if not key:
            return False
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO telegram_updates (update_key) VALUES (?)", (key,)
                )
                return bool(cursor.rowcount)
        except sqlite3.Error as exc:
            raise StorageError("That Telegram action could not be recorded.") from exc

    def was_handled(self, update_key: str) -> bool:
        row = self._db.connection.execute(
            "SELECT 1 FROM telegram_updates WHERE update_key = ?",
            ((update_key or "").strip(),),
        ).fetchone()
        return row is not None

    def prune_updates(self, keep_days: int = 14) -> int:
        """Drop idempotency keys older than ``keep_days``."""
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "DELETE FROM telegram_updates "
                    "WHERE handled_at < datetime('now', '-' || ? || ' days')",
                    (max(int(keep_days), 0),),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Old Telegram keys could not be removed.") from exc


# -- helpers ---------------------------------------------------------------


def _to_session(row: sqlite3.Row) -> ReviewSession:
    return ReviewSession(
        id=row["id"],
        channel=Channel(row["channel"]),
        started_at=_parse(row["started_at"]),
        started_on=row["started_on"],
        plan_id=row["plan_id"],
        finished_at=_parse(row["finished_at"]),
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        current_word_id=row["current_word_id"],
        planned_count=int(row["planned_count"]),
        done_count=int(row["done_count"]),
        flow_state=row["flow_state"],
    )
