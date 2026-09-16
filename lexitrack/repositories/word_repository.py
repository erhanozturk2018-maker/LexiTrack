"""Persistence for vocabulary identities and their per-source metadata."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..core.errors import StorageError
from ..database.connection import Database
from ..models.language import UNDETERMINED
from ..models.user_word_state import ReviewStatus
from ..models.word_entry import WordEntry


@dataclass(frozen=True, slots=True)
class StoredWord:
    """A vocabulary identity as it exists in the database.

    Metadata is flattened from every source the word appears in: the first
    source that supplied a given field wins, so a word found in a plain text
    PDF after being imported from Oxford keeps its CEFR level.

    ``sources`` is provenance (where the word was extracted from). ``lists``
    is membership (what the user is studying it as part of). They are kept as
    separate fields precisely so the UI cannot confuse one for the other.
    """

    id: int
    word: str
    normalized_word: str
    status: ReviewStatus = ReviewStatus.NOT_REVIEWED
    part_of_speech: str | None = None
    cefr_level: str | None = None
    definition: str | None = None
    example: str | None = None
    sources: tuple[str, ...] = ()
    language: str = UNDETERMINED
    lists: tuple[str, ...] = ()
    reviewed_at: str | None = None

    @property
    def source_label(self) -> str:
        """Human readable provenance, e.g. ``Oxford 3000 · Oxford 5000``."""
        return " · ".join(self.sources)

    @property
    def list_label(self) -> str:
        """Human readable membership, e.g. ``Oxford 3000, My Difficult Words``."""
        return ", ".join(self.lists)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """What an import actually changed."""

    source_name: str = ""
    parser_type: str = ""
    parsed: int = 0
    new_words: int = 0
    existing_words: int = 0
    new_links: int = 0
    #: Ids of every stored word, in document order, new and existing alike.
    word_ids: tuple[int, ...] = ()
    #: Names of the lists the words were added to.
    list_names: tuple[str, ...] = ()
    #: How many of the words were not yet in those lists.
    added_to_lists: int = 0
    language: str = UNDETERMINED

    @property
    def total_words(self) -> int:
        return self.new_words + self.existing_words


@dataclass(frozen=True, slots=True)
class IdentityCheck:
    """Which of a set of words already exist, for an import preview."""

    total: int = 0
    existing: int = 0
    existing_words: frozenset[str] = field(default_factory=frozenset)

    @property
    def new(self) -> int:
        return self.total - self.existing


class WordRepository:
    """Reads and writes ``words``, ``word_sources`` and the review queue."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- writing -----------------------------------------------------------

    def add_entries(
        self,
        entries: Sequence[WordEntry],
        source_id: int,
        language: str | None = None,
    ) -> ImportResult:
        """Store ``entries`` against ``source_id`` in one transaction.

        Each entry's identity is ``(language, normalized_word)``, where the
        language is the entry's own if the parser knew it, else ``language``,
        else undetermined.

        Words already in the database keep their identity, their review state
        and their existing metadata; only the link to the new source is added.
        This is what makes a repeat import a no-op from the user's point of
        view.
        """
        new_words = 0
        existing_words = 0
        new_links = 0
        word_ids: list[int] = []
        fallback = language or UNDETERMINED

        try:
            with self._db.transaction() as conn:
                for entry in entries:
                    entry_language = entry.language or fallback
                    word_id, created = self._insert_word(conn, entry, entry_language)
                    word_ids.append(word_id)
                    if created:
                        new_words += 1
                        conn.execute(
                            "INSERT OR IGNORE INTO user_word_state (word_id, status) VALUES (?, ?)",
                            (word_id, ReviewStatus.NOT_REVIEWED.value),
                        )
                    else:
                        existing_words += 1

                    # SQLite reports rowcount 1 for the UPDATE branch of an
                    # upsert too, so the link has to be checked beforehand for
                    # the count to mean what it says.
                    already_linked = conn.execute(
                        "SELECT 1 FROM word_sources WHERE word_id = ? AND source_id = ?",
                        (word_id, source_id),
                    ).fetchone()
                    conn.execute(
                        _UPSERT_WORD_SOURCE,
                        (
                            word_id,
                            source_id,
                            entry.part_of_speech,
                            entry.cefr_level,
                            entry.definition,
                            entry.example,
                            json.dumps(entry.metadata) if entry.metadata else None,
                        ),
                    )
                    if already_linked is None:
                        new_links += 1
        except sqlite3.Error as exc:
            raise StorageError("The imported words could not be saved.") from exc

        return ImportResult(
            parsed=len(entries),
            new_words=new_words,
            existing_words=existing_words,
            new_links=new_links,
            word_ids=tuple(word_ids),
            language=fallback,
        )

    @staticmethod
    def _insert_word(
        conn: sqlite3.Connection, entry: WordEntry, language: str
    ) -> tuple[int, bool]:
        """Return ``(word_id, was_created)`` for ``entry`` in ``language``."""
        cursor = conn.execute(
            "INSERT OR IGNORE INTO words (language, normalized_word, display_word) "
            "VALUES (?, ?, ?)",
            (language, entry.normalized_word, entry.word),
        )
        if cursor.rowcount == 1:
            return int(cursor.lastrowid), True

        row = conn.execute(
            "SELECT id FROM words WHERE language = ? AND normalized_word = ?",
            (language, entry.normalized_word),
        ).fetchone()
        return int(row["id"]), False

    # -- reading -----------------------------------------------------------

    def next_unreviewed(self, list_id: int | None = None) -> StoredWord | None:
        """Return the next word awaiting review, or ``None`` when finished.

        Inside a list the order is the list's own; across the whole vocabulary
        it is insertion order. Either way a session resumes exactly where it
        stopped without storing a cursor anywhere.
        """
        if list_id is None:
            row = self._db.connection.execute(
                _SELECT_WORD + " WHERE st.status = ? ORDER BY w.id LIMIT 1",
                (ReviewStatus.NOT_REVIEWED.value,),
            ).fetchone()
        else:
            row = self._db.connection.execute(
                _SELECT_WORD
                + " JOIN list_words lw ON lw.word_id = w.id"
                + " WHERE lw.list_id = ? AND COALESCE(st.status, 'not_reviewed') = ?"
                + " ORDER BY lw.position, w.id LIMIT 1",
                (list_id, ReviewStatus.NOT_REVIEWED.value),
            ).fetchone()
        return _row_to_word(row) if row else None

    def get(self, word_id: int) -> StoredWord | None:
        row = self._db.connection.execute(
            _SELECT_WORD + " WHERE w.id = ?", (word_id,)
        ).fetchone()
        return _row_to_word(row) if row else None

    def get_many(self, word_ids: Iterable[int]) -> list[StoredWord]:
        """Return the words for ``word_ids``, in the order the ids were given."""
        ids = list(dict.fromkeys(word_ids))
        found: dict[int, StoredWord] = {}
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                _SELECT_WORD + f" WHERE w.id IN ({placeholders})", chunk
            ):
                word = _row_to_word(row)
                found[word.id] = word
        return [found[i] for i in ids if i in found]

    def find(self, normalized_word: str, language: str | None = None) -> StoredWord | None:
        """Find a word by identity. Without a language, the oldest match wins."""
        if language is None:
            row = self._db.connection.execute(
                _SELECT_WORD + " WHERE w.normalized_word = ? ORDER BY w.id LIMIT 1",
                (normalized_word,),
            ).fetchone()
        else:
            row = self._db.connection.execute(
                _SELECT_WORD + " WHERE w.language = ? AND w.normalized_word = ?",
                (language, normalized_word),
            ).fetchone()
        return _row_to_word(row) if row else None

    def list_by_status(
        self, status: ReviewStatus, list_id: int | None = None
    ) -> list[StoredWord]:
        """Words with ``status``, alphabetically, optionally within one list."""
        if list_id is None:
            rows = self._db.connection.execute(
                _SELECT_WORD
                + " WHERE COALESCE(st.status, 'not_reviewed') = ?"
                + " ORDER BY w.normalized_word, w.language",
                (status.value,),
            ).fetchall()
        else:
            rows = self._db.connection.execute(
                _SELECT_WORD
                + " JOIN list_words lw ON lw.word_id = w.id"
                + " WHERE lw.list_id = ? AND COALESCE(st.status, 'not_reviewed') = ?"
                + " ORDER BY w.normalized_word",
                (list_id, status.value),
            ).fetchall()
        return [_row_to_word(row) for row in rows]

    def list_in_list(self, list_id: int) -> list[StoredWord]:
        """Every word in a list, in the list's own order."""
        rows = self._db.connection.execute(
            _SELECT_WORD
            + " JOIN list_words lw ON lw.word_id = w.id"
            + " WHERE lw.list_id = ? ORDER BY lw.position, w.id",
            (list_id,),
        ).fetchall()
        return [_row_to_word(row) for row in rows]

    def count(self) -> int:
        row = self._db.connection.execute("SELECT COUNT(*) AS n FROM words").fetchone()
        return int(row["n"])

    def existing_identities(
        self, normalized_words: Iterable[str], language: str | None = None
    ) -> set[str]:
        """Return which of ``normalized_words`` are already in the database.

        With a language, only that language's vocabulary is considered — which
        is what an import preview needs, since English "gift" says nothing
        about whether German "Gift" is new.
        """
        values = list(normalized_words)
        if not values:
            return set()
        found: set[str] = set()
        for chunk in _chunks(values):
            placeholders = ",".join("?" * len(chunk))
            if language is None:
                sql = f"SELECT normalized_word FROM words WHERE normalized_word IN ({placeholders})"
                params: list[object] = list(chunk)
            else:
                sql = (
                    "SELECT normalized_word FROM words WHERE language = ? "
                    f"AND normalized_word IN ({placeholders})"
                )
                params = [language, *chunk]
            rows = self._db.connection.execute(sql, params).fetchall()
            found.update(row["normalized_word"] for row in rows)
        return found

    def check_identities(
        self, normalized_words: Iterable[str], language: str
    ) -> IdentityCheck:
        unique = list(dict.fromkeys(normalized_words))
        existing = self.existing_identities(unique, language)
        return IdentityCheck(
            total=len(unique), existing=len(existing), existing_words=frozenset(existing)
        )


