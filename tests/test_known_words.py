"""Known words keep their own pace (schema 8).

* The Known check: a word not yet Known is never sent further away than the
  gap Known is offered after, so the offer comes at the first chance.
* Known words aim at their own, lower target, and move to it at once.
* A Known word answered wrong is offered for learning again, never moved.
* Known words that come back at once are spread over the coming days.
* Every status change keeps the card in line (archived, resumed, moved).
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import _SCHEMA_FILES, Database
from lexitrack.database.migrations import SCHEMA_VERSION, read_version
from lexitrack.models.settings import Setting
from lexitrack.models.srs import CardState, Rating
from lexitrack.models.user_word_state import ReviewStatus, StatusCause
from lexitrack.repositories import CardRepository, StateRepository, WordRepository
from lexitrack.services import portable
from lexitrack.services.learning_service import LearningService
from lexitrack.services.vocabulary_service import VocabularyService

from .flow_helpers import answer
from .test_learning_service import clock, engine  # noqa: F401


def _walk(engine: LearningService, clock: FrozenClock, word_id: int, rating: Rating):  # noqa: F811
    """Advance to the word's next due day and answer it."""
    card = CardRepository(engine.database).get(word_id)
    days = max(engine.clock.days_between(clock.now_utc(), card.due_at), 1)
    clock.advance_to_day_start(days)
    return answer(engine, word_id, rating)


def _first(engine: LearningService, clock: FrozenClock) -> int:  # noqa: F811
    word_id = engine.introduce().introduced[0].id
    clock.advance_to_day_start(1)
    answer(engine, word_id, Rating.GOOD)
    return word_id


