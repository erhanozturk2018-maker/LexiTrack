"""The learning engine's single surface.

Everything that wants to know what to study today — the Study tab, the
Telegram thread, the daily brief, the simulator — asks this class, and nothing
else. That is the whole point: two clients that computed "today's queue"
separately would drift apart, and the one the user trusted would be whichever
they happened to open second.

The rules implemented here, in order of how much trouble they save:

* **New words are offered, not scheduled.** The user studies the day's words
  themselves and confirms; only then does a card exist. Nothing is invented on
  their behalf.
* **A word introduced today is never reviewed today.** Enforced in SQL by the
  repository and re-checked here, because it is the difference between a
  finite evening and an endless one.
* **Intake pauses when the review load is already over capacity.** Adding 25
  more words to a day that is already too big does not make tomorrow better.
* **Known is the user's.** A word answered correctly after a long gap (the
  threshold in Settings, 21 days by default) is offered as Known
  (:meth:`known_suggestions`). Only the user marks it (:meth:`confirm_known`,
  recorded as learned here, or by hand). Until then a word is never sent
  further away than that gap, so the offer can come (the Known check).
* **Known words keep their own pace.** While they are still reviewed they aim
  at the Known target (85% by default), and a Known word answered wrong is
  offered for learning again (:meth:`relearn`), never moved silently. With
  reviewing of Known words off, their cards are archived, not deleted.
* **Known words that come back at once are spread.** Turning their reviews
  back on, or raising their target, would put hundreds on one day; they
  return a few a day instead, only where a day has room, so reviews and new
  words never stop for them (:meth:`_spread`).

The service owns no Qt and no network code. It is given a clock, so a test can
run a year of study in a second, and it never sleeps or polls.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from ..core.clock import DayClock
from ..database.connection import Database
from ..models.attempt import ROUTE_V3, LearningAttempt, Task
from ..models.settings import LearningSettings, Setting
from ..models.srs import (
    CardState,
    Channel,
    PlanOutlook,
    Rating,
    ReviewLogEntry,
    SrsCard,
    StudyPlan,
)
from ..models.user_word_state import ReviewStatus, StatusCause
from ..repositories import (
    AttemptRepository,
    CardRepository,
    ListRepository,
    PlanRepository,
    ReviewSession,
    RuntimeRepository,
    SessionRepository,
    SettingsRepository,
    StateRepository,
    StoredWord,
    WordRepository,
)
from ..repositories.word_repository import Candidate
from .review_queue import Queue, effective_capacity
from .review_queue import build as build_queue
from .srs_scheduler import SrsScheduler

#: How far ahead the Study page looks.
FORECAST_DAYS = 7

#: Past this many days, spreading places words whether a day has room or not,
#: so a limit set lower than the day's new words cannot hold them back forever.
_SPREAD_HORIZON = 3650


@dataclass(frozen=True, slots=True)
class StudyItem:
    """One card to answer, with the word it belongs to."""

    word: StoredWord
    card: SrsCard

    @property
    def is_struggling(self) -> bool:
        return self.card.needs_relearning


@dataclass(frozen=True, slots=True)
class DailyPlan:
    """What today looks like: what to learn, what to review, what is left."""

    local_date: str
    plan: StudyPlan | None = None
    #: Words offered for introduction today and not yet confirmed.
    new_words: tuple[StoredWord, ...] = ()
    #: Words already introduced today, in this or an earlier session.
    introduced_today: tuple[StoredWord, ...] = ()
    due_count: int = 0
    #: Due words left for another day because the day's limit is reached.
    due_left_over: int = 0
    reviews_done_today: int = 0
    #: Words in the plan that have never been introduced.
    pool_remaining: int = 0
    #: Words that would be in the pool but have no definition to ask from.
    without_definition: int = 0
    new_target: int = 0
    review_capacity: int = 0
    #: ``(local date, cards due)`` for the next :data:`FORECAST_DAYS` days.
    forecast: tuple[tuple[str, int], ...] = ()
    #: Set when intake was reduced or paused, in words the UI can show.
    intake_note: str | None = None
    #: True when the workload valve stopped intake, as opposed to the day
    #: simply being finished. The two look the same — no new words — and mean
    #: opposite things, so they are not left for the reader to infer.
    intake_paused: bool = False

    @property
    def has_plan(self) -> bool:
        return self.plan is not None

    @property
    def new_remaining(self) -> int:
        return len(self.new_words)

    @property
    def is_intake_done(self) -> bool:
        """True when nothing more is offered today, for whatever reason."""
        return not self.new_words

    @property
    def has_work(self) -> bool:
        return bool(self.new_words) or self.due_count > 0

    @property
    def is_finished(self) -> bool:
        return self.has_plan and not self.has_work


@dataclass(frozen=True, slots=True)
class IntroductionResult:
    """What confirming the day's new words actually did."""

    introduced: tuple[StoredWord, ...] = ()
    #: Words that already had a card, so nothing moved.
    skipped: int = 0
    first_due_on: str | None = None

    @property
    def count(self) -> int:
        return len(self.introduced)


