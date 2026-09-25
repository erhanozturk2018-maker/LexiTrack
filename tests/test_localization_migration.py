"""Schema 5 -> 6: teaching content split into target and learner languages.

Schema 5 kept one learner language's content (written for Turkish-speaking
learners) in the shared rows. The upgrade must move every piece of it into
that language's localization, keep what belongs to the target language where
it is, and leave the rest of the database untouched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lexitrack.database.connection import _SCHEMA_FILES, Database
from lexitrack.database.migrations import SCHEMA_VERSION, read_version
from lexitrack.repositories import ContentRepository


def build_v5_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    for schema_file in _SCHEMA_FILES:
        if schema_file.name == "localization.sql":
            continue
        conn.executescript(schema_file.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    conn.executemany(
        "INSERT INTO words (id, language, normalized_word, display_word) VALUES (?, 'en', ?, ?)",
        [(1, "reluctant", "reluctant"), (2, "cramped", "cramped"), (3, "apple", "apple")],
    )
    conn.execute(
        """INSERT INTO word_content (word_id, core_meaning_tr, nuance, pattern, collocations,
               encoding_type, encoding_cue, depth_hint, source)
           VALUES (1, 'meaning-in-tr', 'nuance-in-tr', 'reluctant to do sth',
                   '["reluctant to admit"]', 'CONTRAST', 'cue-in-tr', 'deep', 'batch_001')"""
    )
    # Target-language content only: nothing to move.
    conn.execute(
        "INSERT INTO word_content (word_id, pattern, source) VALUES (2, 'so cramped that', 'b1')"
    )
    conn.executemany(
        "INSERT INTO word_contexts (id, word_id, text, translation_tr, source) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (10, 1, "She was {{reluctant}} to leave.", "sentence-in-tr", "batch_001"),
            (11, 1, "A {{reluctant}} yes.", None, "batch_001"),
        ],
    )
    conn.commit()
    conn.close()


def test_schema_5_content_moves_to_the_turkish_localization(tmp_path: Path) -> None:
    path = tmp_path / "v5.db"
    build_v5_database(path)
    db = Database(path)
    db.connect()
    try:
        assert read_version(db.connection) == SCHEMA_VERSION == 6
        assert db.migration_backup is not None and db.migration_backup.exists()
        repo = ContentRepository(db)

        teaching = repo.teaching(1, "tr")
        assert teaching.core_meaning == "meaning-in-tr"
        localization = teaching.localization
        assert localization.nuance == "nuance-in-tr"
        assert localization.encoding_type.value == "CONTRAST"
        assert localization.encoding_cue == "cue-in-tr"
        assert localization.source == "batch_001"
        # Target-language content stays shared, where it was.
        assert teaching.content.pattern == "reluctant to do sth"
        assert teaching.content.collocations == ("reluctant to admit",)
        assert teaching.content.depth_hint.value == "deep"
        assert [c.id for c in teaching.contexts] == [10, 11]
        assert teaching.translations == {10: "sentence-in-tr"}

        assert repo.localizations(2) == {}, "nothing learner-specific to move"
        assert repo.content(2).pattern == "so cramped that"
        assert repo.learner_languages() == ["tr"]

        columns = {row[1] for row in db.connection.execute("PRAGMA table_info(word_content)")}
        assert not columns & {"core_meaning_tr", "nuance", "encoding_type", "encoding_cue"}
        columns = {row[1] for row in db.connection.execute("PRAGMA table_info(word_contexts)")}
        assert "translation_tr" not in columns
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        words = db.connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        assert words == 3
    finally:
        db.close()


def test_no_column_or_table_names_a_language(tmp_path: Path) -> None:
    """The schema itself knows no language: a code is data, never a column."""
    db = Database(tmp_path / "fresh.db")
    db.connect()
    try:
        names = []
        for (table,) in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall():
            names.append(table)
            names += [row[1] for row in db.connection.execute(f"PRAGMA table_info({table})")]
        assert not [name for name in names if name.endswith(("_tr", "_de", "_en", "_es"))]
        assert not [name for name in names if "turkish" in name.lower()]
    finally:
        db.close()