_UPSERT_WORD_SOURCE = """
INSERT INTO word_sources (
    word_id, source_id, part_of_speech, cefr_level, definition, example, metadata
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(word_id, source_id) DO UPDATE SET
    part_of_speech = COALESCE(excluded.part_of_speech, word_sources.part_of_speech),
    cefr_level     = COALESCE(excluded.cefr_level,     word_sources.cefr_level),
    definition     = COALESCE(excluded.definition,     word_sources.definition),
    example        = COALESCE(excluded.example,        word_sources.example)
"""

# ``word_sources`` and ``list_words`` rows are collapsed here so the rest of the
# application never has to think about a word having several of either.
_SELECT_WORD = """
SELECT
    w.id,
    w.display_word,
    w.normalized_word,
    w.language,
    COALESCE(st.status, 'not_reviewed') AS status,
    st.reviewed_at,
    (SELECT ws.part_of_speech FROM word_sources ws
      WHERE ws.word_id = w.id AND ws.part_of_speech IS NOT NULL
      ORDER BY ws.source_id LIMIT 1) AS part_of_speech,
    (SELECT ws.cefr_level FROM word_sources ws
      WHERE ws.word_id = w.id AND ws.cefr_level IS NOT NULL
      ORDER BY ws.source_id LIMIT 1) AS cefr_level,
    (SELECT ws.definition FROM word_sources ws
      WHERE ws.word_id = w.id AND ws.definition IS NOT NULL
      ORDER BY ws.source_id LIMIT 1) AS definition,
    (SELECT ws.example FROM word_sources ws
      WHERE ws.word_id = w.id AND ws.example IS NOT NULL
      ORDER BY ws.source_id LIMIT 1) AS example,
    (SELECT GROUP_CONCAT(s.name, '|') FROM word_sources ws
      JOIN sources s ON s.id = ws.source_id
      WHERE ws.word_id = w.id) AS source_names,
    (SELECT GROUP_CONCAT(l.name, '|') FROM list_words lw2
      JOIN lists l ON l.id = lw2.list_id
      WHERE lw2.word_id = w.id) AS list_names
FROM words w
LEFT JOIN user_word_state st ON st.word_id = w.id
"""


def _row_to_word(row: sqlite3.Row) -> StoredWord:
    sources = row["source_names"]
    lists = row["list_names"]
    return StoredWord(
        id=row["id"],
        word=row["display_word"],
        normalized_word=row["normalized_word"],
        status=ReviewStatus(row["status"]),
        part_of_speech=row["part_of_speech"],
        cefr_level=row["cefr_level"],
        definition=row["definition"],
        example=row["example"],
        sources=tuple(dict.fromkeys(sources.split("|"))) if sources else (),
        language=row["language"],
        lists=tuple(sorted(dict.fromkeys(lists.split("|")), key=str.casefold)) if lists else (),
        reviewed_at=row["reviewed_at"],
    )


def _chunks(values: Sequence, size: int = 500):
    for start in range(0, len(values), size):
        yield values[start : start + size]
