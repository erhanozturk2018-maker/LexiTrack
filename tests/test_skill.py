"""Skill: what the record shows a learner can do with a word (Phase 3).

The rules that matter are the ones that keep the evidence honest: only
delayed retrievals count, old reviews count as recognition only, Undo takes
the attempt back with the answer, and a stage never comes from a single line.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import Effort, LearningAttempt, Phase, Role, Task
from lexitrack.models.skill import SkillStage
from lexitrack.models.srs import Rating
from lexitrack.repositories import AttemptRepository, CardRepository
from lexitrack.services.learning_service import LearningService
from lexitrack.services.skill_tracker import LegacyRecord, SkillTracker, derive_skill

from .test_learning_service import clock, engine  # noqa: F401 - fixtures

AT = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def _attempt(
    task: Task,
    success: bool = True,
    *,
    day: str = "2026-09-20",
    phase: Phase = Phase.REVIEW,
    role: Role = Role.PRIMARY,
    effort: Effort | None = Effort.NORMAL,
    context_id: int | None = None,
    novel: bool = False,
) -> LearningAttempt:
    return LearningAttempt(
        word_id=1, at=AT, on_day=day, phase=phase, role=role, task=task,
        success=success, effort=effort, context_id=context_id, novel_context=novel,
    )


def _introduce_and_wait(service: LearningService, day_clock: FrozenClock) -> list[int]:
    ids = [word.id for word in service.introduce().introduced]
    day_clock.advance_to_day_start(1)
    day_clock.advance(hours=4)
    return ids


# -- the rules ----------------------------------------------------------------------


def test_nothing_and_an_introduction() -> None:
    assert derive_skill(1, []).stage is SkillStage.NONE
    assert derive_skill(1, [], introduced=True).stage is SkillStage.ENCOUNTERED


def test_practice_straight_after_teaching_is_not_evidence() -> None:
    practice = [
        _attempt(Task.MEANING_TO_WORD, phase=Phase.INTRODUCTION, role=Role.RETRIEVAL),
        _attempt(Task.CONTEXT_CLOZE, phase=Phase.RELEARN, role=Role.RETRIEVAL),
        _attempt(Task.PRODUCTION, phase=Phase.REPAIR, role=Role.RETRIEVAL),
    ]
    skill = derive_skill(1, practice)
    assert skill.stage is SkillStage.ENCOUNTERED
    assert skill.recalled == 0 and skill.produced_in == 0


def test_stages_follow_the_hardest_delayed_success() -> None:
    assert derive_skill(1, [_attempt(Task.WORD_TO_MEANING)]).stage is SkillStage.RECOGNIZED
    assert derive_skill(1, [_attempt(Task.MEANING_TO_WORD)]).stage is SkillStage.RECALLED
    probe = _attempt(Task.WORD_TO_MEANING, role=Role.PROBE)
    assert derive_skill(1, [probe]).stage is SkillStage.RECOGNIZED
    failed = derive_skill(1, [_attempt(Task.MEANING_TO_WORD, success=False)])
    assert failed.stage is SkillStage.ENCOUNTERED and failed.failures == 1


def test_productive_needs_two_different_contexts_or_tasks() -> None:
    same = [_attempt(Task.PRODUCTION, context_id=7), _attempt(Task.PRODUCTION, context_id=7)]
    assert derive_skill(1, same).stage is SkillStage.RECALLED
    varied = [_attempt(Task.PRODUCTION, context_id=7), _attempt(Task.COLLOCATION, context_id=7)]
    assert derive_skill(1, varied).stage is SkillStage.PRODUCTIVE


def test_automatic_is_evidence_on_different_days_not_a_stage() -> None:
    one_day = [
        _attempt(Task.MEANING_TO_WORD, effort=Effort.INSTANT),
        _attempt(Task.CONTEXT_CLOZE, effort=Effort.INSTANT),
    ]
    assert not derive_skill(1, one_day).automatic
    two_days = one_day + [
        _attempt(Task.MEANING_TO_WORD, effort=Effort.INSTANT, day="2026-09-24")
    ]
    skill = derive_skill(1, two_days)
    assert skill.automatic and skill.stage is SkillStage.RECALLED
    # Instant recognition is not automatic use.
    fast_recognition = [
        _attempt(Task.WORD_TO_MEANING, effort=Effort.INSTANT, day=day)
        for day in ("2026-09-20", "2026-09-21", "2026-09-22")
    ]
    assert not derive_skill(1, fast_recognition).automatic


def test_old_answers_count_as_recognition_only() -> None:
    skill = derive_skill(1, [], LegacyRecord(successes=6, failures=1, last_on="2026-09-10"))
    assert skill.stage is SkillStage.RECOGNIZED
    assert skill.recognized == 6 and skill.failures == 1 and skill.from_v1 == 7
    assert skill.recalled == 0


# -- the record -------------------------------------------------------------------


def test_every_answer_records_its_attempt(
    engine: LearningService, clock: FrozenClock, database: Database  # noqa: F811
) -> None:
    ids = _introduce_and_wait(engine, clock)
    engine.answer(ids[0], Rating.EASY)
    engine.answer(ids[1], Rating.AGAIN)
    attempts = AttemptRepository(database)
    first = attempts.for_word(ids[0])
    assert len(first) == 1
    attempt = first[0]
    assert attempt.task is Task.WORD_TO_MEANING and attempt.route_version == "v1"
    assert attempt.success and attempt.effort is Effort.INSTANT
    assert attempt.review_log_id is not None
    again = attempts.for_word(ids[1])[0]
    assert not again.success and again.effort is None

    tracker = SkillTracker(database)
    skills = tracker.skills(ids[:3])
    assert skills[ids[0]].stage is SkillStage.RECOGNIZED
    assert skills[ids[1]].stage is SkillStage.ENCOUNTERED
    assert skills[ids[2]].stage is SkillStage.ENCOUNTERED  # introduced, not answered


def test_undo_takes_the_attempt_back_with_the_answer(
    engine: LearningService, clock: FrozenClock, database: Database  # noqa: F811
) -> None:
    ids = _introduce_and_wait(engine, clock)
    engine.answer(ids[0], Rating.GOOD)
    assert engine.undo_last_answer() is not None
    attempts = AttemptRepository(database)
    assert attempts.for_word(ids[0]) == []
    kept = attempts.for_word(ids[0], include_undone=True)
    assert len(kept) == 1 and kept[0].undone_at is not None
    assert SkillTracker(database).skill(ids[0]).stage is SkillStage.ENCOUNTERED


def test_answers_from_before_attempts_are_read_from_the_log(
    engine: LearningService, clock: FrozenClock, database: Database  # noqa: F811
) -> None:
    ids = _introduce_and_wait(engine, clock)
    engine.answer(ids[0], Rating.GOOD)
    # As an answer given before schema 5 would look: a log, no attempt.
    database.connection.execute("DELETE FROM learning_attempts")
    skill = SkillTracker(database).skill(ids[0])
    assert skill.stage is SkillStage.RECOGNIZED and skill.from_v1 == 1


def test_reset_clears_the_attempts_too(
    engine: LearningService, clock: FrozenClock, database: Database  # noqa: F811
) -> None:
    ids = _introduce_and_wait(engine, clock)
    engine.answer(ids[0], Rating.GOOD)
    CardRepository(database).clear_all()
    count = database.connection.execute("SELECT COUNT(*) FROM learning_attempts").fetchone()
    assert count[0] == 0
