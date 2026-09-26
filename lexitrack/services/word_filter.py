"""Choosing words by what they are: for exports, and anywhere a set is needed.

A filter combines lists (one, several, or all of them), a status, a CEFR
level, a learning state and whether a word has contexts; every part is
optional and they narrow together.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..models.srs import CardState
from ..models.user_word_state import ReviewStatus
from ..repositories import CardRepository, ContextRepository
from ..repositories.word_repository import StoredWord
from .learning_service import LearningService
from .vocabulary_service import VocabularyService


class LearningState(StrEnum):
    ANY = "any"
    #: Never introduced.
    NOT_STARTED = "not_started"
    #: Introduced, being reviewed, not Known.
    IN_PROGRESS = "in_progress"
    #: Flagged as hard.
    HARD = "hard"
    #: In long-term memory and not yet marked Known: offered as Known.
    LONG_TERM = "long_term"

    @property
    def label(self) -> str:
        return {
            "any": "Any",
            "not_started": "Not started",
            "in_progress": "In progress",
            "hard": "Hard for you",
            "long_term": "In long-term memory",
        }[self.value]


@dataclass(frozen=True, slots=True)
class WordFilter:
    #: The lists to take words from; empty means every list.
    lists: tuple[int, ...] = ()
    status: ReviewStatus | None = None
    cefr: str | None = None
    state: LearningState = LearningState.ANY
    #: True: only words with contexts; False: only words without; None: any.
    contexts: bool | None = None


def filter_words(
    service: VocabularyService, engine: LearningService, choice: WordFilter
) -> list[StoredWord]:
    """The words that match every part of ``choice``, in list order, each once."""
    seen: dict[int, StoredWord] = {}
    for list_id in choice.lists or [lst.id for lst in service.lists()]:
        for word in service.list_words(list_id):
            seen.setdefault(word.id, word)
    words = list(seen.values())
    if choice.status is not None:
        words = [w for w in words if w.status is choice.status]
    if choice.cefr:
        words = [w for w in words if (w.cefr_level or "").upper() == choice.cefr.upper()]
    if choice.contexts is not None:
        counts = ContextRepository(service.database).counts(w.id for w in words)
        words = [w for w in words if (w.id in counts) is choice.contexts]
    if choice.state is LearningState.ANY:
        return words
    if choice.state is LearningState.LONG_TERM:
        ready = {w.id for w in engine.known_suggestions()}
        return [w for w in words if w.id in ready]
    cards = CardRepository(service.database).get_many(w.id for w in words)
    if choice.state is LearningState.NOT_STARTED:
        return [w for w in words if w.id not in cards]
    if choice.state is LearningState.HARD:
        return [w for w in words if w.id in cards and cards[w.id].needs_relearning]
    return [
        w
        for w in words
        if w.id in cards
        and cards[w.id].state is not CardState.ARCHIVED
        and w.status is not ReviewStatus.KNOWN
    ]
