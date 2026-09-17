"""Coordinating exports.

The service decides *what* is exported; the exporters decide *how* it is
formatted. What is exported is a *scope*:

* **A list** — every word in it, with the list's name, language and
  description (the natural choice for JSON, which can be imported again).
* **Unknown words** — in one list, or across the whole vocabulary.
* **A selection** — whatever the user picked in a table.

Every format accepts every scope. The UI picks a sensible default from where
the user is, rather than asking about all of this every time.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from ..core.errors import ExportError
from ..exporters.csv_exporter import export_words_csv
from ..exporters.json_exporter import export_words_json
from ..exporters.pdf_exporter import export_words_pdf
from ..models.language import UNDETERMINED
from ..models.user_word_state import ReviewStatus
from ..models.vocabulary_list import VocabularyList
from ..repositories.list_repository import ListRepository
from ..repositories.word_repository import StoredWord, WordRepository

log = logging.getLogger(__name__)


class ExportFormat(StrEnum):
    PDF = "pdf"
    CSV = "csv"
    JSON = "json"

    @property
    def label(self) -> str:
        return {"pdf": "PDF document", "csv": "CSV spreadsheet", "json": "JSON word list"}[
            self.value
        ]

    @property
    def file_filter(self) -> str:
        return {
            "pdf": "PDF files (*.pdf)",
            "csv": "CSV files (*.csv)",
            "json": "JSON files (*.json)",
        }[self.value]


@dataclass(frozen=True, slots=True)
class ExportContent:
    """A resolved scope: the words, and what to call them."""

    words: list[StoredWord]
    title: str
    subtitle: str
    name: str | None = None
    language: str | None = None
    description: str | None = None
    #: PDF only: a heading before each CEFR level. The words must already be
    #: in level order; the heading appears wherever the level changes.
    group_by_level: bool = False

    @property
    def suggested_filename(self) -> str:
        stem = "".join(c if c.isalnum() else "_" for c in self.title.casefold()).strip("_")
        while "__" in stem:
            stem = stem.replace("__", "_")
        return stem or "vocabulary"


class ExportService:
    """Produces vocabulary exports from the current database contents."""

    def __init__(self, words: WordRepository, lists: ListRepository) -> None:
        self._words = words
        self._lists = lists

    # -- scopes ------------------------------------------------------------

    def list_content(self, list_id: int) -> ExportContent:
        target = self._lists.require(list_id)
        words = self._words.list_in_list(list_id)
        return ExportContent(
            words=words,
            title=target.name,
            subtitle=_subtitle(len(words), "in this list"),
            name=target.name,
            language=target.language,
            description=target.description,
        )

    def unknown_content(self, list_id: int | None = None) -> ExportContent:
        words = self._words.list_by_status(ReviewStatus.UNKNOWN, list_id)
        if list_id is None:
            return ExportContent(
                words=words,
                title="Unknown Words",
                subtitle=_subtitle(len(words), "you marked as unknown"),
                name="Unknown Words",
                language=_common_language(words),
            )
        target = self._lists.require(list_id)
        title = f"{target.name} — Unknown Words"
        return ExportContent(
            words=words,
            title=title,
            subtitle=_subtitle(len(words), "you marked as unknown"),
            name=f"{target.name} (Unknown)",
            language=target.language,
            description=target.description,
        )

    def selection_content(
        self, word_ids: Sequence[int], context: VocabularyList | None = None
    ) -> ExportContent:
        words = self._words.get_many(word_ids)
        title = f"{context.name} — Selection" if context else "Selected Words"
        return ExportContent(
            words=words,
            title=title,
            subtitle=_subtitle(len(words), "selected"),
            name=title,
            language=context.language if context else _common_language(words),
        )

    # -- writing -----------------------------------------------------------

    def write(self, content: ExportContent, path: Path, file_format: ExportFormat) -> Path:
        if file_format is ExportFormat.PDF:
            return export_words_pdf(
                content.words,
                path,
                title=content.title,
                subtitle=content.subtitle,
                group_by_level=content.group_by_level,
            )
        if file_format is ExportFormat.CSV:
            return export_words_csv(content.words, path)
        if file_format is ExportFormat.JSON:
            return export_words_json(
                content.words,
                path,
                name=content.name,
                language=content.language,
                description=content.description,
            )
        raise ExportError(f"{file_format} is not a supported export format.")  # pragma: no cover

    # -- kept for existing callers ----------------------------------------

    def export_unknown_pdf(self, path: Path, list_id: int | None = None) -> Path:
        """Write every word marked unknown to a printable PDF."""
        return self.write(self.unknown_content(list_id), path, ExportFormat.PDF)

    def export_unknown_csv(self, path: Path, list_id: int | None = None) -> Path:
        """Write every word marked unknown to a CSV file."""
        return self.write(self.unknown_content(list_id), path, ExportFormat.CSV)


def _subtitle(count: int, description: str) -> str:
    noun = "word" if count == 1 else "words"
    stamp = datetime.now().strftime("%d %B %Y")
    return f"{count:,} {noun} {description} · exported {stamp}"


def _common_language(words: Sequence[StoredWord]) -> str:
    """The single language all ``words`` share, else undetermined."""
    languages = {word.language for word in words}
    return languages.pop() if len(languages) == 1 else UNDETERMINED