class TestKnownCheck:
    def test_the_interval_stops_at_the_gap_known_is_offered_after(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        assert _walk(engine, clock, word_id, Rating.GOOD).interval_days == 11
        checked = _walk(engine, clock, word_id, Rating.GOOD)
        assert checked.known_check and checked.interval_days == 21, "46 days, cut to 21"
        assert not checked.suggest_known

        offered = _walk(engine, clock, word_id, Rating.GOOD)
        assert offered.suggest_known, "remembered after 21 days: the offer comes"
        assert not offered.known_check and offered.interval_days > 21, "the check is over"

    def test_the_buttons_show_the_check(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        _walk(engine, clock, word_id, Rating.GOOD)
        card = CardRepository(engine.database).get(word_id)
        clock.advance_to_day_start(engine.clock.days_between(clock.now_utc(), card.due_at))
        preview = engine.preview_intervals(word_id)
        assert preview[Rating.GOOD] == 21 and preview[Rating.EASY] == 21
        assert preview[Rating.AGAIN] == 1

    def test_an_again_takes_the_case_away_and_the_check_starts_over(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        for _ in range(3):
            _walk(engine, clock, word_id, Rating.GOOD)
        assert word_id in {w.id for w in engine.known_suggestions()}
        _walk(engine, clock, word_id, Rating.AGAIN)
        assert word_id not in {w.id for w in engine.known_suggestions()}, "forgotten since"
        for _ in range(8):
            outcome = _walk(engine, clock, word_id, Rating.GOOD)
            assert outcome.interval_days <= 21 or outcome.suggest_known

    def test_a_known_word_has_no_check(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        engine.mark_known([word_id])
        for _ in range(3):
            outcome = _walk(engine, clock, word_id, Rating.GOOD)
            assert not outcome.known_check
        assert outcome.interval_days > 21


class TestKnownTarget:
    def test_marking_known_moves_the_card_to_the_known_target_at_once(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        _walk(engine, clock, word_id, Rating.GOOD)
        cards = CardRepository(engine.database)
        before = cards.get(word_id)
        engine.mark_known([word_id])
        after = cards.get(word_id)
        assert after.due_at > before.due_at
        assert after.due_at == engine.scheduler.target_due(after, known=True)
        assert after.stability == before.stability, "only the date moves"

    def test_a_card_due_today_stays_today(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        cards = CardRepository(engine.database)
        due = cards.get(word_id).due_at
        clock.advance_to_day_start(engine.clock.days_between(clock.now_utc(), due))
        engine.mark_known([word_id])
        assert cards.get(word_id).due_at == due

    def test_known_words_come_back_less_often(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        for _ in range(3):
            _walk(engine, clock, word_id, Rating.GOOD)
        card = CardRepository(engine.database).get(word_id)
        clock.advance_to_day_start(engine.clock.days_between(clock.now_utc(), card.due_at))
        general = engine.preview_intervals(word_id)[Rating.GOOD]
        engine.mark_known([word_id])
        known = engine.preview_intervals(word_id)[Rating.GOOD]
        assert known > general * 1.5

    def test_changing_the_target_moves_known_words(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        _walk(engine, clock, word_id, Rating.GOOD)
        engine.mark_known([word_id])
        cards = CardRepository(engine.database)
        at_85 = cards.get(word_id).due_at
        engine.save_settings({Setting.KNOWN_RETENTION: 0.8})
        assert cards.get(word_id).due_at > at_85

    def test_the_settings_are_kept_in_range(self, engine: LearningService) -> None:  # noqa: F811
        saved = engine.save_settings(
            {Setting.KNOWN_RETENTION: 0.5, Setting.KNOWN_BACK_PER_DAY: 1000}
        )
        assert saved.known_retention == 0.75 and saved.known_back_per_day == 100


class TestForgotten:
    def test_a_known_word_answered_wrong_is_offered_for_learning_again(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        engine.mark_known([word_id])
        outcome = _walk(engine, clock, word_id, Rating.AGAIN)
        assert outcome.suggest_relearn and not outcome.suggest_known
        assert outcome.interval_days == 1, "Again always means tomorrow"
        words = WordRepository(engine.database)
        assert words.get(word_id).status is ReviewStatus.KNOWN, "only offered"
        assert [w.id for w in engine.forgotten_known()] == [word_id]

        assert engine.relearn([word_id]) == 1
        assert words.get(word_id).status is ReviewStatus.UNKNOWN
        events = StateRepository(engine.database).events_for_word(word_id)
        forgotten = [e for e in events if e.cause is StatusCause.FORGOTTEN]
        assert [(e.from_status, e.to_status) for e in forgotten] == [
            (ReviewStatus.KNOWN, ReviewStatus.UNKNOWN)
        ]
        assert engine.forgotten_known() == []

    def test_remembering_it_again_takes_the_offer_away(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        engine.mark_known([word_id])
        _walk(engine, clock, word_id, Rating.AGAIN)
        _walk(engine, clock, word_id, Rating.GOOD)
        assert engine.forgotten_known() == []

    def test_only_known_words_can_be_relearned(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        assert engine.relearn([word_id]) == 0


def _known_and_archived(engine: LearningService, clock: FrozenClock, count: int = 25) -> list[int]:  # noqa: F811
    ids = [word.id for word in engine.introduce().introduced][:count]
    clock.advance_to_day_start(1)
    for word_id in ids:
        answer(engine, word_id, Rating.GOOD)
    engine.mark_known(ids)
    engine.save_settings({Setting.REVIEW_KNOWN_WORDS: False})
    return ids


class TestSpreading:
    def test_turning_reviews_off_archives_and_on_spreads_them(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        ids = _known_and_archived(engine, clock)
        cards = CardRepository(engine.database)
        assert all(card.state is CardState.ARCHIVED for card in cards.get_many(ids).values())

        clock.advance_to_day_start(100)
        engine.save_settings({Setting.KNOWN_BACK_PER_DAY: 5})
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: True})
        assert engine.take_spread_note() == "25 Known words will come back over the next 5 days."
        assert engine.take_spread_note() is None, "said once"
        placed = cards.get_many(ids).values()
        assert all(card.state is CardState.REVIEW and card.spread for card in placed)
        days = sorted(engine.clock.local_date(card.due_at) for card in placed)
        assert days[0] == engine.clock.shift_days(1), "from tomorrow, never today"
        assert all(days.count(day) == 5 for day in set(days))

    def test_a_day_without_room_takes_none(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        ids = _known_and_archived(engine, clock)
        clock.advance_to_day_start(100)
        # 30 reviews a day with 25 new words leaves room for 5.
        engine.save_settings({Setting.REVIEW_CAPACITY_PER_DAY: 30})
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: True})
        days = [
            engine.clock.local_date(card.due_at)
            for card in CardRepository(engine.database).get_many(ids).values()
        ]
        assert max(days.count(day) for day in set(days)) == 5

    def test_a_new_number_per_day_places_the_waiting_words_again(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        ids = _known_and_archived(engine, clock)
        clock.advance_to_day_start(100)
        engine.save_settings({Setting.KNOWN_BACK_PER_DAY: 5})
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: True})
        engine.take_spread_note()
        engine.save_settings({Setting.KNOWN_BACK_PER_DAY: 10})
        assert engine.take_spread_note() == "25 Known words will come back over the next 3 days."
        days = [
            engine.clock.local_date(card.due_at)
            for card in CardRepository(engine.database).get_many(ids).values()
        ]
        assert sorted(days.count(day) for day in set(days)) == [5, 10, 10]

    def test_answering_a_spread_word_clears_its_mark(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        ids = _known_and_archived(engine, clock, count=1)
        clock.advance_to_day_start(100)
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: True})
        clock.advance_to_day_start(1)
        answer(engine, ids[0], Rating.GOOD)
        assert not CardRepository(engine.database).get(ids[0]).spread


class TestStatusChanges:
    def test_a_word_set_back_to_unknown_leaves_the_archive(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        """Known with reviews off archived the card; Unknown must bring it back,
        or the word is neither learned nor reviewed."""
        word_id = _first(engine, clock)
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: False})
        engine.mark_known([word_id], cause=StatusCause.MASTERY)
        cards = CardRepository(engine.database)
        assert cards.get(word_id).state is CardState.ARCHIVED
        service = VocabularyService(engine.database)
        service.attach_engine(engine)
        service.change_status([word_id], ReviewStatus.UNKNOWN)
        card = cards.get(word_id)
        assert card.state is CardState.REVIEW
        assert card.due_at >= engine.clock.next_day_start(clock.now_utc())

    def test_a_status_button_follows_the_same_rules(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        _walk(engine, clock, word_id, Rating.GOOD)
        service = VocabularyService(engine.database)
        service.attach_engine(engine)
        cards = CardRepository(engine.database)
        before = cards.get(word_id).due_at
        change = service.change_status([word_id], ReviewStatus.KNOWN)
        assert cards.get(word_id).due_at > before, "moved to the Known target"
        service.undo_status_change(change)
        assert cards.get(word_id).due_at == before, "and back"

    def test_sorting_a_known_word_archives_its_card_when_reviews_are_off(
        self, engine: LearningService, clock: FrozenClock  # noqa: F811
    ) -> None:
        word_id = _first(engine, clock)
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: False})
        StateRepository(engine.database).set_status(word_id, ReviewStatus.NOT_REVIEWED)
        service = VocabularyService(engine.database)
        service.attach_engine(engine)
        session = service.start_review()
        while session.current() is not None and session.current().word.id != word_id:
            session.answer(False)
        session.answer(True)
        assert CardRepository(engine.database).get(word_id).state is CardState.ARCHIVED


class TestSchemaEight:
    def test_a_version_7_database_is_upgraded(self, tmp_path: Path) -> None:
        path = tmp_path / "v7.db"
        conn = sqlite3.connect(path)
        for schema_file in _SCHEMA_FILES:
            if schema_file.name != "known.sql":
                conn.executescript(schema_file.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_version (version) VALUES (7)")
        conn.execute(
            "INSERT INTO words (id, language, normalized_word, display_word) "
            "VALUES (1, 'en', 'word', 'word')"
        )
        conn.execute(
            "INSERT INTO word_status_events (word_id, at, from_status, to_status, cause) "
            "VALUES (1, '2026-01-01T00:00:00+00:00', 'unknown', 'known', 'mastery')"
        )
        conn.commit()
        conn.close()

        database = Database(path)
        database.connect()
        try:
            connection = database.connection
            assert read_version(connection) == SCHEMA_VERSION == 8
            assert connection.execute("SELECT COUNT(*) FROM word_status_events").fetchone()[0] == 1
            connection.execute(
                "INSERT INTO word_status_events (word_id, at, from_status, to_status, cause) "
                "VALUES (1, '2026-02-01T00:00:00+00:00', 'known', 'unknown', 'forgotten')"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(srs_cards)")}
            assert "spread" in columns
        finally:
            database.close()

    def test_a_backup_from_version_7_still_restores(
        self, engine: LearningService, clock: FrozenClock, tmp_path: Path  # noqa: F811
    ) -> None:
        _first(engine, clock)
        summary = portable.export(engine.database, tmp_path / "all.lexitrack")
        older = tmp_path / "v7.lexitrack"
        with zipfile.ZipFile(summary.path) as source, zipfile.ZipFile(older, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(data)
                    manifest["schema_version"] = 7
                    data = json.dumps(manifest).encode()
                elif name == "tables/srs_cards.json":
                    table = json.loads(data)
                    keep = [i for i, c in enumerate(table["columns"]) if c != "spread"]
                    table["columns"] = [table["columns"][i] for i in keep]
                    table["rows"] = [[row[i] for i in keep] for row in table["rows"]]
                    data = json.dumps(table).encode()
                target.writestr(name, data)
        portable.restore(engine.database, older)
        assert CardRepository(engine.database).all_cards()