@dataclass(frozen=True, slots=True)
class AnswerOutcome:
    """What one rating did, in terms the caller can show or send."""

    word: StoredWord
    rating: Rating
    card: SrsCard
    next_due_on: str
    interval_days: int
    #: True when this answer was a repeat that changed nothing.
    duplicate: bool = False
    became_struggling: bool = False
    reached_mastery: bool = False
    #: True when the word was answered correctly after a long gap and is not
    #: yet Known: the caller offers to mark it Known. The engine never does
    #: it itself.
    suggest_known: bool = False
    #: True when a Known word was answered wrong (or rated Again): the caller
    #: offers to learn it again. The engine never does it itself.
    suggest_relearn: bool = False
    #: True when the Known check brought the next review closer.
    known_check: bool = False
    #: The answer's row in review_logs, for practice that follows it.
    log_id: int | None = None
    #: Whether the option chosen was right.
    correct: bool | None = None


@dataclass(frozen=True, slots=True)
class SpreadResult:
    """Known words placed over the coming days, and until when."""

    count: int = 0
    #: How many days, from tomorrow, the last of them is away.
    days: int = 0
    last_day: str | None = None

    def __add__(self, other: SpreadResult) -> SpreadResult:
        if not other.count:
            return self
        if not self.count:
            return other
        later = max(self, other, key=lambda r: r.days)
        return SpreadResult(self.count + other.count, later.days, later.last_day)

    @property
    def message(self) -> str | None:
        """What to tell the learner, or None when nothing was spread."""
        if not self.count:
            return None
        noun = "Known word will" if self.count == 1 else "Known words will"
        if self.days <= 1:
            return f"{self.count:,} {noun} come back tomorrow."
        return f"{self.count:,} {noun} come back over the next {self.days} days."


@dataclass(frozen=True, slots=True)
class _LastAnswer:
    """Enough of the last answer to take it back exactly."""

    word_id: int
    card_before: SrsCard
    log_id: int
    session_id: str | None
    update_key: str | None
    answered_at: datetime


