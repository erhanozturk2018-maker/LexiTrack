"""Backups you can keep and restore (services/portable.py, services/maintenance.py).

A restore replaces everything or nothing, keeps a copy of what it replaced,
and refuses a file it cannot read faithfully.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.core.errors import InvalidFileError, StorageError
from lexitrack.database.connection import Database
from lexitrack.database.migrations import SCHEMA_VERSION
from lexitrack.models.content import WordContent, WordContext, WordLocalization
from lexitrack.models.srs import Rating
from lexitrack.repositories import ContentRepository
from lexitrack.services import portable
from lexitrack.services.learning_service import LearningService
from lexitrack.services.maintenance import Maintenance

from .test_learning_service import clock, engine  # noqa: F401 - fixtures
from .test_localization_migration import build_v5_database


def _counts(database: Database) -> dict[str, int]:
    return {
        table: database.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in portable.TABLES
    }


@pytest.fixture
def studied(engine: LearningService, clock: FrozenClock, database: Database):  # noqa: F811
    """A few days of real use: cards, answers, attempts, content in two languages."""
    ids = [word.id for word in engine.introduce().introduced]
    clock.advance_to_day_start(1)
    for word_id in ids[:10]:
        engine.answer(word_id, Rating.GOOD)
    repo = ContentRepository(database)
    repo.save_content(WordContent(word_id=ids[0], pattern="p", collocations=("a b",)))
    repo.save_localization(WordLocalization(word_id=ids[0], learner_language="de",
                                            core_meaning="Bedeutung"))
    repo.save_localization(WordLocalization(word_id=ids[0], learner_language="es",
                                            core_meaning="significado"))
    (context_id,) = repo.add_contexts([WordContext(word_id=ids[0], text="A {{word}}.")])
    repo.save_translation(context_id, "de", "Ein Wort.")
    return ids


def test_everything_comes_back_as_it_was(
    database: Database, studied, tmp_path: Path, engine: LearningService  # noqa: F811
) -> None:
    before = _counts(database)
    assert before["review_logs"] == 10 and before["learning_attempts"] == 10
    summary = portable.export(database, tmp_path / "all")
    assert summary.path.suffix == ".lexitrack" and summary.words == before["words"]

    with zipfile.ZipFile(summary.path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["tables"] == before
    assert "runtime_state" in manifest["excluded"]
    assert f"tables/{portable.TABLES[0]}.json" in names

    # Lose most of it.
    database.connection.execute("DELETE FROM learning_attempts")
    database.connection.execute("DELETE FROM review_logs")
    database.connection.execute("DELETE FROM word_localizations")
    database.connection.execute("DELETE FROM app_settings")

    portable.restore(database, summary.path)
    assert _counts(database) == before
    teaching = ContentRepository(database).teaching(studied[0], "de")
    assert teaching.core_meaning == "Bedeutung"
    assert teaching.translation(teaching.contexts[0]) == "Ein Wort."
    assert database.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert engine.refresh_settings().new_words_per_day == 25


def test_a_file_from_another_version_changes_nothing(
    database: Database, studied, tmp_path: Path  # noqa: F811
) -> None:
    summary = portable.export(database, tmp_path / "all.lexitrack")
    altered = tmp_path / "other.lexitrack"
    with zipfile.ZipFile(summary.path) as source, zipfile.ZipFile(altered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(data)
                manifest["schema_version"] = SCHEMA_VERSION + 1
                data = json.dumps(manifest).encode()
            target.writestr(name, data)
    before = _counts(database)
    with pytest.raises(InvalidFileError, match="newer"):
        portable.restore(database, altered)
    assert _counts(database) == before


def test_a_damaged_or_foreign_file_is_refused(tmp_path: Path, database: Database) -> None:
    junk = tmp_path / "junk.lexitrack"
    junk.write_text("not a zip", encoding="utf-8")
    with pytest.raises(InvalidFileError):
        portable.read(junk)
    other = tmp_path / "other.lexitrack"
    with zipfile.ZipFile(other, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "something-else"}))
    with pytest.raises(InvalidFileError, match="not a LexiTrack backup"):
        portable.read(other)


def test_a_restore_that_does_not_fit_rolls_back(
    database: Database, studied, tmp_path: Path  # noqa: F811
) -> None:
    summary = portable.export(database, tmp_path / "all.lexitrack")
    broken = tmp_path / "broken.lexitrack"
    with zipfile.ZipFile(summary.path) as source, zipfile.ZipFile(broken, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "tables/words.json":
                table = json.loads(data)
                table["rows"] = table["rows"][:5]  # cards now point at missing words
                data = json.dumps(table).encode()
            if name == "manifest.json":
                manifest = json.loads(data)
                manifest["tables"]["words"] = 5
                data = json.dumps(manifest).encode()
            target.writestr(name, data)
    before = _counts(database)
    with pytest.raises(StorageError, match="broken references"):
        portable.restore(database, broken)
    assert _counts(database) == before, "all or nothing"


def test_a_daily_backup_is_restored_after_a_safety_copy(
    database: Database, studied, tmp_path: Path, clock: FrozenClock  # noqa: F811
) -> None:
    maintenance = Maintenance(database, clock, directory=tmp_path / "backups")
    backup = maintenance.backup()
    before = _counts(database)
    database.connection.execute("DELETE FROM review_logs")
    safety = maintenance.restore_backup(backup)
    assert _counts(database) == before
    assert safety.exists() and safety in maintenance.safety_copies()
    assert safety not in maintenance.backups(), "never rotated away with the daily ones"


def test_an_older_backup_is_upgraded_as_it_is_restored(
    database: Database, tmp_path: Path, clock: FrozenClock  # noqa: F811
) -> None:
    old = tmp_path / "old.db"
    build_v5_database(old)
    maintenance = Maintenance(database, clock, directory=tmp_path / "backups")
    maintenance.restore_backup(old)
    version = database.connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
    assert version[0] == SCHEMA_VERSION
    teaching = ContentRepository(database).teaching(1, "tr")
    assert teaching.core_meaning == "meaning-in-tr"


def test_a_damaged_backup_changes_nothing(
    database: Database, tmp_path: Path, clock: FrozenClock  # noqa: F811
) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    maintenance = Maintenance(database, clock, directory=tmp_path / "backups")
    with pytest.raises(StorageError):
        maintenance.restore_backup(bad)
    assert maintenance.safety_copies() == []


def test_attempts_export_as_csv(
    database: Database, studied, tmp_path: Path, clock: FrozenClock  # noqa: F811
) -> None:
    maintenance = Maintenance(database, clock)
    assert maintenance.export_attempts(tmp_path / "a.csv") == 10
    header = (tmp_path / "a.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.startswith("date,time_utc,word,phase,role,task,level,success")
    assert maintenance.export_review_log(tmp_path / "r.csv") == 10
    header = (tmp_path / "r.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.endswith("memory_result,route")
