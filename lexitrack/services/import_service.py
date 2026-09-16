"""Turning a PDF on disk into rows in the database.

This is the whole import pipeline in one place::

    PDF -> Document -> ParserRegistry -> parser -> WordEntry[]
        -> deduplicate -> SourceRepository -> WordRepository

Callers pass a progress callback; the UI runs this on a worker thread so a
3000-word import never freezes the window.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from ..core.errors import NoWordsFoundError, ParserError
from ..models.source import Source
from ..normalization.deduplicator import deduplicate
from ..parsers.document import Document
from ..parsers.registry import AUTO, ParserRegistry
from ..repositories.source_repository import SourceRepository
from ..repositories.word_repository import ImportResult, WordRepository

log = logging.getLogger(__name__)

#: Called with ``(message, percent)``; ``percent`` is -1 when indeterminate.
ProgressReporter = Callable[[str, int], None]


@dataclass(frozen=True, slots=True)
class ImportRequest:
    """Everything needed to run one import."""

    path: Path
    parser_key: str = AUTO


class ImportService:
    """Runs the import pipeline."""

    def __init__(
        self,
        registry: ParserRegistry,
        words: WordRepository,
        sources: SourceRepository,
    ) -> None:
        self._registry = registry
        self._words = words
        self._sources = sources

    def import_document(
        self,
        request: ImportRequest,
        progress: ProgressReporter | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ImportResult:
        """Import ``request.path`` and return what changed.

        Args:
            request: The file to import and the parser to use.
            progress: Optional callback for UI progress reporting.
            should_cancel: Polled between stages; when it returns ``True`` the
                import stops before anything is written to the database.

        Raises:
            DocumentError: the PDF could not be opened or has no text.
            NoParserError: no parser recognised the document.
            NoWordsFoundError: parsing succeeded but produced no vocabulary.
            StorageError: the results could not be saved.
        """
        report = progress or (lambda _message, _percent: None)
        cancelled = should_cancel or (lambda: False)

        report(f"Opening {request.path.name}…", -1)
        with Document.open(request.path) as document:
            parser = self._registry.select(document, request.parser_key)
            report(f"Reading with the {parser.name} parser…", 0)

            if cancelled():
                return ImportResult(parser_type=parser.key)

            def on_page(current: int, total: int) -> None:
                percent = int(current / total * 80) if total else -1
                report(f"Reading page {current} of {total}…", percent)

            try:
                entries = parser.parse(document, progress=on_page)
            except Exception as exc:
                log.exception("Parser %s failed on %s", parser.key, request.path)
                raise ParserError(
                    f"{request.path.name} could not be processed by the "
                    f"{parser.name} parser."
                ) from exc

            if cancelled():
                return ImportResult(parser_type=parser.key)

            report("Removing duplicates…", 85)
            unique_entries = deduplicate(entries)
            if not unique_entries:
                raise NoWordsFoundError(
                    f"No words could be extracted from {request.path.name}."
                )

            source = Source(
                key=parser.source_key(document),
                name=parser.source_name(document),
                parser_type=parser.key,
                file_path=str(request.path),
            )

        report("Saving to the vocabulary database…", 90)
        stored_source = self._sources.upsert(source)
        assert stored_source.id is not None
        result = self._words.add_entries(unique_entries, stored_source.id)
        report("Import complete.", 100)

        result = replace(
            result,
            source_name=stored_source.name,
            parser_type=source.parser_type,
        )
        log.info(
            "Imported %s: %d parsed, %d unique, %d new, %d already known",
            request.path.name,
            len(entries),
            len(unique_entries),
            result.new_words,
            result.existing_words,
        )
        return result

    def preview(self, path: Path, parser_key: str = AUTO) -> str:
        """Return the name of the parser that would handle ``path``.

        Used by the import dialog to tell the user what will happen before
        they commit to it.
        """
        with Document.open(path) as document:
            return self._registry.select(document, parser_key).name
