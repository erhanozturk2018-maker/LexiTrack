"""The application service the UI talks to.

Everything the review screen needs goes through here: which word comes next,
what the user answered, how far along they are, and what to export. The UI
never sees a repository, a SQL statement or a parser.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..database.connection import Database
from ..models.source import Source
from ..models.user_word_state import Progress, ReviewStatus
from ..parsers.base import ParserInfo
from ..parsers.registry import AUTO, ParserRegistry
from ..repositories.source_repository import SourceRepository
from ..repositories.state_repository import StateRepository
from ..repositories.word_repository import ImportResult, StoredWord, WordRepository
from .export_service import ExportService
from .import_service import ImportRequest, ImportService, ProgressReporter

log = logging.getLogger(__name__)


class VocabularyService:
    """Facade over import, review, progress and export."""

    def __init__(self, database: Database | None = None) -> None:
        self._db = database or Database()
        self._words = WordRepository(self._db)
        self._sources = SourceRepository(self._db)
        self._state = StateRepository(self._db)
        self._registry = ParserRegistry()
        self._import = ImportService(self._registry, self._words, self._sources)
        self._export = ExportService(self._words)

    # -- import ------------------------------------------------------------

    def import_document(
        self,
        path: Path | str,
        parser_key: str = AUTO,
        progress: ProgressReporter | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ImportResult:
        """Import a PDF and return what changed."""
        request = ImportRequest(path=Path(path), parser_key=parser_key)
        return self._import.import_document(request, progress, should_cancel)

    def available_parsers(self) -> list[ParserInfo]:
        """Return the parsers the user can choose from in the import dialog."""
        return self._registry.describe()

    # -- review ------------------------------------------------------------

    def get_next_word(self) -> StoredWord | None:
        """Return the next word awaiting review, or ``None`` when none remain.

        Derived from the database on every call, which is what makes the review
        position survive a restart without storing a cursor.
        """
        return self._words.next_unreviewed()

    def mark_known(self, word_id: int) -> None:
        """Record that the user knows this word."""
        self._state.set_status(word_id, ReviewStatus.KNOWN)

    def mark_unknown(self, word_id: int) -> None:
        """Record that the user does not know this word."""
        self._state.set_status(word_id, ReviewStatus.UNKNOWN)

    def mark(self, word_id: int, known: bool) -> None:
        """Record an answer; convenience wrapper over the two methods above."""
        if known:
            self.mark_known(word_id)
        else:
            self.mark_unknown(word_id)

    def undo(self, word_id: int) -> None:
        """Return ``word_id`` to the not-reviewed state.

        Backs the review screen's Undo action, for the mis-click that would
        otherwise be permanent.
        """
        self._state.set_status(word_id, ReviewStatus.NOT_REVIEWED)

    def get_word(self, word_id: int) -> StoredWord | None:
        return self._words.get(word_id)

    # -- progress ----------------------------------------------------------

    def get_progress(self) -> Progress:
        """Return the counters shown on the review screen."""
        return self._state.progress()

    def list_sources(self) -> list[Source]:
        return self._sources.list_all()

    def list_unknown_words(self) -> list[StoredWord]:
        return self._words.list_by_status(ReviewStatus.UNKNOWN)

    def list_known_words(self) -> list[StoredWord]:
        return self._words.list_by_status(ReviewStatus.KNOWN)

    def is_empty(self) -> bool:
        """True when no document has been imported yet."""
        return self._words.count() == 0

    def reset_progress(self) -> int:
        """Mark every word as not reviewed, keeping the vocabulary itself."""
        count = self._state.reset_all()
        log.info("Review progress reset for %d words", count)
        return count

    # -- export ------------------------------------------------------------

    def export_unknown_pdf(self, path: Path | str) -> Path:
        """Write the unknown words to a PDF and return its path."""
        return self._export.export_unknown_pdf(Path(path))

    def export_unknown_csv(self, path: Path | str) -> Path:
        """Write the unknown words to a CSV file and return its path."""
        return self._export.export_unknown_csv(Path(path))

    def unknown_count(self) -> int:
        return self._state.count_with_status(ReviewStatus.UNKNOWN)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> VocabularyService:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
