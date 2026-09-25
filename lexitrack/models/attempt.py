"""The skill record: attempts to retrieve or use a word.

Memory and skill are different systems. ``review_logs`` is the memory record
(one FSRS rating a day); ``learning_attempts`` is the skill record (every
attempt, graded or not). SkillTracker reads only the second; FSRS only the
first. An attempt that produced the day's rating links to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum


class Phase(StrEnum):
    INTRODUCTION = "introduction"
    REVIEW = "review"
    RELEARN = "relearn"
    REPAIR = "repair"


class Role(StrEnum):
    #: The first, measuring attempt of a review: the one FSRS hears about.
    PRIMARY = "primary"
    #: An easier check after a failed primary, to find what is still there.
    PROBE = "probe"
    #: Practice after teaching: immediate retrieval, relearning, repair.
    RETRIEVAL = "retrieval"


class Level(IntEnum):
    """Retrieval difficulty, from recognising to producing."""

    WORD_TO_MEANING = 1
    MEANING_TO_WORD = 2
    CONTEXT_TO_WORD = 3
    COLLOCATION = 4
    PRODUCTION = 5

    @property
    def label(self) -> str:
        return {
            1: "Word to meaning",
            2: "Meaning to word",
            3: "Context to word",
            4: "Collocation",
            5: "Sentence",
        }[int(self)]


class Task(StrEnum):
    """A concrete task. Several tasks can share a level."""

    WORD_TO_MEANING = "word_to_meaning"
    #: The word chosen among four for a meaning: recognition, level 1, and
    #: the probe after a failed recall, because the word is not shown first.
    CHOOSE_WORD = "choose_word"
    MEANING_TO_WORD = "meaning_to_word"
    SITUATION_TO_WORD = "situation_to_word"
    CONTEXT_CLOZE = "context_cloze"
    COLLOCATION = "collocation"
    PRODUCTION = "production"

    @property
    def level(self) -> Level:
        return _TASK_LEVEL[self]

    @property
    def label(self) -> str:
        """What was asked, as a word's history and the answers table say it."""
        return {
            "word_to_meaning": "Recall the meaning",
            "choose_word": "Choose the word among four",
            "meaning_to_word": "Type the word from its meaning",
            "situation_to_word": "Type the word for a situation",
            "context_cloze": "Complete a sentence",
            "collocation": "Complete a phrase",
            "production": "Write a sentence with it",
        }[self.value]


_TASK_LEVEL = {
    Task.WORD_TO_MEANING: Level.WORD_TO_MEANING,
    Task.CHOOSE_WORD: Level.WORD_TO_MEANING,
    Task.MEANING_TO_WORD: Level.MEANING_TO_WORD,
    Task.SITUATION_TO_WORD: Level.CONTEXT_TO_WORD,
    Task.CONTEXT_CLOZE: Level.CONTEXT_TO_WORD,
    Task.COLLOCATION: Level.COLLOCATION,
    Task.PRODUCTION: Level.PRODUCTION,
}


class Effort(StrEnum):
    INSTANT = "instant"
    NORMAL = "normal"
    EFFORTFUL = "effortful"


class MemoryResult(StrEnum):
    """What a review showed about the word-meaning memory.

    Decided by the strongest retrieval reached *before the answer was shown*:
    seeing the answer and then recognising it is not evidence.
    """

    RECALLED = "RECALLED"
    RECALLED_EFFORT = "RECALLED_EFFORT"
    #: The meaning could be retrieved from the word, but the requested,
    #: harder retrieval failed. The memory is there; the access is weak.
    RECOGNIZED = "RECOGNIZED"
    FORGOTTEN = "FORGOTTEN"

    @property
    def label(self) -> str:
        return {
            "RECALLED": "Recalled",
            "RECALLED_EFFORT": "Recalled with effort",
            "RECOGNIZED": "Recognised only",
            "FORGOTTEN": "Forgotten",
        }[self.value]


class Depth(StrEnum):
    SHORT = "short"
    LIGHT = "light"
    DEEP = "deep"


ROUTE_V1 = "v1"
ROUTE_V2 = "v2"


@dataclass(frozen=True, slots=True)
class LearningAttempt:
    word_id: int
    at: datetime
    on_day: str
    phase: Phase
    role: Role
    task: Task
    success: bool
    session_id: str | None = None
    context_id: int | None = None
    novel_context: bool = False
    effort: Effort | None = None
    response_ms: int | None = None
    review_log_id: int | None = None
    route_version: str = ROUTE_V2
    depth: Depth | None = None
    undone_at: datetime | None = None
    id: int | None = None

    @property
    def level(self) -> Level:
        return self.task.level
