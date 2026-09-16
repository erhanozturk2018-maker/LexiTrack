"""Persistence for the user's review state.

This repository never touches source metadata. Keeping the two apart is what
allows a document to be re-imported without disturbing review progress.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from ..core.errors import StorageError
from ..database.connection import Database
from ..models.user_word_state import Progress, ReviewStatus, UserWordState


class StateRepository:
    """Reads and writes rows in ``user_word_state``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def set_status(self, word_id: int, status: ReviewStatus) -> UserWordState:
        """Record ``status`` for ``word_id`` and return the stored state."""
        reviewed_at = None if status is ReviewStatus.NOT_REVIEWED else datetime.now()
        stamp = reviewed_at.isoformat(timespec="seconds") if reviewed_at else None
        try:
            with self._db.transaction() as conn:
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

    def set_status_many(self, word_ids: Sequence[int], status: ReviewStatus) -> int:
        """Set ``status`` on many words in one transaction. Returns the count changed.

        Words that already have ``status`` are left alone, including their
        ``reviewed_at`` — re-marking a known word as known is not a new review.
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
                    cursor = conn.execute(
                        f"UPDATE user_word_state SET status = ?, reviewed_at = ? "
                        f"WHERE word_id IN ({placeholders}) AND status != ?",
                        [status.value, stamp, *chunk, status.value],
                    )
                    changed += cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Those changes could not be saved.") from exc
        return changed

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

    def count_with_status(self, status: ReviewStatus) -> int:
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM user_word_state WHERE status = ?", (status.value,)
        ).fetchone()
        return int(row["n"])

    def reset_all(self) -> int:
        """Mark every word as not reviewed. Returns the number of rows affected."""
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    "UPDATE user_word_state SET status = ?, reviewed_at = NULL",
                    (ReviewStatus.NOT_REVIEWED.value,),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Review progress could not be reset.") from exc


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - defensive
        return None
