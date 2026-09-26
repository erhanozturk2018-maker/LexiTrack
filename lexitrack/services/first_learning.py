"""First learning: how a new word is met before it is ever reviewed.

A new word is shown whole — the word, its length, CEFR level, part of
speech, definition and contexts — and then asked: Definition → Word, and,
when it has contexts, Context → Definition a little later. None of this is
rated: the answers are practice, recorded as attempts of the introduction
phase. The word's first rating is its first review, on a later day, because
an answer given a minute after being shown measures working memory, not
learning. A miss is asked again a few cards later (services/review_flow.py).

Words are shown in groups of four — four words shown, then the four asked —
so each question comes after a short gap filled by other words, which is
what makes it a retrieval rather than a repetition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..repositories.word_repository import StoredWord

#: Words shown together before they are asked.
GROUP_SIZE = 4

#: Rough seconds a review and a new word take, for the day's estimate: a
#: question and its rating; a word read and asked once or twice. An
#: estimate, shown as "about".
SECONDS_PER_REVIEW = 12
SECONDS_PER_NEW = 40


@dataclass(frozen=True, slots=True)
class Estimate:
    reviews: int
    new: int
    minutes: int

    @property
    def words(self) -> int:
        return self.reviews + self.new


def estimate(reviews: int, new: int) -> Estimate:
    """About how long the day's session takes, in whole minutes."""
    seconds = reviews * SECONDS_PER_REVIEW + new * SECONDS_PER_NEW
    minutes = math.ceil(seconds / 60) if seconds else 0
    return Estimate(reviews=reviews, new=new, minutes=minutes)


def groups(words: list[StoredWord], size: int = GROUP_SIZE) -> list[list[StoredWord]]:
    return [words[start : start + size] for start in range(0, len(words), size)]
