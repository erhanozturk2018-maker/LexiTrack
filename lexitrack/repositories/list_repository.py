"""Persistence for learning lists and their membership.

Two invariants are maintained here, because every screen relies on them:

1. **The vocabulary is the union of the lists.** A word that is removed from
   its last list, or whose last list is deleted, is deleted too — along with
   its learning status. A word that is still in another list is never touched.
   Without this rule, "remove" would silently leave words the user can no
   longer see but that still count in totals and exports.

2. **A list with a language only holds words of that language.** A list whose
   language is ``und`` (unspecified) holds anything. This is what stops a
   German import landing in "Oxford 3000" by mistake.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime

from ..core.errors import (
    DuplicateListError,
    LanguageMismatchError,
    ListError,
    ListNotFoundError,
    StorageError,
)
from ..database.connection import Database
from ..models.language import UNDETERMINED, language_name
from ..models.user_word_state import Progress
from ..models.vocabulary_list import ListKind, VocabularyList

MAX_NAME_LENGTH = 80


class ListRepository:
    """Reads and writes ``lists`` and ``list_words``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- lists -------------------------------------------------------------

    def create(
        self,
        name: str,
        language: str = UNDETERMINED,
        description: str | None = None,
        kind: ListKind = ListKind.CUSTOM,
    ) -> VocabularyList:
        clean = _clean_name(name)
        if self.get_by_name(clean) is not None:
            raise DuplicateListError(f"A list called “{clean}” already exists.")
        try:
            with self._db.transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO lists (name, language, description, kind)
                    VALUES (?, ?, ?, ?)
                    """,
                    (clean, language or UNDETERMINED, _clean_text(description), kind.value),
                )
                list_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise DuplicateListError(f"A list called “{clean}” already exists.") from exc
        except sqlite3.Error as exc:
            raise StorageError("The list could not be created.") from exc
        return self.require(list_id)

    def update(
        self,
        list_id: int,
        name: str | None = None,
        description: str | None = None,
        language: str | None = None,
    ) -> VocabularyList:
        """Rename a list, or change its description or language.

        A language change is refused when the list already holds words in a
        different language; changing it to unspecified is always allowed.
        """
        current = self.require(list_id)
        new_name = current.name if name is None else _clean_name(name)
        if new_name.casefold() != current.name.casefold():
            existing = self.get_by_name(new_name)
            if existing is not None and existing.id != list_id:
                raise DuplicateListError(f"A list called “{new_name}” already exists.")

        new_language = current.language if language is None else (language or UNDETERMINED)
        if new_language != current.language and new_language != UNDETERMINED:
            conflicting = self._db.connection.execute(
                """
                SELECT COUNT(*) FROM list_words lw JOIN words w ON w.id = lw.word_id
                WHERE lw.list_id = ? AND w.language != ?
                """,
                (list_id, new_language),
            ).fetchone()[0]
            if conflicting:
                raise LanguageMismatchError(
                    f"“{current.name}” contains {conflicting:,} words that are not "
                    f"{language_name(new_language)}, so its language cannot be changed."
                )

        new_description = (
            current.description if description is None else _clean_text(description)
        )
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE lists SET name = ?, description = ?, language = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (new_name, new_description, new_language, list_id),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateListError(f"A list called “{new_name}” already exists.") from exc
        except sqlite3.Error as exc:
            raise StorageError("The list could not be changed.") from exc
        return self.require(list_id)

    def delete(self, list_id: int) -> int:
        """Delete a list. Returns how many words were deleted with it.

        Only words that belong to no other list are deleted.
        """
        self.require(list_id)
        try:
            with self._db.transaction() as conn:
                orphans = self._orphans_after_removal(conn, list_id, None)
                conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
                self._delete_words(conn, orphans)
        except sqlite3.Error as exc:
            raise StorageError("The list could not be deleted.") from exc
        return len(orphans)

    def get(self, list_id: int) -> VocabularyList | None:
        row = self._db.connection.execute(
            _SELECT_LIST + " WHERE l.id = ? GROUP BY l.id", (list_id,)
        ).fetchone()
        return _row_to_list(row) if row else None

    def require(self, list_id: int) -> VocabularyList:
        found = self.get(list_id)
        if found is None:
            raise ListNotFoundError()
        return found

    def get_by_name(self, name: str) -> VocabularyList | None:
        row = self._db.connection.execute(
            _SELECT_LIST + " WHERE l.name = ? COLLATE NOCASE GROUP BY l.id",
            (_clean_name(name, strict=False),),
        ).fetchone()
        return _row_to_list(row) if row else None

    def all(self) -> list[VocabularyList]:
        rows = self._db.connection.execute(
            _SELECT_LIST + " GROUP BY l.id ORDER BY l.name COLLATE NOCASE"
        ).fetchall()
        return [_row_to_list(row) for row in rows]

    def count(self) -> int:
        return int(self._db.connection.execute("SELECT COUNT(*) FROM lists").fetchone()[0])

    def unique_name(self, name: str) -> str:
        """``name``, or ``name (2)``, ``name (3)``… whichever is free."""
        base = _clean_name(name, strict=False) or "Untitled List"
        candidate, counter = base, 2
        while self.get_by_name(candidate) is not None:
            candidate = f"{base} ({counter})"
            counter += 1
        return candidate

    # -- membership --------------------------------------------------------

    def add_words(self, list_id: int, word_ids: Sequence[int]) -> int:
        """Append words to a list, skipping any already in it. Returns the count added."""
        target = self.require(list_id)
        ids = list(dict.fromkeys(word_ids))
        if not ids:
            return 0
        self._check_language(target, ids)
        added = 0
        try:
            with self._db.transaction() as conn:
                position = conn.execute(
                    "SELECT COALESCE(MAX(position), 0) FROM list_words WHERE list_id = ?",
                    (list_id,),
                ).fetchone()[0]
                for word_id in ids:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO list_words (list_id, word_id, position) "
                        "VALUES (?, ?, ?)",
                        (list_id, word_id, position + 1),
                    )
                    if cursor.rowcount == 1:
                        position += 1
                        added += 1
                if added:
                    conn.execute(
                        "UPDATE lists SET updated_at = datetime('now') WHERE id = ?", (list_id,)
                    )
        except sqlite3.Error as exc:
            raise StorageError("The words could not be added to the list.") from exc
        return added

    def remove_words(self, list_id: int, word_ids: Sequence[int]) -> tuple[int, int]:
        """Remove words from a list.

        Returns ``(removed_from_list, deleted_entirely)`` — the second counts
        words that belonged to no other list and were therefore deleted.
        """
        self.require(list_id)
        ids = list(dict.fromkeys(word_ids))
        if not ids:
            return 0, 0
        try:
            with self._db.transaction() as conn:
                orphans = self._orphans_after_removal(conn, list_id, ids)
                removed = 0
                for chunk in _chunks(ids):
                    placeholders = ",".join("?" * len(chunk))
                    cursor = conn.execute(
                        f"DELETE FROM list_words WHERE list_id = ? AND word_id IN ({placeholders})",
                        [list_id, *chunk],
                    )
                    removed += cursor.rowcount
                self._delete_words(conn, orphans)
                if removed:
                    conn.execute(
                        "UPDATE lists SET updated_at = datetime('now') WHERE id = ?", (list_id,)
                    )
        except sqlite3.Error as exc:
            raise StorageError("The words could not be removed from the list.") from exc
        return removed, len(orphans)

    def count_exclusive_words(self, list_id: int, word_ids: Sequence[int] | None = None) -> int:
        """How many words would be deleted outright if removed from this list."""
        return len(self._orphans_after_removal(self._db.connection, list_id, word_ids))

    # -- helpers -----------------------------------------------------------

    def _check_language(self, target: VocabularyList, word_ids: Sequence[int]) -> None:
        if target.language == UNDETERMINED:
            return
        mismatched = 0
        for chunk in _chunks(list(word_ids)):
            placeholders = ",".join("?" * len(chunk))
            mismatched += self._db.connection.execute(
                f"SELECT COUNT(*) FROM words WHERE id IN ({placeholders}) AND language != ?",
                [*chunk, target.language],
            ).fetchone()[0]
        if mismatched:
            noun = "word is" if mismatched == 1 else "words are"
            raise LanguageMismatchError(
                f"{mismatched:,} {noun} not {target.language_name}, so "
                f"{'it' if mismatched == 1 else 'they'} cannot be added to "
                f"“{target.name}”."
            )

    @staticmethod
    def _orphans_after_removal(
        conn: sqlite3.Connection, list_id: int, word_ids: Sequence[int] | None
    ) -> list[int]:
        """Words in ``list_id`` (restricted to ``word_ids``) that are in no other list."""
        sql = """
            SELECT lw.word_id FROM list_words lw
            WHERE lw.list_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM list_words other
                  WHERE other.word_id = lw.word_id AND other.list_id != lw.list_id
              )
        """
        if word_ids is None:
            return [row[0] for row in conn.execute(sql, (list_id,))]
        wanted = set(word_ids)
        return [row[0] for row in conn.execute(sql, (list_id,)) if row[0] in wanted]

    @staticmethod
    def _delete_words(conn: sqlite3.Connection, word_ids: Iterable[int]) -> None:
        ids = list(word_ids)
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            # Cascades remove word_sources, list_words and user_word_state rows.
            conn.execute(f"DELETE FROM words WHERE id IN ({placeholders})", chunk)


_SELECT_LIST = """
SELECT
    l.id, l.name, l.language, l.description, l.kind, l.created_at, l.updated_at,
    COUNT(lw.word_id) AS total,
    COALESCE(SUM(st.status = 'known'), 0)   AS known,
    COALESCE(SUM(st.status = 'unknown'), 0) AS unknown
FROM lists l
LEFT JOIN list_words lw ON lw.list_id = l.id
LEFT JOIN user_word_state st ON st.word_id = lw.word_id
"""


def _row_to_list(row: sqlite3.Row) -> VocabularyList:
    return VocabularyList(
        id=row["id"],
        name=row["name"],
        language=row["language"],
        description=row["description"],
        kind=ListKind(row["kind"]),
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
        progress=Progress(
            total=int(row["total"]), known=int(row["known"]), unknown=int(row["unknown"])
        ),
    )


def _clean_name(name: str | None, strict: bool = True) -> str:
    clean = " ".join((name or "").split())
    if strict:
        if not clean:
            raise ListError("A list needs a name.")
        if len(clean) > MAX_NAME_LENGTH:
            raise ListError(
                f"List names can be at most {MAX_NAME_LENGTH} characters."
            )
    return clean


def _clean_text(text: str | None) -> str | None:
    clean = (text or "").strip()
    return clean or None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - defensive
        return None


def _chunks(values: Sequence, size: int = 500):
    for start in range(0, len(values), size):
        yield values[start : start + size]
