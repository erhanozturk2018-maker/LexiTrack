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
* **Mastery is derived, never typed in.** When a word's stability passes the
  threshold it becomes Known by itself; the user can still say so by hand, and
  that archives the card instead of deleting it.

The service owns no Qt and no network code. It is given a clock, so a test can
run a year of study in a second, and it never sleeps or polls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from ..core.clock import DayClock
from ..database.connection import Database
from ..models.attempt import ROUTE_V1, Effort, LearningAttempt, Phase, Role, Task
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
from .srs_scheduler import SrsScheduler

#: How far ahead the Study page looks.
FORECAST_DAYS = 7


@dataclass(frozen=True, slots=True)
class StudyItem:
    """One card to answer, with the word it belongs to."""

    word: StoredWord
    card: SrsCard
    #: True when the meaning should start hidden. Only study-plan reviews hide
    #: it; the free-study flashcards and the word table always show it.
    hide_meaning: bool = True

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
    reviews_done_today: int = 0
    #: Words in the plan that have never been introduced.
    pool_remaining: int = 0
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
    #: True when the word's status became Known as a result.
    marked_known: bool = False


@dataclass(frozen=True, slots=True)
class _LastAnswer:
    """Enough of the last answer to take it back exactly."""

    word_id: int
    card_before: SrsCard
    log_id: int
    status_before: ReviewStatus
    marked_known: bool
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

    # -- configuration -----------------------------------------------------

    @property
    def settings(self) -> LearningSettings:
        return self._settings

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
        self._settings_repo.set_many(values)
        return self.refresh_settings()

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
        due = self._due_cards(scope, limit=None)
        done = self._reviews_done_today(today)
        target, note, paused = self._intake_target(len(introduced_ids), len(due))
        offered = (
            self._plans.candidate_word_ids(
                plan.id,
                limit=target,
                include_not_reviewed=self._settings.new_words_include_not_reviewed,
            )
            if target
            else []
        )
        return DailyPlan(
            local_date=today,
            plan=plan,
            new_words=tuple(self._words_in_order(offered)),
            introduced_today=tuple(self._words_in_order(introduced_ids)),
            due_count=len(due),
            reviews_done_today=done,
            pool_remaining=self._plans.candidate_count(
                plan.id, include_not_reviewed=self._settings.new_words_include_not_reviewed
            ),
            new_target=self._settings.new_words_per_day,
            review_capacity=self._settings.review_capacity_per_day,
            forecast=self.forecast(),
            intake_note=note,
            intake_paused=paused,
        )

    def _intake_target(
        self, introduced_today: int, due_today: int
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
        capacity = settings.review_capacity_per_day
        if capacity and due_today >= capacity:
            return 0, (
                f"New words are paused: {due_today} reviews are due today, "
                f"over the {capacity} you set. Clear some and they will come back."
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
        hide = self._settings.hide_meaning_in_study
        return [
            StudyItem(word=words[card.word_id], card=card, hide_meaning=hide)
            for card in cards
            if card.word_id in words
        ]

    def study_item(self, word_id: int) -> StudyItem | None:
        """One word's card as a review item: what Undo puts back on screen."""
        card = self._cards.get(int(word_id))
        word = self._words.get(int(word_id))
        if card is None or word is None:
            return None
        return StudyItem(word=word, card=card, hide_meaning=self._settings.hide_meaning_in_study)

    def _due_cards(self, scope: Sequence[int], limit: int | None) -> list[SrsCard]:
        capacity = self._settings.review_capacity_per_day or None
        effective = min(filter(None, (limit, capacity))) if (limit or capacity) else None
        cards = self._cards.due_cards(
            scope,
            now=self._clock.now_utc(),
            before_local_date=self._clock.today(),
            limit=effective,
        )
        if self._settings.review_known_words:
            return cards
        known = {
            word.id
            for word in self._words.get_many([card.word_id for card in cards])
            if word.status is ReviewStatus.KNOWN
        }
        return [card for card in cards if card.word_id not in known]

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
        """Store where a session's flow stands (see ``services/study_flow.py``)."""
        self._sessions.set_flow_state(session_id, state)

    def close_stale_sessions(self) -> int:
        """Close sessions left open on an earlier day. Called at startup."""
        return self._sessions.finish_stale(self._clock.today(), self._clock.now_utc())

    def answer(
        self,
        word_id: int,
        rating: Rating,
        *,
        session_id: str | None = None,
        channel: Channel = Channel.DESKTOP,
        update_key: str | None = None,
    ) -> AnswerOutcome | None:
        """Record one answer and reschedule the word.

        ``update_key`` makes the call idempotent, which is what Telegram needs:
        it re-delivers a callback whenever it is not certain the answer
        arrived, and a second delivery must not rate the card twice. The key
        is claimed before anything is written, so the duplicate is recognised
        even if it arrives while the first is still being handled.

        Returns ``None`` when the word has no card at all — a message from
        before the word was removed, say. A duplicate returns an outcome with
        ``duplicate`` set, so the caller can still answer the user.
        """
        card = self._cards.get(int(word_id))
        word = self._words.get(int(word_id))
        if card is None or word is None:
            return None
        rating = Rating(int(rating))

        if update_key and not self._sessions.claim_update(update_key):
            return AnswerOutcome(
                word=word,
                rating=rating,
                card=card,
                next_due_on=self._clock.local_date(card.due_at) if card.due_at else "",
                interval_days=0,
                duplicate=True,
            )

        now = self._clock.now_utc()
        today = self._clock.today()
        result = self._scheduler.review(card, rating, now)
        marked_known = False
        # One answer is one event: the card, its log, the attempt, the
        # session count and any status change are written together or not at
        # all, and Undo takes all of them back.
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
                    params_hash=self._scheduler.params_hash,
                )
            )
            self._attempts.add(_v1_attempt(word.id, rating, now, today, session_id, log_id))
            if session_id:
                self._sessions.update(session_id, done_increment=1, clear_current=True)

            if result.reached_mastery and word.status is not ReviewStatus.KNOWN:
                plan = self.active_plan()
                self._state.set_status(
                    word.id,
                    ReviewStatus.KNOWN,
                    cause=StatusCause.MASTERY,
                    plan_id=plan.id if plan else card.origin_plan_id,
                    at=now,
                )
                marked_known = True
        self._last_answer = _LastAnswer(
            word_id=word.id,
            card_before=card,
            log_id=log_id,
            status_before=word.status,
            marked_known=marked_known,
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
            marked_known=marked_known,
        )

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
            if last.marked_known:
                self._state.set_status(
                    last.word_id, last.status_before, cause=StatusCause.UNDO, at=now
                )
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
        if card is None:
            return {}
        now = self._clock.now_utc()
        return {
            rating: self._clock.days_between(now, due)
            for rating, due in self._scheduler.preview(card, now).items()
        }

    # -- status --------------------------------------------------------------

    def mark_known(self, word_ids: Sequence[int]) -> int:
        """The user declares words Known by hand.

        The card is archived rather than deleted, so resetting the status
        later resumes the schedule it already had instead of starting the word
        over. When the user has asked to keep reviewing Known words, the card
        is left alone entirely.
        """
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        if not ids:
            return 0
        changed = self._state.set_status_many(
            ids, ReviewStatus.KNOWN, cause=StatusCause.MANUAL, at=self._clock.now_utc()
        )
        if not self._settings.review_known_words:
            self._cards.set_state(ids, CardState.ARCHIVED)
        return changed

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
            StudyItem(word=words[card.word_id], card=card, hide_meaning=False)
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


#: What each answer of the V1 route says about the effort of the retrieval.
_V1_EFFORT = {
    Rating.HARD: Effort.EFFORTFUL,
    Rating.GOOD: Effort.NORMAL,
    Rating.EASY: Effort.INSTANT,
}


def _v1_attempt(
    word_id: int,
    rating: Rating,
    at: datetime,
    on_day: str,
    session_id: str | None,
    log_id: int,
) -> LearningAttempt:
    """The attempt a V1 review is: the word shown, its meaning recalled.

    V1 asks one thing — word to meaning, level 1 — and the answer is the
    learner's own judgement of it. That is recorded as it happened, route
    ``v1``, and counts as recognition; it is not stretched into evidence of
    anything the review did not ask.
    """
    return LearningAttempt(
        word_id=int(word_id),
        at=at,
        on_day=on_day,
        phase=Phase.REVIEW,
        role=Role.PRIMARY,
        task=Task.WORD_TO_MEANING,
        success=rating is not Rating.AGAIN,
        session_id=session_id,
        effort=_V1_EFFORT.get(rating),
        review_log_id=log_id,
        route_version=ROUTE_V1,
    )
