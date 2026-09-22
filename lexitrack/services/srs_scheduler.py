"""The scheduler: one rating in, a new schedule out.

This is a thin, deliberate wrapper around the ``fsrs`` library. It exists to
hold four decisions that the library cannot make for us, and to keep them in
one place instead of scattered through the services:

1. **Steps are days, not minutes.** The library's defaults schedule a new card
   1 minute and then 10 minutes later, which is right for a flashcard app the
   user sits in front of for twenty minutes. LexiTrack asks once a day through
   Telegram, so a 10-minute step would simply be missed and the card would
   arrive as "overdue" every time. Both learning and relearning steps are
   therefore a single day.

2. **Due times are snapped to the day boundary.** FSRS returns an instant; the
   engine works in days. A card due at 07:40 tomorrow and one due at 23:10
   tomorrow are the same thing here, and snapping means the morning briefing
   can say "today: 37 reviews" without that number changing during the day.

3. **A card is never due on the day it was answered.** After snapping, the due
   day is pushed out to at least the next learning day. Answering a card must
   visibly finish it for the day; a scheduler that can hand the same word back
   an hour later turns 25 new words into an open-ended evening.

4. **Struggling words are flagged here, not guessed at in the UI.** The three
   leech thresholds the user can set are applied to the card the moment it is
   rated, so ``needs_relearning`` is a stored fact that the Struggling Words
   view and the queue order can both rely on.

The library's own state travels in ``SrsCard.fsrs_state`` as JSON and is never
interpreted outside this module. That is what makes a future parameter
optimisation — or a library upgrade — a change to one file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from fsrs import Card as FsrsCard
from fsrs import Rating as FsrsRating
from fsrs import Scheduler, State

from ..core.clock import DayClock
from ..models.settings import LearningSettings
from ..models.srs import CardState, Rating, SrsCard

#: Recorded on every card and log row, so a schedule can be traced back to the
#: code that produced it after an upgrade changes the parameters.
SCHEDULER_VERSION = "fsrs-6"

#: One day, for both learning and relearning. See decision 1 above.
_STEP = (timedelta(days=1),)

_STATE_BY_FSRS: dict[State, CardState] = {
    State.Learning: CardState.LEARNING,
    State.Review: CardState.REVIEW,
    State.Relearning: CardState.RELEARNING,
}


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    """What a rating did: the new card, and the numbers worth logging."""

    card: SrsCard
    previous_state: CardState
    previous_due_at: datetime | None
    elapsed_days: float | None
    scheduled_days: float | None
    #: True when this answer newly flagged the word as struggling.
    became_struggling: bool = False
    #: True when stability crossed the mastery threshold with this answer.
    reached_mastery: bool = False


class SrsScheduler:
    """Turns ratings into schedules, in whole learning days."""

    def __init__(self, settings: LearningSettings, clock: DayClock) -> None:
        self._settings = settings
        self._clock = clock
        extra = {}
        if settings.fsrs_parameters is not None:
            extra["parameters"] = settings.fsrs_parameters
        self._scheduler = Scheduler(
            **extra,
            desired_retention=settings.desired_retention,
            learning_steps=_STEP,
            relearning_steps=_STEP,
            # Fuzzing spreads due dates to avoid clumps. It is off here
            # because the day boundary already quantises every interval, so
            # fuzzing would only move cards between days at random and make
            # the 7-day forecast unreproducible.
            enable_fuzzing=False,
        )

    @property
    def settings(self) -> LearningSettings:
        return self._settings

    @property
    def version(self) -> str:
        return SCHEDULER_VERSION

    @property
    def parameters(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self._scheduler.parameters)

    @property
    def personalised(self) -> bool:
        """True when the parameters were fitted to the user's own reviews."""
        return self._settings.fsrs_parameters is not None

    @property
    def params_hash(self) -> str:
        """A short fingerprint of everything that shapes an interval.

        Written on every review, so a schedule can be traced to the exact
        parameters and retention that produced it after either changes.
        """
        payload = json.dumps(
            {
                "w": [round(value, 6) for value in self.parameters],
                "retention": round(self._settings.desired_retention, 4),
                "version": SCHEDULER_VERSION,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def retrievability(self, card: SrsCard, now: datetime) -> float | None:
        """How likely the word is to be remembered now, from 0 to 1.

        None for a card never rated: before the first answer FSRS has no
        estimate, and inventing one would be the same mistake as inventing a
        rating for the introduction.
        """
        if not card.fsrs_state or card.stability is None:
            return None
        engine = self._to_engine(card, now)
        return float(self._scheduler.get_card_retrievability(engine, _utc(now)))

    # -- introduction ------------------------------------------------------

    def first_due_at(self, now: datetime) -> datetime:
        """When a word introduced now should first be asked: the next day."""
        return self._clock.next_day_start(now)

    # -- reviewing ---------------------------------------------------------

    def review(self, card: SrsCard, rating: Rating, now: datetime) -> ScheduleResult:
        """Apply one rating and return the card's new schedule.

        The card is not written here — the caller owns the transaction that
        saves the card and appends the log row together.
        """
        rating = Rating(int(rating))
        previous_state = card.state
        previous_due = card.due_at

        engine_card = self._to_engine(card, now)
        elapsed = self._elapsed_days(card, now)
        reviewed, _log = self._scheduler.review_card(
            engine_card, FsrsRating(int(rating)), review_datetime=_utc(now)
        )

        due_at = self._snap(reviewed.due, now)
        state = _STATE_BY_FSRS.get(reviewed.state, CardState.REVIEW)
        lapse = rating.is_lapse
        lapse_count = card.lapse_count + (1 if lapse else 0)
        consecutive = card.consecutive_lapses + 1 if lapse else 0
        stability = reviewed.stability

        struggling = self._is_struggling(
            was_flagged=card.needs_relearning,
            review_count=card.review_count + 1,
            lapse_count=lapse_count,
            consecutive=consecutive,
            stability=stability,
        )
        mastered = stability is not None and stability >= self._settings.mastery_stability_days

        updated = replace(
            card,
            state=state,
            due_at=due_at,
            first_review_at=card.first_review_at or _utc(now),
            last_review_at=_utc(now),
            review_count=card.review_count + 1,
            lapse_count=lapse_count,
            consecutive_lapses=consecutive,
            needs_relearning=struggling,
            stability=stability,
            difficulty=reviewed.difficulty,
            fsrs_state=json.dumps(reviewed.to_dict(), separators=(",", ":")),
            scheduler_version=SCHEDULER_VERSION,
        )
        return ScheduleResult(
            card=updated,
            previous_state=previous_state,
            previous_due_at=previous_due,
            elapsed_days=elapsed,
            scheduled_days=self._scheduled_days(now, due_at),
            became_struggling=struggling and not card.needs_relearning,
            reached_mastery=mastered,
        )

    def preview(self, card: SrsCard, now: datetime) -> dict[Rating, datetime]:
        """What each of the four answers would schedule.

        Shown in Developer Mode and used by the tests that check the four
        buttons are actually different. Cheap enough to call per card: the
        engine state is copied, nothing is written.
        """
        return {rating: self.review(card, rating, now).card.due_at for rating in Rating}

    # -- policies ----------------------------------------------------------

    def _is_struggling(
        self,
        *,
        was_flagged: bool,
        review_count: int,
        lapse_count: int,
        consecutive: int,
        stability: float | None,
    ) -> bool:
        """Decide whether a word counts as struggling after this answer.

        Three ways in, each catching a different failure: a run of Agains (it
        is not sticking now), a long failure history that has just repeated
        itself, and a stability that stays low after several reviews (it is
        being answered right but forgotten fast).

        The third rule waits for four reviews on purpose. A word's stability
        is naturally a day or two at the start, so applying a seven-day
        threshold immediately would flag almost every new word — the first
        version of this did, and it made the list meaningless.

        Getting out is deliberately harder than getting in: a flagged word
        stays flagged until it has no current failure streak *and* its
        stability is back above the weak threshold. Without that hysteresis a
        single lucky Good would empty the Struggling list.
        """
        settings = self._settings
        weak = settings.leech_weak_stability_days
        if consecutive >= settings.leech_consecutive:
            return True
        if lapse_count >= settings.leech_total_lapses and consecutive >= 1:
            return True
        if review_count >= 4 and lapse_count >= 2 and stability is not None and stability < weak:
            return True
        if was_flagged:
            recovered = consecutive == 0 and stability is not None and stability >= weak
            return not recovered
        return False

    # -- conversion --------------------------------------------------------

    def _to_engine(self, card: SrsCard, now: datetime) -> FsrsCard:
        """Rebuild the library's card from stored state.

        A card that has never been rated gets a fresh engine card: the
        introduction is not a review and no rating is invented for it, so as
        far as FSRS is concerned this rating is the word's first.
        """
        if card.fsrs_state:
            try:
                return FsrsCard.from_dict(json.loads(card.fsrs_state))
            except (ValueError, KeyError, TypeError):
                # Unreadable state is rebuilt rather than fatal: losing the
                # interval history costs accuracy, losing the card would cost
                # the user their word.
                pass
        return FsrsCard(due=_utc(now))

    def _elapsed_days(self, card: SrsCard, now: datetime) -> float | None:
        reference = card.last_review_at or card.introduced_at
        if reference is None:
            return None
        return round((_utc(now) - _utc(reference)).total_seconds() / 86400, 3)

    def _scheduled_days(self, now: datetime, due_at: datetime) -> float:
        return round((due_at - _utc(now)).total_seconds() / 86400, 3)

    def _snap(self, due: datetime, now: datetime) -> datetime:
        """Move a due instant onto a learning day boundary, never today.

        Rounding is to the day the instant falls in, so a 20-hour interval
        lands tomorrow rather than being rounded away to nothing.
        """
        clock = self._clock
        earliest = clock.next_day_start(now)
        day = clock.local_date(due)
        candidate = clock.day_start(day)
        return max(candidate, earliest)


def _utc(moment: datetime) -> datetime:
    """Make a datetime timezone-aware; stored values are UTC by convention."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
