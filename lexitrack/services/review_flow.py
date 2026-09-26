"""A day's study as a session: which step comes next, and what it did.

A session is a list of *steps*, each for one word:

* **teach** — a new word shown whole: the word, its length, CEFR level, part
  of speech, definition and contexts;
* **question** — one of the two tasks (services/review_tasks.py), four
  options, one right:

      Definition → Word          Context → Definition

The day's question for a word due for review is asked once — **one word,
one task** — and its answer is the day's rating:

    right ──▶ the answer shown again ──▶ Again / Hard / Good / Easy ──▶ FSRS
    wrong ──▶ the right word and its definition shown ──▶ Again ──▶ FSRS
                 └──▶ asked again a few cards later, the other way round

**Correctness and effort are kept apart**: a right answer is recorded as
correct with the effort the learner chose, which is also its rating; a wrong
one as not correct, rated Again. A word answered wrong — or answered right
and rated Again — comes back later in the session with the other task when it
has contexts (at most twice). That question, like the ones straight after a
new word is shown, is practice: recorded, never rated, since the day's
rating is already given.

New words are shown in groups of four, then asked (Definition → Word), and —
when they have contexts — asked again (Context → Definition) after the next
group is shown, so each question comes after a short gap. They are rated for
the first time at their first review, on a later day.

Any client drives the same flow — the desktop card, the Telegram bot — and
says which channel it is, so every answer is recorded as coming from there.
Each step shown has a number (:attr:`ReviewFlow.step_number`) that changes
whenever the step on screen does, so a client can tell a tap on the current
step from a tap on an older message. The whole session is saved after every
step and restored as it stood.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..models.attempt import ROUTE_V3, Effort, LearningAttempt, Phase, Role, Task
from ..models.context import WordContext
from ..models.srs import Channel, Rating
from ..repositories import AttemptRepository, ContextRepository, WordRepository
from ..repositories.word_repository import StoredWord
from .first_learning import groups
from .learning_service import AnswerOutcome, LearningService, StudyItem
from .review_tasks import (
    MAX_CYCLES,
    REASK_GAP,
    Option,
    OptionPool,
    Question,
    choose_task,
    context_to_definition,
    definition_to_word,
    pick_context,
    retry_task,
)

log = logging.getLogger(__name__)

#: The saved form of a session. Earlier versions (other routes) are not
#: restored: their session is closed and today's is started afresh.
FLOW_VERSION = 3

#: Why a new word's question is asked, for the card.
_PRACTICE = "Right after learning it: practice, not rated. Its first review is tomorrow."
_AGAIN = "Asked again after a miss: practice, not rated. Today's rating is given."


class StepKind(StrEnum):
    #: A new word shown whole, before it is asked.
    TEACH = "teach"
    #: Four options, one right.
    QUESTION = "question"


@dataclass(frozen=True, slots=True)
class Step:
    word: StoredWord
    kind: StepKind
    phase: Phase = Phase.REVIEW
    role: Role = Role.PRIMARY
    #: For QUESTION: which task. The question itself is built when the step
    #: comes on screen (:attr:`ReviewFlow.current`), so a long session starts
    #: at once.
    task: Task | None = None
    question: Question | None = None
    #: For TEACH: the word's contexts.
    contexts: tuple[WordContext, ...] = ()
    is_struggling: bool = False
    #: Why this step, for the learner.
    reason: str | None = None
    #: Makes each question's options its own: the word, the day and how many
    #: questions the word has had in the session.
    seed: str = ""

    @property
    def rated(self) -> bool:
        """The day's question for a word due: its answer is the day's rating."""
        return self.phase is Phase.REVIEW and self.role is Role.PRIMARY

    @property
    def label(self) -> str:
        """What the step is, in a few words, for the card's header."""
        if self.kind is StepKind.TEACH:
            return "New word"
        return self.task.label if self.task is not None else ""


@dataclass(frozen=True, slots=True)
class Feedback:
    """What one answer did, for the card to show before moving on."""

    correct: bool
    word: StoredWord
    #: The right option's index, and the one chosen.
    answer: int
    picked: int
    #: A right answer to the day's question: Again, Hard, Good or Easy comes
    #: next, and nothing is recorded until then.
    awaiting: bool = False
    #: Set when this answer rated the word.
    outcome: AnswerOutcome | None = None
    #: Practice (after a new word is shown, or asked again): never rated.
    practice: bool = False
    #: The word will be asked again later in this session.
    again_later: bool = False
    #: True when no step is left: the session is over.
    finished: bool = False

    @property
    def definition(self) -> str:
        return self.word.definition or ""


