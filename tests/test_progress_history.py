"""The record behind the Progress page: status changes, parameters, undo.

Each test names a way the history could quietly lie: a status change with no
record, a Known word that cannot say when or why, a slip that stays in the
statistics after it was taken back.
"""

from __future__ import annotations

import json
import random
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
from lexitrack.services.optimizer import (
    MIN_REVIEWS,
    FitResult,
    OptimizerMissing,
    Personaliser,
    optimizer_installed,
)
from lexitrack.services.progress import Group, ProgressService
from lexitrack.services.review_session import ReviewSession
from lexitrack.services.srs_scheduler import SrsScheduler

from .conftest import entry
from .flow_helpers import answer, record_known_evidence


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
        [entry(_name(index), cefr_level="A1", definition=f"meaning {index}")
         for index in range(30)],
        source.id
    )
    ids = list(result.word_ids)
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, ids)
    return ids


@pytest.fixture
def engine(database: Database, clock: FrozenClock, words: list[int]) -> LearningService:
    # On the test's clock, like every later event: status history is read in time
    # order, and a wall-clock "now" can fall after the frozen days that follow.
    StateRepository(database).set_status_many(words, ReviewStatus.UNKNOWN, at=clock.now_utc())
    service = LearningService(database, clock)
    service.create_plan("Test plan", list_ids=[ListRepository(database).all()[0].id])
    service.save_settings({Setting.NEW_WORDS_PER_DAY: 5})
    return service


def answer_all(engine: LearningService, rating: Rating, session_id: str | None = None) -> int:
    done = 0
    for item in engine.review_queue():
        answer(engine, item.word.id, rating, session_id=session_id)
        done += 1
    return done