class LearningService:
    """Plans the day, hands out reviews and records answers."""

    def __init__(self, database: Database, clock: DayClock | None = None) -> None:
        self._db = database
        self._settings_repo = SettingsRepository(database)
        self._runtime = RuntimeRepository(database)
        self._plans = PlanRepository(database)
        self._cards = CardRepository(database)
        self._sessions = SessionRepository(database)
        self._attempts = AttemptRepository(database)
        self._words = WordRepository(database)
        self._state = StateRepository(database)
        self._lists = ListRepository(database)
        self._settings = self._settings_repo.load()
        self._clock = clock or DayClock(self._settings.timezone, self._settings.day_start_hour)
        self._scheduler = SrsScheduler(self._settings, self._clock)
        # One answer can be taken back, and only by the client that gave it:
        # the desk and the Telegram thread each hold their own service.
        self._last_answer: _LastAnswer | None = None
        self._last_spread: SpreadResult | None = None

    # -- configuration -----------------------------------------------------

    @property
    def settings(self) -> LearningSettings:
        return self._settings

    @property
    def database(self) -> Database:
        """The database this engine reads and writes, for services built beside it."""
        return self._db

    def plan_word_ids(self) -> list[int]:
        """The active plan's words, in plan order; empty without a plan."""
        plan = self.active_plan()
        return list(self._plans.word_ids(plan.id)) if plan else []

    def choice_candidates(self) -> list[Candidate]:
        """Every word with a definition: what the other three options of a
        question are chosen from (services/review_tasks.py)."""
        return self._words.choice_candidates()

    @property
    def clock(self) -> DayClock:
        return self._clock

    @property
    def scheduler(self) -> SrsScheduler:
        return self._scheduler

    @property
    def runtime(self) -> RuntimeRepository:
        return self._runtime

    def refresh_settings(self) -> LearningSettings:
        """Re-read the settings and rebuild anything derived from them.

        Called after the Settings window saves and, by the Telegram thread,
        before each day's work: the two clients share one settings table, so
        this is how a change made at the desk reaches the bot.
        """
        self._settings = self._settings_repo.load()
        self._clock.configure(self._settings.timezone, self._settings.day_start_hour)
        self._scheduler = SrsScheduler(self._settings, self._clock)
        return self._settings

    def save_settings(self, values: dict[str, object]) -> LearningSettings:
        """Save settings, and do what a change to the Known settings means
        for the cards (see :meth:`_known_settings_changed`)."""
        before = self._settings
        self._settings_repo.set_many(values)
        after = self.refresh_settings()
        spread = self._known_settings_changed(before, after)
        if spread.count:
            self._last_spread = spread
        return after

    def take_spread_note(self) -> str | None:
        """What the last settings change spread, once, for the Settings window."""
        note, self._last_spread = self._last_spread, None
        return note.message if note else None

    # -- plans -------------------------------------------------------------

    def plans(self) -> list[StudyPlan]:
        return self._plans.list_all()

    def active_plan(self) -> StudyPlan | None:
        return self._plans.active()

    def create_plan(
        self,
        name: str,
        list_ids: Sequence[int],
        description: str | None = None,
        make_active: bool = True,
        all_lists: bool = False,
    ) -> StudyPlan:
        plan = self._plans.create(
            name,
            description=description,
            list_ids=list_ids,
            make_active=make_active,
            all_lists=all_lists,
        )
        self.refresh_settings()
        return plan

    def update_plan(
        self,
        plan_id: int,
        name: str | None = None,
        description: str | None = None,
        list_ids: Sequence[int] | None = None,
        all_lists: bool | None = None,
    ) -> StudyPlan:
        return self._plans.update(
            plan_id, name=name, description=description, list_ids=list_ids,
            all_lists=all_lists,
        )

    def set_active_plan(self, plan_id: int) -> StudyPlan:
        plan = self._plans.set_active(plan_id)
        self.refresh_settings()
        return plan

    def delete_plan(self, plan_id: int) -> None:
        """Delete a plan. Its words, cards and progress are untouched."""
        self._plans.delete(plan_id)
        if self._settings.active_plan_id == plan_id:
            remaining = self._plans.list_all()
            if remaining:
                self._plans.set_active(remaining[0].id)
            else:
                self._settings_repo.set(Setting.ACTIVE_PLAN_ID, "")
        self.refresh_settings()

    def plan_counts(self, plan_id: int) -> dict[str, int]:
        return self._plans.counts(plan_id)

    def selection_outlook(
        self, list_ids: Sequence[int], *, all_lists: bool = False
    ) -> PlanOutlook:
        """What a selection of lists would give, before it is saved.

        Uses the engine's own rule for which words are offered, including
        the *Offer words you have never answered* setting, so the numbers
        are the ones the Study page will then show.
        """
        return self._plans.selection_outlook(
            list_ids,
            all_lists=all_lists,
            include_not_reviewed=self._settings.new_words_include_not_reviewed,
        )

    # -- the day -----------------------------------------------------------

    def daily_plan(self) -> DailyPlan:
        """Everything today's screen and today's message need, in one read."""
        today = self._clock.today()
        plan = self.active_plan()
        if plan is None:
            return DailyPlan(local_date=today)

        scope = self._plans.word_ids(plan.id)
        introduced_ids = self._cards.introduced_on(today, scope)
        queue = self._queue(scope)
        due = queue.cards
        done = self._reviews_done_today(today)
        forecast = self.forecast()
        tomorrow = forecast[1][1] if len(forecast) > 1 else 0
        target, note, paused = self._intake_target(
            len(introduced_ids), len(due) + queue.left_over, tomorrow
        )
        offered = (
            self._plans.candidate_word_ids(
                plan.id,
                limit=target,
                include_not_reviewed=self._settings.new_words_include_not_reviewed,
            )
            if target
            else []
        )
        outlook = self._plans.outlook(
            plan.id, include_not_reviewed=self._settings.new_words_include_not_reviewed
        )
        return DailyPlan(
            local_date=today,
            plan=plan,
            new_words=tuple(self._words_in_order(offered)),
            introduced_today=tuple(self._words_in_order(introduced_ids)),
            due_count=len(due),
            due_left_over=queue.left_over,
            reviews_done_today=done,
            pool_remaining=outlook.to_introduce,
            without_definition=outlook.without_definition,
            new_target=self._settings.new_words_per_day,
            review_capacity=effective_capacity(self._settings.review_capacity_per_day),
            forecast=forecast,
            intake_note=note,
            intake_paused=paused,
        )

    def _intake_target(
        self, introduced_today: int, due_today: int, due_tomorrow: int = 0
    ) -> tuple[int, str | None, bool]:
        """How many new words to offer now, and why it is not the full count.

        Two subtractions. The first is bookkeeping: words already confirmed
        today do not come round again. The second is the workload valve — when
        today's reviews already exceed the capacity the user set, intake stops
        rather than making tomorrow worse. It is the only automatic brake in
        the engine, and it is reported in words rather than applied silently.
        """
        settings = self._settings
        remaining = max(settings.new_words_per_day - introduced_today, 0)
        if remaining == 0:
            if settings.new_words_per_day and introduced_today >= settings.new_words_per_day:
                return 0, f"Today's {settings.new_words_per_day} new words are done.", False
            return 0, None, False
        capacity = effective_capacity(settings.review_capacity_per_day)
        if due_today >= capacity:
            return 0, (
                f"New words are paused: {due_today} reviews are due today, "
                f"over the {capacity} you set. Clear some and they will come back."
            ), True
        # Today's new words are all due tomorrow: they must fit beside the
        # reviews already due then, or tomorrow starts over the limit.
        room = capacity - due_tomorrow
        if room < remaining:
            if room <= 0:
                return 0, (
                    f"New words are paused: {due_tomorrow} reviews are already due "
                    f"tomorrow, at your limit of {capacity}."
                ), True
            already = (
                f"{due_tomorrow} reviews are already due tomorrow, and more"
                if due_tomorrow
                else "all of today's new words come back tomorrow, and more"
            )
            return room, (
                f"{room} new words today, not {remaining}: {already} would pass your "
                f"limit of {capacity}."
            ), True
        return remaining, None, False

    def forecast(self, days: int = FORECAST_DAYS) -> tuple[tuple[str, int], ...]:
        """How many cards fall due on each of the next ``days``.

        Overdue cards are counted under today, because that is when the user
        will actually see them. Days are computed with the clock rather than
        in SQL so the time zone and the day start are applied once.
        """
        plan = self.active_plan()
        if plan is None or days <= 0:
            return ()
        today = self._clock.today()
        horizon = self._clock.day_start(self._clock.shift_days(days))
        counts = {self._clock.shift_days(offset): 0 for offset in range(days)}
        for moment in self._cards.due_timestamps(
            self._plans.word_ids(plan.id), until=horizon
        ):
            day = max(self._clock.local_date(moment), today)
            if day in counts:
                counts[day] += 1
        return tuple(sorted(counts.items()))

    def state_counts(self) -> dict[str, int]:
        plan = self.active_plan()
        if plan is None:
            return {state.value: 0 for state in CardState}
        return self._cards.state_counts(self._plans.word_ids(plan.id))

    # -- introducing new words ---------------------------------------------

    def introduce(self, word_ids: Sequence[int] | None = None) -> IntroductionResult:
        """Confirm that today's new words have been studied.

        With no argument this takes exactly the words :meth:`daily_plan`
        offered, so the desktop button and the Telegram button cannot disagree
        about which 25 words were meant. Passing ids explicitly is for the
        user picking a subset.
        """
        plan = self.active_plan()
        if plan is None:
            return IntroductionResult()
        today = self._clock.today()
        now = self._clock.now_utc()
        if word_ids is None:
            word_ids = [word.id for word in self.daily_plan().new_words]
        requested = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not requested:
            return IntroductionResult()

        due_at = self._scheduler.first_due_at(now)
        introduced = self._cards.introduce(
            requested, plan_id=plan.id, now=now, local_date=today, due_at=due_at
        )
        self._runtime.mark_done(RuntimeRepository.LAST_INTAKE_ON, today)
        return IntroductionResult(
            introduced=tuple(self._words_in_order(introduced)),
            skipped=len(requested) - len(introduced),
            first_due_on=self._clock.local_date(due_at) if introduced else None,
        )

    # -- reviewing ---------------------------------------------------------

    def review_queue(self, limit: int | None = None) -> list[StudyItem]:
        """Today's due cards, in the order they should be asked.

        Struggling words come first, then relearning, then the most overdue.
        The list is capped at the review capacity: a queue longer than the
        user agreed to face is a queue they will abandon.
        """
        plan = self.active_plan()
        if plan is None:
            return []
        cards = self._due_cards(self._plans.word_ids(plan.id), limit=limit)
        if not cards:
            return []
        words = {word.id: word for word in self._words.get_many([c.word_id for c in cards])}
        return [
            StudyItem(word=words[card.word_id], card=card)
            for card in cards
            if card.word_id in words
        ]

    def retrievability(self, word_id: int) -> float | None:
        """FSRS's chance that the word is recalled now; None without a card."""
        card = self._cards.get(int(word_id))
        if card is None:
            return None
        return self._scheduler.retrievability(card, self._clock.now_utc())

    def study_item(self, word_id: int) -> StudyItem | None:
        """One word's card as a review item: what Undo puts back on screen."""
        card = self._cards.get(int(word_id))
        word = self._words.get(int(word_id))
        if card is None or word is None:
            return None
        return StudyItem(word=word, card=card)

    def _due_cards(self, scope: Sequence[int], limit: int | None) -> list[SrsCard]:
        cards = list(self._queue(scope).cards)
        return cards[:limit] if limit else cards

    def _queue(self, scope: Sequence[int]) -> Queue:
        """Today's reviews, chosen and ordered by services/review_queue.py."""
        now = self._clock.now_utc()
        cards = self._cards.due_cards(
            scope, now=now, before_local_date=self._clock.today(), limit=None
        )
        if not self._settings.review_known_words:
            # Before the limit is applied, so Known words do not take places.
            known = {
                word.id
                for word in self._words.get_many([card.word_id for card in cards])
                if word.status is ReviewStatus.KNOWN
            }
            cards = [card for card in cards if card.word_id not in known]
        recall = {card.word_id: self._scheduler.retrievability(card, now) for card in cards}
        return build_queue(
            cards,
            recall,
            self._settings.review_capacity_per_day,
            warm_up=self._settings.review_warm_up,
            fragile_every=self._settings.fragile_every,
        )

    def start_session(
        self, channel: Channel = Channel.DESKTOP, chat_id: str | None = None
    ) -> ReviewSession:
        plan = self.active_plan()
        return self._sessions.start(
            channel=channel,
            now=self._clock.now_utc(),
            local_date=self._clock.today(),
            plan_id=plan.id if plan else None,
            planned_count=len(self.review_queue()),
            chat_id=chat_id,
        )

    def open_session(self, channel: Channel) -> ReviewSession | None:
        return self._sessions.open_session(channel)

    def session(self, session_id: str) -> ReviewSession | None:
        return self._sessions.get(session_id)

    def show_in_session(
        self, session_id: str, word_id: int | None, message_id: str | None = None
    ) -> ReviewSession:
        """Record which card a session is showing, and in which message.

        The Telegram client asks this before rendering a card, so that a tap
        on an older card — a message scrolled up, a re-delivered callback —
        can be recognised as not the card on screen and ignored.
        """
        if word_id is None:
            return self._sessions.update(session_id, message_id=message_id, clear_current=True)
        return self._sessions.update(
            session_id, message_id=message_id, current_word_id=int(word_id)
        )

    def finish_session(self, session_id: str) -> ReviewSession:
        return self._sessions.finish(session_id, self._clock.now_utc())

    def save_flow_state(self, session_id: str, state: str | None) -> None:
        """Store where a session's flow stands (see ``services/review_flow.py``)."""
        self._sessions.set_flow_state(session_id, state)

    def close_stale_sessions(self) -> int:
        """Close sessions left open on an earlier day. Called at startup."""
        return self._sessions.finish_stale(self._clock.today(), self._clock.now_utc())

    def review(
        self,
        word_id: int,
        rating: Rating,
        *,
        task: Task,
        correct: bool,
        attempts: Sequence[LearningAttempt] = (),
        session_id: str | None = None,
        channel: Channel = Channel.DESKTOP,
        update_key: str | None = None,
    ) -> AnswerOutcome | None:
        """Record the day's answer for a word and reschedule it.

        ``correct`` is whether the option chosen was right; ``rating`` is the
        effort the learner chose for a right answer, and Again for a wrong
        one. Both are kept, apart, with the task asked. The attempt behind it
        is linked to the answer's log row, so Undo reaches it.

        ``update_key`` makes the call idempotent, which is what Telegram needs:
        it re-delivers a callback whenever it is not certain the answer
        arrived, and a second delivery must not rate the card twice.

        Returns ``None`` when the word has no card at all — a message from
        before the word was removed, say. A duplicate returns an outcome with
        ``duplicate`` set, so the caller can still answer the user.
        """
        return self._rate(
            word_id,
            Rating(int(rating)),
            session_id=session_id,
            channel=channel,
            update_key=update_key,
            task=task,
            correct=correct,
            attempts=attempts,
        )

    def record_practice(self, attempt: LearningAttempt, log_id: int | None) -> int:
        """Record practice — a new word asked, a word asked again after a
        miss — which never changes the schedule.

        Linked to the answer it followed (``log_id``), so taking that answer
        back takes the practice with it.
        """
        return self._attempts.add(replace(attempt, review_log_id=log_id))

    def _rate(
        self,
        word_id: int,
        rating: Rating,
        *,
        session_id: str | None,
        channel: Channel,
        update_key: str | None,
        task: Task,
        correct: bool,
        attempts: Sequence[LearningAttempt],
    ) -> AnswerOutcome | None:
        card = self._cards.get(int(word_id))
        word = self._words.get(int(word_id))
        if card is None or word is None:
            return None
        today = self._clock.today()

        duplicate = bool(update_key) and not self._sessions.claim_update(update_key)
        # One rating per word per day, whichever client asks: the schedule
        # hears about a day's memory of a word once. A second answer the same
        # day — from the other client, from a stale screen — changes nothing.
        if duplicate or self._cards.rated_on(word.id, today):
            return AnswerOutcome(
                word=word,
                rating=rating,
                card=card,
                next_due_on=self._clock.local_date(card.due_at) if card.due_at else "",
                interval_days=0,
                duplicate=True,
            )

        now = self._clock.now_utc()
        known = word.status is ReviewStatus.KNOWN
        result = self._scheduler.review(
            card,
            rating,
            now,
            known=known,
            check_days=self._check_days(word, card, rating, correct=correct, now=now),
        )
        # One answer is one event: the card, its log, the attempts and the
        # session count are written together or not at all, and Undo takes
        # all of them back. The word's status is not part of it: reaching
        # long-term memory is reported, and only the user marks a word Known.
        with self._db.transaction():
            self._cards.save(result.card)
            log_id = self._cards.log(
                ReviewLogEntry(
                    word_id=card.word_id,
                    session_id=session_id,
                    channel=channel,
                    reviewed_at=now,
                    reviewed_on=today,
                    rating=rating,
                    state_before=result.previous_state,
                    state_after=result.card.state,
                    due_before=result.previous_due_at,
                    due_after=result.card.due_at,
                    elapsed_days=result.elapsed_days,
                    scheduled_days=result.scheduled_days,
                    stability_after=result.card.stability,
                    difficulty_after=result.card.difficulty,
                    scheduler_version=result.card.scheduler_version,
                    params_hash=self._scheduler.params_hash_for(known=known),
                    route_version=ROUTE_V3,
                    task=task.value,
                    correct=correct,
                )
            )
            for attempt in attempts:
                self._attempts.add(
                    replace(attempt, review_log_id=log_id, session_id=session_id)
                )
            if session_id:
                self._sessions.update(session_id, done_increment=1, clear_current=True)

        self._last_answer = _LastAnswer(
            word_id=word.id,
            card_before=card,
            log_id=log_id,
            session_id=session_id,
            update_key=update_key,
            answered_at=now,
        )

        due_on = self._clock.local_date(result.card.due_at)
        return AnswerOutcome(
            word=word,
            rating=rating,
            card=result.card,
            next_due_on=due_on,
            interval_days=self._clock.days_between(now, result.card.due_at),
            became_struggling=result.became_struggling,
            reached_mastery=result.reached_mastery,
            suggest_known=(not known and word.id in self.known_evidence([word.id])),
            suggest_relearn=known and rating is Rating.AGAIN,
            known_check=result.known_check,
            log_id=log_id,
            correct=correct,
        )

    def _check_threshold(self) -> int:
        """The gap, in whole days, after which Known is offered."""
        return max(int(round(self._settings.mastery_stability_days)), 1)

    def _check_days(
        self,
        word: StoredWord,
        card: SrsCard,
        rating: Rating,
        *,
        correct: bool,
        now: datetime,
    ) -> int | None:
        """The Known check for this answer: at most this many days to the next
        review, or None when it does not apply.

        It applies to a word that is not Known and has no case for Known yet
        — counting this answer: a right answer after the long gap makes the
        case, so the check stops with it, and an Again takes it away, so the
        check starts again.
        """
        if word.status is ReviewStatus.KNOWN:
            return None
        if rating is Rating.AGAIN:
            return self._check_threshold()
        if word.id in self.known_evidence([word.id]):
            return None
        reference = card.last_review_at or card.introduced_at
        if correct and reference is not None:
            gap = self._clock.days_between(reference, now)
            if gap >= self._settings.mastery_stability_days:
                return None
        return self._check_threshold()

    # -- undo --------------------------------------------------------------

    def can_undo(self, session_id: str | None = None) -> bool:
        """True when the last answer given here, in this session, can be taken back."""
        return self._undoable(session_id) is not None

    def undo_last_answer(self, session_id: str | None = None) -> StoredWord | None:
        """Take back the last answer: the card, the status and the session as before.

        The answer's row in ``review_logs`` is kept and marked as undone, so
        the history still shows the slip; statistics and parameter fitting
        leave it out. Returns the word, now due again, or None when there is
        nothing to take back - no answer yet, a different session, or the
        word has been answered again since.
        """
        last = self._undoable(session_id)
        if last is None:
            return None
        now = self._clock.now_utc()
        with self._db.transaction():
            self._cards.save(last.card_before)
            self._cards.mark_undone(last.log_id, now)
            self._attempts.mark_undone_for_log(last.log_id, now)
            if last.session_id:
                self._sessions.update(
                    last.session_id, done_increment=-1, current_word_id=last.word_id
                )
            if last.update_key:
                self._sessions.release_update(last.update_key)
        self._last_answer = None
        return self._words.get(last.word_id)

    def _undoable(self, session_id: str | None) -> _LastAnswer | None:
        last = self._last_answer
        if last is None or last.session_id != session_id:
            return None
        card = self._cards.get(last.word_id)
        # Answered again since, from the other client: the record is stale.
        if card is None or card.last_review_at != last.answered_at:
            return None
        return last

    def preview_intervals(self, word_id: int) -> dict[Rating, int]:
        """How many days away each answer would put the word.

        Shown under the four answer buttons, so the user can see that Hard and
        Good are not the same thing, and in Developer Mode for checking the
        scheduler's behaviour without reading the database.
        """
        card = self._cards.get(int(word_id))
        word = self._words.get(int(word_id))
        if card is None or word is None:
            return {}
        now = self._clock.now_utc()
        checks = {
            rating: self._check_days(word, card, rating, correct=True, now=now)
            for rating in Rating
        }
        due = self._scheduler.preview(
            card, now, known=word.status is ReviewStatus.KNOWN, check_days=checks
        )
        return {rating: self._clock.days_between(now, moment) for rating, moment in due.items()}

    # -- status --------------------------------------------------------------

    def mark_known(
        self, word_ids: Sequence[int], cause: StatusCause = StatusCause.MANUAL
    ) -> int:
        """Declare words Known.

        With reviewing of Known words on, a card keeps its history and is
        moved at once to the Known target; with it off, the card is archived
        rather than deleted, so resetting the status later resumes the same
        schedule instead of starting the word over.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return 0
        before = {word_id: status for word_id, (status, _) in self._state.states(ids).items()}
        plan = self.active_plan() if cause is StatusCause.MASTERY else None
        changed = self._state.set_status_many(
            ids,
            ReviewStatus.KNOWN,
            cause=cause,
            plan_id=plan.id if plan else None,
            at=self._clock.now_utc(),
        )
        self.status_changed(before)
        return changed

    def status_changed(self, before: Mapping[int, ReviewStatus]) -> SpreadResult:
        """Bring the cards of words whose status changed in line with it.

        ``before`` is each word's status before the change. Called by every
        path that changes a status — the word list, sorting, Undo — so the
        schedule never disagrees with it:

        * Known, with reviewing of Known words off: the card is archived.
        * Otherwise an archived card comes back into the schedule.
        * A word that became Known, or stopped being Known, is moved to the
          target it now has — unless it is due today: today's list stays.
        """
        ids = [int(word_id) for word_id in before]
        if not ids:
            return SpreadResult()
        words = {word.id: word for word in self._words.get_many(ids)}
        cards = self._cards.get_many(ids)
        archive: list[int] = []
        resume: list[int] = []
        moved: list[int] = []
        for word_id, card in cards.items():
            word = words.get(word_id)
            if word is None:
                continue
            is_known = word.status is ReviewStatus.KNOWN
            if is_known and not self._settings.review_known_words:
                if card.state is not CardState.ARCHIVED:
                    archive.append(word_id)
                continue
            was_known = ReviewStatus(before[word_id]) is ReviewStatus.KNOWN
            if card.state is CardState.ARCHIVED:
                resume.append(word_id)
                moved.append(word_id)
            elif was_known != is_known:
                moved.append(word_id)
        if archive:
            self._cards.set_state(archive, CardState.ARCHIVED)
        self.resume(resume)
        return self._retarget(moved, returning=resume)

    def relearn(self, word_ids: Sequence[int]) -> int:
        """Known words the learner forgot and chose to learn again.

        They become Unknown, recorded as forgotten, and go back to the general
        target; their schedule carries on from the answer that missed them.
        Words that are not Known are left alone.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        before = {
            word_id: status
            for word_id, (status, _) in self._state.states(ids).items()
            if status is ReviewStatus.KNOWN
        }
        if not before:
            return 0
        changed = self._state.set_status_many(
            list(before),
            ReviewStatus.UNKNOWN,
            cause=StatusCause.FORGOTTEN,
            at=self._clock.now_utc(),
        )
        self.status_changed(before)
        return changed

    def forgotten_known(self) -> list[StoredWord]:
        """Known words whose latest answer was Again: offered for learning
        again until they are, or until they are remembered."""
        cards = [card for card in self._cards.all_cards() if card.state is not CardState.ARCHIVED]
        last = self._cards.last_ratings([card.word_id for card in cards])
        missed = [card.word_id for card in cards if last.get(card.word_id) is Rating.AGAIN]
        return [
            word for word in self._words_in_order(missed) if word.status is ReviewStatus.KNOWN
        ]

    # -- moving cards ----------------------------------------------------------

    def _retarget(
        self, word_ids: Sequence[int], returning: Sequence[int] = ()
    ) -> SpreadResult:
        """Move reviewed cards to the date their word's target gives.

        A card due today keeps its place, so today's number never changes
        under the learner — unless it is ``returning`` from the archive, when
        it was on no one's list. A card whose date has already passed (a
        raised target, a long pause) is not piled onto tomorrow: it is spread.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return SpreadResult()
        now = self._clock.now_utc()
        tomorrow = self._clock.next_day_start(now)
        cards = self._cards.get_many(ids)
        words = {word.id: word for word in self._words.get_many(ids)}
        evidence = self.known_evidence(ids)
        back = {int(word_id) for word_id in returning}
        moves: list[tuple[int, datetime, bool]] = []
        overdue: list[SrsCard] = []
        for word_id, card in cards.items():
            word = words.get(word_id)
            if word is None or card.state is CardState.ARCHIVED or card.due_at is None:
                continue
            if card.due_at < tomorrow and word_id not in back:
                continue
            known = word.status is ReviewStatus.KNOWN
            check = None if known or word_id in evidence else self._check_threshold()
            target = self._scheduler.target_due(card, known=known, check_days=check)
            if target is None:
                if card.due_at < tomorrow:
                    overdue.append(card)
                continue
            if target <= tomorrow:
                overdue.append(card)
            elif target != card.due_at or card.spread:
                moves.append((word_id, target, False))
        self._cards.move(moves)
        return self._spread(overdue)

    def _spread(self, cards: Sequence[SrsCard]) -> SpreadResult:
        """Place cards over the days from tomorrow, a few a day.

        Weakest first (the lowest chance of recall now). A day takes at most
        the Known words per day setting, and never more than the room left
        once its reviews and the day's new words are counted — so they never
        pause new words or pass the review limit.
        """
        if not cards:
            return SpreadResult()
        settings = self._settings
        now = self._clock.now_utc()
        placing = {card.word_id for card in cards}
        counts = Counter(
            self._clock.local_date(due)
            for word_id, due in self._cards.active_due()
            if word_id not in placing
        )
        order = sorted(
            cards,
            key=lambda card: (self._scheduler.retrievability(card, now) or 0.0, card.word_id),
        )
        per_day = settings.known_back_per_day
        capacity = effective_capacity(settings.review_capacity_per_day)
        placements: list[tuple[int, datetime, bool]] = []
        offset, placed, last_day, last_offset = 1, 0, None, 0
        while placed < len(order):
            day = self._clock.shift_days(offset)
            room = min(per_day, capacity - counts[day] - settings.new_words_per_day)
            if offset > _SPREAD_HORIZON:
                room = per_day
            if room > 0:
                start = self._clock.day_start(day)
                for card in order[placed : placed + room]:
                    placements.append((card.word_id, start, True))
                placed += min(room, len(order) - placed)
                last_day, last_offset = day, offset
            offset += 1
        self._cards.move(placements)
        return SpreadResult(count=len(order), days=last_offset, last_day=last_day)

    def _known_settings_changed(
        self, before: LearningSettings, after: LearningSettings
    ) -> SpreadResult:
        """What a change to the Known settings does to the cards.

        * Reviewing of Known words turned off: their cards are archived.
        * Turned back on: archived cards return, spread over the coming days.
        * The Known target changed: Known words move to it (spread when the
          new date has already passed).
        * The number per day changed: words still waiting from a spread are
          placed again with it.
        """
        result = SpreadResult()
        if before.review_known_words and not after.review_known_words:
            self._cards.set_state(self._known_card_ids(), CardState.ARCHIVED)
            return result
        if not after.review_known_words:
            return result
        if not before.review_known_words:
            known = self._known_card_ids()
            archived = [
                word_id
                for word_id, card in self._cards.get_many(known).items()
                if card.state is CardState.ARCHIVED
            ]
            self.resume(archived)
            result += self._retarget(known, returning=archived)
        elif before.known_retention != after.known_retention:
            result += self._retarget(self._known_card_ids())
        if before.known_back_per_day != after.known_back_per_day:
            tomorrow = self._clock.next_day_start(self._clock.now_utc())
            waiting = [c for c in self._cards.spread_cards() if c.due_at and c.due_at >= tomorrow]
            result += self._spread(waiting)
        return result

    def _known_card_ids(self) -> list[int]:
        """Every Known word that has a card, archived or not."""
        cards = self._cards.all_cards()
        words = self._words.get_many([card.word_id for card in cards])
        return [word.id for word in words if word.status is ReviewStatus.KNOWN]

    def known_evidence(self, word_ids: Sequence[int]) -> set[int]:
        """Of ``word_ids``, the words whose record makes the case for Known:
        answered correctly — and not rated Again — after a long gap, at least
        the threshold in Settings (21 days by default) without a review. A
        forecast is not evidence: stability alone never counts."""
        return self._cards.recalled_after(word_ids, self._settings.mastery_stability_days)

    def known_suggestions(self) -> list[StoredWord]:
        """Words not Known yet whose record makes a strong case for Known
        (:meth:`known_evidence`), most stable first.

        The engine only suggests; :meth:`confirm_known` is the user saying yes.
        """
        cards = [
            card
            for card in self._cards.all_cards()
            if card.state is not CardState.ARCHIVED
        ]
        evidence = self.known_evidence([card.word_id for card in cards])
        cards = [card for card in cards if card.word_id in evidence]
        cards.sort(key=lambda card: card.stability or 0, reverse=True)
        words = self._words_in_order([card.word_id for card in cards])
        return [word for word in words if word.status is not ReviewStatus.KNOWN]

    def confirm_known(self, word_ids: Sequence[int]) -> int:
        """Mark suggested words Known, recorded as learned here (cause MASTERY).

        Only words that are still suggested are marked, so a stale list — a
        word forgotten since, say — cannot record a word as learned that the
        schedule no longer believes is.
        """
        suggested = {word.id for word in self.known_suggestions()}
        return self.mark_known(
            [word_id for word_id in word_ids if int(word_id) in suggested],
            cause=StatusCause.MASTERY,
        )

    def resume(self, word_ids: Sequence[int]) -> int:
        """Bring archived cards back into the schedule."""
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return 0
        cards = self._cards.get_many(ids)
        resumed = 0
        for card in cards.values():
            if card.state is not CardState.ARCHIVED:
                continue
            state = CardState.REVIEW if card.review_count else CardState.INTRODUCED
            self._cards.save(replace(card, state=state))
            resumed += 1
        return resumed

    def struggling_words(self, limit: int | None = None) -> list[StudyItem]:
        """Words the engine has flagged, worst first."""
        plan = self.active_plan()
        if plan is None:
            return []
        cards = self._cards.struggling(self._plans.word_ids(plan.id), limit=limit)
        words = {word.id: word for word in self._words.get_many([c.word_id for c in cards])}
        return [
            StudyItem(word=words[card.word_id], card=card)
            for card in cards
            if card.word_id in words
        ]

    def word_history(self, word_id: int, limit: int = 20) -> list[ReviewLogEntry]:
        return self._cards.logs_for_word(int(word_id), limit=limit)

    # -- statistics ----------------------------------------------------------

    def _reviews_done_today(self, today: str) -> int:
        return self._cards.count_logs_on(today)

    def rating_counts(self, since: str | None = None) -> dict[int, int]:
        return self._cards.rating_counts(since)

    def reviews_per_day(self, days: int = 30) -> dict[str, int]:
        return self._cards.reviews_per_day(days)

    def introduced_per_day(self, days: int = 30) -> dict[str, int]:
        return self._cards.introduced_per_day(days)

    # -- internals -----------------------------------------------------------

    def _words_in_order(self, word_ids: Sequence[int]) -> list[StoredWord]:
        """Load words, keeping the order the caller asked for.

        The candidate order is the CEFR order the plan computed; a bare
        ``get_many`` would return them by id and quietly undo it.
        """
        if not word_ids:
            return []
        found = {word.id: word for word in self._words.get_many(word_ids)}
        return [found[word_id] for word_id in word_ids if word_id in found]
