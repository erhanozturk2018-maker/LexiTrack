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


def derive_skill(
    word_id: int,
    attempts: Sequence[LearningAttempt],
    legacy: LegacyRecord | None = None,
    introduced: bool = False,
) -> WordSkill:
    """A word's skill from its attempts (not undone), oldest first."""
    legacy = legacy or LegacyRecord()
    recognized = legacy.successes
    recalled = 0
    failures = legacy.failures
    novel = 0
    produced: set[tuple[str, int | None]] = set()
    automatic: set[str] = set()
    last_on = legacy.last_on

    for attempt in attempts:
        last_on = max(filter(None, (last_on, attempt.on_day)), default=None)
        if attempt.phase not in _EVIDENCE_PHASES or attempt.role not in _EVIDENCE_ROLES:
            continue
        if not attempt.success:
            if attempt.role is Role.PRIMARY:
                failures += 1
            continue
        level = attempt.level
        if level is Level.WORD_TO_MEANING:
            recognized += 1
        elif level <= Level.CONTEXT_TO_WORD:
            recalled += 1
        else:
            produced.add((attempt.task.value, attempt.context_id))
        if attempt.novel_context:
            novel += 1
        if level > Level.WORD_TO_MEANING and attempt.effort is Effort.INSTANT:
            automatic.add(attempt.on_day)

    if len(produced) >= PRODUCTIVE_VARIETY:
        stage = SkillStage.PRODUCTIVE
    elif recalled or produced:
        # One production is still a retrieval of the word from its use.
        stage = SkillStage.RECALLED
    elif recognized:
        stage = SkillStage.RECOGNIZED
    elif introduced or attempts or legacy.successes or legacy.failures:
        stage = SkillStage.ENCOUNTERED
    else:
        stage = SkillStage.NONE

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
