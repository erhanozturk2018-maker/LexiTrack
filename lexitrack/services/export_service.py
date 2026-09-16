"""Coordinating exports.

The service decides *what* is exported; the exporters decide *how* it is
formatted. Today that means the unknown words, which is the whole point of
reviewing: the list you still have to learn.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..exporters.csv_exporter import export_words_csv
from ..exporters.pdf_exporter import export_words_pdf
from ..models.user_word_state import ReviewStatus
from ..repositories.word_repository import WordRepository

log = logging.getLogger(__name__)


class ExportService:
    """Produces vocabulary exports from the current database contents."""

    def __init__(self, words: WordRepository) -> None:
        self._words = words

    def export_unknown_pdf(self, path: Path) -> Path:
        """Write every word marked unknown to a printable PDF."""
        words = self._words.list_by_status(ReviewStatus.UNKNOWN)
        return export_words_pdf(
            words,
            path,
            title="Unknown Words",
            subtitle=_subtitle(len(words)),
        )

    def export_unknown_csv(self, path: Path) -> Path:
        """Write every word marked unknown to a CSV file."""
        words = self._words.list_by_status(ReviewStatus.UNKNOWN)
        return export_words_csv(words, path)


def _subtitle(count: int) -> str:
    from datetime import datetime

    noun = "word" if count == 1 else "words"
    stamp = datetime.now().strftime("%d %B %Y")
    return f"{count} {noun} you marked as unknown · exported {stamp}"
