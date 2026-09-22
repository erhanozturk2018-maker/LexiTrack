"""Schema versioning and upgrades.

A database file holds a user's review progress, so an upgrade must never lose
it and must never require the user to start again. The rules this module
follows:

* The version lives in ``schema_version``. A database is upgraded one version
  at a time, in order, until it reaches :data:`SCHEMA_VERSION`.
* Before the first step, the file is copied next to itself with SQLite's online
  backup API (which is safe with WAL journalling). If anything goes wrong the
  user still has their data.
* Each step runs in one transaction with foreign keys switched off (required
  for SQLite's table-rebuild procedure), then checks foreign-key integrity and
  row counts before committing. A failed check rolls the step back.
* A database from a *newer* LexiTrack is refused rather than guessed at.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..core.errors import StorageError
from ..models.settings import DEFAULT_SETTINGS

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4


class MigrationError(StorageError):
    default_message = (
        "Your vocabulary database could not be upgraded to this version of "
        "LexiTrack. Nothing was changed, and a backup copy was kept."
    )


# -- entry point -------------------------------------------------------------


def read_version(connection: sqlite3.Connection) -> int | None:
    """Return the schema version, or ``None`` for an empty database."""
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "schema_version" in tables:
        row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        if row and row[0] is not None:
            return int(row[0])
    # Version 1 always wrote schema_version, but a database that has vocabulary
    # tables and somehow lost that row is still recognisably version 1.
    if "words" in tables:
        return 1
    return None


def migrate(connection: sqlite3.Connection, path: Path, from_version: int) -> Path | None:
    """Upgrade ``connection`` from ``from_version`` to :data:`SCHEMA_VERSION`.

    Returns the path of the backup that was taken, or ``None`` for an
    in-memory database.
    """
    if from_version > SCHEMA_VERSION:
        raise StorageError(
            "This vocabulary database was created by a newer version of LexiTrack. "
            "Please update LexiTrack to open it."
        )
    if from_version == SCHEMA_VERSION:
        return None

    backup = _backup(connection, path, from_version)

    version = from_version
    while version < SCHEMA_VERSION:
        step = _STEPS.get(version)
        if step is None:  # pragma: no cover - would be a programming error
            raise MigrationError(f"No migration exists from schema version {version}.")
        log.info("Migrating %s from schema version %d to %d", path, version, version + 1)
        _run_step(connection, step, version + 1)
        version += 1

    log.info("Database %s is now at schema version %d", path, version)
    return backup


# -- mechanics -------------------------------------------------------------


def _backup(connection: sqlite3.Connection, path: Path, version: int) -> Path | None:
    if str(path) == ":memory:":
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.v{version}-backup-{stamp}{path.suffix}")
    try:
        destination = sqlite3.connect(target)
        try:
            connection.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as exc:
        log.exception("Could not back up %s before migrating", path)
        raise MigrationError(
            "Your vocabulary database could not be backed up, so it was not upgraded. "
            "Nothing was changed."
        ) from exc
    log.info("Backed up schema version %d database to %s", version, target)
    return target


def _run_step(
    connection: sqlite3.Connection,
    step: Callable[[sqlite3.Connection], None],
    new_version: int,
) -> None:
    # Foreign keys cannot be toggled inside a transaction, and must be off
    # while a referenced table is rebuilt.
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            step(connection)
            problems = connection.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise MigrationError(
                    f"Upgrade to schema version {new_version} left "
                    f"{len(problems)} broken references."
                )
            connection.execute("DELETE FROM schema_version")
            connection.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (new_version,)
            )
        except Exception as exc:
            connection.execute("ROLLBACK")
            log.exception("Migration to schema version %d failed", new_version)
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError() from exc
        connection.execute("COMMIT")
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


# -- version 1 -> 2 ----------------------------------------------------------
#
# Version 1 had a single implicit vocabulary. Version 2 adds:
#   * language, and (language, normalized_word) identity on words
#   * lists and list_words
#
# Existing words get a language only where the data justifies one: a word
# extracted by the Oxford parser is English. Words that only ever came from a
# generic PDF get "und", because version 1 never knew their language.
#
# Every existing source becomes a list with the same name and the same words in
# the same order, so the user opens the upgraded app and finds "Oxford 3000"
# and "Oxford 5000" exactly where their progress left them.


def _migrate_1_to_2(connection: sqlite3.Connection) -> None:
    words_before = _count(connection, "words")
    states_before = _count(connection, "user_word_state")

    # SQLite cannot change a UNIQUE constraint in place, so words is rebuilt.
    # Ids are copied explicitly: every other table refers to them.
    connection.execute(
        """
        CREATE TABLE words_v2 (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            language        TEXT    NOT NULL DEFAULT 'und',
            normalized_word TEXT    NOT NULL,
            display_word    TEXT    NOT NULL,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (language, normalized_word)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO words_v2 (id, language, normalized_word, display_word, created_at)
        SELECT
            w.id,
            CASE WHEN EXISTS (
                SELECT 1 FROM word_sources ws
                JOIN sources s ON s.id = ws.source_id
                WHERE ws.word_id = w.id AND s.parser_type = 'oxford'
            ) THEN 'en' ELSE 'und' END,
            w.normalized_word,
            w.display_word,
            w.created_at
        FROM words w
        """
    )
    connection.execute("DROP TABLE words")
    connection.execute("ALTER TABLE words_v2 RENAME TO words")
    connection.execute("CREATE INDEX idx_words_normalized ON words(normalized_word)")

    connection.execute(
        """
        CREATE TABLE lists (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL COLLATE NOCASE UNIQUE,
            language    TEXT    NOT NULL DEFAULT 'und',
            description TEXT,
            kind        TEXT    NOT NULL DEFAULT 'custom'
                        CHECK (kind IN ('custom', 'imported')),
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE list_words (
            list_id  INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
            word_id  INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            added_at TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (list_id, word_id)
        )
        """
    )
    connection.execute("CREATE INDEX idx_list_words_position ON list_words(list_id, position)")
    connection.execute("CREATE INDEX idx_list_words_word ON list_words(word_id)")

    used_names: set[str] = set()
    sources = connection.execute(
        "SELECT id, name, parser_type, created_at FROM sources ORDER BY id"
    ).fetchall()
    for source_id, name, parser_type, created_at in sources:
        list_name = _unique_name(name, used_names)
        language = "en" if parser_type == "oxford" else "und"
        cursor = connection.execute(
            """
            INSERT INTO lists (name, language, description, kind, created_at, updated_at)
            VALUES (?, ?, NULL, 'imported', ?, ?)
            """,
            (list_name, language, created_at, created_at),
        )
        connection.execute(
            """
            INSERT INTO list_words (list_id, word_id, position, added_at)
            SELECT ?, ws.word_id, ROW_NUMBER() OVER (ORDER BY ws.word_id), ws.created_at
            FROM word_sources ws
            WHERE ws.source_id = ?
            """,
            (cursor.lastrowid, source_id),
        )

    # Words that belonged to no source cannot occur in version 1, but if one
    # did it would vanish from every view. Keep it reachable.
    orphans = connection.execute(
        "SELECT COUNT(*) FROM words w WHERE NOT EXISTS "
        "(SELECT 1 FROM list_words lw WHERE lw.word_id = w.id)"
    ).fetchone()[0]
    if orphans:
        cursor = connection.execute(
            "INSERT INTO lists (name, language, kind) VALUES (?, 'und', 'imported')",
            (_unique_name("Unsorted Words", used_names),),
        )
        connection.execute(
            """
            INSERT INTO list_words (list_id, word_id, position)
            SELECT ?, w.id, ROW_NUMBER() OVER (ORDER BY w.id)
            FROM words w
            WHERE NOT EXISTS (SELECT 1 FROM list_words lw WHERE lw.word_id = w.id)
            """,
            (cursor.lastrowid,),
        )

    if _count(connection, "words") != words_before:
        raise MigrationError("Upgrade changed the number of words; it was rolled back.")
    if _count(connection, "user_word_state") != states_before:
        raise MigrationError("Upgrade changed review progress; it was rolled back.")




# -- version 2 -> 3 ----------------------------------------------------------
#
# Version 3 adds the learning engine: study plans, SRS cards, review logs and
# the settings both the UI and the Telegram thread read. It changes nothing
# that already exists — no column is added to words, lists or user_word_state
# — so the upgrade is the DDL in learning.sql plus two conveniences:
#
#   * the settings table is seeded with its defaults, and
#   * the user's existing vocabulary becomes a study plan, so the app opens
#     with something to study instead of an empty plan editor.
#
# The plan is built from the largest existing list, not from all of them: a
# plan is a deliberate scope, and "everything at once" is rarely what someone
# means by "what am I studying now". Other lists are one click away.

_LEARNING_SCHEMA = Path(__file__).with_name("learning.sql")


def _migrate_2_to_3(connection: sqlite3.Connection) -> None:
    _run_sql(connection, _LEARNING_SCHEMA.read_text(encoding="utf-8"))
    seed_settings(connection)
    _create_initial_plan(connection)


def _run_sql(connection: sqlite3.Connection, script: str) -> None:
    """Execute a .sql file statement by statement, inside the open transaction.

    ``executescript`` commits whatever transaction is open before it runs, so
    it cannot be used here: the step must stay atomic and roll back as a unit.
    """
    statement = ""
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            statement = ""
    if statement.strip():
        connection.execute(statement)


def _create_initial_plan(connection: sqlite3.Connection) -> None:
    """Make the largest existing list into the active study plan."""
    row = connection.execute(
        """
        SELECT l.id, l.name, l.language, COUNT(lw.word_id) AS words
        FROM lists l
        LEFT JOIN list_words lw ON lw.list_id = l.id
        GROUP BY l.id
        ORDER BY words DESC, l.id
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return

    name = f"{row['name']} Study Plan"
    cursor = connection.execute(
        """
        INSERT INTO study_plans (name, language, description, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (
            name,
            row["language"],
            "Created when LexiTrack upgraded to version 0.3.",
        ),
    )
    plan_id = int(cursor.lastrowid)
    connection.execute(
        "INSERT INTO study_plan_lists (plan_id, list_id, position) VALUES (?, ?, 0)",
        (plan_id, row["id"]),
    )
    connection.execute(
        "UPDATE app_settings SET value = ?, updated_at = datetime('now') WHERE key = ?",
        (str(plan_id), "active_plan_id"),
    )
    log.info("Created study plan %r from list %r", name, row["name"])


def seed_settings(connection: sqlite3.Connection) -> None:
    """Insert any setting that is missing, leaving existing values alone.

    Called when a database is created and after every upgrade, so a setting
    added in a later version appears without its own migration step, and a
    value the user changed is never overwritten.
    """
    connection.executemany(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        list(DEFAULT_SETTINGS.items()),
    )

# -- version 3 -> 4 ----------------------------------------------------------
#
# Version 4 records every status change (word_status_events) and lets a plan
# draw on every list (study_plans.all_lists). Nothing existing changes.
#
# Words that became Known through the schedule before the table existed get
# one reconstructed event each, dated by the review whose stability first
# reached the mastery threshold. Words marked Known by hand left no trace and
# get no event: an invented date would be worse than an honest gap.

_PROGRESS_SCHEMA = Path(__file__).with_name("progress.sql")


def _migrate_3_to_4(connection: sqlite3.Connection) -> None:
    _run_sql(connection, _PROGRESS_SCHEMA.read_text(encoding="utf-8"))
    seed_settings(connection)
    reconstruct_mastery_events(connection)


def reconstruct_mastery_events(connection: sqlite3.Connection) -> int:
    """Infer a mastery event for Known words whose reviews crossed the threshold.

    Returns the number of events written. Safe to run twice: a word that
    already has a mastery event is skipped.
    """
    row = connection.execute(
        "SELECT value FROM app_settings WHERE key = 'mastery_stability_days'"
    ).fetchone()
    try:
        threshold = float(row[0]) if row else 21.0
    except (TypeError, ValueError):
        threshold = 21.0
    cursor = connection.execute(
        """
        INSERT INTO word_status_events
            (word_id, at, from_status, to_status, cause, plan_id, reconstructed)
        SELECT st.word_id,
               (SELECT r.reviewed_at FROM review_logs r
                 WHERE r.word_id = st.word_id AND r.stability_after >= ?
                 ORDER BY r.reviewed_at, r.id LIMIT 1),
               'unknown', 'known', 'mastery', c.origin_plan_id, 1
        FROM user_word_state st
        JOIN srs_cards c ON c.word_id = st.word_id
        WHERE st.status = 'known'
          AND EXISTS (SELECT 1 FROM review_logs r
                       WHERE r.word_id = st.word_id AND r.stability_after >= ?)
          AND NOT EXISTS (SELECT 1 FROM word_status_events e
                           WHERE e.word_id = st.word_id AND e.cause = 'mastery')
        """,
        (threshold, threshold),
    )
    return cursor.rowcount


_STEPS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
    3: _migrate_3_to_4,
}


# -- helpers ---------------------------------------------------------------


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _unique_name(name: str, used: set[str]) -> str:
    base = re.sub(r"\s+", " ", name or "").strip() or "Imported List"
    candidate = base
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{base} ({counter})"
        counter += 1
    used.add(candidate.casefold())
    return candidate
