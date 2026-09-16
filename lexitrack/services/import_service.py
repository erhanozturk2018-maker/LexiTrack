"""Turning a file on disk into words in one or more lists.

Import happens in two steps, so the user can see what will happen before
anything is written::

    prepare():  file -> open_document -> ParserRegistry -> parser
                     -> WordEntry[] -> deduplicate            (no writes)
    commit():   preview + chosen lists -> one transaction:
                     create list? -> upsert source -> upsert words
                     -> add words to every target list

``prepare`` is the slow part and runs on a worker thread in the UI; ``commit``
is a single transaction, so a failure part-way leaves nothing behind.

Language resolution, in order of authority:

1. What the document states (the Oxford parser knows its lists are English; a
   JSON file may say ``"language": "de"``).
2. What the user chose in the preview.
3. The language of the target list(s).
4. Undetermined.

A document that states a language cannot be imported into a list with a
different one — that is how a German file is kept out of "Oxford 3000".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..core.errors import LanguageMismatchError, LexiTrackError, NoWordsFoundError, ParserError
from ..database.connection import Database
from ..models.language import UNDETERMINED, is_determined, language_name
from ..models.source import Source
from ..models.vocabulary_list import ListKind, VocabularyList
from ..models.word_entry import WordEntry
from ..normalization.deduplicator import deduplicate
from ..parsers.base import ListMetadata
from ..parsers.json_document import open_document
from ..parsers.registry import AUTO, ParserRegistry
from ..repositories.list_repository import ListRepository
from ..repositories.source_repository import SourceRepository
from ..repositories.word_repository import IdentityCheck, ImportResult, WordRepository

log = logging.getLogger(__name__)

#: Called with ``(message, percent)``; ``percent`` is -1 when indeterminate.
ProgressReporter = Callable[[str, int], None]


@dataclass(frozen=True, slots=True)
class NewList:
    """A list to create as part of an import."""

    name: str
    language: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ImportTarget:
    """Where imported words go: existing lists, a new list, or both."""

    list_ids: tuple[int, ...] = ()
    new_list: NewList | None = None

    @property
    def is_empty(self) -> bool:
        return not self.list_ids and self.new_list is None


@dataclass(frozen=True, slots=True)
class ImportRequest:
    """Everything needed to run one import in a single call."""

    path: Path
    parser_key: str = AUTO
    target: ImportTarget | None = None
    language: str | None = None


@dataclass(slots=True)
class ImportPreview:
    """A parsed file, not yet written anywhere."""

    path: Path
    parser_key: str
    parser_name: str
    source: Source
    suggested: ListMetadata
    entries: list[WordEntry]
    parsed_count: int
    warnings: list[str] = field(default_factory=list)

    @property
    def format_label(self) -> str:
        kind = "JSON" if self.path.suffix.casefold() == ".json" else "PDF"
        return f"{kind} · {self.parser_name}"

    @property
    def unique_count(self) -> int:
        return len(self.entries)

    @property
    def stated_language(self) -> str | None:
        """The language the document itself states, if any."""
        return self.suggested.language if is_determined(self.suggested.language) else None


class ImportService:
    """Runs the import pipeline."""

    def __init__(
        self,
        database: Database,
        registry: ParserRegistry,
        words: WordRepository,
        sources: SourceRepository,
        lists: ListRepository,
    ) -> None:
        self._db = database
        self._registry = registry
        self._words = words
        self._sources = sources
        self._lists = lists

    # -- step 1: read ------------------------------------------------------

    def prepare(
        self,
        path: Path,
        parser_key: str = AUTO,
        progress: ProgressReporter | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ImportPreview | None:
        """Parse ``path`` without writing anything.

        Returns ``None`` if cancelled.

        Raises:
            DocumentError: the file could not be opened or has no text.
            InvalidFileError: a JSON file has the wrong shape.
            NoParserError: no parser recognised the document.
            NoWordsFoundError: parsing produced no vocabulary.
        """
        report = progress or (lambda _message, _percent: None)
        cancelled = should_cancel or (lambda: False)
        path = Path(path)

        report(f"Opening {path.name}…", -1)
        with open_document(path) as document:
            parser = self._registry.select(document, parser_key)
            report(f"Reading with the {parser.name} parser…", 0)
            if cancelled():
                return None

            def on_page(current: int, total: int) -> None:
                percent = int(current / total * 90) if total else -1
                report(f"Reading page {current} of {total}…", percent)

            try:
                suggested = parser.list_metadata(document)
                entries = parser.parse(document, progress=on_page)
            except LexiTrackError:
                raise
            except Exception as exc:
                log.exception("Parser %s failed on %s", parser.key, path)
                raise ParserError(
                    f"{path.name} could not be processed by the {parser.name} parser."
                ) from exc

            if cancelled():
                return None

            report("Removing duplicates…", 95)
            unique_entries = deduplicate(entries)
            if not unique_entries:
                raise NoWordsFoundError(f"No words could be extracted from {path.name}.")

            source = Source(
                key=parser.source_key(document),
                name=parser.source_name(document),
                parser_type=parser.key,
                file_path=str(path),
            )
            warnings = list(getattr(document, "warnings", []))

        report("Ready to import.", 100)
        return ImportPreview(
            path=path,
            parser_key=parser.key,
            parser_name=parser.name,
            source=source,
            suggested=suggested,
            entries=unique_entries,
            parsed_count=len(entries),
            warnings=warnings,
        )

    def check(self, preview: ImportPreview, language: str) -> IdentityCheck:
        """How many of the preview's words are already in ``language``'s vocabulary."""
        by_language: dict[str, list[str]] = {}
        for entry in preview.entries:
            by_language.setdefault(entry.language or language, []).append(entry.normalized_word)
        total = existing = 0
        found: set[str] = set()
        for code, words in by_language.items():
            result = self._words.check_identities(words, code)
            total += result.total
            existing += result.existing
            found |= result.existing_words
        return IdentityCheck(total=total, existing=existing, existing_words=frozenset(found))

    def resolve_language(
        self,
        preview: ImportPreview,
        target: ImportTarget,
        chosen: str | None = None,
    ) -> str:
        """Decide the language the imported words will have, or explain why not."""
        lists = [self._lists.require(list_id) for list_id in target.list_ids]
        list_languages = {lst.language for lst in lists if is_determined(lst.language)}
        new_language = target.new_list.language if target.new_list else None
        if is_determined(new_language):
            list_languages.add(new_language)  # type: ignore[arg-type]

        stated = preview.stated_language
        if stated:
            clash = sorted(code for code in list_languages if code != stated)
            if clash:
                names = [lst.name for lst in lists if lst.language in clash]
                if target.new_list and target.new_list.language in clash:
                    names.append(target.new_list.name)
                raise LanguageMismatchError(
                    f"{preview.path.name} is {language_name(stated)}, but "
                    f"{_join_names(names)} {'is' if len(names) == 1 else 'are'} "
                    f"{language_name(clash[0])}."
                )
            return stated

        if is_determined(chosen):
            clash = sorted(code for code in list_languages if code != chosen)
            if clash:
                names = [lst.name for lst in lists if lst.language in clash]
                raise LanguageMismatchError(
                    f"You chose {language_name(chosen)}, but {_join_names(names)} "
                    f"{'is' if len(names) == 1 else 'are'} {language_name(clash[0])}."
                )
            return chosen  # type: ignore[return-value]

        if len(list_languages) > 1:
            raise LanguageMismatchError(
                "The selected lists are in different languages, so one file cannot "
                "be imported into all of them."
            )
        if list_languages:
            return next(iter(list_languages))
        return UNDETERMINED

    # -- step 2: write -----------------------------------------------------

    def commit(
        self,
        preview: ImportPreview,
        target: ImportTarget,
        language: str | None = None,
    ) -> ImportResult:
        """Write a prepared import into the target lists, atomically.

        Raises:
            ListError: a target list is missing, a new list's name is taken, or
                the languages are incompatible.
            StorageError: the database could not be written.
        """
        if target.is_empty:
            target = self.default_target(preview)

        word_language = self.resolve_language(preview, target, language)

        with self._db.transaction():
            lists: list[VocabularyList] = [self._lists.require(i) for i in target.list_ids]
            if target.new_list is not None:
                spec = target.new_list
                list_language = spec.language if is_determined(spec.language) else word_language
                lists.append(
                    self._lists.create(
                        spec.name,
                        language=list_language or UNDETERMINED,
                        description=spec.description,
                        kind=ListKind.IMPORTED,
                    )
                )

            stored_source = self._sources.upsert(preview.source)
            assert stored_source.id is not None
            result = self._words.add_entries(preview.entries, stored_source.id, word_language)

            added = 0
            for target_list in lists:
                added += self._lists.add_words(target_list.id, result.word_ids)

        result = replace(
            result,
            source_name=stored_source.name,
            parser_type=preview.parser_key,
            parsed=preview.parsed_count,
            list_names=tuple(lst.name for lst in lists),
            added_to_lists=added,
            language=word_language,
        )
        log.info(
            "Imported %s into %s: %d parsed, %d unique, %d new, %d already known",
            preview.path.name,
            ", ".join(result.list_names),
            preview.parsed_count,
            preview.unique_count,
            result.new_words,
            result.existing_words,
        )
        return result

    def default_target(self, preview: ImportPreview) -> ImportTarget:
        """The list an import goes to when the caller does not choose.

        A list with the suggested name is reused if its language is compatible,
        which is what makes re-importing the same file land in the same list.
        Otherwise a new list is created with a free name.
        """
        suggested = preview.suggested
        existing = self._lists.get_by_name(suggested.name)
        stated = preview.stated_language
        if existing is not None and (
            not stated or not is_determined(existing.language) or existing.language == stated
        ):
            return ImportTarget(list_ids=(existing.id,))
        return ImportTarget(
            new_list=NewList(
                name=self._lists.unique_name(suggested.name),
                language=stated,
                description=suggested.description,
            )
        )

    # -- one call ----------------------------------------------------------

    def import_document(
        self,
        request: ImportRequest,
        progress: ProgressReporter | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ImportResult:
        """Prepare and commit in one go, for callers that need no preview."""
        preview = self.prepare(request.path, request.parser_key, progress, should_cancel)
        if preview is None:
            return ImportResult()
        target = request.target or ImportTarget()
        result = self.commit(preview, target, request.language)
        if progress is not None:
            progress("Import complete.", 100)
        return result


def _join_names(names: Sequence[str]) -> str:
    quoted = [f"“{name}”" for name in names]
    if len(quoted) <= 1:
        return "".join(quoted)
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]
