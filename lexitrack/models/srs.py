"""The learning engine's own vocabulary: plans, cards, ratings, review logs.

Three ideas that are easy to confuse and are kept apart here, as they are in
the database:

* :class:`ReviewStatus` (in ``user_word_state``) is the user's broad judgement
  of a word — Known, Unknown, Not reviewed. It is not in this module.
* :class:`CardState` is where a word sits in the *schedule*. It only exists
  for words that entered an active study plan.
* :class:`Rating` is a single answer to a single card.

A card is created by an explicit introduction, not by a rating: the user
studies the day's new words outside LexiTrack, confirms it, and the card is
scheduled for the next day. No rating is invented for that step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum


class CardState(StrEnum):
    """Where a word is in the schedule."""

    #: Introduced today or later, never rated. Due at the next day start.
    INTRODUCED = "introduced"
    #: Being learned: rated at least once, intervals still short.
    LEARNING = "learning"
    #: In long-term review.
    REVIEW = "review"
    #: Failed and being rebuilt.
    RELEARNING = "relearning"
    #: The user declared the word Known by hand; kept, not deleted.
    ARCHIVED = "archived"

    @property
    def label(self) -> str:
        return {
            "introduced": "Introduced",
            "learning": "Learning",
            "review": "Review",
            "relearning": "Relearning",
            "archived": "Archived",
        }[self.value]


class Rating(IntEnum):
    """One answer. The numbers match FSRS so no translation table is needed."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @property
    def label(self) -> str:
        return {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}[self.value]

    @property
    def is_lapse(self) -> bool:
        return self is Rating.AGAIN


class Channel(StrEnum):
    """Where an answer came from."""

    DESKTOP = "desktop"
    TELEGRAM = "telegram"


@dataclass(frozen=True, slots=True)
class StudyPlan:
    """What the user is actively learning: a name and the lists it draws from."""

    id: int
    name: str
    language: str = "und"
    description: str | None = None
    is_active: bool = False
    #: Ids of the selected lists, in the order they were added.
    list_ids: tuple[int, ...] = ()
    #: Names of the selected lists, for display.
    list_names: tuple[str, ...] = ()

    @property
    def list_label(self) -> str:
        return ", ".join(self.list_names)


@dataclass(frozen=True, slots=True)
class SrsCard:
    """The schedule of one word.

    One card per word, never one per plan: the same word selected by two lists
    of the same plan — or by two plans — must still be reviewed once.
    ``origin_plan_id`` records where it was introduced and is not a scope key.
    """

    word_id: int
    state: CardState = CardState.INTRODUCED
    introduced_at: datetime | None = None
    #: The local date of the introduction, e.g. ``"2026-09-18"``. The
    #: same-day exclusion rule compares against this, not against a timestamp.
    introduced_on: str = ""
    due_at: datetime | None = None
    first_review_at: datetime | None = None
    last_review_at: datetime | None = None
    review_count: int = 0
    lapse_count: int = 0
    consecutive_lapses: int = 0
    needs_relearning: bool = False
    stability: float | None = None
    difficulty: float | None = None
    #: The scheduler library's own state, stored as JSON text and never
    #: interpreted by the rest of the application.
    fsrs_state: str | None = None
    scheduler_version: str | None = None
    origin_plan_id: int | None = None

    @property
    def is_introduced_only(self) -> bool:
        """True while the card has been introduced but never rated."""
        return self.state is CardState.INTRODUCED

    def is_due(self, now: datetime) -> bool:
        return self.due_at is not None and self.due_at <= now


@dataclass(frozen=True, slots=True)
class ReviewLogEntry:
    """One rating, with enough context to reconstruct what happened."""

    word_id: int
    reviewed_at: datetime
    reviewed_on: str
    rating: Rating
    channel: Channel = Channel.DESKTOP
    session_id: str | None = None
    state_before: CardState | None = None
    state_after: CardState | None = None
    due_before: datetime | None = None
    due_after: datetime | None = None
    elapsed_days: float | None = None
    scheduled_days: float | None = None
    stability_after: float | None = None
    difficulty_after: float | None = None
    scheduler_version: str | None = None
    params_hash: str | None = None
    #: Set when the answer was taken back. The row stays, for the record.
    undone_at: datetime | None = None
    id: int | None = None
