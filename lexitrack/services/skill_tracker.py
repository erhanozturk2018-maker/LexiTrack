"""Deriving a word's skill from its record.

Nothing here is stored: a word's skill is recomputed from
``learning_attempts`` (and, for answers from before schema 5, from
``review_logs``) whenever it is asked for, so it can never drift from the
record and an Undo is reflected at once. The rules are in
:mod:`lexitrack.models.skill`; :func:`derive_skill` is the whole of them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..database.connection import Database
from ..models.attempt import Effort, LearningAttempt, Level, Phase, Role
from ..models.skill import PRODUCTIVE_VARIETY, SkillStage, WordSkill
from ..repositories import AttemptRepository, CardRepository

#: Attempts that count as evidence: delayed, so they measure learning.
_EVIDENCE_PHASES = {Phase.REVIEW}
_EVIDENCE_ROLES = {Role.PRIMARY, Role.PROBE}


@dataclass(frozen=True, slots=True)
class LegacyRecord:
    """A word's answers from before schema 5, which asked word → meaning only."""

    successes: int = 0
    failures: int = 0
    last_on: str | None = None


def _events(attempts: Sequence[LearningAttempt]) -> list[list[LearningAttempt]]:
    """The delayed-evidence attempts grouped by the review they belong to, in order.

    A review is its primary question and the probes after it; they share the
    answer's ``review_log_id``. An attempt with none stands alone.
    """
    events: dict[object, list[LearningAttempt]] = {}
    for index, attempt in enumerate(attempts):
        if attempt.phase not in _EVIDENCE_PHASES or attempt.role not in _EVIDENCE_ROLES:
            continue
        key = attempt.review_log_id if attempt.review_log_id is not None else ("a", index)
        events.setdefault(key, []).append(attempt)
    return list(events.values())


def derive_skill(
    word_id: int,
    attempts: Sequence[LearningAttempt],
    legacy: LegacyRecord | None = None,
    introduced: bool = False,
) -> WordSkill:
    """A word's skill from its attempts (not undone), oldest first.

    The **evidence** (how often each thing succeeded) only grows. The **current
    level** — the hardest retrieval the learner can do *now* — follows the
    record review by review and can fall (models/skill.py):

    * a review that ends FORGOTTEN (nothing succeeded, not even a probe)
      caps it at recognition until a recall succeeds again;
    * two failed first questions in a row at the current level drop it one;
    * a success raises it to the level succeeded at.

    The stage is read from the current level: level 1 is RECOGNIZED, 2–3
    RECALLED, 4–5 PRODUCTIVE once used in two different contexts or tasks
    since it last fell below level 4.
    """
    legacy = legacy or LegacyRecord()
    recognized = legacy.successes
    recalled = 0
    failures = legacy.failures
    novel = 0
    produced: set[tuple[str, int | None]] = set()
    automatic: set[str] = set()
    last_on = legacy.last_on
    # Old answers asked word -> meaning: recognition at most.
    level: Level | None = Level.WORD_TO_MEANING if legacy.successes else None
    produced_now: set[tuple[str, int | None]] = set()
    streak = 0
    regressed = False

    for attempt in attempts:
        last_on = max(filter(None, (last_on, attempt.on_day)), default=None)

    for event in _events(attempts):
        for attempt in event:
            if not attempt.success:
                if attempt.role is Role.PRIMARY:
                    failures += 1
                continue
            if attempt.level is Level.WORD_TO_MEANING:
                recognized += 1
            elif attempt.level <= Level.CONTEXT_TO_WORD:
                recalled += 1
            else:
                produced.add((attempt.task.value, attempt.context_id))
            if attempt.novel_context:
                novel += 1
            if attempt.level > Level.WORD_TO_MEANING and attempt.effort is Effort.INSTANT:
                automatic.add(attempt.on_day)

        successes = [attempt for attempt in event if attempt.success]
        primary = event[0] if event[0].role is Role.PRIMARY else None
        if not successes:
            # FORGOTTEN: at most recognition until the word is recalled again.
            if level is not None and level > Level.WORD_TO_MEANING:
                level = Level.WORD_TO_MEANING
                regressed = True
            produced_now.clear()
            streak = 0
            continue
        if primary is not None and not primary.success and level is not None:
            if primary.level == level:
                streak += 1
                if streak >= 2:
                    level = Level(max(level - 1, Level.WORD_TO_MEANING))
                    regressed = True
                    streak = 0
        best = max(attempt.level for attempt in successes)
        if level is None or best > level:
            level = best
        if primary is not None and primary.success and primary.level >= level:
            streak = 0
        if level < Level.COLLOCATION:
            produced_now.clear()
        for attempt in successes:
            if attempt.level >= Level.COLLOCATION:
                produced_now.add((attempt.task.value, attempt.context_id))

    if level is None:
        stage = (
            SkillStage.ENCOUNTERED
            if introduced or attempts or legacy.successes or legacy.failures
            else SkillStage.NONE
        )
    elif level is Level.WORD_TO_MEANING:
        stage = SkillStage.RECOGNIZED
    elif level >= Level.COLLOCATION and len(produced_now) >= PRODUCTIVE_VARIETY:
        stage = SkillStage.PRODUCTIVE
    else:
        # One production is still a retrieval of the word from its use.
        stage = SkillStage.RECALLED

    return WordSkill(
        word_id=int(word_id),
        stage=stage,
        recognized=recognized,
        recalled=recalled,
        produced_in=len(produced),
        novel_context=novel,
        failures=failures,
        automatic_days=len(automatic),
        from_v1=legacy.successes + legacy.failures,
        last_on=last_on,
        level=level,
        regressed=regressed,
    )


class SkillTracker:
    def __init__(self, database: Database) -> None:
        self._attempts = AttemptRepository(database)
        self._cards = CardRepository(database)

    def skill(self, word_id: int) -> WordSkill:
        return self.skills([word_id])[int(word_id)]

    def skills(self, word_ids: Iterable[int]) -> dict[int, WordSkill]:
        ids = [int(word_id) for word_id in dict.fromkeys(word_ids)]
        attempts = self._attempts.for_words(ids)
        legacy = self._attempts.legacy_recognition(ids)
        cards = self._cards.get_many(ids)
        return {
            word_id: derive_skill(
                word_id,
                attempts.get(word_id, []),
                LegacyRecord(*legacy[word_id]) if word_id in legacy else None,
                introduced=word_id in cards,
            )
            for word_id in ids
        }

    def stage_counts(self, word_ids: Iterable[int]) -> dict[SkillStage, int]:
        counts = {stage: 0 for stage in SkillStage}
        for skill in self.skills(word_ids).values():
            counts[skill.stage] += 1
        return counts
