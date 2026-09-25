"""Driving a ReviewFlow the way a learner does, for the tests."""

from __future__ import annotations

from lexitrack.models.attempt import SelfReport
from lexitrack.services.review_flow import Feedback, ReviewFlow


def say(
    flow: ReviewFlow,
    typed: str,
    report: SelfReport = SelfReport.REMEMBERED,
    **observed,
) -> Feedback:
    """Type an answer; when it is right in a review, report how it came."""
    feedback = flow.submit(typed, **observed)
    if feedback.awaiting:
        feedback = flow.assess(report)
    return feedback


def record_known_evidence(engine, word_ids, gap_days: float | None = 30.0) -> None:
    """Give words the record Known is offered on: used well in two different
    tasks (productive), and their latest answer after a long gap without a
    review. Written straight into the record, as the evidence would be."""
    from lexitrack.models.attempt import Effort, LearningAttempt, Phase, Role, Task
    from lexitrack.repositories import AttemptRepository

    attempts = AttemptRepository(engine.database)
    clock = engine.clock
    for word_id in word_ids:
        for task in (Task.PRODUCTION, Task.COLLOCATION):
            attempts.add(LearningAttempt(
                word_id=word_id, at=clock.now_utc(), on_day=clock.today(),
                phase=Phase.REVIEW, role=Role.PRIMARY, task=task, success=True,
                effort=Effort.NORMAL,
            ))
        if gap_days is None:
            continue
        engine.database.connection.execute(
            "UPDATE review_logs SET elapsed_days = ? WHERE id = (SELECT id FROM review_logs "
            "WHERE word_id = ? AND undone_at IS NULL AND rating != 1 "
            "ORDER BY reviewed_at DESC, id DESC LIMIT 1)",
            (gap_days, word_id),
        )
