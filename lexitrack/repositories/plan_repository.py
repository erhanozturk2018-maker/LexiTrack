"""Persistence for study plans and the scope they define.

A study plan is the answer to "what am I learning right now?". It selects
lists; its words are the union of those lists. The plan is a *scope*, not an
owner: it does not own cards, statuses or words, and deleting it leaves all
three untouched. That is why every query here returns word ids and lets the
caller decide what to do with them.

Two rules the rest of the engine relies on:

1. **The union is deduplicated.** A word selected by two of the plan's lists
   appears once, so it can never be introduced or reviewed twice.
2. **Candidates are ordered deterministically**: CEFR level first (A1 before
   C2, no level last), then the word's position in the plan's lists, then the
   word id. Re-running the query gives the same 25 words, which is what makes
   "the pointer did not move" observable rather than a matter of luck.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from ..core.errors import DuplicateListError, ListError, ListNotFoundError, StorageError
from ..database.connection import Database
from ..models.language import UNDETERMINED
from ..models.srs import PlanOutlook, StudyPlan
from ..models.word_entry import CEFR_ORDER

MAX_NAME_LENGTH = 80

#: Orders words by CEFR level with unknown levels last, as a SQL expression
#: over the ``cefr_rank`` column that :data:`_PLAN_WORDS` projects.
_CEFR_RANK = "CASE cefr_rank " + " ".join(
    f"WHEN '{level}' THEN {index}" for index, level in enumerate(CEFR_ORDER)
) + f" ELSE {len(CEFR_ORDER)} END"

#: The lists a saved plan draws on, with the order they were chosen in. A plan
#: over every list takes lists it does not name, after the named ones.
_SAVED_SCOPE = """
SELECT pl.list_id, pl.position FROM study_plan_lists pl WHERE pl.plan_id = :plan
UNION ALL
SELECT l.id, 1000000 + l.id FROM lists l
 WHERE (SELECT all_lists FROM study_plans WHERE id = :plan) = 1
"""

#: The same, for a selection not yet saved: a JSON array of list ids.
_SELECTION_SCOPE = (
    "SELECT CAST(value AS INTEGER) AS list_id, key AS position FROM json_each(:lists)"
)
_EVERY_LIST_SCOPE = "SELECT id AS list_id, id AS position FROM lists"


def _words(scope: str) -> str:
    """The words of a scope, one row per word, with the details ordering needs."""
    return f"""
SELECT
    w.id                               AS word_id,
    COALESCE(st.status, 'not_reviewed') AS status,
    MIN(scope.position)                AS list_position,
    MIN(lw.position)                   AS word_position,
    (SELECT ws.cefr_level FROM word_sources ws
      WHERE ws.word_id = w.id AND ws.cefr_level IS NOT NULL
      ORDER BY ws.source_id LIMIT 1)   AS cefr_rank,
    EXISTS (SELECT 1 FROM word_sources ws
      WHERE ws.word_id = w.id AND trim(COALESCE(ws.definition, '')) <> '')
                                       AS has_definition