@dataclass(frozen=True, slots=True)
class FlowSummary:
    session_id: str
    answered: int
    can_undo: bool
    #: New words learned (introduced) in the session.
    learned: int = 0


@dataclass(slots=True)
class _Run:
    """One word in the session: a review, or a new word being learned."""

    word: StoredWord
    contexts: tuple[WordContext, ...] = ()
    #: The word's card; None for a new word, which has none until it is learned.
    item: StudyItem | None = None
    #: Questions built for the word so far, for each one's seed.
    asked: int = 0
    #: Contexts shown so far, oldest first.
    shown: list[int] = field(default_factory=list)
    log_id: int | None = None
    cycles: int = 0
    rated: bool = False
    #: A new word: shown and practised, not rated, introduced when done.
    new: bool = False
    introduced: bool = False

    @property
    def done(self) -> bool:
        return self.introduced if self.new else self.rated

    @property
    def struggling(self) -> bool:
        return self.item is not None and self.item.is_struggling


@dataclass(frozen=True, slots=True)
class _Awaiting:
    """A right answer to the day's question, until its effort is chosen."""

    picked: int
    response_ms: int | None = None


class ReviewFlow:
    """A day's study: new words and reviews, two tasks, four options."""

    def __init__(
        self,
        engine: LearningService,
        channel: Channel = Channel.DESKTOP,
        chat_id: str | None = None,
    ) -> None:
        self._engine = engine
        self._channel = channel
        self._chat_id = chat_id
        #: Changes whenever the step on screen does; saved with the state.
        self._step_number = 0
        self._contexts = ContextRepository(engine.database)
        self._attempts = AttemptRepository(engine.database)
        self._pool: OptionPool | None = None
        self._steps: list[Step] = []
        self._runs: dict[int, _Run] = {}
        self._order: list[int] = []
        self._session_id: str | None = None
        self._last_session_id: str | None = None
        self._answered = 0
        self._last_answer: tuple[str, Rating] | None = None
        self._last_word_id: int | None = None
        self._learned = 0
        #: A right answer to the day's question, waiting for its effort.
        self._awaiting: _Awaiting | None = None

    # -- reading -----------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._session_id is not None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def current(self) -> Step | None:
        """The step on screen, its question built if it had none yet."""
        if not self.active:
            return None
        while self._steps:
            step = self._steps[0]
            if step.kind is not StepKind.QUESTION or step.question is not None:
                return step
            built = self._build(step)
            if built is not None:
                self._steps[0] = built
                return built
            # No question can be asked (no definition): the word is left for
            # another day rather than shown something it cannot be asked from.
            log.warning("No question for %r: it has no definition", step.word.word)
            self._steps.pop(0)
            self._settle(step.word.id)
        return None

    @property
    def step_number(self) -> int:
        """Which step is on screen, counted over the whole session."""
        return self._step_number

    @property
    def channel(self) -> Channel:
        return self._channel

    @property
    def position(self) -> int:
        """Words done so far: reviews rated and new words learned."""
        return sum(1 for run in self._runs.values() if run.done)

    @property
    def learned(self) -> int:
        """New words learned in this session."""
        return self._learned

    @property
    def total(self) -> int:
        return len(self._order)

    @property
    def answered(self) -> int:
        return self._answered

    @property
    def awaiting(self) -> bool:
        """True while a right answer waits for Again, Hard, Good or Easy."""
        return self._awaiting is not None and self.current is not None

    @property
    def last_answer(self) -> tuple[str, Rating] | None:
        return self._last_answer

    def can_undo(self) -> bool:
        return (
            self._last_answer is not None
            and self._session_id is not None
            and self._engine.can_undo(self._session_id)
        )

    def intervals(self) -> dict[Rating, int]:
        """While a right answer waits for its effort: when each rating would
        bring the word back, in days."""
        step = self.current
        if step is None or not self.awaiting:
            return {}
        return self._engine.preview_intervals(step.word.id)

    def contexts(self, word_id: int) -> tuple[WordContext, ...]:
        """A word's contexts, as the session has them."""
        run = self._runs.get(word_id)
        return run.contexts if run is not None else self._contexts.for_word(word_id)

    # -- starting and finishing ----------------------------------------------

    def start(self, include_new: bool = True) -> bool:
        """Open the day's session: its reviews first, then its new words.

        Reviews come first so that each measures the memory before today's
        new words can interfere with it. False when there is nothing to do.
        """
        queue = [item for item in self._engine.review_queue() if item.word.definition]
        new_words = (
            [w for w in self._engine.daily_plan().new_words if w.definition]
            if include_new else []
        )
        if not queue and not new_words:
            return False
        self._session_id = self._engine.start_session(self._channel, chat_id=self._chat_id).id
        self._last_session_id = self._session_id
        self._answered = 0
        self._learned = 0
        self._last_answer = None
        self._runs = {}
        self._order = []
        self._steps = []
        contexts = self._contexts.for_words(
            [item.word.id for item in queue] + [word.id for word in new_words]
        )
        for item in queue:
            run = _Run(word=item.word, contexts=contexts.get(item.word.id, ()), item=item)
            self._runs[item.word.id] = run
            self._order.append(item.word.id)
            self._steps.append(self._primary(run))
        self._steps.extend(self._learning_steps(new_words, contexts))
        self._on_step()
        self._save()
        return True

    def _learning_steps(
        self, words: list[StoredWord], contexts: dict[int, tuple[WordContext, ...]]
    ) -> list[Step]:
        """New words in groups: show a group, then ask it; the second
        questions of a group come after the next group is shown, so each has
        a gap before it."""
        steps: list[Step] = []
        later: list[Step] = []
        for group in groups(words):
            shown: list[Step] = []
            asked: list[Step] = []
            upcoming: list[Step] = []
            for word in group:
                run = _Run(word=word, contexts=contexts.get(word.id, ()), new=True)
                self._runs[word.id] = run
                self._order.append(word.id)
                shown.append(Step(word, StepKind.TEACH, phase=Phase.INTRODUCTION,
                                  contexts=run.contexts,
                                  reason="A new word: read it, then it is asked."))
                asked.append(self._ask(run, Task.DEFINITION_TO_WORD, Phase.INTRODUCTION,
                                       Role.RETRIEVAL, _PRACTICE))
                if run.contexts:
                    upcoming.append(self._ask(run, Task.CONTEXT_TO_DEFINITION,
                                              Phase.INTRODUCTION, Role.RETRIEVAL, _PRACTICE))
            steps += shown + later + asked
            later = upcoming
        return steps + later

    def finish(self) -> FlowSummary | None:
        if self._session_id is None:
            return None
        session_id = self._session_id
        self._engine.finish_session(session_id)
        self._engine.save_flow_state(session_id, None)
        summary = FlowSummary(
            session_id, self._answered, self._engine.can_undo(session_id), self._learned
        )
        self._session_id = None
        self._steps = []
        return summary

    # -- answering -----------------------------------------------------------

    def choose(self, index: int, response_ms: int | None = None) -> Feedback:
        """The option chosen for the question on screen.

        A right answer to the day's question is not recorded yet: the
        learner says how it went (:meth:`rate`). A wrong one is recorded at
        once — not correct, Again — and the word is asked again later. A
        practice question is recorded as it was answered, never rated.
        """
        step = self._question()
        question = step.question
        assert question is not None
        if not 0 <= index < len(question.options):
            raise ValueError(f"no option {index}")
        run = self._runs[step.word.id]
        correct = index == question.answer
        if correct and step.rated:
            self._awaiting = _Awaiting(picked=index, response_ms=response_ms)
            self._save()
            return Feedback(True, step.word, question.answer, index, awaiting=True)

        self._steps.pop(0)
        attempt = self._attempt(step, correct, None, response_ms)
        outcome = None
        if step.rated:
            outcome = self._engine.review(
                step.word.id, Rating.AGAIN, task=question.task, correct=False,
                attempts=[attempt], session_id=self._session_id, channel=self._channel,
            )
            self._rated(run, outcome, Rating.AGAIN)
        else:
            self._engine.record_practice(attempt, run.log_id)
        again = False
        if not correct:
            again = self._follow_up(run, question.task)
        self._settle(step.word.id)
        self._on_step()
        self._save()
        return Feedback(
            correct, step.word, question.answer, index, outcome=outcome,
            practice=not step.rated, again_later=again, finished=self.current is None,
        )

    def rate(self, rating: Rating) -> Feedback:
        """Again, Hard, Good or Easy for the right answer on screen: the
        effort, recorded with it, and the day's rating."""
        step = self._question()
        waiting = self._awaiting
        if waiting is None or not step.rated:
            raise RuntimeError("no right answer is waiting for its rating")
        question = step.question
        assert question is not None
        rating = Rating(int(rating))
        run = self._runs[step.word.id]
        self._steps.pop(0)
        self._awaiting = None
        attempt = self._attempt(step, True, Effort.of(rating), waiting.response_ms)
        outcome = self._engine.review(
            step.word.id, rating, task=question.task, correct=True, attempts=[attempt],
            session_id=self._session_id, channel=self._channel,
        )
        self._rated(run, outcome, rating)
        # Right, but rated Again: the learner's word that it did not come.
        again = self._follow_up(run, question.task) if rating is Rating.AGAIN else False
        self._on_step()
        self._save()
        return Feedback(
            True, step.word, question.answer, waiting.picked, outcome=outcome,
            again_later=again, finished=self.current is None,
        )

    def proceed(self) -> bool:
        """Leave a TEACH step. False when there was none on screen."""
        step = self.current
        if step is None or step.kind is not StepKind.TEACH:
            return False
        self._steps.pop(0)
        self._settle(step.word.id)
        self._on_step()
        self._save()
        return True

    def pending_feedback(self) -> Feedback | None:
        """A right answer waiting for its rating: the feedback to show again,
        as it was before a restart."""
        step = self.current
        if self._awaiting is None or step is None or step.question is None:
            return None
        return Feedback(True, step.word, step.question.answer, self._awaiting.picked,
                        awaiting=True)

    # -- undo ----------------------------------------------------------------

    def undo(self) -> StoredWord | None:
        """Take back the last rated word, with its practice; ask it again."""
        session_id = self._session_id or self._last_session_id
        if session_id is None:
            return None
        word = self._engine.undo_last_answer(session_id)
        if word is None:
            return None
        self._last_answer = None
        if self.active and word.id in self._runs:
            old = self._runs[word.id]
            item = self._engine.study_item(word.id) or old.item
            run = _Run(word=old.word, contexts=old.contexts, item=item, asked=old.asked,
                       shown=list(old.shown))
            self._runs[word.id] = run
            self._steps = [s for s in self._steps if s.word.id != word.id]
            self._steps.insert(0, self._primary(run))
            self._answered = max(self._answered - 1, 0)
            self._on_step()
            self._save()
        return word

    # -- the steps -------------------------------------------------------------

    def _question(self) -> Step:
        step = self.current
        if step is None or step.kind is not StepKind.QUESTION:
            raise RuntimeError("no question on screen")
        return step

    def _primary(self, run: _Run) -> Step:
        """The day's question for a word due: the task not asked last time."""
        task = choose_task(bool(run.contexts), self._attempts.last_task(run.word.id))
        reason = (
            "The word has no contexts yet, so it is asked from its definition."
            if not run.contexts
            else "Asked the other way from last time."
        )
        return self._ask(run, task, Phase.REVIEW, Role.PRIMARY, reason)

    def _ask(self, run: _Run, task: Task, phase: Phase, role: Role, reason: str | None) -> Step:
        run.asked += 1
        seed = f"{run.word.id}:{self._engine.clock.today()}:{run.asked}"
        return Step(run.word, StepKind.QUESTION, phase=phase, role=role, task=task,
                    is_struggling=run.struggling and phase is Phase.REVIEW,
                    reason=reason, seed=seed)

    def _build(self, step: Step) -> Step | None:
        """The step with its question: options drawn from the vocabulary."""
        run = self._runs.get(step.word.id)
        if run is None or not step.word.definition:
            return None
        pool = self._options()
        task = step.task or Task.DEFINITION_TO_WORD
        question = None
        if task is Task.CONTEXT_TO_DEFINITION:
            context = pick_context(run.contexts, self._used_contexts(run), step.seed)
            if context is not None:
                question = context_to_definition(step.word, context, pool, step.seed)
                run.shown.append(context.id)
        if question is None:
            question = definition_to_word(step.word, pool, step.seed)
        if question is None:
            return None
        return replace(step, task=question.task, question=question)

    def _used_contexts(self, run: _Run) -> list[int]:
        """The word's contexts shown before, oldest first: in earlier
        sessions, then in this one."""
        before = [
            a.context_id for a in self._attempts.for_word(run.word.id)
            if a.context_id is not None
        ]
        return before + run.shown

    def _options(self) -> OptionPool:
        if self._pool is None:
            self._pool = OptionPool(self._engine.choice_candidates())
        return self._pool

    def _rated(self, run: _Run, outcome: AnswerOutcome | None, rating: Rating) -> None:
        run.rated = True
        if outcome is not None and not outcome.duplicate:
            run.log_id = outcome.log_id
            self._answered += 1
            self._last_answer = (run.word.word, rating)
            self._last_word_id = run.word.id

    def _follow_up(self, run: _Run, failed: Task) -> bool:
        """Ask the word again a few cards later, the other way round when it
        has contexts. True when it will be."""
        if run.cycles >= MAX_CYCLES:
            return False
        run.cycles += 1
        phase = Phase.INTRODUCTION if run.new else Phase.RELEARN
        reason = _PRACTICE if run.new else _AGAIN
        step = self._ask(run, retry_task(failed, bool(run.contexts)), phase, Role.RETRIEVAL,
                         reason)
        self._steps.insert(min(REASK_GAP, len(self._steps)), step)
        return True

    def _attempt(
        self, step: Step, correct: bool, effort: Effort | None, response_ms: int | None
    ) -> LearningAttempt:
        question = step.question
        assert question is not None
        return LearningAttempt(
            word_id=step.word.id,
            at=self._engine.clock.now_utc(),
            on_day=self._engine.clock.today(),
            phase=step.phase,
            role=step.role,
            task=question.task,
            correct=correct,
            session_id=self._session_id,
            context_id=question.context_id,
            effort=effort if correct else None,
            response_ms=response_ms,
            route_version=ROUTE_V3,
        )

    def _settle(self, word_id: int) -> None:
        """A new word with nothing left to do is learned: its card is made."""
        run = self._runs.get(word_id)
        if run is None or not run.new or run.introduced:
            return
        if any(step.word.id == word_id for step in self._steps):
            return
        result = self._engine.introduce([word_id])
        run.introduced = True
        if result.count:
            self._learned += 1

    def _on_step(self) -> None:
        self._step_number += 1
        self._awaiting = None

    # -- saving and restoring ----------------------------------------------------

    def state(self) -> dict:
        """Where the session stands, whole, as plain data: the steps still to
        come as they will be asked, each word's progress, and a right answer
        waiting for its rating."""
        return {
            "version": FLOW_VERSION,
            "route": ROUTE_V3,
            "kind": "review",
            "order": self._order,
            "answered": self._answered,
            "learned": self._learned,
            "step": self._step_number,
            "last_answer": (
                {"word": self._last_answer[0], "rating": int(self._last_answer[1])}
                if self._last_answer
                else None
            ),
            "last_word_id": self._last_word_id,
            "steps": [_step_to_json(step) for step in self._steps],
            "runs": {str(word_id): _run_to_json(run) for word_id, run in self._runs.items()},
            "awaiting": (
                {"picked": self._awaiting.picked, "response_ms": self._awaiting.response_ms}
                if self._awaiting is not None
                else None
            ),
        }

    def _save(self) -> None:
        if self._session_id is not None:
            self._engine.save_flow_state(self._session_id, json.dumps(self.state()))

    @classmethod
    def restore(cls, engine: LearningService, session_id: str) -> ReviewFlow | None:
        """An open session as it was saved, so the same question comes back.

        A state saved by another route or version is not guessed at: None,
        and the caller closes that session and starts today's afresh.
        """
        session = engine.session(session_id)
        if session is None or not session.is_open or not session.flow_state:
            return None
        try:
            data = json.loads(session.flow_state)
        except ValueError:
            return None
        if (
            not isinstance(data, dict)
            or data.get("route") != ROUTE_V3
            or int(data.get("version", 0)) != FLOW_VERSION
        ):
            return None
        try:
            return cls._restore(engine, session, data)
        except (KeyError, TypeError, ValueError):
            log.warning("Session %s has a flow state that cannot be read", session_id)
            return None

    @classmethod
    def _restore(cls, engine: LearningService, session, data: dict) -> ReviewFlow:
        flow = cls(engine, session.channel, session.chat_id)
        flow._session_id = session.id
        flow._last_session_id = session.id
        flow._answered = int(data.get("answered", 0))
        flow._learned = int(data.get("learned", 0))
        flow._step_number = int(data.get("step", 0))
        runs = data.get("runs", {})
        needed = {int(word_id) for word_id in data.get("order", [])}
        needed.update(int(key) for key in runs)
        # A word deleted since is left out, with its steps.
        words = {word.id: word for word in WordRepository(engine.database).get_many(needed)}
        contexts = flow._contexts.for_words(words)
        for key, raw in runs.items():
            word = words.get(int(key))
            if word is None:
                continue
            item = None if raw.get("new") else engine.study_item(word.id)
            flow._runs[word.id] = _run_from_json(raw, word, contexts.get(word.id, ()), item)
        flow._order = [int(i) for i in data.get("order", []) if int(i) in flow._runs]
        for raw in data.get("steps", []):
            run = flow._runs.get(int(raw["word_id"]))
            if run is not None:
                flow._steps.append(_step_from_json(raw, run))
        last_word = data.get("last_word_id")
        flow._last_word_id = int(last_word) if last_word is not None else None
        last = data.get("last_answer")
        if isinstance(last, dict) and last.get("word"):
            flow._last_answer = (str(last["word"]), Rating(int(last["rating"])))
        # Past the number saved: a tap on the card shown before is not this one.
        flow._step_number += 1
        waiting = data.get("awaiting")
        if isinstance(waiting, dict) and flow.current is not None and flow.current.rated:
            flow._awaiting = _Awaiting(
                picked=int(waiting["picked"]), response_ms=waiting.get("response_ms")
            )
        return flow


