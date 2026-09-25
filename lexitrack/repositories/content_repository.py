"""Persistence for a word's teaching content: shared and per learner language.

* ``word_content`` — the word's target-language content, one row per word;
* ``word_contexts`` — examples of the word in use, many per word;
* ``word_localizations`` — the word explained in one learner language, one
  row per (word, learner language);
* ``context_translations`` — a context translated into one learner language.

All optional: reading a word with none of it returns an empty
``WordTeaching``. Nothing here knows any particular language.
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
    WordLocalization,
    WordTeaching,
    content_status,
)

_CHUNK = 500


class ContentRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    # -- reading -------------------------------------------------------------

    def content(self, word_id: int) -> WordContent | None:
        row = self._db.connection.execute(
            "SELECT * FROM word_content WHERE word_id = ?", (int(word_id),)
        ).fetchone()
        return _to_content(row) if row else None

    def localization(self, word_id: int, learner_language: str) -> WordLocalization | None:
        row = self._db.connection.execute(
            "SELECT * FROM word_localizations WHERE word_id = ? AND learner_language = ?",
            (int(word_id), learner_language),
        ).fetchone()
        return _to_localization(row) if row else None

    def localizations(self, word_id: int) -> dict[str, WordLocalization]:
        """Every language the word is explained in."""
        rows = self._db.connection.execute(
            "SELECT * FROM word_localizations WHERE word_id = ? ORDER BY learner_language",
            (int(word_id),),
        ).fetchall()
        return {row["learner_language"]: _to_localization(row) for row in rows}

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

    def translations(
        self, context_ids: Iterable[int], learner_language: str
    ) -> dict[int, str]:
        """The translations of these contexts into one learner language."""
        ids = [int(context_id) for context_id in dict.fromkeys(context_ids)]
        found: dict[int, str] = {}
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for row in self._db.connection.execute(
                f"SELECT context_id, text FROM context_translations "
                f"WHERE learner_language = ? AND context_id IN ({marks})",
                [learner_language, *chunk],
            ):
                found[int(row["context_id"])] = row["text"]
        return found

    def context_translations(self, context_id: int) -> dict[str, str]:
        """A context in every language it is translated into."""
        rows = self._db.connection.execute(
            "SELECT learner_language, text FROM context_translations WHERE context_id = ?",
            (int(context_id),),
        ).fetchall()
        return {row["learner_language"]: row["text"] for row in rows}

    def teaching(self, word_id: int, learner_language: str | None = None) -> WordTeaching:
        """What is known about the word, for a learner of ``learner_language``."""
        contexts = self.contexts(word_id)
        localization = None
        translations: dict[int, str] = {}
        if learner_language:
            localization = self.localization(word_id, learner_language)
            translations = self.translations(
                (c.id for c in contexts if c.id is not None), learner_language
            )
        return WordTeaching(
            content=self.content(word_id),
            contexts=contexts,
            learner_language=learner_language,
            localization=localization,
            translations=translations,
        )

    def statuses(
        self, word_ids: Iterable[int], learner_language: str | None = None
    ) -> dict[int, ContentStatus]:
        """Content status of many words for a learner language; words with nothing are NONE."""
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        found: dict[int, ContentStatus] = {}
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            conn = self._db.connection
            contents = {
                int(row["word_id"]): _to_content(row)
                for row in conn.execute(
                    f"SELECT * FROM word_content WHERE word_id IN ({marks})", chunk
                )
            }
            localizations = (
                {
                    int(row["word_id"]): _to_localization(row)
                    for row in conn.execute(
                        f"SELECT * FROM word_localizations "
                        f"WHERE learner_language = ? AND word_id IN ({marks})",
                        [learner_language, *chunk],
                    )
                }
                if learner_language
                else {}
            )
            counts = {
                int(row["word_id"]): int(row["n"])
                for row in conn.execute(
                    f"SELECT word_id, COUNT(*) AS n FROM word_contexts "
                    f"WHERE word_id IN ({marks}) GROUP BY word_id",
                    chunk,
                )
            }
            for word_id in chunk:
                found[word_id] = content_status(
                    contents.get(word_id),
                    localizations.get(word_id),
                    counts.get(word_id, 0),
                    learner_language,
                )
        return found

    def learner_languages(self) -> list[str]:
        """Every learner language any content exists for."""
        rows = self._db.connection.execute(
            "SELECT learner_language FROM word_localizations "
            "UNION SELECT learner_language FROM context_translations ORDER BY 1"
        ).fetchall()
        return [row[0] for row in rows]

    def all_content(self) -> list[WordContent]:
        rows = self._db.connection.execute("SELECT * FROM word_content ORDER BY word_id")
        return [_to_content(row) for row in rows]

    def all_localizations(self) -> list[WordLocalization]:
        rows = self._db.connection.execute(
            "SELECT * FROM word_localizations ORDER BY word_id, learner_language"
        )
        return [_to_localization(row) for row in rows]

    def all_contexts(self) -> list[WordContext]:
        rows = self._db.connection.execute("SELECT * FROM word_contexts ORDER BY word_id, id")
        return [_to_context(row) for row in rows]

    # -- writing -------------------------------------------------------------

    def save_content(self, content: WordContent) -> None:
        """Insert or replace one word's target-language content as given."""
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO word_content
                        (word_id, pattern, collocations, register, related, depth_hint,
                         source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(word_id) DO UPDATE SET
                        pattern = excluded.pattern,
                        collocations = excluded.collocations,
                        register = excluded.register,
                        related = excluded.related,
                        depth_hint = excluded.depth_hint,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(content.word_id),
                        content.pattern,
                        _json_or_none(list(content.collocations)),
                        content.register,
                        _json_or_none(
                            [{"word": r.word, "relation": r.relation} for r in content.related]
                        ),
                        content.depth_hint.value if content.depth_hint else None,
                        content.source,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError("The word's content could not be saved.") from exc

    def save_localization(self, localization: WordLocalization) -> None:
        """Insert or replace one word's content in one learner language."""
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO word_localizations
                        (word_id, learner_language, core_meaning, nuance, usage_note,
                         encoding_type, encoding_cue, notes, source, content_version,
                         updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(word_id, learner_language) DO UPDATE SET
                        core_meaning = excluded.core_meaning,
                        nuance = excluded.nuance,
                        usage_note = excluded.usage_note,
                        encoding_type = excluded.encoding_type,
                        encoding_cue = excluded.encoding_cue,
                        notes = excluded.notes,
                        source = excluded.source,
                        content_version = word_localizations.content_version + 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(localization.word_id),
                        localization.learner_language,
                        localization.core_meaning,
                        localization.nuance,
                        localization.usage_note,
                        localization.encoding_type.value if localization.encoding_type else None,
                        localization.encoding_cue,
                        localization.notes,
                        localization.source,
                        localization.content_version,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError("The word's explanation could not be saved.") from exc

    def add_contexts(self, contexts: Sequence[WordContext]) -> list[int]:
        """Add contexts; returns their new ids. Duplicates are the caller's concern."""
        ids: list[int] = []
        try:
            with self._db.transaction() as conn:
                for context in contexts:
                    cursor = conn.execute(
                        "INSERT INTO word_contexts (word_id, kind, text, source) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            int(context.word_id),
                            ContextKind(context.kind).value,
                            context.text,
                            context.source,
                        ),
                    )
                    ids.append(int(cursor.lastrowid))
        except sqlite3.Error as exc:
            raise StorageError("The contexts could not be saved.") from exc
        return ids

    def save_translation(
        self, context_id: int, learner_language: str, text: str, source: str | None = None
    ) -> None:
        """Insert or replace a context's translation into one learner language."""
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO context_translations
                        (context_id, learner_language, text, source, updated_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(context_id, learner_language) DO UPDATE SET
                        text = excluded.text,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (int(context_id), learner_language, text, source),
                )
        except sqlite3.Error as exc:
            raise StorageError("The translation could not be saved.") from exc

    def delete_contexts(self, context_ids: Sequence[int]) -> int:
        ids = [int(context_id) for context_id in context_ids]
        if not ids:
            return 0
        with self._db.transaction() as conn:
            marks = ",".join("?" * len(ids))
            return conn.execute(f"DELETE FROM word_contexts WHERE id IN ({marks})", ids).rowcount


def _json_or_none(items: list) -> str | None:
    return json.dumps(items, ensure_ascii=False) if items else None


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
        pattern=row["pattern"],
        collocations=tuple(str(item) for item in _json_list(row["collocations"]) if item),
        register=row["register"],
        related=related,
        depth_hint=DepthHint(row["depth_hint"]) if row["depth_hint"] else None,
        source=row["source"],
        updated_at=row["updated_at"],
    )


def _to_localization(row: sqlite3.Row) -> WordLocalization:
    return WordLocalization(
        word_id=int(row["word_id"]),
        learner_language=row["learner_language"],
        core_meaning=row["core_meaning"],
        nuance=row["nuance"],
        usage_note=row["usage_note"],
        encoding_type=EncodingType(row["encoding_type"]) if row["encoding_type"] else None,
        encoding_cue=row["encoding_cue"],
        notes=row["notes"],
        source=row["source"],
        content_version=int(row["content_version"]),
        updated_at=row["updated_at"],
    )


def _to_context(row: sqlite3.Row) -> WordContext:
    return WordContext(
        id=int(row["id"]),
        word_id=int(row["word_id"]),
        kind=ContextKind(row["kind"]),
        text=row["text"],
        source=row["source"],
    )
