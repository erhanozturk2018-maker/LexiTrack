"""What the current settings mean, in days, for one imaginary word.

Settings like "count as known after 21 days" are thresholds on a number the
user never sees — FSRS stability. This module answers the question they
actually have in front of the Learning page: *if I keep pressing this button,
when does the word stop coming back?*

The answer is **computed, never written down**. Every path here runs the real
:class:`SrsScheduler` over a throwaway card with the settings passed in, so
changing the target retention, the mastery threshold or the day start changes
these numbers with no text to keep in step. The one rule this file must obey
is that it touches nothing: no database, no saved settings, no shared clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..core.clock import FrozenClock
from ..models.settings import LearningSettings
from ..models.srs import CardState, Rating, SrsCard
from .srs_scheduler import SrsScheduler

#: Where the imaginary word is introduced. Any instant would do; a fixed one
#: keeps the numbers reproducible in tests and identical between runs.
_ANCHOR = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)

#: Long enough for a slow path to finish, short enough to stay honest about
#: one that never will.
_MAX_ANSWERS = 12


@dataclass(frozen=True, slots=True)
class SchedulePath:
    """One way of answering, and where it leads."""

    #: The answer pressed every time, or None for the mixed example.
    label: str
    #: Days after introduction on which the word is asked.
    asked_on: tuple[int, ...]
    #: The day the word became Known, or None if it never did.
    known_on: int | None

    @property
    def answers(self) -> int:
        return len(self.asked_on)


def paths(settings: LearningSettings) -> list[SchedulePath]:
    """The three paths shown to the user: Easy, Good, and Good with one slip."""
    return [
        _walk("Easy every time", settings, lambda step: Rating.EASY),
        _walk("Good every time", settings, lambda step: Rating.GOOD),
        _walk(
            "Good, with one Again",
            settings,
            lambda step: Rating.AGAIN if step == 2 else Rating.GOOD,
        ),
    ]


def _walk(label: str, settings: LearningSettings, answer) -> SchedulePath:
    clock = FrozenClock(_ANCHOR, settings.timezone, settings.day_start_hour)
    scheduler = SrsScheduler(settings, clock)
    introduced = clock.day_start(clock.now_utc())
    card = SrsCard(
        word_id=0,
        state=CardState.INTRODUCED,
        due_at=scheduler.first_due_at(clock.now_utc()),
    )
    asked: list[int] = []
    known: int | None = None
    for step in range(_MAX_ANSWERS):
        now = card.due_at
        day = (now - introduced).days
        asked.append(day)
        result = scheduler.review(card, answer(step), now)
        card = result.card
        if result.reached_mastery:
            known = day
            break
    return SchedulePath(label=label, asked_on=tuple(asked), known_on=known)
