"""Answering "what will 25 words a day actually cost me?" before it costs it.

The number that matters in a spaced-repetition system is not the new words per
day — it is the reviews per day they generate months later. That number is
easy to get wrong by a factor of five by reasoning about it, and trivial to
measure by running the real scheduler forward over an imaginary year.

This simulator therefore uses the same :class:`SrsScheduler` and the same
settings as the live engine, and touches no database at all. Nothing it does
can create a card, a log row or a status change; the cards exist only as
values in a list. That is what makes it safe to expose as a button in
Developer Mode.

What it cannot tell you is how *you* will answer. The accuracy profile is an
assumption, stated as a number, and the result is only as good as it. Three
profiles are offered because the honest answer is a range.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime

from ..core.clock import DayClock, FrozenClock
from ..models.settings import LearningSettings
from ..models.srs import CardState, Rating, SrsCard
from .srs_scheduler import SrsScheduler


@dataclass(frozen=True, slots=True)
class AccuracyProfile:
    """How the imaginary learner answers, as probabilities that sum to 1."""

    name: str
    again: float
    hard: float
    good: float
    easy: float

    def pick(self, rng: random.Random) -> Rating:
        roll = rng.random()
        if roll < self.again:
            return Rating.AGAIN
        if roll < self.again + self.hard:
            return Rating.HARD
        if roll < self.again + self.hard + self.good:
            return Rating.GOOD
        return Rating.EASY


#: Three plausible learners. The middle one matches the ~10% Again rate a
#: well-tuned FSRS deck tends to settle at; the others bracket it.
PROFILES: dict[str, AccuracyProfile] = {
    "strong": AccuracyProfile("Strong", again=0.05, hard=0.15, good=0.55, easy=0.25),
    "typical": AccuracyProfile("Typical", again=0.10, hard=0.25, good=0.50, easy=0.15),
    "struggling": AccuracyProfile("Struggling", again=0.25, hard=0.30, good=0.40, easy=0.05),
}

DEFAULT_PROFILE = "typical"


@dataclass(frozen=True, slots=True)
class DayLoad:
    """One simulated day."""

    local_date: str
    introduced: int
    reviewed: int
    #: Cards that were due and did not fit inside the capacity.
    deferred: int


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """What a year of the current settings would look like."""

    days: tuple[DayLoad, ...] = ()
    profile: str = DEFAULT_PROFILE
    new_words_per_day: int = 0
    review_capacity_per_day: int = 0
    pool_size: int = 0
    #: Words that were never introduced because the pool ran out.
    pool_left: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_introduced(self) -> int:
        return sum(day.introduced for day in self.days)

    @property
    def total_reviewed(self) -> int:
        return sum(day.reviewed for day in self.days)

    @property
    def peak_reviews(self) -> int:
        return max((day.reviewed for day in self.days), default=0)

    @property
    def mean_reviews(self) -> float:
        if not self.days:
            return 0.0
        return round(self.total_reviewed / len(self.days), 1)

    @property
    def steady_state_reviews(self) -> float:
        """The mean over the last 60 days, once the curve has flattened.

        The first weeks are always light — there is nothing to review yet — so
        an average over the whole year understates the load the user will
        actually live with.
        """
        tail = self.days[-60:]
        if not tail:
            return 0.0
        return round(sum(day.reviewed for day in tail) / len(tail), 1)

    @property
    def max_deferred(self) -> int:
        return max((day.deferred for day in self.days), default=0)

    @property
    def fits_capacity(self) -> bool:
        return self.max_deferred == 0

    def summary(self) -> str:
        """One paragraph a person can act on."""
        lines = [
            f"{self.new_words_per_day} new words a day, {self.profile} answers, "
            f"{len(self.days)} days.",
            f"Reviews: {self.mean_reviews} a day on average, "
            f"{self.steady_state_reviews} in the last two months, "
            f"{self.peak_reviews} at the busiest.",
            f"Introduced {self.total_introduced} of {self.pool_size} words; "
            f"{self.pool_left} left in the pool.",
        ]
        if self.fits_capacity:
            lines.append(f"Never exceeded the {self.review_capacity_per_day} review capacity.")
        else:
            lines.append(
                f"Went over the {self.review_capacity_per_day} review capacity; "
                f"up to {self.max_deferred} cards were pushed to the next day."
            )
        return "\n".join(lines)


class WorkloadSimulator:
    """Runs the real scheduler forward over an imaginary pool of words."""

    def __init__(self, settings: LearningSettings, start: datetime | None = None) -> None:
        self._settings = settings
        self._start = start

    def run(
        self,
        *,
        days: int = 365,
        pool_size: int = 6825,
        profile: str = DEFAULT_PROFILE,
        seed: int = 20260917,
    ) -> SimulationResult:
        """Simulate ``days`` days of study.

        The seed is fixed by default so that the same settings give the same
        answer twice: a simulation whose number changes on every click is not
        evidence of anything.
        """
        settings = self._settings
        accuracy = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
        rng = random.Random(seed)
        clock = FrozenClock(
            self._start or datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
            settings.timezone,
            settings.day_start_hour,
        )
        clock.set(clock.day_start(clock.today()))
        scheduler = SrsScheduler(settings, clock)

        cards: list[SrsCard] = []
        # Cards bucketed by the local day they fall due on. A year of 6,825
        # words is 2.7 million (day, card) pairs to look at if every day scans
        # the whole deck; bucketing turns the daily question into one lookup,
        # which is the difference between a button that responds and one that
        # appears to hang.
        buckets: dict[str, list[int]] = {}
        remaining_pool = max(int(pool_size), 0)
        next_word_id = 1
        capacity = settings.review_capacity_per_day or None
        loads: list[DayLoad] = []

        for _ in range(max(int(days), 0)):
            today = clock.today()
            now = clock.now_utc()

            # Everything due today or earlier: an overdue card from a skipped
            # day is not lost, it simply arrives late.
            overdue = sorted(day for day in buckets if day <= today)
            due: list[int] = []
            for day in overdue:
                due.extend(buckets.pop(day))

            answered = due if capacity is None else due[:capacity]
            for index in answered:
                card = scheduler.review(cards[index], accuracy.pick(rng), now).card
                cards[index] = card
                buckets.setdefault(clock.local_date(card.due_at), []).append(index)
            deferred = due[len(answered) :]
            if deferred:
                buckets.setdefault(today, []).extend(deferred)

            # New words are paused on exactly the same rule as the live engine.
            wanted = settings.new_words_per_day
            if capacity is not None and len(due) >= capacity:
                wanted = 0
            introduced = min(wanted, remaining_pool)
            due_at = scheduler.first_due_at(now)
            first_due_on = clock.local_date(due_at)
            for _index in range(introduced):
                cards.append(
                    SrsCard(
                        word_id=next_word_id,
                        state=CardState.INTRODUCED,
                        introduced_at=now,
                        introduced_on=today,
                        due_at=due_at,
                    )
                )
                buckets.setdefault(first_due_on, []).append(len(cards) - 1)
                next_word_id += 1
            remaining_pool -= introduced

            loads.append(
                DayLoad(
                    local_date=today,
                    introduced=introduced,
                    reviewed=len(answered),
                    deferred=len(deferred),
                )
            )
            clock.advance_to_day_start(1)

        result = SimulationResult(
            days=tuple(loads),
            profile=accuracy.name,
            new_words_per_day=settings.new_words_per_day,
            review_capacity_per_day=settings.review_capacity_per_day,
            pool_size=max(int(pool_size), 0),
            pool_left=remaining_pool,
            warnings=_warnings(loads, settings, remaining_pool),
        )
        return result


def _warnings(
    loads: list[DayLoad], settings: LearningSettings, pool_left: int
) -> tuple[str, ...]:
    """Things the user should read before trusting the plan."""
    notes: list[str] = []
    if not loads:
        return ()
    peak = max(day.reviewed for day in loads)
    if settings.review_capacity_per_day and peak >= settings.review_capacity_per_day:
        notes.append(
            "The busiest day hits the review capacity, so new words will pause "
            "sometimes. Raise the capacity or lower the daily new words."
        )
    if any(day.deferred for day in loads):
        notes.append("Some days could not fit all their reviews and pushed cards forward.")
    # An empty last day means one of two different things, and saying the
    # wrong one would send the user to the wrong setting.
    if pool_left == 0 and settings.new_words_per_day:
        notes.append("The word pool ran out before the end of the simulation.")
    return tuple(notes)


def simulate_current_settings(
    settings: LearningSettings,
    pool_size: int,
    days: int = 365,
    profile: str = DEFAULT_PROFILE,
    clock: DayClock | None = None,
) -> SimulationResult:
    """Convenience entry point for the Developer page."""
    start = clock.now_utc() if clock else None
    return WorkloadSimulator(settings, start).run(
        days=days, pool_size=pool_size, profile=profile
    )
