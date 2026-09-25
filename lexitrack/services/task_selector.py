"""Which question a word gets first in a review.

No calendar decides it — not "day 3 is a sentence". The word's **skill**
does: the target starts from its current level (services/skill_tracker.py —
the hardest retrieval it can do now, after any fall), and the last first
question moves it:

* **One harder** when that question was answered without effort
  (Remembered or Instant).
* **The same** when it was missed or took effort.
* **One easier** when the word was forgotten (not even picked out among four).
* **Never harder while the memory is weak**: when FSRS gives less than a 75 %
  chance of recall today, the target does not go up. A harder question on a
  fading memory measures the fading, not the skill.
* **Never above the target**: the question is at the highest level the
  content can ask at that is not above the target. A context needs contexts,
  a collocation collocations, a sentence of one's own an example sentence to
  compare it with; missing content never makes a question harder.
* **Variety**: the context used longest ago comes first, collocations take
  turns, and no kind of question is asked three times in a row in a session
  when another is possible (:func:`repeats`, :func:`lower_levels`).

Levels run from 2 (the word from its meaning) to 5 (a sentence). Level 1 is
only ever a probe, and a word with no meaning to ask from is reviewed the V1
way. Every choice comes with a reason in words, shown on the card, so the
learner can see why this question and not another.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models.attempt import Effort, LearningAttempt, Level, Phase, Role, Task
from ..models.content import WordTeaching
from ..repositories.word_repository import StoredWord
from .review_route import (
    Prompt,
    collocation_prompt,
    context_prompt,
    examples,
    has_meaning,
    meaning_prompt,
    production_prompt,
)

#: Below this chance of recall, no question is harder than last time.
WEAK_MEMORY = 0.75

_LOWEST = Level.MEANING_TO_WORD
_HIGHEST = Level.PRODUCTION


@dataclass(frozen=True, slots=True)
class LevelChoice:
    level: Level
    #: Why, in a sentence for the learner.
    reason: str


def available_levels(word: StoredWord, teaching: WordTeaching) -> set[Level]:
    """The levels this word's content can ask at."""
    if not has_meaning(word, teaching):
        return set()
    levels = {Level.MEANING_TO_WORD}
    if any(context.target for context in teaching.contexts):
        levels.add(Level.CONTEXT_TO_WORD)
    # A sentence of your own is graded against how the word is used; with no
    # example to compare with, it is not asked. A word with no content stays
    # on the short route: the word from its meaning.
    if examples(teaching):
        levels.add(Level.PRODUCTION)
    if collocation_prompt(word, teaching) is not None:
        levels.add(Level.COLLOCATION)
    return levels


def _review_events(attempts: Sequence[LearningAttempt]) -> list[list[LearningAttempt]]:
    """The word's past reviews, oldest first, each as its primary and probes."""
    events: dict[object, list[LearningAttempt]] = {}
    for attempt in attempts:
        if attempt.phase is not Phase.REVIEW or attempt.role is Role.RETRIEVAL:
            continue
        key = attempt.review_log_id if attempt.review_log_id is not None else ("a", attempt.id)
        events.setdefault(key, []).append(attempt)
    return [event for event in events.values() if event[0].role is Role.PRIMARY]


def choose_level(
    attempts: Sequence[LearningAttempt],
    available: set[Level],
    retrievability: float | None = None,
    skill_level: Level | None = None,
) -> LevelChoice | None:
    """The level of the first question, from the word's attempts (oldest first).

    ``skill_level`` is the word's current level; when not given it is read
    from ``attempts`` (answers from before attempts were recorded are then
    not counted). None when nothing can be asked (no meaning to ask from).
    """
    if not available:
        return None
    if skill_level is None:
        # Imported here: skill_tracker builds on this module's neighbours.
        from .skill_tracker import derive_skill

        skill_level = derive_skill(0, attempts).level
    # Encountered or recognised aims from recognition; the first question
    # is never below the word from its meaning.
    anchor = skill_level or Level.WORD_TO_MEANING
    events = _review_events(attempts)
    if not events:
        target = anchor + 1 if skill_level else _LOWEST
        reason = "First question for this word: the word from its meaning."
    else:
        last = events[-1]
        primary = last[0]
        label = Level(max(primary.level, _LOWEST)).label.lower()
        forgotten = not any(attempt.success for attempt in last)
        if forgotten:
            target = anchor - 1
            reason = "You forgot it last time, so an easier question."
        elif primary.success and primary.effort is not Effort.EFFORTFUL:
            target = anchor + 1
            reason = (
                "You recognised it last time; now the word from its meaning."
                if primary.level < _LOWEST
                else f"Last time “{label}” came easily, so one step harder."
            )
        elif primary.success:
            target = anchor
            reason = f"Last time “{label}” took effort, so the same kind again."
        else:
            target = anchor
            reason = f"Last time “{label}” was missed, so the same kind again."
        if retrievability is not None and retrievability < WEAK_MEMORY and target > anchor:
            target = anchor
            reason = (
                f"The chance you remember it today is {retrievability:.0%}, "
                "so no harder question until the memory is stronger."
            )
    target = Level(min(max(target, _LOWEST), _HIGHEST))
    below = sorted((level for level in available if level <= target), reverse=True)
    level = below[0] if below else min(available)
    if level != target:
        reason = f"{reason} Nothing stored for “{target.label.lower()}” yet."
    return LevelChoice(level, reason)


def repeats(recent: Sequence[Task], task: Task) -> bool:
    """True when ``task`` would be the third of its kind in a row."""
    return len(recent) >= 2 and recent[-1] is task and recent[-2] is task


def lower_levels(level: Level, available: set[Level]) -> list[Level]:
    """The levels below ``level`` the content can ask at, hardest first:
    where to look for a different kind of question without going harder."""
    return sorted((other for other in available if other < level), reverse=True)


def prompt_for(
    level: Level,
    word: StoredWord,
    teaching: WordTeaching,
    attempts: Sequence[LearningAttempt],
    context_uses: dict[int, object],
) -> Prompt | None:
    """The question at ``level``, varied against what was asked before."""
    if level is Level.CONTEXT_TO_WORD:
        return context_prompt(word, teaching, used=context_uses)
    if level is Level.COLLOCATION:
        asked = sum(1 for a in attempts if a.level is Level.COLLOCATION and a.phase is Phase.REVIEW)
        collocations = teaching.content.collocations if teaching.content else ()
        if collocations:
            # Take turns: skip the ones asked most recently.
            skip = set(collocations[: asked % len(collocations)])
            prompt = collocation_prompt(word, teaching, exclude=skip)
            if prompt is not None:
                return prompt
        return collocation_prompt(word, teaching)
    if level is Level.PRODUCTION:
        return production_prompt(word, teaching)
    return meaning_prompt(word, teaching)
