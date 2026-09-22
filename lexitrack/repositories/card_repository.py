"""Persistence for the schedule: SRS cards and the review history.

Two tables, one job each:

* ``srs_cards`` is the *current* state of a word's schedule. One row per word,
  never per plan, so a word selected by two lists cannot be reviewed twice.
* ``review_logs`` is *what happened*, append-only. It is the source of truth
  for statistics and for any future FSRS parameter optimisation; the CSV files
  under ``data/logs/reviews`` are generated from it.

A card is created by :meth:`CardRepository.introduce`, which writes no log
entry and no rating: the user studied the word outside LexiTrack and said so.
The first real rating arrives on a later day, and only then does the card get
FSRS state. That is what keeps "today's new words never appear in today's
review queue" true in the data rather than in a filter.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime

from ..core.clock import from_storage as _parse
from ..core.clock import to_storage as _stamp
from ..core.errors import StorageError
from ..database.connection import Database
from ..models.srs import CardState, Channel, Rating, ReviewLogEntry, SrsCard

_CHUNK = 500

_SELECT_CARD = """
SELECT word_id, origin_plan_id, state, introduced_at, introduced_on, due_at,
       first_review_at, last_review_at, review_count, lapse_count,
       consecutive_lapses, needs_relearning, stability, difficulty,
       fsrs_state, scheduler_version