def confirm_suggested(engine: LearningService) -> int:
    """Say yes to every "mark Known?" suggestion, as the user would — after
    giving the words in long-term memory the evidence a suggestion needs (a
    right answer after a long gap), which these tests take as given."""
    threshold = engine.settings.mastery_stability_days
    stable = [card.word_id for card in CardRepository(engine.database).all_cards()
              if (card.stability or 0) >= threshold]
    record_known_evidence(engine, stable)
    return engine.confirm_known([word.id for word in engine.known_suggestions()])


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
        answer_all(engine, Rating.EASY)  # day 9: past 21, suggested
        events = StateRepository(database).all_events()
        assert not [event for event in events if event.cause is StatusCause.MASTERY]
        assert confirm_suggested(engine) == 5
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
        confirm_suggested(engine)
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
        answer(engine, first.word.id, Rating.EASY)
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
        answer(engine, item.word.id, Rating.GOOD, session_id=session.id, update_key=key)
        assert engine.session(session.id).done_count == 1
        engine.undo_last_answer(session_id=session.id)
        assert engine.session(session.id).done_count == 0
        assert engine.session(session.id).current_word_id == item.word.id
        assert SessionRepository(database).claim_update(key), "the word can be answered again"

    def test_undoing_the_answer_that_reached_long_term_memory_takes_back_the_suggestion(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.EASY)
        # What is missing is a right answer after a long gap.
        assert engine.known_suggestions() == []
        clock.advance_to_day_start(30)
        queue = engine.review_queue()
        outcome = answer(engine, queue[0].word.id, Rating.EASY)
        assert outcome.suggest_known, "recalled after 29 days: the case is made"
        assert queue[0].word.id in {word.id for word in engine.known_suggestions()}
        engine.undo_last_answer()
        assert queue[0].word.id not in {word.id for word in engine.known_suggestions()}
        state = StateRepository(database)
        assert state.get(queue[0].word.id).status is ReviewStatus.UNKNOWN
        assert not [
            event for event in state.events_for_word(queue[0].word.id)
            if event.cause in (StatusCause.MASTERY, StatusCause.UNDO)
        ], "the status was never touched, so there is nothing to take back"

    def test_a_word_answered_again_elsewhere_cannot_be_undone_here(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        item = engine.review_queue()[0]
        answer(engine, item.word.id, Rating.GOOD)
        other = LearningService(database, clock)
        clock.advance_to_day_start(2)
        answer(other, item.word.id, Rating.GOOD)
        assert engine.undo_last_answer() is None


class TestJourney:
    def test_a_word_tells_its_story_in_order(
        self, engine: LearningService, clock: FrozenClock, database: Database, words
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.AGAIN)
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.EASY)
        clock.advance_to_day_start(8)
        answer_all(engine, Rating.EASY)
        confirm_suggested(engine)
        journey = ProgressService(database, engine).journey(words[0])
        kinds = [step.kind for step in journey.steps]
        assert kinds[0] == "introduced"
        assert [s.rating for s in journey.steps if s.kind == "answer"][:1] == [Rating.AGAIN]
        assert journey.answers == len([k for k in kinds if k == "answer"])
        assert journey.agains == 1
        assert journey.plan_name == "Test plan"
        assert journey.group is Group.LEARNED
        assert journey.known_on == clock.today()
        assert kinds[-1] == "status", "Known comes after the answer that suggested it"

    def test_an_answer_taken_back_is_shown_and_not_counted(
        self, engine: LearningService, clock: FrozenClock, database: Database, words
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        clock.advance(hours=9)
        answer(engine, words[0], Rating.EASY)
        engine.undo_last_answer()
        journey = ProgressService(database, engine).journey(words[0])
        answers = [s for s in journey.steps if s.kind == "answer"]
        assert len(answers) == 1 and answers[0].undone
        assert journey.answers == 0
        assert journey.last_answer is None
        assert journey.due_today

    def test_a_word_never_studied_says_why_it_is_not_scheduled(
        self, engine: LearningService, database: Database, words
    ) -> None:
        journey = ProgressService(database, engine).journey(words[-1])
        assert journey.card is None and journey.group is None
        assert journey.steps[0].kind == "status", "the Unknown set up by the fixture"


class TestProgressViews:
    def test_the_groups_separate_learned_from_marked_and_from_known_before(
        self, engine: LearningService, clock: FrozenClock, database: Database, words
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.EASY)
        clock.advance_to_day_start(8)
        answer_all(engine, Rating.EASY)
        confirm_suggested(engine)  # the first five: learned here
        engine.introduce()
        engine.mark_known([words[5]])  # introduced today, then Known by hand
        StateRepository(database).set_status(words[-1], ReviewStatus.KNOWN)  # never studied
        progress = ProgressService(database, engine)
        rows = progress.words()
        groups = {row.word.id: row.group for row in rows}
        assert {groups[w] for w in words[:5]} == {Group.LEARNED}
        assert groups[words[5]] is Group.MARKED_KNOWN
        summary = progress.summary(rows)
        assert (summary.learned, summary.marked_known) == (5, 1)
        assert summary.known_before == 1
        learned = next(row for row in rows if row.word.id == words[0])
        assert learned.days_to_known == 9

    def test_every_answer_is_listed_including_those_taken_back(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.GOOD)
        engine.undo_last_answer()
        progress = ProgressService(database, engine)
        rows = progress.answers()
        assert len(rows) == 5
        assert sum(1 for row in rows if row.entry.undone_at is not None) == 1
        assert progress.summary().taken_back == 1

    def test_the_pipeline_accounts_for_every_studied_word(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.GOOD)
        engine.introduce()
        stages = ProgressService(database, engine).pipeline()
        assert sum(stage.count for stage in stages) == 10
        assert stages[0].label == "Not answered yet" and stages[0].count == 5

    def test_calibration_compares_the_forecast_with_the_answers(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        engine.introduce()
        for _ in range(12):
            clock.advance_to_day_start(1)
            engine.introduce()
            for index, item in enumerate(engine.review_queue()):
                answer(engine, item.word.id, Rating.AGAIN if index % 4 == 0 else Rating.GOOD)
        calibration = ProgressService(database, engine).calibration()
        assert calibration.reviews > 0
        assert 0 < calibration.predicted <= 1 and 0 <= calibration.actual <= 1
        assert sum(b.count for b in calibration.bins) == calibration.reviews
        assert not calibration.enough or calibration.reviews >= 100

    def test_the_forgetting_curve_is_the_librarys_own(
        self, engine: LearningService, clock: FrozenClock, database: Database, words
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        answer_all(engine, Rating.GOOD)
        card = CardRepository(database).get(words[0])
        clock.advance_to_day_start(4)
        scheduler = engine.scheduler
        expected = scheduler.retrievability(card, clock.now_utc())
        elapsed = (clock.now_utc() - card.last_review_at).days
        assert abs(scheduler.predicted_recall(card.stability, elapsed) - expected) < 1e-9


class TestPersonalising:
    def study_weeks(self, engine: LearningService, clock: FrozenClock, days: int = 12) -> None:
        rng = random.Random(7)
        engine.introduce()
        for _ in range(days):
            clock.advance_to_day_start(1)
            engine.introduce()
            # Answers drawn from the default model itself: each review is
            # remembered with the chance FSRS predicts, from a fixed seed. On
            # such data the default parameters must predict better than broken
            # ones, whatever order the queue asks in.
            for item in engine.review_queue():
                recalled = rng.random() < (engine.retrievability(item.word.id) or 0.0)
                answer(engine, item.word.id, Rating.GOOD if recalled else Rating.AGAIN)

    def test_readiness_counts_what_the_optimizer_counts(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        self.study_weeks(engine, clock)
        ready = Personaliser(database, engine).readiness()
        logs = CardRepository(database).all_logs(include_undone=False)
        first_answers = len({log.word_id for log in logs})
        assert ready.usable == len(logs) - first_answers, "every answer after a word's first"
        assert ready.required == MIN_REVIEWS
        assert not ready.enough

    def test_same_day_answers_and_answers_taken_back_do_not_count(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        self.study_weeks(engine, clock, days=3)
        before = Personaliser(database, engine).readiness().usable
        clock.advance_to_day_start(1)
        item = engine.review_queue()[0]
        answer(engine, item.word.id, Rating.GOOD)
        engine.undo_last_answer()
        assert Personaliser(database, engine).readiness().usable == before

    def test_scoring_prefers_parameters_that_predict_better(
        self, engine: LearningService, clock: FrozenClock, database: Database
    ) -> None:
        self.study_weeks(engine, clock)
        personaliser = Personaliser(database, engine)
        default = personaliser.score(engine.scheduler.parameters)
        assert default > 0
        broken = list(engine.scheduler.parameters)
        broken[:4] = [0.01, 0.01, 0.01, 0.01]  # nearly no initial memory at all
        assert personaliser.score(broken) > default

    def test_applying_a_fit_changes_the_scheduler_and_can_be_undone(
        self, engine: LearningService, database: Database
    ) -> None:
        personaliser = Personaliser(database, engine)
        fitted = tuple(value * 1.02 for value in engine.scheduler.parameters)
        default_hash = engine.scheduler.params_hash
        personaliser.apply(FitResult(fitted, reviews=600, loss_before=0.4, loss_after=0.35))
        assert engine.scheduler.personalised
        assert engine.scheduler.params_hash != default_hash
        assert personaliser.fit_note()["reviews"] == 600
        personaliser.revert()
        assert not engine.scheduler.personalised
        assert engine.scheduler.params_hash == default_hash
        assert personaliser.fit_note() is None

    @pytest.mark.skipif(optimizer_installed(), reason="the optimizer is installed here")
    def test_without_the_optimizer_fitting_says_how_to_get_it(
        self, engine: LearningService, database: Database
    ) -> None:
        with pytest.raises(OptimizerMissing, match="lexitrack\\[optimizer\\]"):
            Personaliser(database, engine).fit([])
