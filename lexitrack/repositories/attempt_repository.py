"""Persistence for the skill record, ``learning_attempts``.

Append-only like ``review_logs``: Undo marks an attempt undone and keeps it.
SkillTracker reads attempts that were not undone; the history shows all.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from ..core.clock import from_storage, to_storage
from ..core.errors import StorageError
from ..database.connection import Database
from ..models.attempt import Depth, Effort, LearningAttempt, Phase, Role, Task


class AttemptRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def add(self, attempt: LearningAttempt) -> int:
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO learning_attempts
                        (word_id, session_id, at, on_day, phase, role, task, level,
                         context_id, novel_context, success, effort, response_ms,
                         review_log_id, route_version, depth)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(attempt.word_id),
                        attempt.session_id,
                        to_storage(attempt.at),
                        attempt.on_day,
                        attempt.phase.value,
                        attempt.role.value,
                        attempt.task.value,
                        int(attempt.level),
                        attempt.context_id,
                        int(attempt.novel_context),
                        int(attempt.success),
                        attempt.effort.value if attempt.effort else None,
                        attempt.response_ms,
                        attempt.review_log_id,
                        attempt.route_version,
                        attempt.depth.value if attempt.depth else None,
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

    def context_uses(self, word_id: int) -> dict[int, datetime]:
        """When each of the word's contexts was last shown in an attempt."""
        rows = self._db.connection.execute(
            "SELECT context_id, MAX(at) AS last FROM learning_attempts "
            "WHERE word_id = ? AND context_id IS NOT NULL GROUP BY context_id",
            (int(word_id),),
        ).fetchall()
        return {int(row["context_id"]): from_storage(row["last"]) for row in rows}

    def legacy_recognition(
        self, word_ids: Sequence[int]
    ) -> dict[int, tuple[int, int, str | None]]:
        """Answers with no attempt behind them: ``(successes, failures, last day)``.

        Those are the answers from before schema 5. They only ever asked
        word → meaning, so they are evidence of recognition and nothing more;
        they are read from ``review_logs`` rather than copied into attempts,
        so nothing is recorded that did not happen. Undone answers are left
        out, and Again is a failure.
        """
        found: dict[int, tuple[int, int, str | None]] = {}
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                f"""
                SELECT l.word_id,
                       SUM(CASE WHEN l.rating > 1 THEN 1 ELSE 0 END) AS good,
                       SUM(CASE WHEN l.rating = 1 THEN 1 ELSE 0 END) AS bad,
                       MAX(l.reviewed_on) AS last_on
                FROM review_logs l
                WHERE l.word_id IN ({marks})
                  AND l.undone_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_attempts a WHERE a.review_log_id = l.id
                  )
                GROUP BY l.word_id
                """,
                chunk,
            ):
                found[int(row["word_id"])] = (int(row["good"]), int(row["bad"]), row["last_on"])
        return found

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
        success=bool(row["success"]),
        context_id=row["context_id"],
        novel_context=bool(row["novel_context"]),
        effort=Effort(row["effort"]) if row["effort"] else None,
        response_ms=row["response_ms"],
        review_log_id=row["review_log_id"],
        route_version=row["route_version"],
        depth=Depth(row["depth"]) if row["depth"] else None,
        undone_at=from_storage(row["undone_at"]),
    )
