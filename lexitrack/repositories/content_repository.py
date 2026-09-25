"""Persistence for a word's teaching content and its contexts.

``word_content`` is one row per word; ``word_contexts`` many. Both are
optional: reading a word with neither returns an empty ``WordTeaching``.
Writes merge rather than replace unless told otherwise, because content
arrives in batches and a later batch must not silently erase an earlier one.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence

from ..core.errors import StorageError
from ..database.connection import Database
from ..models.content import (
    ContentStatus,
    ContextKind,
    DepthHint,
    EncodingType,
    Related,
    WordContent,
    WordContext,
    WordTeaching,
    content_status,
)

_CHUNK = 500


class ContentRepository:
    """Reads and writes ``word_content`` and ``word_contexts``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # -- reading -------------------------------------------------------------

    def content(self, word_id: int) -> WordContent | None:
        row = self._db.connection.execute(
            "SELECT * FROM word_content WHERE word_id = ?", (int(word_id),)
        ).fetchone()
        return _to_content(row) if row else None

    def contexts(self, word_id: int) -> tuple[WordContext, ...]:
        rows = self._db.connection.execute(
            "SELECT * FROM word_contexts WHERE word_id = ? ORDER BY id", (int(word_id),)
        ).fetchall()
        return tuple(_to_context(row) for row in rows)

    def context(self, context_id: int) -> WordContext | None:
        row = self._db.connection.execute(
            "SELECT * FROM word_contexts WHERE id = ?", (int(context_id),)
        ).fetchone()
        return _to_context(row) if row else None

    def teaching(self, word_id: int) -> WordTeaching:
        return WordTeaching(self.content(word_id), self.contexts(word_id))

    def statuses(self, word_ids: Iterable[int]) -> dict[int, ContentStatus]:
        """Content status of many words; words with nothing are NONE."""
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        found: dict[int, ContentStatus] = {}
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            contents = {
                int(row["word_id"]): _to_content(row)
                for row in self._db.connection.execute(
                    f"SELECT * FROM word_content WHERE word_id IN ({marks})", chunk
                )
            }
            counts = {
                int(row["word_id"]): int(row["n"])
                for row in self._db.connection.execute(
                    f"SELECT word_id, COUNT(*) AS n FROM word_contexts "
                    f"WHERE word_id IN ({marks}) GROUP BY word_id",
                    chunk,
                )
            }
            for word_id in chunk:
                found[word_id] = content_status(contents.get(word_id), counts.get(word_id, 0))
        return found

    def all_content(self) -> list[WordContent]:
        rows = self._db.connection.execute("SELECT * FROM word_content ORDER BY word_id")
        return [_to_content(row) for row in rows]

    def all_contexts(self) -> list[WordContext]:
        rows = self._db.connection.execute("SELECT * FROM word_contexts ORDER BY word_id, id")
        return [_to_context(row) for row in rows]

    # -- writing -------------------------------------------------------------

    def save_content(self, content: WordContent) -> None:
        """Insert or replace one word's content as given."""
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO word_content
                        (word_id, core_meaning_tr, nuance, pattern, collocations, register,
                         encoding_type, encoding_cue, related, depth_hint, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(word_id) DO UPDATE SET
                        core_meaning_tr = excluded.core_meaning_tr,
                        nuance = excluded.nuance,
                        pattern = excluded.pattern,
                        collocations = excluded.collocations,
                        register = excluded.register,
                        encoding_type = excluded.encoding_type,
                        encoding_cue = excluded.encoding_cue,
                        related = excluded.related,
                        depth_hint = excluded.depth_hint,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    _content_row(content),
                )
        except sqlite3.Error as exc:
            raise StorageError("The word's content could not be saved.") from exc

    def add_contexts(self, contexts: Sequence[WordContext]) -> list[int]:
        """Add contexts; returns their new ids. Duplicates are the caller's concern."""
        ids: list[int] = []
        try:
            with self._db.transaction() as conn:
                for context in contexts:
                    cursor = conn.execute(
                        "INSERT INTO word_contexts (word_id, kind, text, translation_tr, source) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            int(context.word_id),
                            ContextKind(context.kind).value,
                            context.text,
                            context.translation_tr,
                            context.source,
                        ),
                    )
                    ids.append(int(cursor.lastrowid))
        except sqlite3.Error as exc:
            raise StorageError("The contexts could not be saved.") from exc
        return ids

    def delete_contexts(self, context_ids: Sequence[int]) -> int:
        ids = [int(context_id) for context_id in context_ids]
        if not ids:
            return 0
        with self._db.transaction() as conn:
            marks = ",".join("?" * len(ids))
            return conn.execute(f"DELETE FROM word_contexts WHERE id IN ({marks})", ids).rowcount


def _content_row(content: WordContent) -> tuple:
    return (
        int(content.word_id),
        content.core_meaning_tr,
        content.nuance,
        content.pattern,
        (
            json.dumps(list(content.collocations), ensure_ascii=False)
            if content.collocations
            else None
        ),
        content.register,
        content.encoding_type.value if content.encoding_type else None,
        content.encoding_cue,
        json.dumps(
            [{"word": r.word, "relation": r.relation} for r in content.related], ensure_ascii=False
        )
        if content.related
        else None,
        content.depth_hint.value if content.depth_hint else None,
        content.source,
    )


def _json_list(text: str | None) -> list:
    if not text:
        return []
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _to_content(row: sqlite3.Row) -> WordContent:
    related = tuple(
        Related(str(item.get("word", "")), str(item.get("relation", "")))
        for item in _json_list(row["related"])
        if isinstance(item, dict) and item.get("word")
    )
    return WordContent(
        word_id=int(row["word_id"]),
        core_meaning_tr=row["core_meaning_tr"],
        nuance=row["nuance"],
        pattern=row["pattern"],
        collocations=tuple(str(item) for item in _json_list(row["collocations"]) if item),
        register=row["register"],
        encoding_type=EncodingType(row["encoding_type"]) if row["encoding_type"] else None,
        encoding_cue=row["encoding_cue"],
        related=related,
        depth_hint=DepthHint(row["depth_hint"]) if row["depth_hint"] else None,
        source=row["source"],
        updated_at=row["updated_at"],
    )


def _to_context(row: sqlite3.Row) -> WordContext:
    return WordContext(
        id=int(row["id"]),
        word_id=int(row["word_id"]),
        kind=ContextKind(row["kind"]),
        text=row["text"],
        translation_tr=row["translation_tr"],
        source=row["source"],
    )