# -- the state, as plain data ------------------------------------------------------


def _question_to_json(question: Question | None) -> dict | None:
    if question is None:
        return None
    return {
        "task": question.task.value,
        "prompt": question.prompt,
        "options": [{"word_id": o.word_id, "text": o.text} for o in question.options],
        "answer": question.answer,
        "context_id": question.context_id,
        "highlight": list(question.highlight) if question.highlight else None,
    }


def _question_from_json(raw: dict | None) -> Question | None:
    if not raw:
        return None
    highlight = raw.get("highlight")
    return Question(
        task=Task(raw["task"]),
        prompt=str(raw["prompt"]),
        options=tuple(Option(int(o["word_id"]), str(o["text"])) for o in raw["options"]),
        answer=int(raw["answer"]),
        context_id=raw.get("context_id"),
        highlight=(int(highlight[0]), int(highlight[1])) if highlight else None,
    )


def _step_to_json(step: Step) -> dict:
    return {
        "word_id": step.word.id,
        "kind": step.kind.value,
        "phase": step.phase.value,
        "role": step.role.value,
        "task": step.task.value if step.task else None,
        "question": _question_to_json(step.question),
        "is_struggling": step.is_struggling,
        "reason": step.reason,
        "seed": step.seed,
    }


def _step_from_json(raw: dict, run: _Run) -> Step:
    return Step(
        word=run.word,
        kind=StepKind(raw["kind"]),
        phase=Phase(raw["phase"]),
        role=Role(raw["role"]),
        task=Task(raw["task"]) if raw.get("task") else None,
        question=_question_from_json(raw.get("question")),
        contexts=run.contexts if raw["kind"] == StepKind.TEACH.value else (),
        is_struggling=bool(raw.get("is_struggling")),
        reason=raw.get("reason"),
        seed=str(raw.get("seed", "")),
    )


def _run_to_json(run: _Run) -> dict:
    return {
        "new": run.new,
        "introduced": run.introduced,
        "rated": run.rated,
        "cycles": run.cycles,
        "asked": run.asked,
        "shown": list(run.shown),
        "log_id": run.log_id,
    }


def _run_from_json(
    raw: dict, word: StoredWord, contexts: tuple[WordContext, ...], item: StudyItem | None
) -> _Run:
    return _Run(
        word=word,
        contexts=contexts,
        item=item,
        asked=int(raw.get("asked", 0)),
        shown=[int(i) for i in raw.get("shown", [])],
        log_id=raw.get("log_id"),
        cycles=int(raw.get("cycles", 0)),
        rated=bool(raw.get("rated")),
        new=bool(raw.get("new")),
        introduced=bool(raw.get("introduced")),
    )
