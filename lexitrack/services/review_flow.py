"""Review route V2 as a session: which question comes next, and what it did.

The rules live in :mod:`.review_route`; this module runs them over a day's
queue. A session is a list of *steps*, each one question or one page of
teaching, for one word:

    primary ──fail──▶ probe (level 2) ──fail──▶ probe (choose among four)
       │                  │                           │
       └──────── success ─┴──────────── result ───────┘
                              │
                     rated once (FSRS hears the memory result)
                              │
               forgotten, or a skill failed? ── teach ── … 3 cards … ── ask again
                                                   (at most two cycles)

The answer is kept hidden until the word is rated, so no probe can be
answered by having just seen it. **The learner says how a retrieval went**:
after a correct typed answer they choose Effortful, Remembered or Instant,
after a written sentence one of Forgot / Effortful / Remembered / Instant, and
for a word with no meaning to ask from the same four before anything is
shown (:meth:`ReviewFlow.assess`). That report is the result; the time taken,
a hint and a slip are kept as telemetry only. Relearning and repair questions use a
different prompt from the one that failed, are recorded as practice linked to
the answer, and never change the rating.

A word with no meaning to ask from (no definition, and no meaning in the
learner's language) is shown and the learner reports, before anything else
is shown, whether they knew it. The page shows
steps and reports what the learner did; it holds no state of its own.

Any client drives the same flow — the desktop card, the Telegram bot — and
says which channel it is, so every answer is recorded as coming from there.
Each step shown has a number (:attr:`ReviewFlow.step_number`) that changes
whenever the step on screen does, so a client can tell a tap on the current
step from a tap on an older message.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..models.attempt import (
    ROUTE_V2,
    Depth,
    Effort,
    LearningAttempt,
    Level,
    MemoryResult,
    Phase,
    Role,
    SelfReport,
    Task,
)
from ..models.content import WordTeaching
from ..models.srs import Channel, Rating
from ..repositories import AttemptRepository, ContentRepository, WordRepository
from ..repositories.word_repository import StoredWord
from .first_learning import choose_depth, deeper, groups, second_question
from .learning_service import AnswerOutcome, LearningService, StudyItem
from .review_route import (
    MAX_CYCLES,
    REASK_GAP,
    FollowUp,
    Prompt,
    Resolution,
    Result,
    Source,
    check_typed,
    choice_options,
    collocation_prompt,
    context_prompt,
    effort_for,
    meaning_prompt,
    next_probe,
    resolve,
)
from .skill_tracker import SkillTracker
from .task_selector import available_levels, choose_level, lower_levels, prompt_for, repeats

log = logging.getLogger(__name__)

#: Version 2 saves the session whole: every step still to come as it will be
#: asked, each word's attempts and results so far, and a right answer waiting
#: for its report. Version 1 (the words not yet rated) is still read.
FLOW_VERSION = 2

#: Why a new word's question is asked, for the card.
_PRACTICE = "Right after learning it: practice, not rated. Its first review is tomorrow."


class StepKind(StrEnum):
    #: Type the word (levels 2–4).
    TYPE = "type"
    #: Choose the word among four (the level 1 probe).
    CHOOSE = "choose"
    #: Write a sentence, then compare it with examples and grade it (level 5).
    WRITE = "write"
    #: A word with no meaning to ask from: shown, and the learner reports.
    RECALL = "recall"
    #: The word taught again, before it is asked again.
    TEACH = "teach"


@dataclass(frozen=True, slots=True)
class Step:
    word: StoredWord
    kind: StepKind
    phase: Phase = Phase.REVIEW
    role: Role = Role.PRIMARY
    prompt: Prompt | None = None
    options: tuple[StoredWord, ...] = ()
    teaching: WordTeaching | None = None
    #: For RECALL: whether the meaning starts hidden.
    hide_meaning: bool = True
    is_struggling: bool = False
    #: Why this question, for the learner (see services/task_selector.py).
    reason: str | None = None
    #: For TEACH: how much to show (SHORT, LIGHT, DEEP); None shows everything.
    depth: Depth | None = None

    @property
    def label(self) -> str:
        """What the step asks, in a few words, for the card's header."""
        if self.kind is StepKind.TEACH:
            return {
                Phase.INTRODUCTION: "New word",
                Phase.RELEARN: "Relearn",
            }.get(self.phase, "Look again")
        if self.kind is StepKind.CHOOSE:
            return "Which word is it?"
        if self.kind is StepKind.RECALL:
            return "Do you know it?"
        if self.prompt is None:
            return ""
        return {
            Task.MEANING_TO_WORD: "Meaning → word",
            Task.CONTEXT_CLOZE: "Complete the sentence",
            Task.SITUATION_TO_WORD: "Which word fits?",
            Task.COLLOCATION: "Complete the phrase",
            Task.PRODUCTION: "Use it in a sentence",
        }.get(self.prompt.task, "")


