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
