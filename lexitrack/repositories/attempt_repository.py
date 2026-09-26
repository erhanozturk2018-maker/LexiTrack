"""Persistence for every question answered, ``learning_attempts``.

Append-only like ``review_logs``: Undo marks an attempt undone and keeps it.
Statistics read attempts that were not undone; the history shows all.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from ..core.clock import from_storage, to_storage
from ..core.errors import StorageError
from ..database.connection import Database
from ..models.attempt import Effort, LearningAttempt, Phase, Role, Task


class AttemptRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, attempt: LearningAttempt) -> int:
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO learning_attempts
                        (word_id, session_id, at, on_day, phase, role, task, context_id,
                         correct, effort, response_ms, review_log_id, route_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(attempt.word_id),
                        attempt.session_id,
                        to_storage(attempt.at),
                        attempt.on_day,
                        attempt.phase.value,
                        attempt.role.value,
                        attempt.task.value,
                        attempt.context_id,
                        int(attempt.correct),
                        attempt.effort.value if attempt.effort else None,
                        attempt.response_ms,
                        attempt.review_log_id,
                        attempt.route_version,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise StorageError("That attempt could not be recorded.") from exc

    def for_word(self, word_id: int, *, include_undone: bool = False) -> list[LearningAttempt]:
        """One word's attempts, oldest first."""
        where = "" if include_undone else " AND undone_at IS NULL"
        rows = self._db.connection.execute(
            f"SELECT * FROM learning_attempts WHERE word_id = ?{where} ORDER BY at, id",
            (int(word_id),),
        ).fetchall()
        return [_to_attempt(row) for row in rows]

    def for_words(self, word_ids: Sequence[int]) -> dict[int, list[LearningAttempt]]:
        """Attempts of many words that count, oldest first, grouped by word."""
        grouped: dict[int, list[LearningAttempt]] = {}
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                f"SELECT * FROM learning_attempts WHERE word_id IN ({marks}) "
                f"AND undone_at IS NULL ORDER BY at, id",
                chunk,
            ):
                attempt = _to_attempt(row)
                grouped.setdefault(attempt.word_id, []).append(attempt)
        return grouped

    def all(self, *, include_undone: bool = False) -> list[LearningAttempt]:
        where = "" if include_undone else " WHERE undone_at IS NULL"
        rows = self._db.connection.execute(
            f"SELECT * FROM learning_attempts{where} ORDER BY at, id"
        ).fetchall()
        return [_to_attempt(row) for row in rows]

    def last_task(self, word_id: int) -> Task | None:
        """The task of the word's latest answer that counts, any phase."""
        row = self._db.connection.execute(
            "SELECT task FROM learning_attempts WHERE word_id = ? AND undone_at IS NULL "
            "ORDER BY at DESC, id DESC LIMIT 1",
            (int(word_id),),
        ).fetchone()
        return _task(row["task"]) if row else None

    def mark_undone_for_log(self, log_id: int, at: datetime) -> int:
        """Undo reaches every attempt that belonged to the answer taken back."""
        with self._db.transaction() as conn:
            return conn.execute(
                "UPDATE learning_attempts SET undone_at = ? "
                "WHERE review_log_id = ? AND undone_at IS NULL",
                (to_storage(at), int(log_id)),
            ).rowcount

    def mark_undone(self, attempt_ids: Sequence[int], at: datetime) -> int:
        ids = [int(attempt_id) for attempt_id in attempt_ids]
        if not ids:
            return 0
        with self._db.transaction() as conn:
            marks = ",".join("?" * len(ids))
            return conn.execute(
                f"UPDATE learning_attempts SET undone_at = ? WHERE id IN ({marks})",
                [to_storage(at), *ids],
            ).rowcount


def _task(value: str) -> Task | None:
    try:
        return Task(value)
    except ValueError:
        return None


def _to_attempt(row: sqlite3.Row) -> LearningAttempt:
    return LearningAttempt(
        id=int(row["id"]),
        word_id=int(row["word_id"]),
        session_id=row["session_id"],
        at=from_storage(row["at"]),
        on_day=row["on_day"],
        phase=Phase(row["phase"]),
        role=Role(row["role"]),
        task=Task(row["task"]),
        correct=bool(row["correct"]),
        context_id=row["context_id"],
        effort=Effort(row["effort"]) if row["effort"] else None,
        response_ms=row["response_ms"],
        review_log_id=row["review_log_id"],
        route_version=row["route_version"],
        undone_at=from_storage(row["undone_at"]),
    )