FROM srs_cards
"""


class CardRepository:
    """Reads and writes ``srs_cards`` and ``review_logs``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- introduction ------------------------------------------------------

    def introduce(
        self,
        word_ids: Sequence[int],
        *,
        plan_id: int | None,
        now: datetime,
        local_date: str,
        due_at: datetime,
    ) -> list[int]:
        """Create cards for words that have never been introduced.

        Returns the ids actually introduced. Words that already have a card
        are skipped rather than reset: introduction happens once in a word's
        life, and a repeated confirmation (a second tap, a re-sent Telegram
        message) must not move anything.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return []
        introduced: list[int] = []
        try:
            with self._db.transaction() as conn:
                for word_id in ids:
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO srs_cards
                            (word_id, origin_plan_id, state, introduced_at,
                             introduced_on, due_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            word_id,
                            plan_id,
                            CardState.INTRODUCED.value,
                            _stamp(now),
                            local_date,
                            _stamp(due_at),
                        ),
                    )
                    if cursor.rowcount:
                        introduced.append(word_id)
        except sqlite3.Error as exc:
            raise StorageError("Those new words could not be saved.") from exc
        return introduced

    # -- reading -----------------------------------------------------------

    def get(self, word_id: int) -> SrsCard | None:
        row = self._db.connection.execute(
            _SELECT_CARD + " WHERE word_id = ?", (word_id,)
        ).fetchone()
        return _to_card(row) if row else None

    def get_many(self, word_ids: Iterable[int]) -> dict[int, SrsCard]:
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        found: dict[int, SrsCard] = {}
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                _SELECT_CARD + f" WHERE word_id IN ({placeholders})", chunk
            ):
                card = _to_card(row)
                found[card.word_id] = card
        return found

    def introduced_on(self, local_date: str, word_ids: Sequence[int] | None = None) -> list[int]:
        """Word ids introduced on a given local date, optionally within a scope."""
        if word_ids is None:
            rows = self._db.connection.execute(
                "SELECT word_id FROM srs_cards WHERE introduced_on = ?", (local_date,)
            ).fetchall()
            return [int(row["word_id"]) for row in rows]
        found: list[int] = []
        for chunk in _chunks([int(w) for w in word_ids]):
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.connection.execute(
                f"SELECT word_id FROM srs_cards WHERE introduced_on = ? "
                f"AND word_id IN ({placeholders})",
                [local_date, *chunk],
            ).fetchall()
            found.extend(int(row["word_id"]) for row in rows)
        return found

    def due_cards(
        self,
        word_ids: Sequence[int],
        *,
        now: datetime,
        before_local_date: str,
        include_archived: bool = False,
        limit: int | None = None,
    ) -> list[SrsCard]:
        """Cards of ``word_ids`` that are due and were introduced before today.

        ``before_local_date`` is today's local date: a card introduced today is
        excluded whatever its due date says. That is the hard invariant, and it
        is enforced here rather than trusted to the scheduler.

        Ordering is the review priority: struggling cards first, then
        relearning, then the most overdue.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return []
        states = (
            "('introduced', 'learning', 'review', 'relearning', 'archived')"
            if include_archived
            else "('introduced', 'learning', 'review', 'relearning')"
        )
        cards: list[SrsCard] = []
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.connection.execute(
                _SELECT_CARD
                + f"""
                WHERE word_id IN ({placeholders})
                  AND introduced_on < ?
                  AND due_at <= ?
                  AND state IN {states}
                ORDER BY needs_relearning DESC,
                         state = 'relearning' DESC,
                         due_at ASC,
                         word_id ASC
                """,
                [*chunk, before_local_date, _stamp(now)],
            ).fetchall()
            cards.extend(_to_card(row) for row in rows)
        cards.sort(
            key=lambda card: (
                not card.needs_relearning,
                card.state is not CardState.RELEARNING,
                card.due_at or datetime.max,
                card.word_id,
            )
        )
        return cards[:limit] if limit is not None else cards

    def struggling(self, word_ids: Sequence[int], limit: int | None = None) -> list[SrsCard]:
        """Cards flagged as needing relearning, worst first."""
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return []
        cards: list[SrsCard] = []
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.connection.execute(
                _SELECT_CARD
                + f" WHERE word_id IN ({placeholders}) AND needs_relearning = 1",
                chunk,
            ).fetchall()
            cards.extend(_to_card(row) for row in rows)
        cards.sort(key=lambda card: (-card.lapse_count, -card.consecutive_lapses, card.word_id))
        return cards[:limit] if limit is not None else cards

    def state_counts(self, word_ids: Sequence[int] | None = None) -> dict[str, int]:
        """How many cards are in each state, optionally within a scope."""
        counts = {state.value: 0 for state in CardState}
        if word_ids is None:
            rows = self._db.connection.execute(
                "SELECT state, COUNT(*) AS n FROM srs_cards GROUP BY state"
            ).fetchall()
            for row in rows:
                counts[row["state"]] = int(row["n"])
            return counts
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.connection.execute(
                f"SELECT state, COUNT(*) AS n FROM srs_cards "
                f"WHERE word_id IN ({placeholders}) GROUP BY state",
                chunk,
            ).fetchall()
            for row in rows:
                counts[row["state"]] += int(row["n"])
        return counts

    def due_timestamps(
        self,
        word_ids: Sequence[int],
        *,
        until: datetime | None = None,
        include_archived: bool = False,
    ) -> list[datetime]:
        """The due instant of every scheduled card in scope, soonest first.

        Returns instants rather than a day-by-day count on purpose. Which
        local day an instant belongs to depends on the time zone *and* on the
        configured day start, and that arithmetic lives in ``DayClock``; doing
        it in SQL here would silently group by UTC dates and put a card due at
        01:00 Istanbul on the previous day. The 7-day forecast is therefore
        built by the service, from these values.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return []
        states = (
            "('introduced', 'learning', 'review', 'relearning', 'archived')"
            if include_archived
            else "('introduced', 'learning', 'review', 'relearning')"
        )
        horizon = _stamp(until)
        moments: list[datetime] = []
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            sql = (
                f"SELECT due_at FROM srs_cards WHERE word_id IN ({placeholders}) "
                f"AND state IN {states}"
            )
            params: list[object] = [*chunk]
            if horizon is not None:
                sql += " AND due_at < ?"
                params.append(horizon)
            for row in self._db.connection.execute(sql, params):
                moment = _parse(row["due_at"])
                if moment is not None:
                    moments.append(moment)
        moments.sort()
        return moments

    # -- writing -----------------------------------------------------------

    def save(self, card: SrsCard) -> None:
        """Write a card's new state. The card must already exist."""
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE srs_cards
                       SET state = ?, due_at = ?, first_review_at = ?, last_review_at = ?,
                           review_count = ?, lapse_count = ?, consecutive_lapses = ?,
                           needs_relearning = ?, stability = ?, difficulty = ?,
                           fsrs_state = ?, scheduler_version = ?,
                           origin_plan_id = COALESCE(?, origin_plan_id),
                           updated_at = datetime('now')
                     WHERE word_id = ?
                    """,
                    (
                        card.state.value,
                        _stamp(card.due_at),
                        _stamp(card.first_review_at),
                        _stamp(card.last_review_at),
                        card.review_count,
                        card.lapse_count,
                        card.consecutive_lapses,
                        int(card.needs_relearning),
                        card.stability,
                        card.difficulty,
                        card.fsrs_state,
                        card.scheduler_version,
                        card.origin_plan_id,
                        card.word_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError("That answer could not be saved.") from exc

    def set_state(self, word_ids: Sequence[int], state: CardState) -> int:
        """Move cards to a state without touching their schedule.

        Used when the user declares a word Known by hand: the card is archived,
        not deleted, so resetting the status later resumes the same schedule.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return 0
        changed = 0
        try:
            with self._db.transaction() as conn:
                for chunk in _chunks(ids):
                    placeholders = ",".join("?" * len(chunk))
                    cursor = conn.execute(
                        f"UPDATE srs_cards SET state = ?, updated_at = datetime('now') "
                        f"WHERE word_id IN ({placeholders}) AND state != ?",
                        [state.value, *chunk, state.value],
                    )
                    changed += cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Those cards could not be updated.") from exc
        return changed

    def delete(self, word_ids: Sequence[int]) -> int:
        """Remove cards outright. Only used when a word itself is gone."""
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return 0
        removed = 0
        try:
            with self._db.transaction() as conn:
                for chunk in _chunks(ids):
                    placeholders = ",".join("?" * len(chunk))
                    cursor = conn.execute(
                        f"DELETE FROM srs_cards WHERE word_id IN ({placeholders})", chunk
                    )
                    removed += cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError("Those cards could not be removed.") from exc
        return removed

    def clear_all(self) -> int:
        """Remove every card, session and review. Part of "reset all progress".

        Returns the number of cards removed. The caller wraps this in the same
        transaction as the status reset, so the two can never disagree.
        """
        try:
            with self._db.transaction() as conn:
                conn.execute("DELETE FROM review_logs")
                conn.execute("DELETE FROM review_sessions")
                return conn.execute("DELETE FROM srs_cards").rowcount
        except sqlite3.Error as exc:
            raise StorageError("The review schedule could not be cleared.") from exc

    # -- history -----------------------------------------------------------

    def log(self, entry: ReviewLogEntry) -> int:
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO review_logs
                        (word_id, session_id, channel, reviewed_at, reviewed_on, rating,
                         state_before, state_after, due_before, due_after, elapsed_days,
                         scheduled_days, stability_after, difficulty_after,
                         scheduler_version, params_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.word_id,
                        entry.session_id,
                        Channel(entry.channel).value,
                        _stamp(entry.reviewed_at),
                        entry.reviewed_on,
                        int(entry.rating),
                        entry.state_before.value if entry.state_before else None,
                        entry.state_after.value if entry.state_after else None,
                        _stamp(entry.due_before),
                        _stamp(entry.due_after),
                        entry.elapsed_days,
                        entry.scheduled_days,
                        entry.stability_after,
                        entry.difficulty_after,
                        entry.scheduler_version,
                        entry.params_hash,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise StorageError("That review could not be recorded.") from exc

    def logs_on(self, local_date: str) -> list[ReviewLogEntry]:
        rows = self._db.connection.execute(
            """
            SELECT * FROM review_logs WHERE reviewed_on = ? AND undone_at IS NULL
            ORDER BY reviewed_at, id
            """,
            (local_date,),
        ).fetchall()
        return [_to_log(row) for row in rows]

    def count_logs_on(self, local_date: str) -> int:
        row = self._db.connection.execute(
            "SELECT COUNT(*) AS n FROM review_logs "
            "WHERE reviewed_on = ? AND undone_at IS NULL",
            (local_date,),
        ).fetchone()
        return int(row["n"])

    def logs_for_word(self, word_id: int, limit: int = 50) -> list[ReviewLogEntry]:
        """A word's answers, newest first, including any taken back."""
        rows = self._db.connection.execute(
            "SELECT * FROM review_logs WHERE word_id = ? ORDER BY reviewed_at DESC, id DESC "
            "LIMIT ?",
            (word_id, limit),
        ).fetchall()
        return [_to_log(row) for row in rows]

    def all_logs(self, *, include_undone: bool = True) -> list[ReviewLogEntry]:
        """Every answer, oldest first. The Progress page shows all of them."""
        where = "" if include_undone else " WHERE undone_at IS NULL"
        rows = self._db.connection.execute(
            f"SELECT * FROM review_logs{where} ORDER BY reviewed_at, id"
        ).fetchall()
        return [_to_log(row) for row in rows]

    def mark_undone(self, log_id: int, at: datetime) -> None:
        """Mark an answer as taken back. The row itself is kept."""
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "UPDATE review_logs SET undone_at = ? WHERE id = ?",
                    (_stamp(at), int(log_id)),
                )
        except sqlite3.Error as exc:
            raise StorageError("That answer could not be taken back.") from exc

    def rating_counts(self, since_local_date: str | None = None) -> dict[int, int]:
        """How many Again / Hard / Good / Easy answers, for the Again rate."""
        if since_local_date is None:
            rows = self._db.connection.execute(
                "SELECT rating, COUNT(*) AS n FROM review_logs WHERE undone_at IS NULL "
                "GROUP BY rating"
            ).fetchall()
        else:
            rows = self._db.connection.execute(
                "SELECT rating, COUNT(*) AS n FROM review_logs "
                "WHERE reviewed_on >= ? AND undone_at IS NULL "
                "GROUP BY rating",
                (since_local_date,),
            ).fetchall()
        counts = {int(rating): 0 for rating in Rating}
        for row in rows:
            counts[int(row["rating"])] = int(row["n"])
        return counts

    def reviews_per_day(self, days: int = 30) -> dict[str, int]:
        rows = self._db.connection.execute(
            "SELECT reviewed_on AS day, COUNT(*) AS n FROM review_logs "
            "WHERE undone_at IS NULL GROUP BY reviewed_on ORDER BY day DESC LIMIT ?",
            (days,),
        ).fetchall()
        return {row["day"]: int(row["n"]) for row in reversed(rows)}

    def introduced_per_day(self, days: int = 30) -> dict[str, int]:
        rows = self._db.connection.execute(
            "SELECT introduced_on AS day, COUNT(*) AS n FROM srs_cards "
            "GROUP BY introduced_on ORDER BY day DESC LIMIT ?",
            (days,),
        ).fetchall()
        return {row["day"]: int(row["n"]) for row in reversed(rows)}


