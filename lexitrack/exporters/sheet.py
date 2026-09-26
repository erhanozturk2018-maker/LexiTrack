"""What a PDF or CSV export shows for each word: the columns, and their content.

Besides the word itself, an export can show its part of speech, CEFR level,
length, definition and contexts. Nothing about scheduling is ever a column:
cards, intervals and reviews are learning data, exported on their own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ExportColumn(StrEnum):
    PART_OF_SPEECH = "part_of_speech"
    CEFR = "cefr"
    #: How many letters the word has.
    LENGTH = "length"
    DEFINITION = "definition"
    #: The word's contexts, one sentence each.
    CONTEXTS = "contexts"

    @property
    def label(self) -> str:
        return {
            "part_of_speech": "Part of speech",
            "cefr": "CEFR level",
            "length": "Length",
            "definition": "Definition",
            "contexts": "Contexts",
        }[self.value]


#: Every column, in the order they are shown.
ALL_COLUMNS = tuple(ExportColumn)
#: Unless the user chooses otherwise: the study sheet LexiTrack has always printed.
DEFAULT_COLUMNS = (ExportColumn.PART_OF_SPEECH, ExportColumn.CEFR, ExportColumn.DEFINITION)


@dataclass(frozen=True, slots=True)
class WordSheet:
    """The columns of one export, and the contexts they are read from."""

    columns: tuple[ExportColumn, ...] = DEFAULT_COLUMNS
    #: Each word's contexts by word id; empty when the column is not chosen.
    contexts: Mapping[int, tuple[str, ...]] = field(default_factory=dict)

    def has(self, column: ExportColumn) -> bool:
        return column in self.columns

    def contexts_for(self, word_id: int) -> tuple[str, ...]:
        return tuple(self.contexts.get(word_id, ()))
