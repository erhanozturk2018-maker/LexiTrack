"""Which question a word gets first in a review.

No calendar decides it — not "day 3 is a sentence". The word's own record
does, one step at a time:

* **Harder** by one level when the last first question at the current level
  was answered without effort (no hint, no slip, not slow).
* **The same** level when it was missed or took effort.
* **Easier** by one level when the word was forgotten last time (not even
  picked out among four).
* **Never harder while the memory is weak**: when FSRS gives less than a 75 %
  chance of recall today, the level does not go up. A harder question on a
  fading memory measures the fading, not the skill.
* **Only what the content allows**: a context needs contexts, a collocation
  collocations, a sentence of one's own an example sentence to compare it
  with. A word with no content stays on the short route: its meaning. A
  level with nothing to ask from is stepped over going up, and stepped down
  from going down.
* **Variety** within a level: the context used longest ago comes first, and
  collocations take turns.

Levels run from 2 (the word from its meaning) to 5 (a sentence). Level 1 is
only ever a probe, and a word with no meaning to ask from is reviewed the V1
way. Every choice comes with a reason in words, shown on the card, so the
learner can see why this question and not another.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models.attempt import Effort, LearningAttempt, Level, Phase, Role
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
) -> LevelChoice | None:
    """The level of the first question, from the word's attempts (oldest first).

    None when nothing can be asked (no meaning to ask from).
    """
    if not available:
        return None
    events = _review_events(attempts)
    if not events:
        target, reason = _LOWEST, "First question for this word: the word from its meaning."
        last_level = _LOWEST
    else:
        last = events[-1]
        primary = last[0]
        # A V1 review asked word → meaning (level 1); anything asked before
        # counts from level 2 up.
        last_level = max(primary.level, _LOWEST)
        label = last_level.label.lower()
        forgotten = not any(attempt.success for attempt in last)
        if forgotten:
            target = Level(max(last_level - 1, _LOWEST))
            reason = "You forgot it last time, so an easier question."
        elif primary.success and primary.effort is not Effort.EFFORTFUL:
            if primary.level < _LOWEST:
                target = _LOWEST
                reason = "You recognised it last time; now the word from its meaning."
            else:
                target = Level(min(last_level + 1, _HIGHEST))
                reason = f"Last time “{label}” came easily, so one step harder."
        elif primary.success:
            target = last_level
            reason = f"Last time “{label}” took effort, so the same kind again."
        else:
            target = last_level
            reason = f"Last time “{label}” was missed, so the same kind again."

        if (
            retrievability is not None
            and retrievability < WEAK_MEMORY
            and target > last_level
        ):
            target = last_level
            reason = (
                f"The chance you remember it today is {retrievability:.0%}, "
                "so no harder question until the memory is stronger."
            )

    if target in available:
        return LevelChoice(target, reason)
    going_up = target > last_level
    if going_up:
        # Step over a level the content cannot ask at.
        higher = sorted(level for level in available if level > last_level)
        if higher:
            return LevelChoice(
                higher[0], f"{reason} Nothing stored for “{target.label.lower()}” yet."
            )
    lower = sorted((level for level in available if level <= target), reverse=True)
    level = lower[0] if lower else min(available)
    if level != target:
        reason = f"{reason} Nothing stored for “{target.label.lower()}” yet."
    return LevelChoice(level, reason)


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
