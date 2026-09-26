"""Driving the engine and a ReviewFlow the way a learner does, for the tests."""

from __future__ import annotations

from lexitrack.models.attempt import Effort, LearningAttempt, Phase, Role, Task
from lexitrack.models.srs import Rating
from lexitrack.services.review_flow import Feedback, ReviewFlow, StepKind


def answer(engine, word_id: int, rating: Rating, **kwargs):
    """The day's answer for a word without a session's questions: a
    Definition → Word answer, right unless rated Again, recorded as the
    session would record it."""
    rating = Rating(int(rating))
    correct = rating is not Rating.AGAIN
    attempt = LearningAttempt(
        word_id=int(word_id),
        at=engine.clock.now_utc(),
        on_day=engine.clock.today(),
        phase=Phase.REVIEW,
        role=Role.PRIMARY,
        task=Task.DEFINITION_TO_WORD,
        correct=correct,
        effort=Effort.of(rating) if correct else None,
    )
    kwargs.setdefault("attempts", [attempt])
    return engine.review(
        word_id, rating, task=Task.DEFINITION_TO_WORD, correct=correct, **kwargs
    )


def right(flow: ReviewFlow, rating: Rating = Rating.GOOD) -> Feedback:
    """Choose the right option; in a review, rate it."""
    step = flow.current
    feedback = flow.choose(step.question.answer)
    if feedback.awaiting:
        feedback = flow.rate(rating)
    return feedback


def wrong(flow: ReviewFlow) -> Feedback:
    """Choose an option that is not the right one."""
    question = flow.current.question
    return flow.choose((question.answer + 1) % len(question.options))


def finish_learning(flow: ReviewFlow) -> None:
    """Go through the session's new words: read each, answer each right."""
    while flow.current is not None:
        if flow.current.kind is StepKind.TEACH:
            flow.proceed()
        else:
            right(flow)


def record_known_evidence(engine, word_ids, gap_days: float | None = 30.0) -> None:
    """Give words the record Known is offered on: their latest answer right,
    after a long gap without a review. Written straight into the record, as
    the evidence would be."""
    if gap_days is None:
        return
    for word_id in word_ids:
        engine.database.connection.execute(
            "UPDATE review_logs SET elapsed_days = ? WHERE id = (SELECT id FROM review_logs "
            "WHERE word_id = ? AND undone_at IS NULL AND rating != 1 "
            "ORDER BY reviewed_at DESC, id DESC LIMIT 1)",
            (gap_days, word_id),
        )
