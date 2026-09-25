"""What a PDF or CSV export shows for each word: the columns, and their content.

Besides the word itself, an export can show the dictionary's fields and the
word's teaching content — its meaning and nuance in the language words are
explained in, its pattern, collocations, examples and their translations.
Nothing about scheduling is ever a column: cards, intervals and reviews are
learning data, exported on their own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from ..models.content import WordTeaching


class ExportColumn(StrEnum):
    PART_OF_SPEECH = "part_of_speech"
    CEFR = "cefr"
    #: The dictionary's definition, with its example and note.
    DEFINITION = "definition"
    #: The core meaning, in the language words are explained in.
    MEANING = "meaning"
    #: The nuance and the usage note, in that language.
    NUANCE = "nuance"
    PATTERN = "pattern"
    COLLOCATIONS = "collocations"
    #: The example contexts, in the target language.
    EXAMPLES = "examples"
    #: The examples' translations into the language words are explained in.
    TRANSLATIONS = "translations"

    @property
    def label(self) -> str:
        return {
            "part_of_speech": "Part of speech",
            "cefr": "CEFR level",
            "definition": "Definition",
            "meaning": "Meaning",
            "nuance": "Nuance and usage",
            "pattern": "Pattern",
            "collocations": "Collocations",
            "examples": "Examples",
            "translations": "Translations",
        }[self.value]

    @property
    def is_teaching(self) -> bool:
        """Read from the word's teaching content rather than the dictionary."""
        return self not in DICTIONARY_COLUMNS

    @property
    def needs_language(self) -> bool:
        """Exists only in a learner language: nothing to show without one."""
        return self in (ExportColumn.MEANING, ExportColumn.NUANCE, ExportColumn.TRANSLATIONS)


DICTIONARY_COLUMNS = (ExportColumn.PART_OF_SPEECH, ExportColumn.CEFR, ExportColumn.DEFINITION)
#: Unless the user chooses otherwise: the study sheet LexiTrack has always printed.
DEFAULT_COLUMNS = DICTIONARY_COLUMNS


@dataclass(frozen=True, slots=True)
class WordSheet:
    """The columns of one export, and the teaching content they are read from."""

    columns: tuple[ExportColumn, ...] = DEFAULT_COLUMNS
    #: The language meanings and translations are in; None when none is chosen.
    learner_language: str | None = None
    #: Teaching content by word id; empty when no teaching column is chosen.
    teaching: Mapping[int, WordTeaching] = field(default_factory=dict)

    def has(self, column: ExportColumn) -> bool:
        return column in self.columns

    @property
    def teaches(self) -> bool:
        return any(column.is_teaching for column in self.columns)

    def teaching_for(self, word_id: int) -> WordTeaching:
        return self.teaching.get(word_id) or WordTeaching(content=None)