FROM ({scope}) AS scope
JOIN list_words lw ON lw.list_id = scope.list_id
JOIN words w       ON w.id = lw.word_id
LEFT JOIN user_word_state st ON st.word_id = w.id
GROUP BY w.id
"""


_PLAN_WORDS = _words(_SAVED_SCOPE)


def _allowed(include_not_reviewed: bool) -> str:
    return "('unknown', 'not_reviewed')" if include_not_reviewed else "('unknown')"


def _outlook_sql(words: str, include_not_reviewed: bool) -> str:
    allowed = _allowed(include_not_reviewed)
    return f"""
    SELECT
        COUNT(*)                                          AS total,
        COALESCE(SUM(status = 'known'), 0)                AS known,
        COALESCE(SUM(status = 'unknown'), 0)              AS unknown,
        COALESCE(SUM(status = 'not_reviewed'), 0)         AS not_reviewed,
        COALESCE(SUM(c.word_id IS NOT NULL AND status != 'known'), 0) AS in_progress,
        COALESCE(SUM(c.word_id IS NOT NULL AND status = 'known'), 0)  AS learned_here,
        COALESCE(SUM(c.word_id IS NULL AND status IN {allowed} AND has_definition), 0)
                                                          AS to_introduce,
        COALESCE(SUM(c.word_id IS NULL AND status IN {allowed} AND NOT has_definition), 0)
                                                          AS without_definition
    FROM ({words}) AS plan_words
    LEFT JOIN srs_cards c ON c.word_id = plan_words.word_id
    """


class PlanRepository:
    """Reads and writes ``study_plans`` and ``study_plan_lists``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- plans -------------------------------------------------------------

    def create(
        self,
        name: str,
        language: str = UNDETERMINED,
        description: str | None = None,
        list_ids: Sequence[int] = (),
        make_active: bool = True,
        all_lists: bool = False,
    ) -> StudyPlan:
        clean = _clean_name(name)
        if self.get_by_name(clean) is not None:
            raise DuplicateListError(f"A study plan called “{clean}” already exists.")
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO study_plans (name, language, description, is_active, all_lists)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (clean, language, (description or "").strip() or None, int(all_lists)),
                )
                plan_id = int(cursor.lastrowid)
                self._replace_lists(conn, plan_id, list_ids)
                if make_active:
                    self._activate(conn, plan_id)
        except sqlite3.Error as exc:
            raise StorageError("The study plan could not be created.") from exc
        return self.require(plan_id)

    def update(
        self,
        plan_id: int,
        name: str | None = None,
        language: str | None = None,
        description: str | None = None,
        list_ids: Sequence[int] | None = None,
        all_lists: bool | None = None,
    ) -> StudyPlan:
        plan = self.require(plan_id)
        clean = _clean_name(name) if name is not None else plan.name
        if clean.casefold() != plan.name.casefold():
            existing = self.get_by_name(clean)
            if existing is not None and existing.id != plan_id:
                raise DuplicateListError(f"A study plan called “{clean}” already exists.")
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE study_plans
                       SET name = ?, language = ?, description = ?,
                           updated_at = datetime('now')
                     WHERE id = ?
                    """,
                    (
                        clean,
                        language if language is not None else plan.language,
                        (description if description is not None else plan.description) or None,
                        plan_id,
                    ),
                )
                if list_ids is not None:
                    self._replace_lists(conn, plan_id, list_ids)
                if all_lists is not None:
                    conn.execute(
                        "UPDATE study_plans SET all_lists = ? WHERE id = ?",
                        (int(all_lists), plan_id),
                    )
        except sqlite3.Error as exc:
            raise StorageError("The study plan could not be saved.") from exc
        return self.require(plan_id)

    def delete(self, plan_id: int) -> None:
        """Delete a plan. Cards, words and statuses are untouched by design."""
        try:
            with self._db.transaction() as conn:
                conn.execute("DELETE FROM study_plans WHERE id = ?", (plan_id,))
        except sqlite3.Error as exc:
            raise StorageError("The study plan could not be deleted.") from exc

    def set_active(self, plan_id: int) -> StudyPlan:
        self.require(plan_id)
        try:
            with self._db.transaction() as conn:
                self._activate(conn, plan_id)
        except sqlite3.Error as exc:
            raise StorageError("The active study plan could not be changed.") from exc
        return self.require(plan_id)

    def list_all(self) -> list[StudyPlan]:
        rows = self._db.connection.execute(
            "SELECT id FROM study_plans ORDER BY is_active DESC, name COLLATE NOCASE"
        ).fetchall()
        return [self.require(int(row["id"])) for row in rows]

    def get(self, plan_id: int) -> StudyPlan | None:
        row = self._db.connection.execute(
            "SELECT id, name, language, description, is_active, all_lists "
            "FROM study_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        selections = self._db.connection.execute(
            """
            SELECT l.id, l.name
            FROM study_plan_lists pl
            JOIN lists l ON l.id = pl.list_id
            WHERE pl.plan_id = ?
            ORDER BY pl.position, l.name COLLATE NOCASE
            """,
            (plan_id,),
        ).fetchall()
        return StudyPlan(
            id=int(row["id"]),
            name=row["name"],
            language=row["language"],
            description=row["description"],
            is_active=bool(row["is_active"]),
            list_ids=tuple(int(item["id"]) for item in selections),
            list_names=tuple(item["name"] for item in selections),
            all_lists=bool(row["all_lists"]),
        )

    def require(self, plan_id: int) -> StudyPlan:
        plan = self.get(plan_id)
        if plan is None:
            raise ListNotFoundError("That study plan no longer exists.")
        return plan

    def get_by_name(self, name: str) -> StudyPlan | None:
        row = self._db.connection.execute(
            "SELECT id FROM study_plans WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return self.get(int(row["id"])) if row else None

    def active(self) -> StudyPlan | None:
        row = self._db.connection.execute(
            "SELECT id FROM study_plans WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return self.get(int(row["id"])) if row else None

    # -- scope -------------------------------------------------------------

    def word_ids(self, plan_id: int) -> list[int]:
        """Every word in the plan, deduplicated, in candidate order."""
        rows = self._db.connection.execute(
            f"SELECT word_id FROM ({_PLAN_WORDS}) AS plan_words "
            f"ORDER BY {_CEFR_RANK}, list_position, word_position, word_id",
            {"plan": plan_id},
        ).fetchall()
        return [int(row["word_id"]) for row in rows]

    def candidate_word_ids(
        self,
        plan_id: int,
        limit: int | None = None,
        include_not_reviewed: bool = False,
    ) -> list[int]:
        """Words of the plan that have never been introduced.

        This is the new-word pool. "Never introduced" is the absence of a row
        in ``srs_cards`` — there is no pointer to keep in step, so a partial
        day, a missed day or a restart cannot move it.

        Unknown words come first. ``include_not_reviewed`` also offers words
        that have never been answered at all, which is what a freshly imported
        list consists of; without it such a plan would have nothing to study.
        A word with no definition is not offered: both questions need one.
        """
        if limit is not None and limit <= 0:
            return []
        allowed = _allowed(include_not_reviewed)
        sql = f"""
        SELECT word_id FROM ({_PLAN_WORDS}) AS plan_words
        WHERE status IN {allowed}
          AND has_definition
          AND word_id NOT IN (SELECT word_id FROM srs_cards)
        ORDER BY status = 'unknown' DESC,
                 {_CEFR_RANK}, list_position, word_position, word_id
        """
        params: dict[str, object] = {"plan": plan_id}
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        rows = self._db.connection.execute(sql, params).fetchall()
        return [int(row["word_id"]) for row in rows]

    def candidate_count(self, plan_id: int, include_not_reviewed: bool = False) -> int:
        return self.outlook(plan_id, include_not_reviewed).to_introduce

    def counts(self, plan_id: int) -> dict[str, int]:
        """Totals for the plan: words, and how they are distributed."""
        outlook = self.outlook(plan_id)
        return {
            "total": outlook.total,
            "known": outlook.known,
            "unknown": outlook.unknown,
            "not_reviewed": outlook.not_reviewed,
            "introduced": outlook.in_progress + outlook.learned_here,
        }

    def outlook(self, plan_id: int, include_not_reviewed: bool = False) -> PlanOutlook:
        """What the saved plan means: words to introduce, in progress, learned."""
        row = self._db.connection.execute(
            _outlook_sql(_PLAN_WORDS, include_not_reviewed), {"plan": plan_id}
        ).fetchone()
        return PlanOutlook(**{key: int(row[key]) for key in row.keys()})

    def selection_outlook(
        self,
        list_ids: Sequence[int],
        *,
        all_lists: bool = False,
        include_not_reviewed: bool = False,
    ) -> PlanOutlook:
        """The same figures for a selection that has not been saved yet."""
        if all_lists:
            words, params = _words(_EVERY_LIST_SCOPE), {}
        else:
            words = _words(_SELECTION_SCOPE)
            params = {"lists": json.dumps([int(list_id) for list_id in list_ids])}
        row = self._db.connection.execute(
            _outlook_sql(words, include_not_reviewed), params
        ).fetchone()
        return PlanOutlook(**{key: int(row[key]) for key in row.keys()})

    # -- internals ---------------------------------------------------------

    def _replace_lists(
        self, conn: sqlite3.Connection, plan_id: int, list_ids: Sequence[int]
    ) -> None:
        conn.execute("DELETE FROM study_plan_lists WHERE plan_id = ?", (plan_id,))
        seen: set[int] = set()
        position = 0
        for list_id in list_ids:
            if list_id in seen:
                continue
            seen.add(int(list_id))
            conn.execute(
                "INSERT INTO study_plan_lists (plan_id, list_id, position) VALUES (?, ?, ?)",
                (plan_id, int(list_id), position),
            )
            position += 1

    def _activate(self, conn: sqlite3.Connection, plan_id: int) -> None:
        """Exactly one plan is active; the setting mirrors it for the bot."""
        conn.execute("UPDATE study_plans SET is_active = 0 WHERE is_active = 1")
        conn.execute(
            "UPDATE study_plans SET is_active = 1, updated_at = datetime('now') WHERE id = ?",
            (plan_id,),
        )
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES ('active_plan_id', ?)
            ON CONFLICT(key) DO UPDATE
               SET value = excluded.value, updated_at = datetime('now')
            """,
            (str(plan_id),),
        )


def _clean_name(name: str) -> str:
    clean = " ".join((name or "").split())[:MAX_NAME_LENGTH].strip()
    if not clean:
        raise ListError("A study plan needs a name.")
    return clean
