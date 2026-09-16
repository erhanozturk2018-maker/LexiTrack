"""A learning list: what the user has chosen to study.

A list is not a source. "Oxford 3000" the *list* is a collection the user
reviews; "Oxford 3000" the *source* records that some words were extracted
from a particular PDF. The two often share a name after an import, and are
still different things: the user can rename the list, add words from
elsewhere, or build a list that has no single source at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .language import UNDETERMINED, language_name
from .user_word_state import Progress


class ListKind(StrEnum):
    #: Created by the user in the application.
    CUSTOM = "custom"
    #: Created by importing a file.
    IMPORTED = "imported"


@dataclass(frozen=True, slots=True)
class VocabularyList:
    id: int
    name: str
    language: str = UNDETERMINED
    description: str | None = None
    kind: ListKind = ListKind.CUSTOM
    created_at: datetime | None = None
    updated_at: datetime | None = None
    progress: Progress = Progress()

    @property
    def language_name(self) -> str:
        return language_name(self.language)
