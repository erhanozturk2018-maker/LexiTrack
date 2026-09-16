"""Persistence for vocabulary identities and their per-source metadata."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..core.errors import StorageError
from ..database.connection import Database
from ..models.user_word_state import ReviewStatus
from ..models.word_entry import WordEntry


@dataclass(frozen=True, slots=True)
class StoredWord:
    """A vocabulary identity as it exists in the database.

    Metadata is flattened from every source the word appears in: the first
    source that supplied a given field wins, so a word found in a plain text
    PDF after being imported from Oxford keeps its CEFR level.
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

    @property
    def source_label(self) -> str:
        """Human readable source line, e.g. ``Oxford 3000 - Oxford 5000``."""
        return " · ".join(self.sources)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """What an import actually changed."""

    source_name: str = ""
    parser_type: str = ""
    parsed: int = 0
    new_words: int = 0
    existing_words: int = 0
    new_links: int = 0

    @property
    def total_words(self) -> int:
        return self.new_words + self.existing_words


class WordRepository:
    """Reads and writes ``words``, ``word_sources`` and the review queue."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- writing -----------------------------------------------------------

    def add_entries(self, entries: Sequence[WordEntry], source_id: int) -> ImportResult:
        """Store ``entries`` against ``source_id`` in one transaction.

        Words already in the database keep their identity, their review state
        and their existing metadata; only the link to the new source is added.
        This is what makes a repeat import a no-op from the user's point of
        view.
        """
        new_words = 0
        existing_words = 0
        new_links = 0

        try:
            with self._db.transaction() as conn:
                for entry in entries:
                    word_id, created = self._insert_word(conn, entry)
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
        )

    @staticmethod
    def _insert_word(conn: sqlite3.Connection, entry: WordEntry) -> tuple[int, bool]:
        """Return ``(word_id, was_created)`` for ``entry``."""
        cursor = conn.execute(
            "INSERT OR IGNORE INTO words (normalized_word, display_word) VALUES (?, ?)",
            (entry.normalized_word, entry.word),
        )
        if cursor.rowcount == 1:
            return int(cursor.lastrowid), True

        row = conn.execute(
            "SELECT id FROM words WHERE normalized_word = ?", (entry.normalized_word,)
        ).fetchone()
        return int(row["id"]), False

    # -- reading -----------------------------------------------------------

    def next_unreviewed(self) -> StoredWord | None:
        """Return the next word awaiting review, or ``None`` when finished.

        Ordering is by insertion, so a session resumes exactly where it stopped
        without storing a cursor anywhere.
        """
        row = self._db.connection.execute(
            _SELECT_WORD + " WHERE st.status = ? ORDER BY w.id LIMIT 1",
            (ReviewStatus.NOT_REVIEWED.value,),
        ).fetchone()
        return _row_to_word(row) if row else None

    def get(self, word_id: int) -> StoredWord | None:
        row = self._db.connection.execute(
            _SELECT_WORD + " WHERE w.id = ?", (word_id,)
        ).fetchone()
        return _row_to_word(row) if row else None

    def find(self, normalized_word: str) -> StoredWord | None:
        row = self._db.connection.execute(
            _SELECT_WORD + " WHERE w.normalized_word = ?", (normalized_word,)
        ).fetchone()
        return _row_to_word(row) if row else None

    def list_by_status(self, status: ReviewStatus) -> list[StoredWord]:
        rows = self._db.connection.execute(
            _SELECT_WORD + " WHERE st.status = ? ORDER BY w.normalized_word",
            (status.value,),
        ).fetchall()
        return [_row_to_word(row) for row in rows]

    def count(self) -> int:
        row = self._db.connection.execute("SELECT COUNT(*) AS n FROM words").fetchone()
        return int(row["n"])

    def existing_identities(self, normalized_words: Iterable[str]) -> set[str]:
        """Return which of ``normalized_words`` are already known to the database."""
        values = list(normalized_words)
        if not values:
            return set()
        found: set[str] = set()
        for start in range(0, len(values), 500):
            chunk = values[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.connection.execute(
                f"SELECT normalized_word FROM words WHERE normalized_word IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update(row["normalized_word"] for row in rows)
        return found


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

# ``word_sources`` rows are collapsed here so the rest of the application never
# has to think about a word appearing in more than one document.
_SELECT_WORD = """
SELECT
    w.id,
    w.display_word,
    w.normalized_word,
    COALESCE(st.status, 'not_reviewed') AS status,
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
      WHERE ws.word_id = w.id) AS source_names
FROM words w
LEFT JOIN user_word_state st ON st.word_id = w.id
"""


def _row_to_word(row: sqlite3.Row) -> StoredWord:
    names = row["source_names"]
    return StoredWord(
        id=row["id"],
        word=row["display_word"],
        normalized_word=row["normalized_word"],
        status=ReviewStatus(row["status"]),
        part_of_speech=row["part_of_speech"],
        cefr_level=row["cefr_level"],
        definition=row["definition"],
        example=row["example"],
        sources=tuple(dict.fromkeys(names.split("|"))) if names else (),
    )