# -- helpers ---------------------------------------------------------------


def _chunks(values: Sequence[int], size: int = _CHUNK):
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _to_card(row: sqlite3.Row) -> SrsCard:
    return SrsCard(
        word_id=int(row["word_id"]),
        state=CardState(row["state"]),
        introduced_at=_parse(row["introduced_at"]),
        introduced_on=row["introduced_on"],
        due_at=_parse(row["due_at"]),
        first_review_at=_parse(row["first_review_at"]),
        last_review_at=_parse(row["last_review_at"]),
        review_count=int(row["review_count"]),
        lapse_count=int(row["lapse_count"]),
        consecutive_lapses=int(row["consecutive_lapses"]),
        needs_relearning=bool(row["needs_relearning"]),
        stability=row["stability"],
        difficulty=row["difficulty"],
        fsrs_state=row["fsrs_state"],
        scheduler_version=row["scheduler_version"],
        origin_plan_id=row["origin_plan_id"],
    )


def _to_log(row: sqlite3.Row) -> ReviewLogEntry:
    return ReviewLogEntry(
        id=int(row["id"]),
        word_id=int(row["word_id"]),
        session_id=row["session_id"],
        channel=Channel(row["channel"]),
        reviewed_at=_parse(row["reviewed_at"]),
        reviewed_on=row["reviewed_on"],
        rating=Rating(int(row["rating"])),
        state_before=CardState(row["state_before"]) if row["state_before"] else None,
        state_after=CardState(row["state_after"]) if row["state_after"] else None,
        due_before=_parse(row["due_before"]),
        due_after=_parse(row["due_after"]),
        elapsed_days=row["elapsed_days"],
        scheduled_days=row["scheduled_days"],
        stability_after=row["stability_after"],
        difficulty_after=row["difficulty_after"],
        scheduler_version=row["scheduler_version"],
        params_hash=row["params_hash"],
        undone_at=_parse(row["undone_at"]),
    )
