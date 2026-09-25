"""What has been learned, and how: the data behind Progress and a word's history.

Everything here is read-only and derived from three records that already
exist: ``srs_cards`` (where each studied word stands), ``review_logs`` (every
answer, including those taken back) and ``word_status_events`` (every change
of status, with its cause). Nothing is cached or stored twice, so the Progress
page can never disagree with the Study page or the export.

Four groups answer "which words did I learn here?":

* **Learned here** - introduced by a study plan and marked Known by the
  schedule, because its stability passed the threshold.
* **In progress** - introduced, not yet Known.
* **Marked Known** - introduced, then marked Known by hand (or on the Review
  tab) before the schedule got there.
* **Known before** - Known without ever being studied here; only counted.

Memory and skill are shown apart, as the engine keeps them: *memory* is how
long a word is expected to be remembered (the cards), *skill* what the
learner has shown they can do with it (``learning_attempts``, through
:class:`SkillTracker`). Evidence that is neither a stage nor a schedule —
retrieval that came instantly on several days, a word remembered after a long
gap, a word retrieved in a context never seen before — is counted on its own.

The calibration check compares what the scheduler predicted with what the user
then did. It is the honest answer to "does the algorithm fit me?", and it
needs no training: see :meth:`ProgressService.calibration`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from ..database.connection import Database
from ..models.attempt import MemoryResult, Role, Task
from ..models.skill import SkillStage, WordSkill
from ..models.srs import CardState, Channel, Rating, ReviewLogEntry, SrsCard
from ..models.user_word_state import ReviewStatus, StatusCause, StatusEvent
from ..repositories import (
    AttemptRepository,
    CardRepository,
    PlanRepository,
    StateRepository,
    StoredWord,
    WordRepository,
)
from .skill_tracker import SkillTracker

if TYPE_CHECKING:
    from .learning_service import LearningService

#: The default gap after which a recall is long-term evidence; the one in use
#: is the learner's setting (*Long-term evidence after*, 21 days by default).
LONG_INTERVAL_DAYS = 21
#: The Overview's recent window.
RECENT_DAYS = 30


class Group(StrEnum):
    LEARNED = "learned"
    IN_PROGRESS = "in_progress"
    MARKED_KNOWN = "marked_known"


GROUP_TITLES = {
    Group.LEARNED: "Learned here",
    Group.IN_PROGRESS: "In progress",
    Group.MARKED_KNOWN: "Marked Known",
}


@dataclass(frozen=True, slots=True)
class WordProgress:
    """One studied word, as a row of the Progress table."""

    word: StoredWord
    card: SrsCard
    group: Group
    introduced_on: str
    known_on: str | None
    days_to_known: int | None
    answers: int
    agains: int
    #: What the record shows the learner can do with the word.
    skill: WordSkill | None = None

    @property
    def struggling(self) -> bool:
        return self.card.needs_relearning

    @property
    def stability(self) -> float | None:
        return self.card.stability


@dataclass(frozen=True, slots=True)
class JourneyStep:
    """One thing that happened to a word, in order."""

    at: datetime
    day: str
    kind: str  # "introduced", "answer" or "status"
    rating: Rating | None = None
    state_before: CardState | None = None
    state_after: CardState | None = None
    stability: float | None = None
    interval_days: float | None = None
    channel: Channel | None = None
    undone: bool = False
    status_from: ReviewStatus | None = None
    status_to: ReviewStatus | None = None
    cause: StatusCause | None = None
    reconstructed: bool = False
    #: What the answer asked, when the review recorded it (version 5 on).
    asked: Task | None = None
    #: What the answer showed about the memory, when recorded.
    memory_result: MemoryResult | None = None


@dataclass(frozen=True, slots=True)
class WordJourney:
    """Everything recorded about one word, and where it stands now."""

    word: StoredWord
    card: SrsCard | None
    steps: tuple[JourneyStep, ...]
    plan_name: str | None
    group: Group | None
    known_on: str | None
    known_cause: StatusCause | None
    answers: int
    agains: int
    #: The chance of remembering it now, 0-1, or None before the first answer.
    recall_now: float | None
    next_due_on: str | None
    due_today: bool
    mastery_days: float
    #: The last answer that still counts: why the word is due when it is.
    last_answer: JourneyStep | None
    #: What the record shows the learner can do with it; None before study.
    skill: WordSkill | None = None

    @property
    def introduced_on(self) -> str | None:
        return next((s.day for s in self.steps if s.kind == "introduced"), None)

    @property
    def stability_points(self) -> list[tuple[str, float]]:
        """(day, stability) after each answer that counts, for the chart."""
        return [
            (step.day, step.stability)
            for step in self.steps
            if step.kind == "answer" and not step.undone and step.stability is not None
        ]


@dataclass(frozen=True, slots=True)
class PipelineStage:
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class DayTotals:
    """Running totals on one day: introduced so far, learned so far."""

    day: str
    introduced: int
    learned: int


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    label: str
    predicted: float
    actual: float
    count: int


@dataclass(frozen=True, slots=True)
class Calibration:
    """Predicted recall against actual recall, over the reviews that allow it."""

    bins: tuple[CalibrationBin, ...]
    reviews: int
    predicted: float | None
    actual: float | None

    @property
    def enough(self) -> bool:
        """Below this the difference is mostly chance, and saying so is kinder."""
        return self.reviews >= 100


@dataclass(frozen=True, slots=True)
class Summary:
    learned: int
    in_progress: int
    marked_known: int
    known_before: int
    struggling: int
    answers: int
    taken_back: int


@dataclass(frozen=True, slots=True)
class WeekSummary:
    """Seven days ending today, for the Sunday message."""

    first_day: str
    last_day: str
    answers: int
    agains: int
    introduced: int
    learned: tuple[str, ...]
    hard: tuple[str, ...]
    in_progress: int

    @property
    def again_rate(self) -> float | None:
        return self.agains / self.answers if self.answers else None


@dataclass(frozen=True, slots=True)
class AnswerRow:
    """One answer, for the All answers table."""

    entry: ReviewLogEntry
    word: str
    day: str
    #: What the answer asked, when the review recorded it.
    asked: Task | None = None

    @property
    def memory_result(self) -> MemoryResult | None:
        return _memory_result(self.entry.memory_result)


@dataclass(frozen=True, slots=True)
class SkillOverview:
    """What the studied words' record shows, beside where their memory stands."""

    #: Encountered, Recognised, Recalled, Productive: every studied word once.
    stages: tuple[PipelineStage, ...]
    #: Words retrieved instantly, above recognition, on several days.
    automatic: int
    #: Words remembered after LONG_INTERVAL_DAYS or more without a review.
    long_interval: int
    #: Words retrieved in a context they had not been seen in.
    new_context: int
    #: Words whose only evidence is answers from before version 5, which
    #: asked word → meaning and so show recognition at most.
    only_v1: int


