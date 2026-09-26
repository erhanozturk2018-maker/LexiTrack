"""Persistence for the user's review state, and the history of its changes.

This repository never touches source metadata. Keeping the two apart is what
allows a document to be re-imported without disturbing review progress.

Every change of status also appends a row to ``word_status_events`` in the
same transaction, with the cause. ``user_word_state`` answers "what is this
word now?"; the events answer "since when, and why?" - the question the
Progress page and a word's history ask. Writing both here, rather than in each
caller, is what makes it impossible to change a status without a record.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from ..core.clock import from_storage, to_storage
from ..core.errors import StorageError
from ..database.connection import Database
from ..models.user_word_state import (
    Progress,
    ReviewStatus,
    StatusCause,
    StatusEvent,
    UserWordState,
)


class StateRepository:
    """Reads and writes rows in ``user_word_state``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def set_status(
        self,
        word_id: int,
        status: ReviewStatus,
        *,
        cause: StatusCause = StatusCause.MANUAL,
        plan_id: int | None = None,
        at: datetime | None = None,
    ) -> UserWordState:
        """Record ``status`` for ``word_id`` and return the stored state.

        An event is written only when the status really changes, so answering
        a Known word Known again leaves no noise in its history.
        """
        reviewed_at = None if status is ReviewStatus.NOT_REVIEWED else datetime.now()
        stamp = reviewed_at.isoformat(timespec="seconds") if reviewed_at else None
        try:
            with self._db.transaction() as conn:
                row = conn.execute(
                    "SELECT status FROM user_word_state WHERE word_id = ?", (word_id,)
                ).fetchone()
                before = ReviewStatus(row["status"]) if row else ReviewStatus.NOT_REVIEWED
                conn.execute(
                    """
                    INSERT INTO user_word_state (word_id, status, reviewed_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(word_id) DO UPDATE SET
                        status = excluded.status,
                        reviewed_at = excluded.reviewed_at
                    """,
                    (word_id, status.value, stamp),
                )
                if before is not status:
                    self._append_events(conn, [(word_id, before)], status, cause, plan_id, at)
        except sqlite3.Error as exc:
            raise StorageError("Your answer could not be saved.") from exc
        return UserWordState(word_id=word_id, status=status, reviewed_at=reviewed_at)

    def get(self, word_id: int) -> UserWordState | None:
        row = self._db.connection.execute(
            "SELECT word_id, status, reviewed_at FROM user_word_state WHERE word_id = ?",
            (word_id,),
        ).fetchone()
        if row is None:
            return None
        return UserWordState(
            word_id=row["word_id"],
            status=ReviewStatus(row["status"]),
            reviewed_at=_parse(row["reviewed_at"]),
        )

    def set_status_many(
        self,
        word_ids: Sequence[int],
        status: ReviewStatus,
        *,
        cause: StatusCause = StatusCause.MANUAL,
        plan_id: int | None = None,
        at: datetime | None = None,
    ) -> int:
        """Set ``status`` on many words in one transaction. Returns the count changed.

        Words that already have ``status`` are left alone, including their
        ``reviewed_at`` - re-marking a known word as known is not a new review,
        and it writes no event.
        """
        ids = list(dict.fromkeys(word_ids))
        if not ids:
            return 0
        stamp = (
            None
            if status is ReviewStatus.NOT_REVIEWED
            else datetime.now().isoformat(timespec="seconds")
        )
        changed = 0
        try:
            with self._db.transaction() as conn:
                for start in range(0, len(ids), 500):
                    chunk = ids[start : start + 500]
                    placeholders = ",".join("?" * len(chunk))
                    # Words imported before a state row existed still count.
                    conn.execute(
                        f"INSERT OR IGNORE INTO user_word_state (word_id, status) "
                        f"SELECT id, 'not_reviewed' FROM words WHERE id IN ({placeholders})",
                        chunk,
                    )
                    moving = [
                        (int(row["word_id"]), ReviewStatus(row["status"]))
                        for row in conn.execute(
                            f"SELECT word_id, status FROM user_word_state "
                            f"WHERE word_id IN ({placeholders}) AND status != ?",
                            [*chunk, status.value],
                        )
                    ]
                    cursor = conn.execute(
                        f"UPDATE user_word_state SET status = ?, reviewed_at = ? "
                        f"WHERE word_id IN ({placeholders}) AND status != ?",
                        [status.value, stamp, *chunk, status.value],
                    )
                    changed += cursor.rowcount
                    self._append_events(conn, moving, status, cause, plan_id, at)
        except sqlite3.Error as exc:
            raise StorageError("Those changes could not be saved.") from exc
        return changed

    def states(self, word_ids: Sequence[int]) -> dict[int, tuple[ReviewStatus, str | None]]:
        """Each word's status and stored ``reviewed_at`` as they are now: what
        :meth:`restore` puts back. A word without a state row is Not reviewed."""
        ids = list(dict.fromkeys(int(word_id) for word_id in word_ids))
        found: dict[int, tuple[ReviewStatus, str | None]] = {
            word_id: (ReviewStatus.NOT_REVIEWED, None) for word_id in ids
        }
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                f"SELECT word_id, status, reviewed_at FROM user_word_state "
                f"WHERE word_id IN ({placeholders})",
                chunk,
            ):
                found[int(row["word_id"])] = (ReviewStatus(row["status"]), row["reviewed_at"])
        return found

    def restore(
        self,
        states: dict[int, tuple[ReviewStatus, str | None]],
        *,
        at: datetime | None = None,
    ) -> int:
        """Put statuses back as :meth:`states` read them: an Undo.

        Each word that really changes back gets an event with the cause
        ``undo``, so its history shows the change and that it was taken back.
        Returns how many words changed.
        """
        if not states:
            return 0
        changed = 0
        try:
            with self._db.transaction() as conn:
                now = self.states(list(states))
                for word_id, (status, reviewed_at) in states.items():
                    current = now[word_id][0]
                    conn.execute(
                        """
                        INSERT INTO user_word_state (word_id, status, reviewed_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(word_id) DO UPDATE SET
                            status = excluded.status,
                            reviewed_at = excluded.reviewed_at
                        """,
                        (word_id, status.value, reviewed_at),
                    )
                    if current is not status:
                        self._append_events(
                            conn, [(word_id, current)], status, StatusCause.UNDO, None, at
                        )
                        changed += 1
        except sqlite3.Error as exc:
            raise StorageError("Those changes could not be taken back.") from exc
        return changed

    # -- history -----------------------------------------------------------

    @staticmethod
    def _append_events(
        conn: sqlite3.Connection,
        moving: Sequence[tuple[int, ReviewStatus]],
        status: ReviewStatus,
        cause: StatusCause,
        plan_id: int | None,
        at: datetime | None,
    ) -> None:
        if not moving:
            return
        when = to_storage(at or datetime.now(UTC))
        conn.executemany(
            "INSERT INTO word_status_events "
            "(word_id, at, from_status, to_status, cause, plan_id) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (word_id, when, before.value, status.value, cause.value, plan_id)
                for word_id, before in moving
            ],
        )

    def events_for_word(self, word_id: int) -> list[StatusEvent]:
        """Every status change of one word, oldest first."""
        rows = self._db.connection.execute(
            "SELECT * FROM word_status_events WHERE word_id = ? ORDER BY at, id",
            (word_id,),
        ).fetchall()
        return [_to_event(row) for row in rows]

    def all_events(self) -> list[StatusEvent]:
        """Every status change, oldest first. For the Progress page."""
        rows = self._db.connection.execute(
            "SELECT * FROM word_status_events ORDER BY at, id"
        ).fetchall()
        return [_to_event(row) for row in rows]

    def progress(self, list_id: int | None = None) -> Progress:
        """Return review counters for one list, or for the whole vocabulary."""
        if list_id is None:
            row = self._db.connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(st.status = 'known'), 0)   AS known,
                    COALESCE(SUM(st.status = 'unknown'), 0) AS unknown
                FROM words w
                LEFT JOIN user_word_state st ON st.word_id = w.id
                """
            ).fetchone()
        else:
            row = self._db.connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(st.status = 'known'), 0)   AS known,
                    COALESCE(SUM(st.status = 'unknown'), 0) AS unknown
                FROM list_words lw
                LEFT JOIN user_word_state st ON st.word_id = lw.word_id
                WHERE lw.list_id = ?
                """,
                (list_id,),
            ).fetchone()
        return Progress(
            total=int(row["total"]),
            known=int(row["known"]),
            unknown=int(row["unknown"]),
        )

    def known_without_card(self) -> int:
        """Words Known that were never studied here: known before the plan."""
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM user_word_state "
            "WHERE status = 'known' AND word_id NOT IN (SELECT word_id FROM srs_cards)"
        ).fetchone()
        return int(row["n"])

    def count_with_status(self, status: ReviewStatus) -> int:
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM user_word_state WHERE status = ?", (status.value,)
        ).fetchone()
        return int(row["n"])

    def reset_all(self) -> int:
        """Mark every word as not reviewed and forget how each got where it was.

        Returns the number of rows affected. The status history goes with the
        statuses: starting over means the Progress page starts over too.
        """
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "UPDATE user_word_state SET status = ?, reviewed_at = NULL",
                    (ReviewStatus.NOT_REVIEWED.value,),
                )
                conn.execute("DELETE FROM word_status_events")
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Review progress could not be reset.") from exc


def _to_event(row: sqlite3.Row) -> StatusEvent:
    return StatusEvent(
        word_id=int(row["word_id"]),
        at=from_storage(row["at"]) or datetime.now(UTC),
        from_status=ReviewStatus(row["from_status"]) if row["from_status"] else None,
        to_status=ReviewStatus(row["to_status"]),
        cause=StatusCause(row["cause"]),
        plan_id=row["plan_id"],
        reconstructed=bool(row["reconstructed"]),
    )


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - defensive
        return None
