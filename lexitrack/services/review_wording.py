"""What a study step says, in words every client shows the same way.

The desktop card and the Telegram bot show the same session
(services/review_flow.py). What each says after an answer, and how an
interval is put, is decided once here, as plain text; each client only lays
it out.

After an answer the right word and its definition are always shown, so the
learner sees at once what was asked — whichever way the question went:

    Incorrect                        Incorrect
    Correct answer: sleep in         Word: sleep in
    Definition: to sleep later …     Correct definition: to sleep later …
    (Definition → Word)              (Context → Definition)
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.attempt import Task
from ..models.srs import Rating
from .review_flow import Feedback, Step


def interval_text(days: int | None) -> str:
    """An interval in the shortest honest words: "tomorrow", "5 days", "2 months"."""
    if days is None:
        return "–"
    if days <= 1:
        return "tomorrow"
    if days < 30:
        return f"{days} days"
    if days < 365:
        months = round(days / 30)
        return f"{months} month" + ("s" if months != 1 else "")
    years = days / 365
    return "1 year" if round(years) == 1 else f"{years:.1f} years"


def when_text(days: int) -> str:
    """"tomorrow" or "in 5 days"."""
    text = interval_text(days)
    return text if text == "tomorrow" else f"in {text}"


@dataclass(frozen=True, slots=True)
class FeedbackText:
    """What the card says after an answer."""

    #: "Correct" or "Incorrect".
    title: str
    #: good or bad, for the colour.
    tone: str
    #: (label, text): the word and its definition, labelled for the task.
    lines: tuple[tuple[str, str], ...]
    #: What happens next, or None.
    note: str | None = None


def feedback_text(step: Step | None, feedback: Feedback) -> FeedbackText:
    """The right word and its definition, and what the answer did."""
    word = feedback.word.word
    definition = feedback.definition
    task = step.task if step is not None else Task.DEFINITION_TO_WORD
    if feedback.correct:
        lines = (("Word", word), ("Definition", definition))
    elif task is Task.CONTEXT_TO_DEFINITION:
        lines = (("Word", word), ("Correct definition", definition))
    else:
        lines = (("Correct answer", word), ("Definition", definition))
    notes: list[str] = []
    if feedback.awaiting:
        notes.append("How did it go?")
    elif feedback.practice:
        notes.append("Practice: not rated.")
    outcome = feedback.outcome
    if outcome is not None and not outcome.duplicate and not feedback.correct:
        notes.append(f"Rated Again: back {when_text(outcome.interval_days)}.")
    if feedback.again_later:
        notes.append("It comes back in a few cards.")
    return FeedbackText(
        title="Correct" if feedback.correct else "Incorrect",
        tone="good" if feedback.correct else "bad",
        lines=tuple((label, text) for label, text in lines if text),
        note=" ".join(notes) or None,
    )


def rated_line(feedback: Feedback) -> str | None:
    """After Again / Hard / Good / Easy: "“sleep in”: Good · back in 5 days"."""
    outcome = feedback.outcome
    if outcome is None:
        return None
    text = f"“{feedback.word.word}”: {Rating(outcome.rating).label}"
    if not outcome.duplicate:
        text += f" · back {when_text(outcome.interval_days)}"
    if feedback.again_later:
        text += " · asked again in a few cards"
    return text