@dataclass(frozen=True, slots=True)
class Feedback:
    """What one step did, for the card to show before moving on."""

    correct: bool
    #: Accepted with a slip.
    near_miss: bool = False
    #: The right answer, or None while it must stay hidden for a probe.
    answer: str | None = None
    #: Set when this step rated the word.
    outcome: AnswerOutcome | None = None
    resolution: Resolution | None = None
    #: True when no step is left: the session is over.
    finished: bool = False
    #: The typed answer was right; the learner's report comes next
    #: (Effortful, Remembered or Instant) before anything is recorded.
    awaiting: bool = False


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
    teaching: WordTeaching
    #: The word's card; None for a new word, which has none until it is learned.
    item: StudyItem | None = None
    results: list[Result] = field(default_factory=list)
    attempts: list[LearningAttempt] = field(default_factory=list)
    #: Prompts already used this session, so a retrieval uses another one.
    sources: set[Source] = field(default_factory=set)
    contexts: set[int] = field(default_factory=set)
    collocations: set[str] = field(default_factory=set)
    resolution: Resolution | None = None
    log_id: int | None = None
    cycles: int = 0
    rated: bool = False
    #: A new word: taught and practised, not rated, introduced when done.
    new: bool = False
    depth: Depth | None = None
    introduced: bool = False

    @property
    def done(self) -> bool:
        return self.introduced if self.new else self.rated

    @property
    def struggling(self) -> bool:
        return self.item is not None and self.item.is_struggling


@dataclass(frozen=True, slots=True)
class _Awaiting:
    """A right typed answer, and what was observed of it, until the report."""

    near_miss: bool = False
    response_ms: int | None = None
    hinted: bool = False
    #: For a WRITE step: the sentence written, waiting for its report.
    written: str | None = None


#: What a report on a shown word says about the memory, and the rating for it.
_REPORT_MEMORY = {
    SelfReport.FORGOT: MemoryResult.FORGOTTEN,
    SelfReport.EFFORTFUL: MemoryResult.RECALLED_EFFORT,
    SelfReport.REMEMBERED: MemoryResult.RECALLED,
    SelfReport.INSTANT: MemoryResult.RECALLED,
}
_MEMORY_RATING = {
    MemoryResult.FORGOTTEN: Rating.AGAIN,
    MemoryResult.RECALLED_EFFORT: Rating.HARD,
    MemoryResult.RECALLED: Rating.GOOD,
}


