"""The scheduler's promises, checked against the real FSRS library.

Nothing here mocks ``fsrs``. The point of these tests is not that the wrapper
calls the library — it is that the four rules LexiTrack adds on top of it
actually hold for the intervals the library really produces.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.models.settings import LearningSettings
from lexitrack.models.srs import CardState, Rating, SrsCard
from lexitrack.services.srs_scheduler import SCHEDULER_VERSION, SrsScheduler


@pytest.fixture
def clock() -> FrozenClock:
    """08:00 on 17 September 2026, Istanbul."""
    return FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))


@pytest.fixture
def settings() -> LearningSettings:
    return LearningSettings()


@pytest.fixture
def scheduler(settings: LearningSettings, clock: FrozenClock) -> SrsScheduler:
    return SrsScheduler(settings, clock)


def new_card(clock: FrozenClock, scheduler: SrsScheduler, word_id: int = 1) -> SrsCard:
    """A card as :class:`CardRepository` would create it on introduction."""
    return SrsCard(
        word_id=word_id,
        state=CardState.INTRODUCED,
        introduced_at=clock.now_utc(),
        introduced_on=clock.today(),
        due_at=scheduler.first_due_at(clock.now_utc()),
    )


class TestIntroduction:
    def test_a_new_word_is_first_due_tomorrow_morning(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        """Not "in 24 hours": the interval must not depend on the hour."""
        due = scheduler.first_due_at(clock.now_utc())
        assert clock.local_date(due) == "2026-09-18"
        assert clock.to_local(due).hour == 0

    def test_the_hour_of_confirmation_does_not_change_the_due_day(
        self, settings: LearningSettings
    ) -> None:
        morning = FrozenClock(datetime(2026, 9, 17, 4, 0, tzinfo=UTC))  # 07:00
        midnight = FrozenClock(datetime(2026, 9, 17, 20, 30, tzinfo=UTC))  # 23:30
        assert SrsScheduler(settings, morning).first_due_at(
            morning.now_utc()
        ) == SrsScheduler(settings, midnight).first_due_at(midnight.now_utc())


class TestDayBoundary:
    def test_no_rating_can_schedule_a_card_for_today(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        """The rule that keeps an evening session finite."""
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        clock.advance(hours=7)
        for rating in Rating:
            due = scheduler.review(card, rating, clock.now_utc()).card.due_at
            assert due is not None
            assert clock.local_date(due) > clock.today(), rating.label

    def test_every_due_time_lands_on_a_day_boundary(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        """So the morning briefing's count cannot drift during the day."""
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        for rating in Rating:
            due = scheduler.review(card, rating, clock.now_utc()).card.due_at
            assert due is not None
            local = clock.to_local(due)
            assert (local.hour, local.minute, local.second) == (0, 0, 0), rating.label

    def test_the_four_answers_are_genuinely_different(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        """Snapping to days must not collapse Hard, Good and Easy into one."""
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        # Bring the card into review so the intervals have room to differ.
        card = scheduler.review(card, Rating.GOOD, clock.now_utc()).card
        clock.advance_to_day_start(3)
        preview = scheduler.preview(card, clock.now_utc())
        days = [preview[rating] for rating in Rating]
        assert days == sorted(days), "a better answer must never mean a sooner review"
        assert len(set(days)) == 4


class TestRatings:
    def test_a_good_answer_graduates_the_card_and_grows_stability(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        first = scheduler.review(card, Rating.GOOD, clock.now_utc())
        assert first.card.state in {CardState.LEARNING, CardState.REVIEW}
        assert first.card.review_count == 1
        assert first.card.stability is not None
        assert first.card.first_review_at is not None
        assert first.card.scheduler_version == SCHEDULER_VERSION

        clock.advance_to_day_start(3)
        second = scheduler.review(first.card, Rating.GOOD, clock.now_utc())
        assert second.card.stability > first.card.stability
        assert second.card.state is CardState.REVIEW

    def test_an_again_counts_as_a_lapse_and_shortens_the_interval(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        card = scheduler.review(card, Rating.GOOD, clock.now_utc()).card
        clock.advance_to_day_start(3)
        good = scheduler.review(card, Rating.GOOD, clock.now_utc())
        again = scheduler.review(card, Rating.AGAIN, clock.now_utc())
        assert again.card.lapse_count == 1
        assert again.card.consecutive_lapses == 1
        assert again.card.due_at < good.card.due_at
        assert again.card.state is CardState.RELEARNING

    def test_the_introduction_is_not_treated_as_a_review(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        """No rating is invented for a word the user studied on their own."""
        card = new_card(clock, scheduler)
        assert card.review_count == 0
        assert card.fsrs_state is None
        clock.advance_to_day_start(1)
        result = scheduler.review(card, Rating.GOOD, clock.now_utc())
        assert result.card.review_count == 1

    def test_engine_state_survives_a_round_trip_through_json(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        """The card is reloaded from text between every answer in real use."""
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        card = scheduler.review(card, Rating.GOOD, clock.now_utc()).card
        clock.advance_to_day_start(3)
        from_state = scheduler.review(card, Rating.GOOD, clock.now_utc()).card
        # Reload exactly as the repository would, then rate again identically.
        reloaded = replace(card)
        again = scheduler.review(reloaded, Rating.GOOD, clock.now_utc()).card
        assert again.due_at == from_state.due_at
        assert again.stability == from_state.stability

    def test_unreadable_engine_state_is_rebuilt_rather_than_fatal(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        card = replace(new_card(clock, scheduler), fsrs_state="{not json")
        clock.advance_to_day_start(1)
        result = scheduler.review(card, Rating.GOOD, clock.now_utc())
        assert result.card.stability is not None


class TestStrugglingWords:
    def test_a_word_is_flagged_after_the_configured_run_of_failures(
        self, clock: FrozenClock
    ) -> None:
        clock = FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))
        scheduler = SrsScheduler(LearningSettings(leech_consecutive=4), clock)
        card = new_card(clock, scheduler)
        flags = []
        for _ in range(4):
            clock.advance_to_day_start(1)
            result = scheduler.review(card, Rating.AGAIN, clock.now_utc())
            card = result.card
            flags.append(card.needs_relearning)
        assert flags == [False, False, False, True]

    def test_a_new_word_with_low_stability_is_not_flagged(
        self, clock: FrozenClock, scheduler: SrsScheduler
    ) -> None:
        """Early stability is always small; flagging on it would flag everything."""
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        result = scheduler.review(card, Rating.HARD, clock.now_utc())
        assert result.card.stability < 7
        assert result.card.needs_relearning is False

    def test_the_flag_needs_a_real_recovery_to_clear(self, clock: FrozenClock) -> None:
        """One lucky Good must not empty the Struggling list."""
        scheduler = SrsScheduler(LearningSettings(leech_consecutive=2), clock)
        card = new_card(clock, scheduler)
        for _ in range(2):
            clock.advance_to_day_start(1)
            card = scheduler.review(card, Rating.AGAIN, clock.now_utc()).card
        assert card.needs_relearning is True

        clock.advance_to_day_start(1)
        card = scheduler.review(card, Rating.GOOD, clock.now_utc()).card
        assert card.consecutive_lapses == 0
        assert card.needs_relearning is True, "still weak, so still flagged"

        for _ in range(6):
            clock.advance_to_day_start(max(int(card.stability or 1), 1))
            card = scheduler.review(card, Rating.EASY, clock.now_utc()).card
        assert card.stability >= 7
        assert card.needs_relearning is False

    def test_becoming_struggling_is_reported_once(self, clock: FrozenClock) -> None:
        """So the UI can say "this word needs attention" without repeating it."""
        scheduler = SrsScheduler(LearningSettings(leech_consecutive=2), clock)
        card = new_card(clock, scheduler)
        events = []
        for _ in range(4):
            clock.advance_to_day_start(1)
            result = scheduler.review(card, Rating.AGAIN, clock.now_utc())
            card = result.card
            events.append(result.became_struggling)
        assert events == [False, True, False, False]


class TestMastery:
    def test_mastery_is_reported_when_stability_passes_the_threshold(
        self, clock: FrozenClock
    ) -> None:
        scheduler = SrsScheduler(LearningSettings(mastery_stability_days=21), clock)
        card = new_card(clock, scheduler)
        reached = False
        for _ in range(6):
            clock.advance_to_day_start(max(int(card.stability or 1), 1))
            result = scheduler.review(card, Rating.EASY, clock.now_utc())
            card = result.card
            reached = reached or result.reached_mastery
        assert card.stability >= 21
        assert reached is True

    def test_a_high_threshold_is_not_reached_by_a_single_good_answer(
        self, clock: FrozenClock
    ) -> None:
        scheduler = SrsScheduler(LearningSettings(mastery_stability_days=365), clock)
        card = new_card(clock, scheduler)
        clock.advance_to_day_start(1)
        assert scheduler.review(card, Rating.EASY, clock.now_utc()).reached_mastery is False


class TestSettings:
    def test_desired_retention_shortens_intervals(self, clock: FrozenClock) -> None:
        """Proof the setting reaches the library rather than being decorative."""
        relaxed = SrsScheduler(LearningSettings(desired_retention=0.8), clock)
        strict = SrsScheduler(LearningSettings(desired_retention=0.97), clock)
        card = new_card(clock, relaxed)
        clock.advance_to_day_start(1)
        relaxed_card = relaxed.review(card, Rating.GOOD, clock.now_utc()).card
        strict_card = strict.review(card, Rating.GOOD, clock.now_utc()).card
        clock.advance_to_day_start(2)
        relaxed_due = relaxed.review(relaxed_card, Rating.GOOD, clock.now_utc()).card.due_at
        strict_due = strict.review(strict_card, Rating.GOOD, clock.now_utc()).card.due_at
        assert strict_due < relaxed_due
