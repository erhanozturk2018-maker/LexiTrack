"""Persistence for the user's review state.

This repository never touches source metadata. Keeping the two apart is what
allows a document to be re-imported without disturbing review progress.
"""

from __future__ import annotations

import sqlite3
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

    def progress(self) -> Progress:
        """Return the counters shown on the review screen."""
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
