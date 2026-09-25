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
answered by having just seen it. Relearning and repair questions use a
different prompt from the one that failed, are recorded as practice linked to
the answer, and never change the rating.

A word with no meaning to ask from (no definition, and no meaning in the
learner's language) is
reviewed the V1 way: shown, revealed, rated by the learner. The page shows
steps and reports what the learner did; it holds no state of its own.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..models.attempt import (
    ROUTE_V2,
    Depth,
    Effort,
    LearningAttempt,
    Level,
    Phase,
    Role,
    Task,
)
from ..models.content import WordTeaching
from ..models.srs import Rating
from ..repositories import AttemptRepository, ContentRepository
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
from .task_selector import available_levels, choose_level, prompt_for

log = logging.getLogger(__name__)

FLOW_VERSION = 1

#: Why a new word's question is asked, for the card.
_PRACTICE = "Right after learning it: practice, not rated. Its first review is tomorrow."


class StepKind(StrEnum):
    #: Type the word (levels 2–4).
    TYPE = "type"
    #: Choose the word among four (the level 1 probe).
    CHOOSE = "choose"
    #: Write a sentence, then compare it with examples and grade it (level 5).
    WRITE = "write"
    #: V1: the word shown, the meaning revealed, one of four answers.
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


class ReviewFlow:
    """A day's reviews by route V2."""

    def __init__(self, engine: LearningService) -> None:
        self._engine = engine
        self._content = ContentRepository(engine.database)
        self._attempts = AttemptRepository(engine.database)
        self._steps: list[Step] = []
        self._runs: dict[int, _Run] = {}
        self._order: list[int] = []
        self._session_id: str | None = None
        self._last_session_id: str | None = None
        self._answered = 0
        self._last_answer: tuple[str, Rating] | None = None
        self._last_word_id: int | None = None
        self._revealed = False
        self._learned = 0

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
    def revealed(self) -> bool:
        """For a RECALL step: whether the meaning shows."""
        return self._revealed

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
        """For a RECALL step: when each answer would bring the word back."""
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
        self._session_id = self._engine.start_session().id
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
        """A typed answer to a TYPE step. An empty answer is "I don't know"."""
        step = self._take(StepKind.TYPE)
        prompt = step.prompt
        check = check_typed(prompt.accepted, typed)
        effort = (
            effort_for(response_ms, typed, hinted=hinted, near_miss=check.near_miss)
            if check.correct
            else None
        )
        return self._record(step, check.correct, effort, response_ms, near_miss=check.near_miss)

    def choose(self, index: int, response_ms: int | None = None) -> Feedback:
        """The option picked in a CHOOSE step."""
        step = self._take(StepKind.CHOOSE)
        picked = step.options[index] if 0 <= index < len(step.options) else None
        correct = picked is not None and picked.id == step.word.id
        return self._record(step, correct, Effort.NORMAL if correct else None, response_ms)

    def grade(self, used_well: bool, effortful: bool = False) -> Feedback:
        """The learner's grade of their own sentence in a WRITE step."""
        step = self._take(StepKind.WRITE)
        effort = (Effort.EFFORTFUL if effortful else Effort.NORMAL) if used_well else None
        return self._record(step, used_well, effort, None)

    def reveal(self) -> bool:
        step = self.current
        if step is None or step.kind is not StepKind.RECALL or self._revealed:
            return False
        self._revealed = True
        return True

    def rate(self, rating: Rating) -> Feedback:
        """A V1 answer to a RECALL step."""
        step = self._take(StepKind.RECALL)
        run = self._runs[step.word.id]
        outcome = self._engine.answer(step.word.id, rating, session_id=self._session_id)
        self._steps.pop(0)
        self._rated(run, outcome, rating)
        self._on_step()
        self._save()
        return Feedback(
            correct=rating is not Rating.AGAIN,
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
        choice = choose_level(
            history,
            available_levels(word, run.teaching),
            self._engine.retrievability(word.id),
        )
        if choice is None:
            hide = run.item.hide_meaning if run.item else True
            return Step(word, StepKind.RECALL, hide_meaning=hide, is_struggling=struggling,
                        reason="No meaning is stored to ask from, so the word is shown.")
        prompt = prompt_for(
            choice.level, word, run.teaching, history, self._attempts.context_uses(word.id)
        ) or meaning_prompt(word, run.teaching)
        return self._ask(run, prompt, Role.PRIMARY, Phase.REVIEW, struggling, choice.reason)

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
        task = Task.CHOOSE_WORD if step.kind is StepKind.CHOOSE else prompt.task
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
        step = self.current
        self._revealed = step is not None and step.kind is StepKind.RECALL and not (
            step.hide_meaning
        )

    # -- saving and restoring ----------------------------------------------------

    def state(self) -> dict:
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
        """An open session as saved: the words not yet rated are asked from the start.

        A word part-way through its probes starts again at its first question,
        and teaching owed to words already rated is not restored: practice is
        only worth doing while the failure is fresh.
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
            or int(data.get("version", 0)) != FLOW_VERSION
            or data.get("route") != ROUTE_V2
        ):
            return None
        flow = cls(engine)
        flow._session_id = session_id
        flow._last_session_id = session_id
        flow._answered = int(data.get("answered", 0))
        flow._learned = int(data.get("learned", 0))
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
