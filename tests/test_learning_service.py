"""The engine's behaviour as the Study tab and the bot will see it.

These tests are the specification of the daily rhythm. Each one names a way
the engine could plausibly be wrong in a way the user would only notice weeks
later: the day's words coming round twice, a missed week arriving as a
thousand-card queue, an answer tapped twice on a phone counting twice.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.settings import Setting
from lexitrack.models.source import Source
from lexitrack.models.srs import CardState, Channel, Rating
from lexitrack.models.user_word_state import ReviewStatus, StatusCause
from lexitrack.repositories import (
    ListRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.learning_service import LearningService

from .conftest import entry

LEVELS = ["A1", "A2", "B1", "B2", "C1"]


def _name(index: int) -> str:
    """A distinct pronounceable test word; the normalizer drops digits."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    return f"{letters[index // 26]}{letters[index % 26]}word"


@pytest.fixture
def clock() -> FrozenClock:
    """07:00 on 17 September 2026, Istanbul."""
    return FrozenClock(datetime(2026, 9, 17, 4, 0, tzinfo=UTC))


@pytest.fixture
def engine(database: Database, clock: FrozenClock) -> LearningService:
    """A service with one plan over a 120-word list, all unknown."""
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    entries = [
        entry(_name(index), cefr_level=LEVELS[index % len(LEVELS)]) for index in range(120)
    ]
    result = WordRepository(database).add_entries(entries, source.id)
    word_ids = list(result.word_ids)
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, word_ids)
    StateRepository(database).set_status_many(word_ids, ReviewStatus.UNKNOWN)

    service = LearningService(database, clock)
    service.create_plan("Test plan", list_ids=[a_list.id])
    return service


def study(service: LearningService, clock: FrozenClock, rating: Rating = Rating.GOOD) -> int:
    """Answer everything due today. Returns how many were answered."""
    answered = 0
    for item in service.review_queue():
        assert service.answer(item.word.id, rating) is not None
        answered += 1
    return answered


class TestDailyPlan:
    def test_the_day_offers_exactly_the_configured_number_of_new_words(
        self, engine: LearningService
    ) -> None:
        plan = engine.daily_plan()
        assert plan.has_plan is True
        assert len(plan.new_words) == 25
        assert plan.due_count == 0
        assert plan.pool_remaining == 120

    def test_new_words_come_in_cefr_order(self, engine: LearningService) -> None:
        levels = [word.cefr_level for word in engine.daily_plan().new_words]
        assert levels == sorted(levels, key=LEVELS.index)

    def test_a_changed_daily_count_takes_effect_immediately(
        self, engine: LearningService
    ) -> None:
        """The setting is shared with the bot, so it must not be cached."""
        engine.save_settings({Setting.NEW_WORDS_PER_DAY: 8})
        assert len(engine.daily_plan().new_words) == 8

    def test_zero_new_words_a_day_offers_nothing_and_still_reviews(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """A user pausing intake for a week must not lose their reviews."""
        engine.introduce()
        engine.save_settings({Setting.NEW_WORDS_PER_DAY: 0})
        clock.advance_to_day_start(1)
        plan = engine.daily_plan()
        assert plan.new_words == ()
        assert plan.due_count == 25

    def test_with_no_active_plan_the_day_is_simply_empty(
        self, database: Database, clock: FrozenClock
    ) -> None:
        service = LearningService(database, clock)
        plan = service.daily_plan()
        assert plan.has_plan is False
        assert plan.has_work is False
        assert service.review_queue() == []

    def test_a_finished_day_says_so(self, engine: LearningService) -> None:
        engine.introduce()
        assert engine.daily_plan().is_finished is True


class TestIntroduction:
    def test_confirming_introduces_the_offered_words_and_nothing_else(
        self, engine: LearningService
    ) -> None:
        offered = [word.id for word in engine.daily_plan().new_words]
        result = engine.introduce()
        assert result.count == 25
        assert [word.id for word in result.introduced] == offered
        assert result.first_due_on == "2026-09-18"

    def test_the_same_words_are_not_offered_again_on_the_same_day(
        self, engine: LearningService
    ) -> None:
        first = {word.id for word in engine.daily_plan().new_words}
        engine.introduce()
        second = engine.daily_plan()
        assert second.new_words == ()
        assert {word.id for word in second.introduced_today} == first
        assert second.intake_note is not None
        assert second.intake_paused is False, "the day is finished, not throttled"

    def test_confirming_twice_introduces_nothing_the_second_time(
        self, engine: LearningService
    ) -> None:
        """A re-sent Telegram message must not consume another 25 words."""
        engine.introduce()
        again = engine.introduce()
        assert again.count == 0
        assert engine.daily_plan().pool_remaining == 95

    def test_a_partial_confirmation_offers_the_rest_later_the_same_day(
        self, engine: LearningService
    ) -> None:
        offered = [word.id for word in engine.daily_plan().new_words]
        engine.introduce(offered[:10])
        remaining = engine.daily_plan()
        assert len(remaining.new_words) == 15
        assert len(remaining.introduced_today) == 10

    def test_todays_new_words_are_not_reviewable_today(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """The invariant the whole day depends on, checked through the service."""
        engine.introduce()
        assert engine.review_queue() == []
        clock.advance(hours=16)  # 23:00, same day
        assert engine.review_queue() == []
        clock.advance_to_day_start(1)
        assert len(engine.review_queue()) == 25

    def test_the_pool_runs_out_rather_than_repeating(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        seen: set[int] = set()
        for _ in range(5):
            batch = {word.id for word in engine.introduce().introduced}
            assert seen.isdisjoint(batch)
            seen |= batch
            clock.advance_to_day_start(1)
        assert len(seen) == 120
        assert engine.daily_plan().new_words == ()
        assert engine.daily_plan().pool_remaining == 0


class TestReviewQueue:
    def test_the_queue_is_yesterdays_words(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        introduced = {word.id for word in engine.introduce().introduced}
        clock.advance_to_day_start(1)
        assert {item.word.id for item in engine.review_queue()} == introduced

    def test_answering_removes_a_word_from_the_queue_for_the_day(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        first = engine.review_queue()[0]
        engine.answer(first.word.id, Rating.GOOD)
        assert first.word.id not in {item.word.id for item in engine.review_queue()}

    def test_even_again_does_not_bring_a_word_back_the_same_day(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """What stops an evening session from never ending."""
        engine.introduce()
        clock.advance_to_day_start(1)
        for item in engine.review_queue():
            engine.answer(item.word.id, Rating.AGAIN)
        assert engine.review_queue() == []
        clock.advance(hours=20)
        assert engine.review_queue() == []

    def test_struggling_words_are_asked_first(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.save_settings({Setting.LEECH_CONSECUTIVE: 2})
        engine.introduce()
        clock.advance_to_day_start(1)
        victim = engine.review_queue()[5].word.id
        for _ in range(2):
            engine.answer(victim, Rating.AGAIN)
            clock.advance_to_day_start(1)
        queue = engine.review_queue()
        assert queue[0].word.id == victim
        assert queue[0].is_struggling is True
        assert [item.word.id for item in engine.struggling_words()] == [victim]

    def test_the_queue_is_capped_at_the_review_capacity(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """A queue longer than the user agreed to face is one they abandon."""
        engine.introduce()
        clock.advance_to_day_start(1)
        engine.save_settings({Setting.REVIEW_CAPACITY_PER_DAY: 10})
        assert len(engine.review_queue()) == 10

    def test_meaning_is_hidden_in_study_reviews_only_when_asked(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        assert engine.review_queue()[0].hide_meaning is True
        engine.save_settings({Setting.HIDE_MEANING_IN_STUDY: False})
        assert engine.review_queue()[0].hide_meaning is False
        # The Struggling view is for reading, so it never hides anything.
        assert all(not item.hide_meaning for item in engine.struggling_words())


class TestAnswers:
    def test_an_answer_reschedules_the_card_and_is_logged(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        item = engine.review_queue()[0]
        outcome = engine.answer(item.word.id, Rating.GOOD, channel=Channel.TELEGRAM)
        assert outcome is not None
        assert outcome.next_due_on > clock.today()
        assert outcome.interval_days >= 1
        assert outcome.card.review_count == 1
        history = engine.word_history(item.word.id)
        assert len(history) == 1
        assert history[0].channel is Channel.TELEGRAM
        assert history[0].rating is Rating.GOOD

    def test_a_repeated_telegram_tap_changes_nothing(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """Telegram re-delivers callbacks; the second one must be inert."""
        engine.introduce()
        clock.advance_to_day_start(1)
        item = engine.review_queue()[0]
        first = engine.answer(item.word.id, Rating.GOOD, update_key="cb:1:good")
        second = engine.answer(item.word.id, Rating.AGAIN, update_key="cb:1:good")
        assert first is not None and first.duplicate is False
        assert second is not None and second.duplicate is True
        assert len(engine.word_history(item.word.id)) == 1
        assert engine.daily_plan().reviews_done_today == 1

    def test_answering_a_word_with_no_card_is_refused_quietly(
        self, engine: LearningService
    ) -> None:
        """A tap on a message sent before the word was removed."""
        word_id = engine.daily_plan().new_words[0].id
        assert engine.answer(word_id, Rating.GOOD) is None

    def test_a_session_counts_what_it_did(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        session = engine.start_session(Channel.TELEGRAM, chat_id="42")
        assert session.planned_count == 25
        for item in engine.review_queue()[:3]:
            engine.answer(item.word.id, Rating.GOOD, session_id=session.id)
        assert engine.open_session(Channel.TELEGRAM).done_count == 3
        finished = engine.finish_session(session.id)
        assert finished.is_open is False

    def test_yesterdays_open_session_is_closed_at_startup(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        engine.start_session(Channel.TELEGRAM)
        clock.advance_to_day_start(1)
        assert engine.close_stale_sessions() == 1
        assert engine.open_session(Channel.TELEGRAM) is None

    def test_developer_mode_can_see_what_each_answer_would_do(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        word_id = engine.review_queue()[0].word.id
        preview = engine.preview_intervals(word_id)
        assert set(preview) == set(Rating)
        assert len(set(preview.values())) >= 2
        assert all(days >= 1 for days in preview.values()), "nothing comes back today"
        assert preview[Rating.AGAIN] <= preview[Rating.GOOD] <= preview[Rating.EASY]


class TestWorkload:
    def test_intake_pauses_when_the_day_is_already_over_capacity(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """The engine's only automatic brake, and it explains itself."""
        engine.introduce()
        clock.advance_to_day_start(1)
        engine.save_settings({Setting.REVIEW_CAPACITY_PER_DAY: 10})
        plan = engine.daily_plan()
        assert plan.due_count == 10
        assert plan.new_words == ()
        assert plan.intake_paused is True
        assert plan.intake_note is not None
        assert "paused" in plan.intake_note

    def test_intake_resumes_once_the_backlog_is_cleared(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        engine.save_settings({Setting.REVIEW_CAPACITY_PER_DAY: 10})
        assert engine.daily_plan().new_words == ()
        # The queue is capped at 10 too, so clearing the backlog takes several
        # passes. Intake stays paused until it is actually down.
        while study(engine, clock):
            pass
        assert engine.daily_plan().due_count == 0
        assert len(engine.daily_plan().new_words) == 25

    def test_a_missed_week_does_not_multiply_the_new_words(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """Coming back after a holiday must not owe seven days of intake."""
        engine.introduce()
        clock.advance_to_day_start(7)
        plan = engine.daily_plan()
        assert len(plan.new_words) == 25
        assert plan.due_count == 25

    def test_the_forecast_covers_a_week_and_counts_overdue_cards_today(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        forecast = dict(engine.forecast())
        assert len(forecast) == 7
        assert forecast[clock.today()] == 25

        # Skip three days: those 25 are still owed, and they show up today.
        clock.advance_to_day_start(3)
        later = dict(engine.forecast())
        assert later[clock.today()] == 25

    def test_the_forecast_spreads_out_as_intervals_grow(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.introduce()
        for _ in range(4):
            clock.advance_to_day_start(1)
            engine.introduce()
            study(engine, clock, Rating.GOOD)
        busy_days = [count for _, count in engine.forecast() if count]
        assert len(busy_days) >= 2, "everything cannot be due on one day"


class TestMastery:
    def test_a_stable_word_is_suggested_as_known_never_marked(
        self, engine: LearningService, database: Database, clock: FrozenClock
    ) -> None:
        """Long-term memory is derived from the schedule; Known is the user's word."""
        engine.save_settings({Setting.MASTERY_STABILITY_DAYS: 10})
        engine.introduce()
        word_id = None
        # Easy answers stretch the interval fast, so most of these days have
        # nothing due; mastery is reached in reviews, not in elapsed time.
        for _ in range(60):
            clock.advance_to_day_start(1)
            for item in engine.review_queue():
                outcome = engine.answer(item.word.id, Rating.EASY)
                if outcome and outcome.suggest_known:
                    word_id = outcome.word.id
        assert word_id is not None
        words = WordRepository(database)
        assert words.get(word_id).status is ReviewStatus.UNKNOWN
        assert word_id in {word.id for word in engine.known_suggestions()}

        assert engine.confirm_known([word_id]) == 1
        assert words.get(word_id).status is ReviewStatus.KNOWN
        event = StateRepository(database).events_for_word(word_id)[-1]
        assert event.cause is StatusCause.MASTERY
        assert word_id not in {word.id for word in engine.known_suggestions()}

    def test_only_suggested_words_can_be_confirmed(
        self, engine: LearningService, database: Database, clock: FrozenClock
    ) -> None:
        """A stale suggestion cannot record a word as learned that is not."""
        introduced = [word.id for word in engine.introduce().introduced]
        clock.advance_to_day_start(1)
        engine.answer(introduced[0], Rating.GOOD)
        assert engine.known_suggestions() == []
        assert engine.confirm_known(introduced[:1]) == 0
        assert WordRepository(database).get(introduced[0]).status is ReviewStatus.UNKNOWN

    def test_a_manual_known_archives_the_card_when_known_words_are_not_reviewed(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: False})
        introduced = [word.id for word in engine.introduce().introduced]
        clock.advance_to_day_start(1)
        engine.mark_known(introduced[:5])
        queue = {item.word.id for item in engine.review_queue()}
        assert queue.isdisjoint(introduced[:5])
        assert len(queue) == 20

    def test_known_words_keep_being_reviewed_when_the_user_wants_that(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        introduced = [word.id for word in engine.introduce().introduced]
        clock.advance_to_day_start(1)
        engine.mark_known(introduced[:5])
        assert len(engine.review_queue()) == 25

    def test_an_archived_card_resumes_where_it_left_off(
        self, engine: LearningService, clock: FrozenClock
    ) -> None:
        """Resetting a status must not start the word over."""
        engine.save_settings({Setting.REVIEW_KNOWN_WORDS: False})
        introduced = [word.id for word in engine.introduce().introduced]
        clock.advance_to_day_start(1)
        engine.answer(introduced[0], Rating.GOOD)
        engine.mark_known([introduced[0]])
        assert engine.resume([introduced[0]]) == 1
        history = engine.word_history(introduced[0])
        assert len(history) == 1, "the history survived"
        assert engine.state_counts()[CardState.ARCHIVED.value] == 0


class TestPlans:
    def test_switching_plans_changes_the_scope_but_keeps_the_cards(
        self, engine: LearningService, database: Database, clock: FrozenClock
    ) -> None:
        engine.introduce()
        clock.advance_to_day_start(1)
        other = ListRepository(database).create("Other list")
        engine.create_plan("Second plan", list_ids=[other.id])
        assert engine.review_queue() == [], "the other plan has no words yet"

        first = next(plan for plan in engine.plans() if plan.name == "Test plan")
        engine.set_active_plan(first.id)
        assert len(engine.review_queue()) == 25

    def test_deleting_the_active_plan_falls_back_to_another(
        self, engine: LearningService, database: Database
    ) -> None:
        other = ListRepository(database).create("Other list")
        second = engine.create_plan("Second plan", list_ids=[other.id])
        engine.delete_plan(second.id)
        assert engine.active_plan() is not None
        assert engine.active_plan().name == "Test plan"

    def test_a_word_in_two_of_a_plans_lists_is_introduced_once(
        self, engine: LearningService, database: Database
    ) -> None:
        lists = ListRepository(database)
        words = [word.id for word in engine.daily_plan().new_words]
        overlap = lists.create("Overlap")
        lists.add_words(overlap.id, words[:5])
        plan = engine.active_plan()
        engine.update_plan(plan.id, list_ids=[*plan.list_ids, overlap.id])

        offered = [word.id for word in engine.daily_plan().new_words]
        assert len(offered) == len(set(offered)) == 25
        result = engine.introduce()
        assert result.count == 25
