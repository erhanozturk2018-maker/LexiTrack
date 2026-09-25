"""What a study step says, in words every client shows the same way.

The desktop card, the Telegram bot and — later — a phone all show the same
session (services/review_flow.py). What each says after an answer, how an
interval is put, and what a teaching page holds are decided once here, as
plain text; each client only lays it out. No markup and no widgets: the
desktop turns a :class:`TeachingPage` into rich text, the bot into Telegram
HTML.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.attempt import Depth, MemoryResult, Phase, Role
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


def feedback_text(step: Step | None, feedback: Feedback) -> tuple[str, str]:
    """The line under an answer, and its tone: good, bad or neutral."""
    if feedback.answer is None:
        return "Not quite. One more question about this word.", "bad"
    answer = feedback.answer
    outcome = feedback.outcome
    back = ""
    if outcome is not None and not outcome.duplicate:
        back = f"Back {when_text(outcome.interval_days)}"
    later = ""
    if feedback.resolution is not None and feedback.resolution.follow_up.value != "none":
        later = "; you'll go over it again in a moment"
    if step is not None and step.phase is not Phase.REVIEW:
        # Practice after teaching: never rated, so nothing about when.
        if feedback.correct:
            return f"✓ “{answer}”", "good"
        return f"It is “{answer}”.", "bad"
    probe = step is not None and step.role is Role.PROBE
    if feedback.correct and not probe:
        slip = " — accepted; mind the spelling" if feedback.near_miss else ""
        return f"✓ “{answer}”{slip} · {back.lower()}" if back else f"✓ “{answer}”{slip}", "good"
    memory = feedback.resolution.memory if feedback.resolution else None
    tail = f" {back}{later}." if back else ""
    if memory is MemoryResult.RECALLED or memory is MemoryResult.RECALLED_EFFORT:
        return f"It is “{answer}” — the word itself you knew.{tail}", "neutral"
    if memory is MemoryResult.RECOGNIZED:
        return f"It is “{answer}” — you recognised it.{tail}", "neutral"
    return f"It is “{answer}”.{tail}", "bad"


@dataclass(frozen=True, slots=True)
class TeachingPage:
    """A word's teaching page: titled sections, then its examples."""

    #: (title, text) in the order shown.
    sections: tuple[tuple[str, str], ...]
    #: (sentence, translation or None), under "In use".
    examples: tuple[tuple[str, str | None], ...]

    @property
    def empty(self) -> bool:
        return not self.sections and not self.examples


def teaching_page(step: Step) -> TeachingPage:
    """What is known about the word, as much as its depth asks.

    SHORT: the meaning. LIGHT: also how it is used and one example. DEEP, and
    teaching again after a miss: everything stored, two examples.
    """
    depth = step.depth
    usage = depth is not Depth.SHORT
    everything = depth is None or depth is Depth.DEEP
    word = step.word
    teaching = step.teaching
    content = teaching.content if teaching else None
    localization = teaching.localization if teaching else None
    sections: list[tuple[str, str]] = []

    def add(title: str, text: str | None) -> None:
        if text:
            sections.append((title, text))

    add("Meaning", teaching.core_meaning if teaching else None)
    add("Definition", word.definition)
    if content and usage:
        add("Pattern", content.pattern)
        if content.collocations:
            add("Goes with", " · ".join(content.collocations))
    if localization and everything:
        add("Nuance", localization.nuance)
        add("How it is used", localization.usage_note)
        add("To remember", localization.encoding_cue)
        add("Note", localization.notes)
    examples: list[tuple[str, str | None]] = []
    if teaching and teaching.contexts and usage:
        for context in teaching.contexts[: 2 if everything else 1]:
            examples.append((context.plain, teaching.translation(context)))
    return TeachingPage(tuple(sections), tuple(examples))
