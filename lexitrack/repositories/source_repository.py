"""Persistence for imported documents."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..core.errors import StorageError
from ..database.connection import Database
from ..models.source import Source


class SourceRepository:
    """Reads and writes rows in ``sources``."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, source: Source) -> Source:
        """Insert ``source``, or return the existing row with the same key.

        Re-importing a document must not create a second source, so ``key`` is
        the stable identity here. The recorded file path is refreshed because
        the same document may be imported from a different folder.
        """
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO sources (key, name, parser_type, file_path)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        name = excluded.name,
                        parser_type = excluded.parser_type,
                        file_path = COALESCE(excluded.file_path, sources.file_path)
                    """,
                    (source.key, source.name, source.parser_type, source.file_path),
                )
        except sqlite3.Error as exc:
            raise StorageError("The document could not be registered.") from exc

        stored = self.get_by_key(source.key)
        if stored is None:  # pragma: no cover - defensive
            raise StorageError("The document could not be registered.")
        return stored

    def get_by_key(self, key: str) -> Source | None:
        row = self._db.connection.execute(
            f"SELECT {_COLUMNS} FROM sources s WHERE s.key = ?", (key,)
        ).fetchone()
        return _row_to_source(row) if row else None

    def list_all(self) -> list[Source]:
        rows = self._db.connection.execute(
            f"SELECT {_COLUMNS} FROM sources s ORDER BY s.created_at, s.id"
        ).fetchall()
        return [_row_to_source(row) for row in rows]

    def count(self) -> int:
        row = self._db.connection.execute("SELECT COUNT(*) AS n FROM sources").fetchone()
        return int(row["n"])


_COLUMNS = """
    s.id, s.key, s.name, s.parser_type, s.file_path, s.created_at,
    (SELECT COUNT(*) FROM word_sources ws WHERE ws.source_id = s.id) AS word_count
"""


def _row_to_source(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        key=row["key"],
        name=row["name"],
        parser_type=row["parser_type"],
        file_path=row["file_path"],
        created_at=_parse_timestamp(row["created_at"]),
        word_count=row["word_count"],
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - defensive
        return None
