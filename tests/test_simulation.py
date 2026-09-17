"""The workload simulator, and through it the cost of 25 words a day.

The ``review_capacity_per_day`` default of 250 was chosen from these numbers
rather than picked. These tests keep that reasoning honest: if a library
upgrade changes the intervals enough to move the steady-state load, one of
them fails and the default gets revisited instead of quietly becoming wrong.
A year of 6,825 words takes about fifteen seconds to simulate, so the tests
that need long runs are marked ``slow`` and left out of a normal run. Run them
with ``pytest -m slow`` — and do run them after changing the scheduler,
because they are the ones that guard the capacity default.
"""

from __future__ import annotations

import pytest

from lexitrack.models.settings import LearningSettings
from lexitrack.services.simulation import PROFILES, WorkloadSimulator


def run(days: int = 180, pool: int = 2000, profile: str = "typical", **kwargs: object):
    settings = LearningSettings(**kwargs)  # type: ignore[arg-type]
    return WorkloadSimulator(settings).run(days=days, pool_size=pool, profile=profile)


class TestBasics:
    def test_the_simulation_covers_the_days_it_was_asked_for(self) -> None:
        result = run(days=30)
        assert len(result.days) == 30
        assert result.days[0].local_date < result.days[-1].local_date

    def test_nothing_is_reviewed_on_the_first_day(self) -> None:
        """There is nothing to review yet, and today's words are not due today."""
        result = run(days=3)
        assert result.days[0].reviewed == 0
        assert result.days[0].introduced == 25
        assert result.days[1].reviewed == 25

    def test_the_same_settings_give_the_same_answer_twice(self) -> None:
        """A number that changes on every click is not evidence."""
        first = run(days=40, pool=600)
        second = run(days=40, pool=600)
        assert [day.reviewed for day in first.days] == [day.reviewed for day in second.days]

    def test_an_empty_pool_produces_an_empty_year(self) -> None:
        result = run(days=30, pool=0)
        assert result.total_introduced == 0
        assert result.total_reviewed == 0
        assert result.peak_reviews == 0

    def test_zero_days_is_not_an_error(self) -> None:
        result = run(days=0)
        assert result.days == ()
        assert result.mean_reviews == 0.0
        assert result.summary()


class TestWorkload:
    def test_the_review_load_is_many_times_the_new_word_count(self) -> None:
        """The point of the simulator: 25 a day is not 25 cards a day."""
        result = run(days=150, pool=1500)
        assert result.steady_state_reviews > 25

    @pytest.mark.slow
    def test_the_load_flattens_rather_than_growing_without_limit(self) -> None:
        """Intervals grow, so a steady 25 a day does not compound forever."""
        result = run(days=365, pool=6000)
        early = sum(day.reviewed for day in result.days[60:120]) / 60
        late = sum(day.reviewed for day in result.days[300:360]) / 60
        assert late < early * 2

    @pytest.mark.slow
    def test_the_default_capacity_is_enough_for_a_typical_learner(self) -> None:
        """Why review_capacity_per_day defaults to 250."""
        result = run(days=365, pool=6825, profile="typical")
        assert result.peak_reviews <= 250
        assert result.steady_state_reviews < 250

    @pytest.mark.slow
    def test_a_struggling_learner_saturates_the_capacity(self) -> None:
        """Worth knowing before it happens, so the simulator says so."""
        result = run(days=365, pool=6825, profile="struggling")
        assert result.max_deferred > 0
        assert any("capacity" in note for note in result.warnings)

    @pytest.mark.slow
    def test_fewer_new_words_a_day_means_a_lighter_load(self) -> None:
        # The pool is deep enough for both to keep introducing all the way
        # through: if the heavier one ran out, its load would taper and the
        # comparison would invert for the wrong reason.
        light = run(days=100, pool=2500, new_words_per_day=10)
        heavy = run(days=100, pool=2500, new_words_per_day=25)
        assert light.total_reviewed < heavy.total_reviewed
        assert light.steady_state_reviews < heavy.steady_state_reviews

    @pytest.mark.slow
    def test_a_higher_retention_target_costs_more_reviews(self) -> None:
        relaxed = run(days=150, pool=1500, desired_retention=0.8)
        strict = run(days=150, pool=1500, desired_retention=0.95)
        assert strict.total_reviewed > relaxed.total_reviewed


class TestCapacity:
    def test_intake_pauses_when_the_capacity_is_reached(self) -> None:
        """The same brake the live engine applies, so the numbers match it."""
        result = run(days=90, pool=2000, review_capacity_per_day=30)
        assert any(day.introduced == 0 for day in result.days)
        assert result.pool_left > 0

    def test_no_capacity_means_nothing_is_ever_deferred(self) -> None:
        result = run(days=150, pool=1500, review_capacity_per_day=0)
        assert result.max_deferred == 0
        assert result.fits_capacity is True

    def test_an_exhausted_pool_and_a_paused_intake_are_reported_differently(self) -> None:
        """They send the user to different settings, so they must not be confused."""
        exhausted = run(days=120, pool=200)
        assert any("pool ran out" in note for note in exhausted.warnings)

        paused = run(days=90, pool=2000, review_capacity_per_day=30)
        assert not any("pool ran out" in note for note in paused.warnings)


class TestProfiles:
    @pytest.mark.parametrize("name", sorted(PROFILES))
    def test_every_profile_sums_to_one(self, name: str) -> None:
        profile = PROFILES[name]
        total = profile.again + profile.hard + profile.good + profile.easy
        assert round(total, 6) == 1.0

    @pytest.mark.slow
    def test_a_weaker_learner_reviews_more(self) -> None:
        strong = run(days=150, pool=1500, profile="strong")
        struggling = run(days=150, pool=1500, profile="struggling")
        assert struggling.total_reviewed > strong.total_reviewed

    def test_an_unknown_profile_falls_back_instead_of_raising(self) -> None:
        result = run(days=10, profile="nonsense")
        assert result.profile == PROFILES["typical"].name


def test_the_summary_is_something_a_person_can_read() -> None:
    result = run(days=90, pool=1000)
    summary = result.summary()
    assert "new words a day" in summary
    assert str(result.peak_reviews) in summary
    assert summary.count("\n") >= 3
