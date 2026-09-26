"""Persistence for a word's contexts, ``word_contexts``: id, word, text.

A word has any number of contexts, in the order they were added. The same
sentence twice for one word is not stored: case and spacing are ignored when
comparing, and the table's UNIQUE constraint backs that up. Deleting a word
deletes its contexts with it (ON DELETE CASCADE), so a word deleted and added
again starts with none.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence

from ..core.errors import StorageError, WordError
from ..database.connection import Database
from ..models.context import MAX_CONTEXT_LENGTH, WordContext, clean_context, same_context

_CHUNK = 500


class ContextRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    # -- reading -------------------------------------------------------------

    def for_word(self, word_id: int) -> tuple[WordContext, ...]:
        rows = self._db.connection.execute(
            "SELECT id, word_id, text FROM word_contexts WHERE word_id = ? ORDER BY id",
            (int(word_id),),
        ).fetchall()
        return tuple(_to_context(row) for row in rows)

    def for_words(self, word_ids: Iterable[int]) -> dict[int, tuple[WordContext, ...]]:
        """The contexts of many words, each word's in order; words with none
        are left out."""
        found: dict[int, list[WordContext]] = {}
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                f"SELECT id, word_id, text FROM word_contexts WHERE word_id IN ({marks}) "
                "ORDER BY word_id, id",
                chunk,
            ):
                context = _to_context(row)
                found.setdefault(context.word_id, []).append(context)
        return {word_id: tuple(contexts) for word_id, contexts in found.items()}

    def get(self, context_id: int) -> WordContext | None:
        row = self._db.connection.execute(
            "SELECT id, word_id, text FROM word_contexts WHERE id = ?", (int(context_id),)
        ).fetchone()
        return _to_context(row) if row else None

    def counts(self, word_ids: Iterable[int] | None = None) -> dict[int, int]:
        """How many contexts each word has; words with none are left out."""
        if word_ids is None:
            rows = self._db.connection.execute(
                "SELECT word_id, COUNT(*) AS n FROM word_contexts GROUP BY word_id"
            ).fetchall()
            return {int(row["word_id"]): int(row["n"]) for row in rows}
        counts: dict[int, int] = {}
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                f"SELECT word_id, COUNT(*) AS n FROM word_contexts WHERE word_id IN ({marks}) "
                "GROUP BY word_id",
                chunk,
            ):
                counts[int(row["word_id"])] = int(row["n"])
        return counts

    def total(self) -> int:
        return int(
            self._db.connection.execute("SELECT COUNT(*) FROM word_contexts").fetchone()[0]
        )

    # -- writing -------------------------------------------------------------

    def add(self, word_id: int, text: str) -> WordContext | None:
        """Add one context. None when the word already has that sentence."""
        added = self.add_many(word_id, [text])
        return added[0] if added else None

    def add_many(self, word_id: int, texts: Sequence[str]) -> list[WordContext]:
        """Add contexts to a word, skipping any it already has (or that repeat
        in ``texts``). Returns the contexts added, in order.

        An empty sentence, or one longer than :data:`MAX_CONTEXT_LENGTH`, is
        refused with a :class:`WordError` and nothing is added.
        """
        cleaned = [_checked(text) for text in texts]
        added: list[WordContext] = []
        try:
            with self._db.transaction() as conn:
                found = conn.execute("SELECT 1 FROM words WHERE id = ?", (int(word_id),))
                if found.fetchone() is None:
                    raise WordError("That word no longer exists.")
                existing = [c.text for c in self.for_word(word_id)]
                for text in cleaned:
                    if any(same_context(text, other) for other in existing):
                        continue
                    cursor = conn.execute(
                        "INSERT INTO word_contexts (word_id, text) VALUES (?, ?)",
                        (int(word_id), text),
                    )
                    existing.append(text)
                    added.append(WordContext(int(word_id), text, int(cursor.lastrowid)))
        except sqlite3.Error as exc:
            raise StorageError("The context could not be saved.") from exc
        return added

    def update(self, context_id: int, text: str) -> WordContext:
        """Correct a context's text in place, keeping its id: an answer that
        was asked from it still points at it."""
        clean = _checked(text)
        try:
            with self._db.transaction() as conn:
                context = self.get(context_id)
                if context is None:
                    raise WordError("That context no longer exists.")
                others = [c.text for c in self.for_word(context.word_id) if c.id != context.id]
                if any(same_context(clean, other) for other in others):
                    raise WordError("The word already has that sentence.")
                conn.execute(
                    "UPDATE word_contexts SET text = ? WHERE id = ?", (clean, int(context_id))
                )
        except sqlite3.Error as exc:
            raise StorageError("The context could not be saved.") from exc
        return WordContext(context.word_id, clean, context.id)

    def delete(self, context_id: int) -> bool:
        """Delete one context. False when it was already gone."""
        try:
            with self._db.transaction() as conn:
                return (
                    conn.execute(
                        "DELETE FROM word_contexts WHERE id = ?", (int(context_id),)
                    ).rowcount
                    == 1
                )
        except sqlite3.Error as exc:
            raise StorageError("The context could not be deleted.") from exc


def _checked(text: str) -> str:
    """A context cleaned, or a :class:`WordError` saying why it cannot be one."""
    clean = clean_context(text)
    if not clean:
        raise WordError("A context cannot be empty.")
    if len(clean) > MAX_CONTEXT_LENGTH:
        raise WordError(
            f"A context can be at most {MAX_CONTEXT_LENGTH} characters: a sentence or two."
        )
    return clean


def _to_context(row: sqlite3.Row) -> WordContext:
    return WordContext(word_id=int(row["word_id"]), text=row["text"], id=int(row["id"]))
