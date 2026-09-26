"""The portable file: everything that is yours, in one file you can keep.

A ``.lexitrack`` file is a ZIP of plain JSON, readable by anything and meant
to outlive this program:

* ``manifest.json`` — the format, its version, the database schema version,
  the app version, when it was made, and how many rows each table holds;
* ``tables/<table>.json`` — one per table: its column names and its rows, in
  the database's own form (ids kept, so every reference stays valid).

It holds the vocabulary, lists, statuses and their history, the study plans,
the cards and every review, the learning attempts, the contexts, and the
settings. It leaves out what belongs to one machine
— the Telegram chat and update bookkeeping, runtime state — and nothing
secret is in the database to begin with (the bot token lives in a file of
its own).

A PDF or CSV export is for reading; this is the backup.

**Restoring** replaces everything in those tables with the file's contents,
in one transaction, after a copy of the current database is saved: it either
all happens or none of it does. A file written by a different schema version
is refused rather than guessed at.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .. import __version__
from ..core.errors import InvalidFileError, StorageError
from ..database.connection import Database
from ..database.migrations import SCHEMA_VERSION

log = logging.getLogger(__name__)

FORMAT = "lexitrack-backup"
FORMAT_VERSION = 1
SUFFIX = ".lexitrack"

#: The tables written, in an order where every table comes after those it
#: refers to, so rows can be inserted top to bottom.
TABLES: tuple[str, ...] = (
    "sources",
    "words",
    "word_sources",
    "lists",
    "list_words",
    "user_word_state",
    "word_status_events",
    "study_plans",
    "study_plan_lists",
    "srs_cards",
    "review_sessions",
    "review_logs",
    "word_contexts",
    "learning_attempts",
    "app_settings",
)
#: Left out on purpose: kept per machine, or recreated as needed.
EXCLUDED: tuple[str, ...] = ("schema_version", "telegram_updates", "runtime_state")


@dataclass(frozen=True, slots=True)
class PortableSummary:
    path: Path
    schema_version: int
    app_version: str
    created_at: str
    counts: dict[str, int]

    @property
    def words(self) -> int:
        return self.counts.get("words", 0)

    @property
    def reviews(self) -> int:
        return self.counts.get("review_logs", 0)


def export(database: Database, path: Path | str) -> PortableSummary:
    """Write every table in :data:`TABLES` to ``path``, a ``.lexitrack`` file."""
    target = Path(path)
    if target.suffix != SUFFIX:
        target = target.with_suffix(SUFFIX)
    partial = target.with_name(target.name + ".partial")
    counts: dict[str, int] = {}
    created = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with database.lock, zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            connection = database.connection
            for table in TABLES:
                cursor = connection.execute(f"SELECT * FROM {table}")
                columns = [column[0] for column in cursor.description]
                rows = [list(row) for row in cursor.fetchall()]
                counts[table] = len(rows)
                archive.writestr(
                    f"tables/{table}.json",
                    json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False),
                )
            manifest = {
                "format": FORMAT,
                "format_version": FORMAT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "app_version": __version__,
                "created_at": created,
                "tables": counts,
                "excluded": list(EXCLUDED),
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        # Only a finished file gets the real name.
        partial.replace(target)
    except (OSError, sqlite3.Error) as exc:
        partial.unlink(missing_ok=True)
        raise StorageError(f"The portable file could not be written: {exc}") from exc
    log.info("Exported everything to %s (%s)", target, counts)
    return PortableSummary(target, SCHEMA_VERSION, __version__, created, counts)


def read(path: Path | str) -> PortableSummary:
    """Check a ``.lexitrack`` file without restoring it."""
    source = Path(path)
    manifest, tables = _open(source)
    return PortableSummary(
        source,
        int(manifest["schema_version"]),
        str(manifest.get("app_version", "")),
        str(manifest.get("created_at", "")),
        {name: len(data["rows"]) for name, data in tables.items()},
    )


def restore(database: Database, path: Path | str) -> PortableSummary:
    """Replace every table in :data:`TABLES` with the file's rows, atomically.

    The caller saves a copy of the current database first; see
    :meth:`Maintenance.backup`. Raises :class:`InvalidFileError` for a file
    that is not a LexiTrack backup, is damaged, or was written by another
    schema version, and :class:`StorageError` if the rows do not fit.
    """
    source = Path(path)
    manifest, tables = _open(source)
    connection = database.connection
    try:
        with database.transaction():
            connection.execute("PRAGMA defer_foreign_keys = ON")
            for table in reversed(TABLES):
                connection.execute(f"DELETE FROM {table}")
            for table in TABLES:
                data = tables[table]
                existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                columns = [c for c in data["columns"] if c in existing]
                if len(columns) != len(data["columns"]):
                    raise InvalidFileError(f"The file's {table} table does not match this version.")
                marks = ",".join("?" * len(columns))
                names = ",".join(columns)
                connection.executemany(
                    f"INSERT INTO {table} ({names}) VALUES ({marks})", data["rows"]
                )
            problems = connection.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise StorageError(
                    f"The file's rows do not fit together ({len(problems)} broken references)."
                )
            for table in TABLES:
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if count != len(tables[table]["rows"]):
                    raise StorageError(f"{table} did not restore completely.")
    except sqlite3.Error as exc:
        raise StorageError(f"The file could not be restored: {exc}") from exc
    log.info("Restored everything from %s", source)
    return PortableSummary(
        source,
        int(manifest["schema_version"]),
        str(manifest.get("app_version", "")),
        str(manifest.get("created_at", "")),
        {name: len(data["rows"]) for name, data in tables.items()},
    )


def _open(source: Path) -> tuple[dict, dict[str, dict]]:
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != FORMAT:
                raise InvalidFileError(f"{source.name} is not a LexiTrack backup.")
            if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
                raise InvalidFileError(
                    f"{source.name} was written by a newer LexiTrack. Update LexiTrack first."
                )
            version = int(manifest.get("schema_version", 0))
            if version != SCHEMA_VERSION:
                newer = version > SCHEMA_VERSION
                raise InvalidFileError(
                    f"{source.name} was written by "
                    + ("a newer" if newer else "an older")
                    + " LexiTrack (data version "
                    + f"{version}, this one reads {SCHEMA_VERSION}). "
                    + ("Update LexiTrack first." if newer else
                       "Restore it with that version, or restore a daily backup instead.")
                )
            tables = {}
            for table in TABLES:
                data = json.loads(archive.read(f"tables/{table}.json"))
                if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
                    raise InvalidFileError(f"{source.name}: {table} is damaged.")
                expected = manifest.get("tables", {}).get(table)
                if expected is not None and int(expected) != len(data["rows"]):
                    raise InvalidFileError(f"{source.name}: {table} is incomplete.")
                tables[table] = data
    except InvalidFileError:
        raise
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise InvalidFileError(f"{source.name} could not be read as a LexiTrack backup.") from exc
    return manifest, tables
