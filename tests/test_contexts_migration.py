"""Schema 6 -> 7: a word is its definition and its contexts.

The teaching content of schemas 5 and 6 is dropped, contexts become
``id, word_id, text`` with their ``{{word}}`` markers removed, every answer
records its task and whether it was right, and the attempts of the old routes
are kept, their efforts carried over as the rating each one stood for.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lexitrack.database.connection import _SCHEMA_FILES, Database
from lexitrack.database.migrations import SCHEMA_VERSION, read_version


def build_v6_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    for schema_file in _SCHEMA_FILES:
        if schema_file.name == "contexts.sql":
            continue
        conn.executescript(schema_file.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO schema_version (version) VALUES (6)")
    conn.executemany(
        "INSERT INTO words (id, language, normalized_word, display_word) VALUES (?, 'en', ?, ?)",
        [(1, "reluctant", "reluctant"), (2, "cramped", "cramped")],
    )
    conn.execute(
        "INSERT INTO word_content (word_id, pattern, collocations) "
        "VALUES (1, 'reluctant to do sth', '[\"reluctant to admit\"]')"
    )
    conn.execute(
        "INSERT INTO word_localizations (word_id, learner_language, core_meaning) "
        "VALUES (1, 'tr', 'meaning')"
    )
    conn.executemany(
        "INSERT INTO word_contexts (id, word_id, kind, text) VALUES (?, ?, ?, ?)",
        [
            (10, 1, "sentence", "She was {{reluctant}} to leave."),
            (11, 1, "situation", "A {{ reluctant }} yes."),
            # The same sentence once its marker goes: kept once.
            (12, 1, "sentence", "She was reluctant to leave."),
            (13, 2, "sentence", "The room was {{cramped}}."),
        ],
    )
    conn.execute(
        "INSERT INTO context_translations (context_id, learner_language, text) "
        "VALUES (10, 'tr', 'translated')"
    )
    conn.execute(
        "INSERT INTO review_sessions (id, started_at, started_on) "
        "VALUES ('s1', '2026-09-20T10:00:00+00:00', '2026-09-20')"
    )
    conn.execute(
        "INSERT INTO review_logs (id, word_id, reviewed_at, reviewed_on, rating, "
        "memory_result, route_version) VALUES "
        "(1, 1, '2026-09-20T10:00:00+00:00', '2026-09-20', 3, 'RECALLED', 'v2')"
    )
    conn.executemany(
        "INSERT INTO learning_attempts (id, word_id, session_id, at, on_day, phase, role, "
        "task, level, context_id, novel_context, success, effort, review_log_id, depth) "
        "VALUES (?, ?, 's1', '2026-09-20T10:00:00+00:00', '2026-09-20', ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?)",
        [
            (1, 1, "review", "primary", "context_cloze", 3, 10, 1, 1, "instant", 1, None),
            (2, 1, "review", "probe", "choose_word", 1, None, 0, 0, None, 1, None),
            (3, 2, "introduction", "retrieval", "meaning_to_word", 2, None, 0, 1, "effortful",
             None, "short"),
        ],
    )
    conn.commit()
    conn.close()


def test_a_version_6_database_is_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    build_v6_database(path)
    db = Database(path)
    connection = db.connect()
    try:
        assert read_version(connection) == SCHEMA_VERSION == 7
        assert db.migration_backup is not None and db.migration_backup.exists()

        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        assert not tables & {"word_content", "word_localizations", "context_translations"}

        columns = [r[1] for r in connection.execute("PRAGMA table_info(word_contexts)")]
        assert columns == ["id", "word_id", "text"]
        contexts = connection.execute(
            "SELECT id, word_id, text FROM word_contexts ORDER BY id"
        ).fetchall()
        assert [tuple(r) for r in contexts] == [
            (10, 1, "She was reluctant to leave."),
            (11, 1, "A reluctant yes."),
            (13, 2, "The room was cramped."),
        ]

        attempts = connection.execute(
            "SELECT id, task, context_id, correct, effort, review_log_id "
            "FROM learning_attempts ORDER BY id"
        ).fetchall()
        assert [tuple(r) for r in attempts] == [
            (1, "context_cloze", 10, 1, "easy", 1),
            (2, "choose_word", None, 0, None, 1),
            (3, "meaning_to_word", None, 1, "hard", None),
        ]
        log = connection.execute("SELECT task, correct, rating FROM review_logs").fetchone()
        assert tuple(log) == (None, None, 3)
        settings = {r[0] for r in connection.execute("SELECT key FROM app_settings")}
        assert "learner_language" not in settings
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_the_upgraded_schema_matches_a_fresh_one(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    build_v6_database(path)
    upgraded = Database(path)
    fresh = Database(tmp_path / "fresh.db")

    def shape(db: Database) -> dict[str, str]:
        rows = db.connect().execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {name: " ".join(sql.split()) for name, sql in rows}

    try:
        assert shape(upgraded) == shape(fresh)
    finally:
        upgraded.close()
        fresh.close()


def test_contexts_and_answers_go_with_a_deleted_word(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    build_v6_database(path)
    db = Database(path)
    connection = db.connect()
    try:
        connection.execute("DELETE FROM words WHERE id = 1")
        for table in ("word_contexts", "learning_attempts", "review_logs"):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE word_id = 1"
            ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()
