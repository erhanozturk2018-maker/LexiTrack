"""The record behind the Progress page: status changes, parameters, undo.

Each test names a way the history could quietly lie: a status change with no
record, a Known word that cannot say when or why, a slip that stays in the
statistics after it was taken back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.database.migrations import reconstruct_mastery_events
from lexitrack.models.settings import LearningSettings, Setting
from lexitrack.models.source import Source
from lexitrack.models.srs import Channel, Rating
from lexitrack.models.user_word_state import ReviewStatus, StatusCause
from lexitrack.repositories import (
    CardRepository,
    ListRepository,
    SessionRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_session import ReviewSession
from lexitrack.services.srs_scheduler import SrsScheduler

from .conftest import entry


def _name(index: int) -> str:
    letters = "abcdefghijklmnopqrstuvwxyz"
    return f"{letters[index // 26]}{letters[index % 26]}word"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 17, 4, 0, tzinfo=UTC))


@pytest.fixture
def words(database: Database) -> list[int]:
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    result = WordRepository(database).add_entries(
        [entry(_name(index), cefr_level="A1") for index in range(30)], source.id
    )
    ids = list(result.word_ids)
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, ids)
    return ids


@pytest.fixture
def engine(database: Database, clock: FrozenClock, words: list[int]) -> LearningService:
    StateRepository(database).set_status_many(words, ReviewStatus.UNKNOWN)
    service = LearningService(database, clock)
    service.create_plan("Test plan", list_ids=[ListRepository(database).all()[0].id])
    service.save_settings({Setting.NEW_WORDS_PER_DAY: 5})
    return service


def answer_all(engine: LearningService, rating: Rating, session_id: str | None = None) -> int:
    done = 0
    for item in engine.review_queue():
        engine.answer(item.word.id, rating, session_id=session_id)
        done += 1
    return done


class TestStatusEvents:
    def test_a_change_is_recorded_with_its_cause(self, database: Database, words) -> None:
        state = StateRepository(database)
        state.set_status(words[0], ReviewStatus.UNKNOWN, cause=StatusCause.MANUAL)
        (event,) = state.events_for_word(words[0])
        assert event.from_status is ReviewStatus.NOT_REVIEWED
        assert event.to_status is ReviewStatus.UNKNOWN
        assert event.cause is StatusCause.MANUAL
        assert event.reconstructed is False

    def test_setting_the_same_status_again_leaves_no_trace(
        self, database: Database, words
    ) -> None:
        state = StateRepository(database)
        state.set_status(words[0], ReviewStatus.KNOWN)
        state.set_status(words[0], ReviewStatus.KNOWN)
        state.set_status_many(words[:3], ReviewStatus.KNOWN)
        assert len(state.events_for_word(words[0])) == 1
        assert len(state.all_events()) == 3

    def test_the_review_tab_is_recorded_as_sorting(self, database: Database, words) -> None:
        session = ReviewSession(WordRepository(database), StateRepository(database))
        first = session.current().word.id
        session.answer(True)
        (event,) = StateRepository(database).events_for_word(first)
        assert event.cause is StatusCause.SORTING
        assert event.to_status is ReviewStatus.KNOWN

    def test_starting_over_forgets_the_history_too(self, database: Database, words) -> None:
        state = StateRepository(database)
        state.set_status_many(words, ReviewStatus.KNOWN)
        state.reset_all()
        assert state.all_events() == []


class TestMastery:
    def test_known_by_the_schedule_says_when_and_in_which_plan(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.EASY)  # day 1: 8 days of stability
        clock.advance_to_day_start(8)
        answer_all(engine, Rating.EASY)  # day 9: past 21, Known
        known = [
            event
            for event in StateRepository(database).all_events()
            if event.cause is StatusCause.MASTERY
        ]
        assert len(known) == 5, "the day's five words, each once"
        assert {event.plan_id for event in known} == {engine.active_plan().id}
        assert {event.at for event in known} == {clock.now_utc()}
        assert all(event.from_status is ReviewStatus.UNKNOWN for event in known)

    def test_the_upgrade_reconstructs_what_it_can_and_nothing_more(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.EASY)
        clock.advance_to_day_start(8)
        answer_all(engine, Rating.EASY)
        conn = database.connection
        mastered = conn.execute(
            "SELECT COUNT(*) FROM word_status_events WHERE cause = 'mastery'"
        ).fetchone()[0]
        assert mastered == 5
        # As a version 3 database would have it: the statuses, no events.
        conn.execute("DELETE FROM word_status_events")
        written = reconstruct_mastery_events(conn)
        assert written == mastered
        assert reconstruct_mastery_events(conn) == 0, "safe to run twice"
        rows = conn.execute(
            "SELECT reconstructed, cause FROM word_status_events"
        ).fetchall()
        assert all(row[0] == 1 and row[1] == "mastery" for row in rows)


class TestParameters:
    def test_every_answer_records_the_parameters_that_scheduled_it(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.GOOD)
        hashes = {log.params_hash for log in CardRepository(database).all_logs()}
        assert hashes == {engine._scheduler.params_hash}
        assert len(next(iter(hashes))) == 12

    def test_fitted_parameters_change_the_fingerprint(self, clock: FrozenClock) -> None:
        default = SrsScheduler(LearningSettings(), clock)
        fitted = SrsScheduler(
            LearningSettings(fsrs_parameters=tuple(v * 1.01 for v in default.parameters)),
            clock,
        )
        assert fitted.personalised and not default.personalised
        assert fitted.params_hash != default.params_hash

    @pytest.mark.parametrize(
        "stored, expected_default",
        [("", True), ("not json", True), ("[1, 2, 3]", True), (None, True)],
    )
    def test_unusable_stored_parameters_fall_back_to_the_defaults(
        self, stored, expected_default
    ) -> None:
        settings = LearningSettings.from_values({Setting.FSRS_PARAMETERS: stored or ""})
        assert (settings.fsrs_parameters is None) is expected_default

    def test_twenty_one_numbers_are_accepted(self) -> None:
        values = [0.5] * 21
        settings = LearningSettings.from_values({Setting.FSRS_PARAMETERS: json.dumps(values)})
        assert settings.fsrs_parameters == tuple(values)

    def test_recall_probability_exists_only_after_an_answer_and_falls_with_time(
        self, engine: LearningService, clock: FrozenClock, database: Database, words
    ) -> None:
        engine.introduce()
        cards = CardRepository(database)
        scheduler = engine._scheduler
        assert scheduler.retrievability(cards.get(words[0]), clock.now_utc()) is None
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.GOOD)
        answered = cards.get(words[0])
        soon = scheduler.retrievability(answered, clock.now_utc())
        clock.advance_to_day_start(10)
        later = scheduler.retrievability(answered, clock.now_utc())
        assert 0 < later < soon <= 1


class TestUndo:
    def test_undo_puts_the_card_back_and_keeps_the_answer_marked(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        clock.advance(hours=9)
        first = engine.review_queue()[0]
        before = CardRepository(database).get(first.word.id)
        engine.answer(first.word.id, Rating.EASY)
        assert engine.can_undo()
        word = engine.undo_last_answer()
        assert word is not None and word.id == first.word.id
        restored = CardRepository(database).get(first.word.id)
        assert restored.review_count == before.review_count
        assert restored.due_at == before.due_at
        assert first.word.id in [item.word.id for item in engine.review_queue()]
        (log,) = CardRepository(database).all_logs()
        assert log.undone_at is not None, "the slip is kept, marked"
        assert engine.daily_plan().reviews_done_today == 0
        assert sum(engine.rating_counts().values()) == 0

    def test_only_the_last_answer_and_only_once(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.GOOD)
        assert engine.undo_last_answer() is not None
        assert engine.undo_last_answer() is None
        assert not engine.can_undo()

    def test_another_session_cannot_undo_it(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        session = engine.start_session(Channel.DESKTOP)
        answer_all(engine, Rating.GOOD, session_id=session.id)
        assert engine.undo_last_answer(session_id="someone-else") is None
        assert engine.undo_last_answer(session_id=session.id) is not None

    def test_undo_restores_the_session_count_and_frees_the_telegram_key(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        session = engine.start_session(Channel.TELEGRAM, chat_id="1")
        item = engine.review_queue()[0]
        key = f"ans:{session.id}:{item.word.id}"
        engine.answer(item.word.id, Rating.GOOD, session_id=session.id, update_key=key)
        assert engine.session(session.id).done_count == 1
        engine.undo_last_answer(session_id=session.id)
        assert engine.session(session.id).done_count == 0
        assert engine.session(session.id).current_word_id == item.word.id
        assert SessionRepository(database).claim_update(key), "the word can be answered again"

    def test_undoing_the_answer_that_made_a_word_known_takes_that_back_too(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.EASY)
        clock.advance_to_day_start(8)
        queue = engine.review_queue()
        engine.answer(queue[0].word.id, Rating.EASY)
        state = StateRepository(database)
        assert state.get(queue[0].word.id).status is ReviewStatus.KNOWN
        engine.undo_last_answer()
        assert state.get(queue[0].word.id).status is ReviewStatus.UNKNOWN
        causes = [event.cause for event in state.events_for_word(queue[0].word.id)]
        assert causes[-2:] == [StatusCause.MASTERY, StatusCause.UNDO]

    def test_a_word_answered_again_elsewhere_cannot_be_undone_here(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        item = engine.review_queue()[0]
        engine.answer(item.word.id, Rating.GOOD)
        other = LearningService(database, clock)
        clock.advance_to_day_start(2)
        other.answer(item.word.id, Rating.GOOD)
        assert engine.undo_last_answer() is None
