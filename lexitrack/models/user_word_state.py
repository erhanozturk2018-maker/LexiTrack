"""The user's own knowledge of a word, kept separate from source metadata.

A word's part of speech comes from the document; whether the user knows it does
not. Mixing the two would mean a second import could overwrite review progress,
so the two concepts live in separate tables and separate models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReviewStatus(StrEnum):
    """Review state of a single vocabulary identity."""

    NOT_REVIEWED = "not_reviewed"
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class UserWordState:
    word_id: int
    status: ReviewStatus = ReviewStatus.NOT_REVIEWED
    reviewed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Progress:
    """Aggregate review counters shown in the UI."""

    total: int = 0
    known: int = 0
    unknown: int = 0

    @property
    def reviewed(self) -> int:
        return self.known + self.unknown

    @property
    def remaining(self) -> int:
        return max(self.total - self.reviewed, 0)

    @property
    def percent_complete(self) -> float:
        if self.total == 0:
            return 0.0
        return self.reviewed / self.total * 100.0
