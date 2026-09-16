"""Schema version 1 -> 2 migration.

Users already have version 1 databases full of review progress. These tests
build a genuine version 1 file from a frozen copy of that schema, upgrade it,
and check that nothing the user cares about changed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lexitrack.core.errors import StorageError
from lexitrack.database.connection import Database
from lexitrack.database.migrations import SCHEMA_VERSION, MigrationError, read_version

V1_SCHEMA = Path(__file__).parent / "fixtures" / "schema_v1.sql"


def build_v1_database(path: Path) -> None:
    """A version 1 database shaped like a real user's: two Oxford lists and a PDF.

    * ``ability`` is in both Oxford lists (one word, two sources).
    * ``zebra`` came only from a generic PDF, so its language is unknown.
    * Some words are known, some unknown, some not reviewed.
    """
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.executemany(
        "INSERT INTO sources (id, key, name, parser_type, file_path) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "oxford3000", "Oxford 3000", "oxford", "C:/pdfs/o3000.pdf"),
            (2, "oxford5000", "Oxford 5000", "oxford", "C:/pdfs/o5000.pdf"),
            (3, "generic:novel", "Novel", "generic", "C:/pdfs/novel.pdf"),
        ],
    )
    words = [(1, "abandon", "abandon"), (2, "ability", "ability"), (3, "absorb", "absorb"),
             (4, "zebra", "Zebra")]
    conn.executemany(
        "INSERT INTO words (id, normalized_word, display_word) VALUES (?, ?, ?)", words
    )
    conn.executemany(
        "INSERT INTO word_sources (word_id, source_id, part_of_speech, cefr_level) "
        "VALUES (?, ?, ?, ?)",
        [(1, 1, "verb", "B2"), (2, 1, "noun", "A2"), (2, 2, None, None),
         (3, 2, "verb", "B2"), (4, 3, None, None)],
    )
    conn.executemany(
        "INSERT INTO user_word_state (word_id, status, reviewed_at) VALUES (?, ?, ?)",
        [(1, "known", "2026-09-01T10:00:00"), (2, "unknown", "2026-09-01T10:00:05"),
         (3, "not_reviewed", None), (4, "known", "2026-09-02T09:00:00")],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def v1_path(tmp_path: Path) -> Path:
    path = tmp_path / "vocabulary.db"
    build_v1_database(path)
    return path


@pytest.fixture
def migrated(v1_path: Path):
    db = Database(v1_path)
    db.connect()
    yield db
    db.close()


def rows(db: Database, sql: str, params=()) -> list[tuple]:
    return [tuple(r) for r in db.connection.execute(sql, params)]


def test_a_version_1_database_is_upgraded_on_open(migrated: Database) -> None:
    assert read_version(migrated.connection) == SCHEMA_VERSION == 2


def test_a_backup_is_taken_before_upgrading(v1_path: Path, migrated: Database) -> None:
    backup = migrated.migration_backup
    assert backup is not None and backup.exists()
    assert backup.parent == v1_path.parent
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 4
    finally:
        conn.close()


def test_word_ids_and_spellings_survive(migrated: Database) -> None:
    assert rows(migrated, "SELECT id, normalized_word, display_word FROM words ORDER BY id") == [
        (1, "abandon", "abandon"),
        (2, "ability", "ability"),
        (3, "absorb", "absorb"),
        (4, "zebra", "Zebra"),
    ]


def test_review_status_and_timestamps_survive(migrated: Database) -> None:
    assert rows(
        migrated, "SELECT word_id, status, reviewed_at FROM user_word_state ORDER BY word_id"
    ) == [
        (1, "known", "2026-09-01T10:00:00"),
        (2, "unknown", "2026-09-01T10:00:05"),
        (3, "not_reviewed", None),
        (4, "known", "2026-09-02T09:00:00"),
    ]


def test_source_relationships_and_oxford_metadata_survive(migrated: Database) -> None:
    assert rows(
        migrated,
        "SELECT word_id, source_id, part_of_speech, cefr_level FROM word_sources "
        "ORDER BY word_id, source_id",
    ) == [
        (1, 1, "verb", "B2"),
        (2, 1, "noun", "A2"),
        (2, 2, None, None),
        (3, 2, "verb", "B2"),
        (4, 3, None, None),
    ]


def test_oxford_words_become_english_and_others_stay_undetermined(migrated: Database) -> None:
    """Only words the Oxford parser extracted are known to be English."""
    assert dict(rows(migrated, "SELECT normalized_word, language FROM words")) == {
        "abandon": "en",
        "ability": "en",
        "absorb": "en",
        "zebra": "und",
    }


def test_every_source_becomes_a_list_with_the_same_words_in_order(migrated: Database) -> None:
    lists = rows(migrated, "SELECT id, name, language, kind FROM lists ORDER BY id")
    assert lists == [
        (1, "Oxford 3000", "en", "imported"),
        (2, "Oxford 5000", "en", "imported"),
        (3, "Novel", "und", "imported"),
    ]
    assert rows(
        migrated, "SELECT list_id, word_id, position FROM list_words ORDER BY list_id, position"
    ) == [(1, 1, 1), (1, 2, 2), (2, 2, 1), (2, 3, 2), (3, 4, 1)]


def test_a_word_shared_by_two_sources_is_in_both_lists_but_is_one_word(
    migrated: Database,
) -> None:
    assert rows(migrated, "SELECT list_id FROM list_words WHERE word_id = 2 ORDER BY list_id") == [
        (1,),
        (2,),
    ]
    assert rows(migrated, "SELECT COUNT(*) FROM words WHERE normalized_word = 'ability'") == [(1,)]


def test_foreign_keys_are_intact_and_enforced(migrated: Database) -> None:
    assert migrated.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrated.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        migrated.connection.execute(
            "INSERT INTO list_words (list_id, word_id, position) VALUES (99, 1, 1)"
        )


def test_language_aware_identity_applies_after_upgrade(migrated: Database) -> None:
    migrated.connection.execute(
        "INSERT INTO words (language, normalized_word, display_word) VALUES ('de', 'ability', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated.connection.execute(
            "INSERT INTO words (language, normalized_word, display_word) "
            "VALUES ('en', 'ability', 'x')"
        )


def test_new_words_do_not_reuse_migrated_ids(migrated: Database) -> None:
    cursor = migrated.connection.execute(
        "INSERT INTO words (language, normalized_word, display_word) VALUES ('en', 'new', 'new')"
    )
    assert cursor.lastrowid > 4


def test_reopening_an_upgraded_database_does_nothing(v1_path: Path, migrated: Database) -> None:
    migrated.close()
    again = Database(v1_path)
    try:
        again.connect()
        assert again.migration_backup is None
        assert len(list(v1_path.parent.glob("*backup*"))) == 1
    finally:
        again.close()


def test_migrated_schema_matches_a_fresh_database(migrated: Database, tmp_path: Path) -> None:
    """Upgrading and creating from scratch must produce the same structure.

    If schema.sql and migrations.py ever drift apart, users who upgraded would
    silently run on a different schema from users who installed fresh.
    """
    fresh = Database(tmp_path / "fresh.db")
    fresh.connect()
    try:
        assert _structure(migrated.connection) == _structure(fresh.connection)
    finally:
        fresh.close()


def _structure(conn: sqlite3.Connection) -> dict[str, object]:
    tables = sorted(
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    shape: dict[str, object] = {}
    for table in tables:
        columns = [
            (c[1], c[2].upper(), c[3], c[4], c[5])
            for c in conn.execute(f"PRAGMA table_info({table})")
        ]
        foreign_keys = sorted(
            (f[2], f[3], f[4], f[6]) for f in conn.execute(f"PRAGMA foreign_key_list({table})")
        )
        indexes = sorted(
            (
                i[2],
                tuple(c[2] for c in conn.execute(f"PRAGMA index_info('{i[1]}')")),
            )
            for i in conn.execute(f"PRAGMA index_list({table})")
        )
        shape[table] = (columns, foreign_keys, indexes)
    shape["named_indexes"] = sorted(
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
        )
    )
    return shape


def test_a_database_from_a_newer_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    Database(path).connect()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = 99")
    conn.commit()
    conn.close()

    with pytest.raises(StorageError, match="newer version"):
        Database(path).connect()


def test_a_failed_migration_changes_nothing(v1_path: Path, monkeypatch) -> None:
    from lexitrack.database import migrations

    def broken(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM user_word_state")
        raise RuntimeError("simulated failure half-way through")

    monkeypatch.setitem(migrations._STEPS, 1, broken)

    with pytest.raises(MigrationError):
        Database(v1_path).connect()

    conn = sqlite3.connect(v1_path)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM user_word_state").fetchone()[0] == 4
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "lists" not in tables
    finally:
        conn.close()


def test_duplicate_source_names_get_distinct_list_names(tmp_path: Path) -> None:
    path = tmp_path / "dupes.db"
    build_v1_database(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO sources (id, key, name, parser_type) VALUES (4, 'generic:novel2', 'novel', "
        "'generic')"
    )
    conn.execute("INSERT INTO words (id, normalized_word, display_word) VALUES (5, 'yak', 'yak')")
    conn.execute("INSERT INTO word_sources (word_id, source_id) VALUES (5, 4)")
    conn.execute("INSERT INTO user_word_state (word_id) VALUES (5)")
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        db.connect()
        names = [r[0] for r in db.connection.execute("SELECT name FROM lists ORDER BY id")]
    finally:
        db.close()
    assert names == ["Oxford 3000", "Oxford 5000", "Novel", "novel (2)"]