class ReviewFlow:
    """A day's reviews by route V2."""

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
        self._content = ContentRepository(engine.database)
        self._attempts = AttemptRepository(engine.database)
        self._skills = SkillTracker(engine.database)
        #: The kinds of the last first questions shown, for variety.
        self._recent_tasks: list[Task] = []
        self._steps: list[Step] = []
        self._runs: dict[int, _Run] = {}
        self._order: list[int] = []
        self._session_id: str | None = None
        self._last_session_id: str | None = None
        self._answered = 0
        self._last_answer: tuple[str, Rating] | None = None
        self._last_word_id: int | None = None
        self._learned = 0
        #: A correct typed answer waiting for the learner's report.
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
        return self._steps[0] if self.active and self._steps else None

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
        """True while a correct typed answer waits for the learner's report."""
        step = self.current
        return (
            self._awaiting is not None
            and step is not None
            and step.kind is StepKind.TYPE
        )

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
        """For a RECALL step: when each report would bring the word back, by the
        rating it maps to."""
        step = self.current
        if step is None or step.kind is not StepKind.RECALL:
            return {}
        return self._engine.preview_intervals(step.word.id)

    # -- starting and finishing ----------------------------------------------

    def start(self, include_new: bool = True) -> bool:
        """Open the day's session: its reviews first, then its new words.

        Reviews come first so that each measures the memory before today's
        new words can interfere with it. False when there is nothing to do.
        """
        queue = self._engine.review_queue()
        new_words = list(self._engine.daily_plan().new_words) if include_new else []
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
        for item in queue:
            run = _Run(word=item.word, teaching=self._teaching(item.word.id), item=item)
            self._runs[item.word.id] = run
            self._order.append(item.word.id)
            self._steps.append(self._primary(run))
        self._steps.extend(self._learning_steps(new_words))
        self._on_step()
        self._save()
        return True

    def _learning_steps(self, words: list[StoredWord]) -> list[Step]:
        """New words in groups: teach a group, then ask it; the second
        questions of a group come after the next group is taught, so each
        has a gap before it (services/first_learning.py)."""
        steps: list[Step] = []
        later: list[Step] = []
        for group in groups(words):
            taught: list[Step] = []
            asked: list[Step] = []
            upcoming: list[Step] = []
            for word in group:
                teaching = self._teaching(word.id)
                choice = choose_depth(teaching)
                run = _Run(word=word, teaching=teaching, new=True, depth=choice.depth)
                self._runs[word.id] = run
                self._order.append(word.id)
                taught.append(Step(word, StepKind.TEACH, phase=Phase.INTRODUCTION,
                                   teaching=teaching, depth=choice.depth, reason=choice.reason))
                prompt = meaning_prompt(word, teaching)
                if prompt is not None:
                    asked.append(self._ask(run, prompt, Role.RETRIEVAL, Phase.INTRODUCTION,
                                           reason=_PRACTICE))
                    second = self._second_prompt(run) if second_question(choice.depth) else None
                    if second is not None:
                        upcoming.append(self._ask(run, second, Role.RETRIEVAL,
                                                  Phase.INTRODUCTION, reason=_PRACTICE))
            steps += taught + later + asked
            later = upcoming
        return steps + later

    def _second_prompt(self, run: _Run) -> Prompt | None:
        """A new word's second question: a context, else its meaning another way."""
        prompt = context_prompt(run.word, run.teaching, exclude=run.contexts)
        if prompt is not None:
            return prompt
        if run.depth is Depth.DEEP:
            avoid = Source.LEARNER if Source.LEARNER in run.sources else Source.DEFINITION
            return meaning_prompt(run.word, run.teaching, avoid=avoid)
        return None

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

    def submit(self, typed: str, response_ms: int | None = None, hinted: bool = False) -> Feedback:
        """A typed answer to a TYPE step. An empty answer is Forgot.

        A right answer in a review is not recorded yet: the learner's report
        (:meth:`assess`) says how it came. Practice straight after teaching is
        never rated and needs none; its effort is the observed one.
        """
        step = self._take(StepKind.TYPE)
        prompt = step.prompt
        check = check_typed(prompt.accepted, typed)
        if check.correct and step.phase is Phase.REVIEW:
            self._awaiting = _Awaiting(near_miss=check.near_miss, response_ms=response_ms,
                                       hinted=hinted)
            self._save()
            return Feedback(True, check.near_miss, answer=prompt.answer, awaiting=True)
        effort = (
            effort_for(response_ms, typed, hinted=hinted, near_miss=check.near_miss)
            if check.correct
            else None
        )
        return self._record(step, check.correct, effort, response_ms, near_miss=check.near_miss)

    def assess(self, report: SelfReport) -> Feedback:
        """The learner's report on the step on screen: after a correct typed
        answer, after a written sentence, or for a word shown without a meaning.
        """
        step = self.current
        if step is None:
            raise RuntimeError("no step on screen")
        if step.kind is StepKind.TYPE and self.awaiting:
            waiting, self._awaiting = self._awaiting, None
            # Forgot after a right answer: the learner's word that it was a guess.
            return self._record(step, report.success, report.effort, waiting.response_ms,
                                near_miss=waiting.near_miss)
        if step.kind is StepKind.WRITE:
            self._awaiting = None
            return self._record(step, report.success, report.effort, None)
        if step.kind is StepKind.RECALL:
            return self._recall(step, report)
        raise RuntimeError(f"nothing to report on a {step.kind.value} step")

    def choose(self, index: int, response_ms: int | None = None) -> Feedback:
        """The option picked in a CHOOSE step."""
        step = self._take(StepKind.CHOOSE)
        picked = step.options[index] if 0 <= index < len(step.options) else None
        correct = picked is not None and picked.id == step.word.id
        return self._record(step, correct, Effort.NORMAL if correct else None, response_ms)

    def _recall(self, step: Step, report: SelfReport) -> Feedback:
        """A word with no meaning to ask from, reported before anything showed:
        recorded as a word-to-meaning retrieval and rated from the report."""
        run = self._runs[step.word.id]
        attempt = replace(
            self._attempt(step, report.success, report.effort, None), task=Task.WORD_TO_MEANING
        )
        memory = _REPORT_MEMORY[report]
        rating = _MEMORY_RATING[memory] if report is not SelfReport.INSTANT else Rating.EASY
        outcome = self._engine.review(
            step.word.id, rating, memory_result=memory, attempts=[attempt],
            session_id=self._session_id, channel=self._channel,
        )
        self._steps.pop(0)
        self._rated(run, outcome, rating)
        self._on_step()
        self._save()
        return Feedback(
            correct=report.success,
            answer=step.word.word,
            outcome=outcome,
            finished=not self._steps,
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

    # -- undo ----------------------------------------------------------------

    def undo(self) -> StoredWord | None:
        """Take back the last rated word, with its probes and practice; ask it again."""
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
            run = _Run(word=old.word, teaching=old.teaching, item=item)
            self._runs[word.id] = run
            self._steps = [s for s in self._steps if s.word.id != word.id]
            self._steps.insert(0, self._primary(run))
            self._answered = max(self._answered - 1, 0)
            self._on_step()
            self._save()
        return word

    # -- the rules applied ------------------------------------------------------

    def _teaching(self, word_id: int) -> WordTeaching:
        """What is known about the word, in the learner's chosen language."""
        return self._content.teaching(word_id, self._engine.settings.learner_language)

    def _take(self, kind: StepKind) -> Step:
        step = self.current
        if step is None or step.kind is not kind:
            raise RuntimeError(f"no {kind.value} step on screen")
        return step

    def _primary(self, run: _Run) -> Step:
        """The first question for a word, chosen from its record (TaskSelector)."""
        word = run.word
        struggling = run.struggling
        history = self._attempts.for_word(word.id)
        available = available_levels(word, run.teaching)
        choice = choose_level(
            history,
            available,
            self._engine.retrievability(word.id),
            self._skills.skill(word.id).level,
        )
        if choice is None:
            hide = run.item.hide_meaning if run.item else True
            self._shown(Task.WORD_TO_MEANING)
            return Step(word, StepKind.RECALL, hide_meaning=hide, is_struggling=struggling,
                        reason="No meaning is stored to ask from, so the word is shown.")
        uses = self._attempts.context_uses(word.id)
        prompt = prompt_for(choice.level, word, run.teaching, history, uses) or meaning_prompt(
            word, run.teaching
        )
        reason = choice.reason
        if repeats(self._recent_tasks, prompt.task):
            other = self._other_kind(run, choice.level, prompt.task, available, history, uses)
            if other is not None:
                prompt = other
                reason = f"{reason} Asked another way: the last two questions were alike."
        self._shown(prompt.task)
        return self._ask(run, prompt, Role.PRIMARY, Phase.REVIEW, struggling, reason)

    def _shown(self, task: Task) -> None:
        self._recent_tasks = [*self._recent_tasks[-1:], task]

    def _other_kind(self, run, level, task, available, history, uses) -> Prompt | None:
        """A question of another kind, never harder: the other kind of context
        at level 3, else the hardest lower level whose question differs."""
        if level is Level.CONTEXT_TO_WORD:
            prompt = context_prompt(run.word, run.teaching, used=uses, avoid_task=task)
            if prompt is not None and prompt.task is not task:
                return prompt
        for lower in lower_levels(level, available):
            prompt = prompt_for(lower, run.word, run.teaching, history, uses)
            if prompt is not None and prompt.task is not task:
                return prompt
        return None

    def _ask(
        self,
        run: _Run,
        prompt: Prompt,
        role: Role,
        phase: Phase,
        struggling: bool = False,
        reason: str | None = None,
    ) -> Step:
        run.sources.add(prompt.source)
        if prompt.context_id is not None:
            run.contexts.add(prompt.context_id)
        if prompt.task is Task.COLLOCATION:
            run.collocations.add(prompt.answer)
        kind = StepKind.WRITE if prompt.task is Task.PRODUCTION else StepKind.TYPE
        return Step(run.word, kind, phase=phase, role=role, prompt=prompt,
                    teaching=run.teaching, is_struggling=struggling, reason=reason)

    def _choice(self, run: _Run) -> Step:
        word = run.word
        seed = f"{word.id}:{self._engine.clock.today()}"
        options = choice_options(word, self._engine.choice_pool(word.id, seed), seed)
        prompt = meaning_prompt(word, run.teaching)
        return Step(word, StepKind.CHOOSE, role=Role.PROBE, prompt=prompt, options=options,
                    teaching=run.teaching, is_struggling=run.struggling)

    def _record(
        self,
        step: Step,
        success: bool,
        effort: Effort | None,
        response_ms: int | None,
        near_miss: bool = False,
    ) -> Feedback:
        run = self._runs[step.word.id]
        self._steps.pop(0)
        attempt = self._attempt(step, success, effort, response_ms)

        if step.phase is not Phase.REVIEW:
            # Practice after teaching: recorded, never rated.
            self._engine.record_practice(replace(attempt, depth=run.depth), run.log_id)
            if not success:
                self._follow_up(run, step.phase)
            self._settle(step.word.id)
            self._on_step()
            self._save()
            return Feedback(success, near_miss, answer=step.prompt.answer,
                            finished=not self._steps)

        run.attempts.append(attempt)
        run.results.append(Result(attempt.task, success, effort, probe=step.role is Role.PROBE))
        probe = next_probe(run.results)
        if probe is not None:
            if probe is Level.MEANING_TO_WORD:
                prompt = meaning_prompt(step.word, run.teaching)
                self._steps.insert(0, self._ask(run, prompt, Role.PROBE, Phase.REVIEW))
            else:
                self._steps.insert(0, self._choice(run))
            self._on_step()
            self._save()
            # Not rated yet: the answer stays hidden for the probe.
            return Feedback(False, answer=None)

        resolution = resolve(run.results)
        run.resolution = resolution
        outcome = self._engine.review(
            step.word.id,
            resolution.rating,
            memory_result=resolution.memory,
            attempts=run.attempts,
            session_id=self._session_id,
            channel=self._channel,
        )
        self._rated(run, outcome, resolution.rating)
        if resolution.follow_up is not FollowUp.NONE:
            phase = Phase.RELEARN if resolution.follow_up is FollowUp.RELEARN else Phase.REPAIR
            self._follow_up(run, phase)
        self._on_step()
        self._save()
        answer = step.prompt.answer if step.prompt else step.word.word
        return Feedback(success, near_miss, answer=answer, outcome=outcome,
                        resolution=resolution, finished=not self._steps)

    def _rated(self, run: _Run, outcome: AnswerOutcome | None, rating: Rating) -> None:
        run.rated = True
        if outcome is not None and not outcome.duplicate:
            run.log_id = outcome.log_id
            self._answered += 1
            self._last_answer = (run.word.word, rating)
            self._last_word_id = run.word.id

    def _follow_up(self, run: _Run, phase: Phase) -> None:
        """Teach the word, and ask it again a few cards later with a new prompt."""
        if run.cycles >= MAX_CYCLES:
            return
        run.cycles += 1
        word = run.word
        if run.new and run.depth is not None:
            # A new word missed right after teaching is taught again, deeper.
            run.depth = deeper(run.depth, run.teaching)
        self._steps.insert(0, Step(word, StepKind.TEACH, phase=phase, teaching=run.teaching,
                                   depth=run.depth if run.new else None))
        prompt = self._retrieval_prompt(run)
        if prompt is None:
            return
        position = min(1 + REASK_GAP, len(self._steps))
        self._steps.insert(position, self._ask(run, prompt, Role.RETRIEVAL, phase))

    def _retrieval_prompt(self, run: _Run) -> Prompt | None:
        """A different question for the level that failed."""
        word = run.word
        level = run.resolution.repair_level if run.resolution else None
        if level is Level.COLLOCATION:
            prompt = collocation_prompt(word, run.teaching, exclude=run.collocations)
            if prompt is not None:
                return prompt
        if level is not None and level >= Level.CONTEXT_TO_WORD:
            prompt = context_prompt(word, run.teaching, exclude=run.contexts)
            if prompt is not None:
                return prompt
        avoid = Source.LEARNER if Source.LEARNER in run.sources else (
            Source.DEFINITION if Source.DEFINITION in run.sources else None
        )
        return meaning_prompt(word, run.teaching, avoid=avoid)

    def _attempt(
        self, step: Step, success: bool, effort: Effort | None, response_ms: int | None
    ) -> LearningAttempt:
        prompt = step.prompt
        task = (
            Task.CHOOSE_WORD if step.kind is StepKind.CHOOSE
            else Task.WORD_TO_MEANING if prompt is None
            else prompt.task
        )
        return LearningAttempt(
            word_id=step.word.id,
            at=self._engine.clock.now_utc(),
            on_day=self._engine.clock.today(),
            phase=step.phase,
            role=step.role,
            task=task,
            success=success,
            session_id=self._session_id,
            context_id=prompt.context_id if prompt and step.kind is StepKind.TYPE else None,
            novel_context=bool(prompt and prompt.novel_context and step.kind is StepKind.TYPE),
            effort=effort if success else None,
            response_ms=response_ms,
            route_version=ROUTE_V2,
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
        """Where the session stands, whole, as plain data (version 2).

        Enough to put the same question back on screen after a restart: the
        steps still to come exactly as they will be asked, each word's
        attempts and results so far (a word part-way through its probes goes
        on from there), the prompts it has used, and a right typed answer
        waiting for its report. The version-1 keys are kept alongside.
        """
        data = self._summary_state()
        data["steps"] = [_step_to_json(step) for step in self._steps]
        data["runs"] = {str(word_id): _run_to_json(run) for word_id, run in self._runs.items()}
        data["recent_tasks"] = [task.value for task in self._recent_tasks]
        data["last_word_id"] = self._last_word_id
        data["awaiting"] = (
            {"near_miss": self._awaiting.near_miss, "response_ms": self._awaiting.response_ms,
             "hinted": self._awaiting.hinted, "written": self._awaiting.written}
            if self._awaiting is not None
            else None
        )
        return data

    def _summary_state(self) -> dict:
        pending = [
            word_id for word_id in self._order
            if not self._runs[word_id].done and not self._runs[word_id].new
        ]
        new_pending = [
            word_id for word_id in self._order
            if self._runs[word_id].new and not self._runs[word_id].introduced
        ]
        return {
            "version": FLOW_VERSION,
            "route": ROUTE_V2,
            "kind": "review",
            "order": self._order,
            "pending": pending,
            "new_pending": new_pending,
            "answered": self._answered,
            "learned": self._learned,
            "step": self._step_number,
            "last_answer": (
                {"word": self._last_answer[0], "rating": int(self._last_answer[1])}
                if self._last_answer
                else None
            ),
        }

    def _save(self) -> None:
        if self._session_id is not None:
            self._engine.save_flow_state(self._session_id, json.dumps(self.state()))

    @classmethod
    def restore(cls, engine: LearningService, session_id: str) -> ReviewFlow | None:
        """An open session as it was saved, so the same question comes back.

        A version-2 state is restored whole (see :meth:`state`). A version-1
        state, from before, restores the words not yet rated from their first
        question. Anything else — another route, a newer version, damage — is
        not guessed at.
        """
        session = engine.session(session_id)
        if session is None or not session.is_open or not session.flow_state:
            return None
        try:
            data = json.loads(session.flow_state)
        except ValueError:
            return None
        if not isinstance(data, dict) or data.get("route") != ROUTE_V2:
            return None
        version = int(data.get("version", 0))
        if version == FLOW_VERSION and "steps" in data:
            try:
                return cls._restore_whole(engine, session, data)
            except (KeyError, TypeError, ValueError):
                log.warning("Session %s has a flow state that cannot be read", session_id)
                return None
        if version != 1:
            return None
        flow = cls(engine, session.channel, session.chat_id)
        flow._session_id = session_id
        flow._last_session_id = session_id
        flow._answered = int(data.get("answered", 0))
        flow._learned = int(data.get("learned", 0))
        # Past the number saved: the step on screen before is not this one.
        flow._step_number = int(data.get("step", 0))
        order = [int(word_id) for word_id in data.get("order", [])]
        pending = {int(word_id) for word_id in data.get("pending", [])}
        new_pending = [int(word_id) for word_id in data.get("new_pending", [])]
        for word_id in order:
            if word_id in new_pending:
                continue
            item = engine.study_item(word_id)
            if item is None:
                continue
            run = _Run(word=item.word, teaching=flow._teaching(word_id), item=item,
                       rated=word_id not in pending)
            flow._runs[word_id] = run
            flow._order.append(word_id)
            if not run.rated:
                flow._steps.append(flow._primary(run))
        # New words not yet learned start again from their teaching.
        offered = {word.id: word for word in engine.daily_plan().new_words}
        flow._steps.extend(
            flow._learning_steps([offered[i] for i in new_pending if i in offered])
        )
        last = data.get("last_answer")
        if isinstance(last, dict) and last.get("word"):
            flow._last_answer = (str(last["word"]), Rating(int(last["rating"])))
        flow._on_step()
        return flow

    @classmethod
    def _restore_whole(cls, engine: LearningService, session, data: dict) -> ReviewFlow:
        flow = cls(engine, session.channel, session.chat_id)
        flow._session_id = session.id
        flow._last_session_id = session.id
        flow._answered = int(data.get("answered", 0))
        flow._learned = int(data.get("learned", 0))
        flow._step_number = int(data.get("step", 0))
        flow._order = [int(word_id) for word_id in data.get("order", [])]
        runs = data.get("runs", {})
        needed = set(flow._order)
        for step in data["steps"]:
            needed.add(int(step["word_id"]))
            needed.update(int(option) for option in step.get("options", []))
        words = {word.id: word for word in WordRepository(engine.database).get_many(needed)}
        for key, raw in runs.items():
            word_id = int(key)
            word = words.get(word_id)
            if word is None:
                continue
            item = None if raw.get("new") else engine.study_item(word_id)
            flow._runs[word_id] = _run_from_json(raw, word, flow._teaching(word_id), item)
        flow._order = [word_id for word_id in flow._order if word_id in flow._runs]
        for raw in data["steps"]:
            run = flow._runs.get(int(raw["word_id"]))
            if run is None:
                continue
            flow._steps.append(_step_from_json(raw, run, words))
        flow._recent_tasks = [Task(value) for value in data.get("recent_tasks", [])]
        last_word = data.get("last_word_id")
        flow._last_word_id = int(last_word) if last_word is not None else None
        last = data.get("last_answer")
        if isinstance(last, dict) and last.get("word"):
            flow._last_answer = (str(last["word"]), Rating(int(last["rating"])))
        # Past the number saved: a tap on the card shown before is not this one.
        flow._step_number += 1
        waiting = data.get("awaiting")
        if isinstance(waiting, dict) and flow.current is not None:
            flow._awaiting = _Awaiting(
                near_miss=bool(waiting.get("near_miss")),
                response_ms=waiting.get("response_ms"),
                hinted=bool(waiting.get("hinted")),
                written=waiting.get("written"),
            )
        return flow

    def note_written(self, sentence: str) -> None:
        """A sentence written for the WRITE step on screen, kept until its report
        so that a restart shows it again rather than asking for it twice."""
        step = self.current
        if step is not None and step.kind is StepKind.WRITE:
            self._awaiting = _Awaiting(written=sentence)
            self._save()

    def pending_feedback(self) -> Feedback | None:
        """For a right typed answer waiting for its report: the feedback to
        show again, as it was before a restart."""
        if not self.awaiting:
            return None
        step = self.current
        return Feedback(True, self._awaiting.near_miss, answer=step.prompt.answer, awaiting=True)

    @property
    def written(self) -> str | None:
        """The sentence written for the step on screen and waiting for its report."""
        return self._awaiting.written if self._awaiting is not None else None


# -- the state, as plain data ------------------------------------------------------


def _prompt_to_json(prompt: Prompt | None) -> dict | None:
    if prompt is None:
        return None
    return {
        "task": prompt.task.value,
        "text": prompt.text,
        "accepted": list(prompt.accepted),
        "answer": prompt.answer,
        "source": prompt.source.value,
        "detail": prompt.detail,
        "context_id": prompt.context_id,
        "novel_context": prompt.novel_context,
    }


def _prompt_from_json(raw: dict | None) -> Prompt | None:
    if not raw:
        return None
    return Prompt(
        task=Task(raw["task"]),
        text=str(raw["text"]),
        accepted=tuple(str(value) for value in raw["accepted"]),
        answer=str(raw["answer"]),
        source=Source(raw["source"]),
        detail=raw.get("detail"),
        context_id=raw.get("context_id"),
        novel_context=bool(raw.get("novel_context")),
    )


def _step_to_json(step: Step) -> dict:
    return {
        "word_id": step.word.id,
        "kind": step.kind.value,
        "phase": step.phase.value,
        "role": step.role.value,
        "prompt": _prompt_to_json(step.prompt),
        "options": [option.id for option in step.options],
        "hide_meaning": step.hide_meaning,
        "is_struggling": step.is_struggling,
        "reason": step.reason,
        "depth": step.depth.value if step.depth else None,
    }


def _step_from_json(raw: dict, run: _Run, words: dict) -> Step:
    return Step(
        word=run.word,
        kind=StepKind(raw["kind"]),
        phase=Phase(raw["phase"]),
        role=Role(raw["role"]),
        prompt=_prompt_from_json(raw.get("prompt")),
        options=tuple(words[int(i)] for i in raw.get("options", []) if int(i) in words),
        teaching=run.teaching,
        hide_meaning=bool(raw.get("hide_meaning", True)),
        is_struggling=bool(raw.get("is_struggling")),
        reason=raw.get("reason"),
        depth=Depth(raw["depth"]) if raw.get("depth") else None,
    )


def _attempt_to_json(attempt: LearningAttempt) -> dict:
    out: dict[str, Any] = {}
    for spec in fields(attempt):
        value = getattr(attempt, spec.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, StrEnum):
            value = value.value
        out[spec.name] = value
    return out


def _attempt_from_json(raw: dict) -> LearningAttempt:
    return LearningAttempt(
        word_id=int(raw["word_id"]),
        at=datetime.fromisoformat(raw["at"]),
        on_day=str(raw["on_day"]),
        phase=Phase(raw["phase"]),
        role=Role(raw["role"]),
        task=Task(raw["task"]),
        success=bool(raw["success"]),
        session_id=raw.get("session_id"),
        context_id=raw.get("context_id"),
        novel_context=bool(raw.get("novel_context")),
        effort=Effort(raw["effort"]) if raw.get("effort") else None,
        response_ms=raw.get("response_ms"),
        review_log_id=raw.get("review_log_id"),
        route_version=raw.get("route_version") or ROUTE_V2,
        depth=Depth(raw["depth"]) if raw.get("depth") else None,
    )


def _run_to_json(run: _Run) -> dict:
    resolution = run.resolution
    return {
        "new": run.new,
        "depth": run.depth.value if run.depth else None,
        "introduced": run.introduced,
        "rated": run.rated,
        "cycles": run.cycles,
        "log_id": run.log_id,
        "results": [
            {"task": r.task.value, "success": r.success,
             "effort": r.effort.value if r.effort else None, "probe": r.probe}
            for r in run.results
        ],
        "attempts": [_attempt_to_json(attempt) for attempt in run.attempts],
        "sources": sorted(source.value for source in run.sources),
        "contexts": sorted(run.contexts),
        "collocations": sorted(run.collocations),
        "resolution": (
            {"memory": resolution.memory.value, "rating": int(resolution.rating),
             "follow_up": resolution.follow_up.value,
             "repair_level": int(resolution.repair_level) if resolution.repair_level else None}
            if resolution is not None
            else None
        ),
    }


def _run_from_json(raw: dict, word: StoredWord, teaching: WordTeaching, item) -> _Run:
    resolution = raw.get("resolution")
    return _Run(
        word=word,
        teaching=teaching,
        item=item,
        results=[
            Result(Task(r["task"]), bool(r["success"]),
                   Effort(r["effort"]) if r.get("effort") else None, bool(r.get("probe")))
            for r in raw.get("results", [])
        ],
        attempts=[_attempt_from_json(a) for a in raw.get("attempts", [])],
        sources={Source(value) for value in raw.get("sources", [])},
        contexts={int(value) for value in raw.get("contexts", [])},
        collocations=set(raw.get("collocations", [])),
        resolution=Resolution(
            memory=MemoryResult(resolution["memory"]),
            rating=Rating(int(resolution["rating"])),
            follow_up=FollowUp(resolution["follow_up"]),
            repair_level=Level(resolution["repair_level"]) if resolution.get("repair_level")
            else None,
        ) if resolution else None,
        log_id=raw.get("log_id"),
        cycles=int(raw.get("cycles", 0)),
        rated=bool(raw.get("rated")),
        new=bool(raw.get("new")),
        depth=Depth(raw["depth"]) if raw.get("depth") else None,
        introduced=bool(raw.get("introduced")),
    )