@dataclass(frozen=True, slots=True)
class RecentTotals:
    """The last so many learning days, today included."""

    days: int
    answers: int
    agains: int
    introduced: int
    #: Studied words in long-term memory now, Known or not.
    long_term: int

    @property
    def again_rate(self) -> float | None:
        return self.agains / self.answers if self.answers else None


class ProgressService:
    """Read-only views over the learning record."""

    def __init__(self, database: Database, engine: LearningService) -> None:
        self._db = database
        self._engine = engine
        self._cards = CardRepository(database)
        self._state = StateRepository(database)
        self._words = WordRepository(database)
        self._plans = PlanRepository(database)
        self._attempts = AttemptRepository(database)
        self._skills = SkillTracker(database)

    @property
    def database(self) -> Database:
        return self._db

    # -- the words ---------------------------------------------------------

    def words(self) -> list[WordProgress]:
        """Every word that has been studied here, newest introduction first."""
        cards = self._cards.all_cards()
        if not cards:
            return []
        words = {w.id: w for w in self._words.get_many([c.word_id for c in cards])}
        counts = self._answer_counts()
        known = self._known_events()
        clock = self._engine.clock
        threshold = self._engine.settings.mastery_stability_days
        skills = self._skills.skills(card.word_id for card in cards)
        rows: list[WordProgress] = []
        for card in cards:
            word = words.get(card.word_id)
            if word is None:
                continue
            event = known.get(card.word_id)
            group = _group(word, card, event, threshold)
            known_on = (
                clock.local_date(event.at)
                if event is not None and group is not Group.IN_PROGRESS
                else None
            )
            days = (
                (date.fromisoformat(known_on) - date.fromisoformat(card.introduced_on)).days
                if known_on
                else None
            )
            answers, agains = counts.get(card.word_id, (0, 0))
            rows.append(
                WordProgress(
                    word=word,
                    card=card,
                    group=group,
                    introduced_on=card.introduced_on,
                    known_on=known_on,
                    days_to_known=days,
                    answers=answers,
                    agains=agains,
                    skill=skills.get(card.word_id),
                )
            )
        rows.sort(key=lambda row: (row.introduced_on, row.word.word.casefold()), reverse=True)
        return rows

    def summary(self, rows: list[WordProgress] | None = None) -> Summary:
        rows = self.words() if rows is None else rows
        groups = Counter(row.group for row in rows)
        logs = self._cards.all_logs()
        return Summary(
            learned=groups[Group.LEARNED],
            in_progress=groups[Group.IN_PROGRESS],
            marked_known=groups[Group.MARKED_KNOWN],
            known_before=self._state.known_without_card(),
            struggling=sum(1 for row in rows if row.struggling),
            answers=sum(1 for log in logs if log.undone_at is None),
            taken_back=sum(1 for log in logs if log.undone_at is not None),
        )

    # -- one word ----------------------------------------------------------

    def journey(self, word_id: int) -> WordJourney | None:
        word = self._words.get(int(word_id))
        if word is None:
            return None
        clock = self._engine.clock
        card = self._cards.get(word.id)
        steps: list[JourneyStep] = []
        if card is not None:
            steps.append(
                JourneyStep(
                    at=card.introduced_at, day=card.introduced_on, kind="introduced"
                )
            )
        logs = sorted(
            self._cards.logs_for_word(word.id, limit=10_000),
            key=lambda log: (log.reviewed_at, log.id or 0),
        )
        asked = _asked(self._attempts.for_word(word.id, include_undone=True))
        for log in logs:
            steps.append(
                JourneyStep(
                    at=log.reviewed_at,
                    day=log.reviewed_on,
                    kind="answer",
                    rating=log.rating,
                    state_before=log.state_before,
                    state_after=log.state_after,
                    stability=log.stability_after,
                    interval_days=log.scheduled_days,
                    channel=log.channel,
                    undone=log.undone_at is not None,
                    asked=asked.get(log.id) if log.id is not None else None,
                    memory_result=_memory_result(log.memory_result),
                )
            )
        events = self._state.events_for_word(word.id)
        for event in events:
            steps.append(
                JourneyStep(
                    at=event.at,
                    day=clock.local_date(event.at),
                    kind="status",
                    status_from=event.from_status,
                    status_to=event.to_status,
                    cause=event.cause,
                    reconstructed=event.reconstructed,
                )
            )
        order = {"introduced": 0, "answer": 1, "status": 2}
        steps.sort(key=lambda step: (step.at, order[step.kind]))

        known_event = next(
            (e for e in reversed(events) if e.to_status is ReviewStatus.KNOWN), None
        )
        live = [log for log in logs if log.undone_at is None]
        last = next(
            (s for s in reversed(steps) if s.kind == "answer" and not s.undone), None
        )
        now = clock.now_utc()
        threshold = self._engine.settings.mastery_stability_days
        plan_name = None
        if card is not None and card.origin_plan_id is not None:
            plan = self._plans.get(card.origin_plan_id)
            plan_name = plan.name if plan else None
        group = _group(word, card, known_event, threshold) if card is not None else None
        due_today = (
            card is not None
            and card.state is not CardState.ARCHIVED
            and card.due_at is not None
            and card.due_at <= now
        )
        return WordJourney(
            word=word,
            card=card,
            steps=tuple(steps),
            plan_name=plan_name,
            group=group,
            known_on=clock.local_date(known_event.at)
            if known_event and word.status is ReviewStatus.KNOWN
            else None,
            known_cause=known_event.cause
            if known_event and word.status is ReviewStatus.KNOWN
            else None,
            answers=len(live),
            agains=sum(1 for log in live if log.rating is Rating.AGAIN),
            recall_now=self._engine.scheduler.retrievability(card, now) if card else None,
            next_due_on=clock.local_date(card.due_at)
            if card is not None and card.due_at is not None
            and card.state is not CardState.ARCHIVED
            else None,
            due_today=due_today,
            mastery_days=threshold,
            last_answer=last,
            skill=self._skills.skill(word.id) if card is not None or logs else None,
        )

    # -- everything answered -----------------------------------------------

    def answers(self) -> list[AnswerRow]:
        """Every answer ever given, newest first, including those taken back."""
        logs = self._cards.all_logs()
        words = {w.id: w.word for w in self._words.get_many({log.word_id for log in logs})}
        asked = _asked(self._attempts.all(include_undone=True))
        rows = [
            AnswerRow(
                entry=log,
                word=words.get(log.word_id, "?"),
                day=log.reviewed_on,
                asked=asked.get(log.id) if log.id is not None else None,
            )
            for log in logs
        ]
        rows.reverse()
        return rows

    def week(self) -> WeekSummary:
        """The last seven learning days, today included."""
        clock = self._engine.clock
        last = clock.today()
        first = clock.shift_days(-6)
        logs = [
            log
            for log in self._cards.all_logs(include_undone=False)
            if first <= log.reviewed_on <= last
        ]
        rows = self.words()
        learned = sorted(
            row.word.word
            for row in rows
            if row.group is Group.LEARNED and row.known_on and first <= row.known_on <= last
        )
        hard = [row.word.word for row in sorted(rows, key=lambda r: -r.agains) if row.struggling]
        return WeekSummary(
            first_day=first,
            last_day=last,
            answers=len(logs),
            agains=sum(1 for log in logs if log.rating is Rating.AGAIN),
            introduced=sum(1 for row in rows if first <= row.introduced_on <= last),
            learned=tuple(learned),
            hard=tuple(hard),
            in_progress=sum(1 for row in rows if row.group is Group.IN_PROGRESS),
        )

    def recent(
        self, rows: list[WordProgress] | None = None, days: int = RECENT_DAYS
    ) -> RecentTotals:
        """The last ``days`` learning days, today included."""
        rows = self.words() if rows is None else rows
        clock = self._engine.clock
        first = clock.shift_days(-(days - 1))
        logs = [
            log
            for log in self._cards.all_logs(include_undone=False)
            if log.reviewed_on >= first
        ]
        threshold = self._engine.settings.mastery_stability_days
        return RecentTotals(
            days=days,
            answers=len(logs),
            agains=sum(1 for log in logs if log.rating is Rating.AGAIN),
            introduced=sum(1 for row in rows if row.introduced_on >= first),
            long_term=sum(
                1 for row in rows if row.stability is not None and row.stability >= threshold
            ),
        )

    def skill_overview(self, rows: list[WordProgress] | None = None) -> SkillOverview:
        """Where the studied words' skill stands, and the evidence beside it."""
        rows = self.words() if rows is None else rows
        skills = [row.skill for row in rows if row.skill is not None]
        counts = Counter(skill.stage for skill in skills)
        remembered_late = self._cards.recalled_after(
            (row.word.id for row in rows), self._engine.settings.mastery_stability_days
        )
        return SkillOverview(
            stages=tuple(
                PipelineStage(stage.label, counts[stage])
                for stage in (
                    SkillStage.ENCOUNTERED,
                    SkillStage.RECOGNIZED,
                    SkillStage.RECALLED,
                    SkillStage.PRODUCTIVE,
                )
            ),
            automatic=sum(1 for skill in skills if skill.automatic),
            long_interval=len(remembered_late),
            new_context=sum(1 for skill in skills if skill.novel_context),
            only_v1=sum(
                1
                for skill in skills
                if skill.from_v1
                and skill.from_v1 == skill.recognized + skill.failures
                and skill.recalled == 0
            ),
        )

    # -- charts --------------------------------------------------------------

    def pipeline(self, rows: list[WordProgress] | None = None) -> list[PipelineStage]:
        """Where the studied words are now, from just introduced to Known."""
        rows = self.words() if rows is None else rows
        threshold = self._engine.settings.mastery_stability_days
        stages = Counter()
        for row in rows:
            if row.group is not Group.IN_PROGRESS:
                stages["known"] += 1
            elif row.stability is None:
                stages["new"] += 1
            elif row.stability >= threshold:
                # Expected to last past the threshold. Memory only: whether it
                # is offered as Known depends on skill and a real long recall.
                # Checked first, so a low threshold never hides it as fragile.
                stages["ready"] += 1
            elif row.stability < 3:
                stages["short"] += 1
            elif row.stability < 7:
                stages["week"] += 1
            else:
                stages["near"] += 1
        return [
            PipelineStage("Not answered yet", stages["new"]),
            PipelineStage("Under 3 days", stages["short"]),
            PipelineStage("3 to 7 days", stages["week"]),
            PipelineStage(f"7 to {threshold:g} days", stages["near"]),
            PipelineStage(f"{threshold:g}+ days, not Known", stages["ready"]),
            PipelineStage("Known", stages["known"]),
        ]

    def timeline(self, rows: list[WordProgress] | None = None) -> list[DayTotals]:
        """Running totals per day since the first word was introduced."""
        rows = self.words() if rows is None else rows
        if not rows:
            return []
        introduced = Counter(row.introduced_on for row in rows)
        learned = Counter(
            row.known_on for row in rows if row.group is Group.LEARNED and row.known_on
        )
        start = date.fromisoformat(min(introduced))
        end = date.fromisoformat(self._engine.clock.today())
        totals: list[DayTotals] = []
        running_in = running_learned = 0
        day = start
        while day <= end:
            key = day.isoformat()
            running_in += introduced.get(key, 0)
            running_learned += learned.get(key, 0)
            totals.append(DayTotals(key, running_in, running_learned))
            day += timedelta(days=1)
        return totals

    def calibration(self) -> Calibration:
        """How well the scheduler's predictions matched the user's answers.

        For every answer that followed an earlier answer to the same word, the
        scheduler's forgetting curve gives the chance it expected the word to
        be remembered after that many days. Again means it was not; any other
        answer means it was. Grouped by prediction, the two should match: of
        the words it gave a 90% chance, about 90% should have been remembered.
        Answers taken back are left out; so is each word's first answer,
        which has no earlier stability to predict from.
        """
        scheduler = self._engine.scheduler
        by_word: dict[int, list[ReviewLogEntry]] = defaultdict(list)
        for log in self._cards.all_logs(include_undone=False):
            by_word[log.word_id].append(log)
        pairs: list[tuple[float, bool]] = []
        for logs in by_word.values():
            logs.sort(key=lambda log: (log.reviewed_at, log.id or 0))
            for previous, current in zip(logs, logs[1:], strict=False):
                if previous.stability_after is None or current.elapsed_days is None:
                    continue
                predicted = scheduler.predicted_recall(
                    previous.stability_after, current.elapsed_days
                )
                pairs.append((predicted, current.rating is not Rating.AGAIN))
        if not pairs:
            return Calibration(bins=(), reviews=0, predicted=None, actual=None)
        edges = ((0.0, 0.7, "Under 70%"), (0.7, 0.8, "70-80%"),
                 (0.8, 0.9, "80-90%"), (0.9, 1.01, "90% and over"))
        bins = []
        for low, high, label in edges:
            inside = [(p, ok) for p, ok in pairs if low <= p < high]
            if inside:
                bins.append(
                    CalibrationBin(
                        label=label,
                        predicted=sum(p for p, _ in inside) / len(inside),
                        actual=sum(1 for _, ok in inside if ok) / len(inside),
                        count=len(inside),
                    )
                )
        return Calibration(
            bins=tuple(bins),
            reviews=len(pairs),
            predicted=sum(p for p, _ in pairs) / len(pairs),
            actual=sum(1 for _, ok in pairs if ok) / len(pairs),
        )

    # -- helpers -------------------------------------------------------------

    def _answer_counts(self) -> dict[int, tuple[int, int]]:
        counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for log in self._cards.all_logs(include_undone=False):
            counts[log.word_id][0] += 1
            if log.rating is Rating.AGAIN:
                counts[log.word_id][1] += 1
        return {word_id: (a, b) for word_id, (a, b) in counts.items()}

    def _known_events(self) -> dict[int, StatusEvent]:
        """The latest change to Known of each word."""
        latest: dict[int, StatusEvent] = {}
        for event in self._state.all_events():
            if event.to_status is ReviewStatus.KNOWN:
                latest[event.word_id] = event
            elif event.word_id in latest:
                del latest[event.word_id]
        return latest


def _asked(attempts) -> dict[int, Task]:
    """The task of the first, measuring attempt behind each answer, by answer id."""
    found: dict[int, Task] = {}
    for attempt in attempts:
        if attempt.review_log_id is not None and attempt.role is Role.PRIMARY:
            found.setdefault(attempt.review_log_id, attempt.task)
    return found


def _memory_result(value: str | None) -> MemoryResult | None:
    try:
        return MemoryResult(value) if value else None
    except ValueError:
        return None


def _group(
    word: StoredWord, card: SrsCard, event: StatusEvent | None, threshold: float
) -> Group:
    if word.status is not ReviewStatus.KNOWN:
        return Group.IN_PROGRESS
    if event is not None:
        return Group.LEARNED if event.cause is StatusCause.MASTERY else Group.MARKED_KNOWN
    # Known before version 4 recorded why: judge by the card itself.
    if card.stability is not None and card.stability >= threshold:
        return Group.LEARNED
    return Group.MARKED_KNOWN
