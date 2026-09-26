"""Backups, the integrity check, the review log export and starting over."""

from __future__ import annotations

import csv
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.source import Source
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import (
    ListRepository,
    SessionRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.learning_service import LearningService
from lexitrack.services.maintenance import KEEP_BACKUPS, Maintenance
from lexitrack.services.vocabulary_service import VocabularyService

from .conftest import entry
from .flow_helpers import answer


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))


@pytest.fixture
def studied(database: Database, clock: FrozenClock) -> LearningService:
    """Thirty words, a plan, one day introduced and one day reviewed."""
    source = SourceRepository(database).upsert(Source(key="t", name="T", parser_type="generic"))
    names = [f"a{c}word" for c in "abcdefghijklmnopqrstuvwxyz"] + ["zeta", "yolk", "xray", "wax"]
    entries = [entry(n, definition=f"the {n}") for n in names]
    ids = list(WordRepository(database).add_entries(entries, source.id).word_ids)
    a_list = ListRepository(database).create("L")
    ListRepository(database).add_words(a_list.id, ids)
    StateRepository(database).set_status_many(ids, ReviewStatus.UNKNOWN)
    engine = LearningService(database, clock)
    engine.create_plan("P", list_ids=[a_list.id])
    engine.introduce()
    clock.advance_to_day_start(1)
    for index, item in enumerate(engine.review_queue()):
        answer(engine, item.word.id, Rating.AGAIN if index % 5 == 0 else Rating.GOOD)
    return engine


@pytest.fixture
def maintenance(database: Database, clock: FrozenClock, tmp_path: Path) -> Maintenance:
    return Maintenance(database, clock, directory=tmp_path / "backups")


class TestBackups:
    def test_a_backup_is_a_complete_readable_database(
        self, studied: LearningService, maintenance: Maintenance
    ) -> None:
        target = maintenance.backup()
        assert target is not None and target.exists()
        assert target.name == f"vocabulary-{maintenance._clock.today()}.db"
        copy = sqlite3.connect(target)
        try:
            assert copy.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 30
            assert copy.execute("SELECT COUNT(*) FROM review_logs").fetchone()[0] == 25
        finally:
            copy.close()

    def test_no_half_written_file_is_left_behind(
        self, studied: LearningService, maintenance: Maintenance
    ) -> None:
        maintenance.backup()
        assert not list(maintenance.directory.glob("*.partial"))

    def test_the_daily_pass_backs_up_once_a_day(
        self, studied: LearningService, maintenance: Maintenance, clock: FrozenClock
    ) -> None:
        assert maintenance.run_daily() is not None
        assert maintenance.run_daily() is None, "already done today"
        clock.advance_to_day_start(1)
        assert maintenance.run_daily() is not None
        assert len(maintenance.backups()) == 2

    def test_only_the_newest_backups_are_kept(
        self, studied: LearningService, maintenance: Maintenance, clock: FrozenClock
    ) -> None:
        for _ in range(KEEP_BACKUPS + 3):
            maintenance.backup()
            clock.advance_to_day_start(1)
        kept = maintenance.backups()
        assert len(kept) == KEEP_BACKUPS
        assert kept == sorted(kept, reverse=True)

    def test_other_files_in_the_folder_are_never_pruned(
        self, studied: LearningService, maintenance: Maintenance, clock: FrozenClock
    ) -> None:
        maintenance.directory.mkdir(parents=True)
        precious = maintenance.directory / "vocabulary.v2-backup-20260917.db"
        precious.write_bytes(b"x")
        for _ in range(KEEP_BACKUPS + 2):
            maintenance.backup()
            clock.advance_to_day_start(1)
        assert precious.exists()

    def test_a_damaged_database_is_not_backed_up(
        self, maintenance: Maintenance, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fourteen bad copies would push out the last good one."""
        from lexitrack.services import maintenance as module

        monkeypatch.setattr(
            Maintenance, "check_integrity", lambda self: module.IntegrityReport(False, "bad")
        )
        assert maintenance.backup() is None
        assert maintenance.backups() == []

    def test_the_daily_pass_prunes_old_telegram_keys(
        self, database: Database, maintenance: Maintenance
    ) -> None:
        sessions = SessionRepository(database)
        sessions.claim_update("old")
        database.connection.execute(
            "UPDATE telegram_updates SET handled_at = datetime('now', '-40 days')"
        )
        maintenance.run_daily()
        assert not sessions.was_handled("old")


def test_a_healthy_database_passes_its_check(maintenance: Maintenance) -> None:
    report = maintenance.check_integrity()
    assert report.ok is True
    assert report.detail == "ok"


def test_the_review_log_exports_every_answer(
    studied: LearningService, maintenance: Maintenance, tmp_path: Path
) -> None:
    target = tmp_path / "out" / "history.csv"
    assert maintenance.export_review_log(target) == 25
    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 25
    assert {row["answer"] for row in rows} == {"Again", "Good"}
    assert rows[0]["date"] == "2026-09-18"
    assert rows[0]["word"]
    assert float(rows[0]["interval_days"]) >= 1


class TestResetProgress:
    def test_reset_clears_statuses_schedule_and_history_together(
        self, studied: LearningService, database: Database
    ) -> None:
        service = VocabularyService(database)
        service.reset_progress()
        counts = {
            table: database.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("srs_cards", "review_logs", "review_sessions")
        }
        assert counts == {"srs_cards": 0, "review_logs": 0, "review_sessions": 0}
        assert service.get_progress().reviewed == 0

    def test_reset_keeps_words_lists_and_plans(
        self, studied: LearningService, database: Database
    ) -> None:
        VocabularyService(database).reset_progress()
        assert database.connection.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 30
        assert studied.active_plan() is not None

    def test_after_a_reset_the_plan_starts_again(
        self, studied: LearningService, database: Database
    ) -> None:
        VocabularyService(database).reset_progress()
        studied.save_settings({"new_words_include_not_reviewed": True})
        plan = studied.daily_plan()
        assert len(plan.new_words) == 25
        assert plan.due_count == 0
