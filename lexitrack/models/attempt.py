"""Every question answered: what was asked, whether it was right, how it went.

``review_logs`` is the memory record FSRS reads: one rating per word per day.
``learning_attempts`` is every question answered — the day's review, a
question asked again after a wrong answer, practice right after a new word
was shown. An attempt that produced the day's rating links to it.

Correctness and effort are kept apart. **Correct** is whether the option
chosen was the right one; **effort** is the learner's own account of a correct
answer — Again, Hard, Good or Easy — which is also the rating FSRS hears.
A wrong answer has no effort: it is rated Again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .srs import Rating


class Phase(StrEnum):
    INTRODUCTION = "introduction"
    REVIEW = "review"
    #: A question asked again later in the session, after a wrong answer (or
    #: Again) in a review.
    RELEARN = "relearn"
    #: Kept for answers from earlier versions.
    REPAIR = "repair"


class Role(StrEnum):
    #: The day's question for a word: the one FSRS hears about.
    PRIMARY = "primary"
    #: Kept for answers from earlier versions (an easier check after a miss).
    PROBE = "probe"
    #: Practice: after a new word is shown, or after a wrong answer. Recorded,
    #: never rated.
    RETRIEVAL = "retrieval"


class Task(StrEnum):
    """What a question asks. Only the first two are asked now."""

    #: The definition shown, the word chosen among four.
    DEFINITION_TO_WORD = "definition_to_word"
    #: A context shown, the word picked out in it, the definition chosen
    #: among four.
    CONTEXT_TO_DEFINITION = "context_to_definition"

    # Earlier versions' tasks, kept so their answers can still be read.
    WORD_TO_MEANING = "word_to_meaning"
    CHOOSE_WORD = "choose_word"
    MEANING_TO_WORD = "meaning_to_word"
    SITUATION_TO_WORD = "situation_to_word"
    CONTEXT_CLOZE = "context_cloze"
    COLLOCATION = "collocation"
    PRODUCTION = "production"

    @property
    def label(self) -> str:
        """What was asked, as a word's history and the answers table say it."""
        return {
            "definition_to_word": "Definition → Word",
            "context_to_definition": "Context → Definition",
            "word_to_meaning": "Recall the meaning (earlier version)",
            "choose_word": "Choose the word (earlier version)",
            "meaning_to_word": "Type the word (earlier version)",
            "situation_to_word": "Word for a situation (earlier version)",
            "context_cloze": "Complete a sentence (earlier version)",
            "collocation": "Complete a phrase (earlier version)",
            "production": "Write a sentence (earlier version)",
        }[self.value]

    @property
    def is_current(self) -> bool:
        return self in CURRENT_TASKS


#: The two questions LexiTrack asks.
CURRENT_TASKS = (Task.DEFINITION_TO_WORD, Task.CONTEXT_TO_DEFINITION)


class Effort(StrEnum):
    """How a correct answer went, in the learner's words: the four ratings."""

    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"

    @property
    def rating(self) -> Rating:
        return {
            "again": Rating.AGAIN,
            "hard": Rating.HARD,
            "good": Rating.GOOD,
            "easy": Rating.EASY,
        }[self.value]

    @classmethod
    def of(cls, rating: Rating) -> Effort:
        return {
            Rating.AGAIN: cls.AGAIN,
            Rating.HARD: cls.HARD,
            Rating.GOOD: cls.GOOD,
            Rating.EASY: cls.EASY,
        }[Rating(int(rating))]

    @property
    def label(self) -> str:
        return self.value.capitalize()


ROUTE_V1 = "v1"
ROUTE_V2 = "v2"
#: Definition → Word and Context → Definition, four choices each.
ROUTE_V3 = "v3"


@dataclass(frozen=True, slots=True)
class LearningAttempt:
    word_id: int
    at: datetime
    on_day: str
    phase: Phase
    role: Role
    task: Task
    correct: bool
    session_id: str | None = None
    #: The context shown, for Context → Definition.
    context_id: int | None = None
    #: For a correct answer, how it went; None for a wrong one, and for
    #: practice, which is never rated.
    effort: Effort | None = None
    response_ms: int | None = None
    review_log_id: int | None = None
    route_version: str = ROUTE_V3
    undone_at: datetime | None = None
    id: int | None = None
