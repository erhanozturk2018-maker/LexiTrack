"""The application service the UI talks to.

Everything the screens need goes through here: lists and their words, review
sessions, status changes, imports and exports. The UI never sees a repository,
a SQL statement or a parser.

Methods that take an optional ``list_id`` work on one list when given it and
on the whole vocabulary when not. The whole-vocabulary forms are what version 1
exposed, and they keep working unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import ListError, WordError
from ..database.connection import Database
from ..models.context import WordContext, clean_context
from ..models.language import UNDETERMINED
from ..models.source import Source
from ..models.user_word_state import Progress, ReviewStatus
from ..models.vocabulary_list import ListKind, VocabularyList
from ..models.word_entry import WordEntry
from ..normalization.word_normalizer import display_form, normalize_word
from ..parsers.base import ParserInfo
from ..parsers.registry import AUTO, ParserRegistry
from ..repositories.card_repository import CardRepository
from ..repositories.context_repository import ContextRepository
from ..repositories.list_repository import ListRepository
from ..repositories.source_repository import SourceRepository
from ..repositories.state_repository import StateRepository
from ..repositories.word_repository import (
    IdentityCheck,
    ImportResult,
    StoredWord,
    WordRepository,
)
from .export_service import ExportContent, ExportFormat, ExportService
from .import_service import (
    ImportPreview,
    ImportRequest,
    ImportService,
    ImportTarget,
    ProgressReporter,
)
from .review_session import ReviewSession

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WordEdit:
    """Everything the details panel's edit mode saves, at once."""

    #: The new definition; None leaves it as it is.
    definition: str | None = None
    #: Contexts corrected in place, by id.
    changed: Mapping[int, str] = field(default_factory=dict)
    #: Contexts deleted, by id.
    deleted: Sequence[int] = ()
    #: New contexts, in order.
    added: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class StatusChange:
    """A status change as it was made, so it can be taken back."""

    status: ReviewStatus
    #: The words it changed, with the status and review time each had.
    before: Mapping[int, tuple[ReviewStatus, str | None]]

    @property
    def changed(self) -> int:
        return len(self.before)

#: Provenance recorded for words the user types in themselves.
MANUAL_SOURCE = Source(key="manual", name="Added manually", parser_type="manual")


class VocabularyService:
    """Facade over lists, review, status, import and export."""

    def __init__(self, database: Database | None = None) -> None:
        self._db = database or Database()
        self._words = WordRepository(self._db)
        self._sources = SourceRepository(self._db)
        self._state = StateRepository(self._db)
        self._lists = ListRepository(self._db)
        self._contexts = ContextRepository(self._db)
        self._registry = ParserRegistry()
        self._import = ImportService(
            self._db, self._registry, self._words, self._sources, self._lists
        )
        self._export = ExportService(self._words, self._lists, self._contexts)

    @property
    def database(self) -> Database:
        return self._db

    # -- import ------------------------------------------------------------

    def import_document(
        self,
        path: Path | str,
        parser_key: str = AUTO,
        progress: ProgressReporter | None = None,
        should_cancel: Callable[[], bool] | None = None,
        target: ImportTarget | None = None,
        language: str | None = None,
    ) -> ImportResult:
        """Import a file in one call.

        Without a ``target`` the words go to a list named after the document,
        reusing it if it already exists — so re-importing lands in the same
        place.
        """
        request = ImportRequest(
            path=Path(path), parser_key=parser_key, target=target, language=language
        )
        return self._import.import_document(request, progress, should_cancel)

    def prepare_import(
        self,
        path: Path | str,
        parser_key: str = AUTO,
        progress: ProgressReporter | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ImportPreview | None:
        """Parse a file for the import preview, writing nothing."""
        return self._import.prepare(Path(path), parser_key, progress, should_cancel)

    def check_import(self, preview: ImportPreview, language: str) -> IdentityCheck:
        return self._import.check(preview, language)

    def resolve_import_language(
        self, preview: ImportPreview, target: ImportTarget, chosen: str | None = None
    ) -> str:
        return self._import.resolve_language(preview, target, chosen)

    def commit_import(
        self, preview: ImportPreview, target: ImportTarget, language: str | None = None
    ) -> ImportResult:
        return self._import.commit(preview, target, language)

    def default_import_target(self, preview: ImportPreview) -> ImportTarget:
        return self._import.default_target(preview)

    def available_parsers(self) -> list[ParserInfo]:
        """Return the parsers the user can choose from in the import dialog."""
        return self._registry.describe()

    # -- lists -------------------------------------------------------------

    def lists(self) -> list[VocabularyList]:
        """Every list, alphabetically, with its progress."""
        return self._lists.all()

    def get_list(self, list_id: int) -> VocabularyList | None:
        return self._lists.get(list_id)

    def create_list(
        self, name: str, language: str = UNDETERMINED, description: str | None = None
    ) -> VocabularyList:
        created = self._lists.create(name, language, description, ListKind.CUSTOM)
        log.info("Created list %r (%s)", created.name, created.language)
        return created

    def update_list(
        self,
        list_id: int,
        name: str | None = None,
        description: str | None = None,
        language: str | None = None,
    ) -> VocabularyList:
        return self._lists.update(list_id, name, description, language)

    def delete_list(self, list_id: int) -> int:
        """Delete a list. Returns how many words were deleted because no other list had them."""
        deleted = self._lists.delete(list_id)
        log.info("Deleted list %s and %d words exclusive to it", list_id, deleted)
        return deleted

    def exclusive_word_count(self, list_id: int, word_ids: Sequence[int] | None = None) -> int:
        """How many words would disappear entirely if removed from this list."""
        return self._lists.count_exclusive_words(list_id, word_ids)

    def list_words(self, list_id: int) -> list[StoredWord]:
        """Every word in a list, in review order."""
        return self._words.list_in_list(list_id)

    def add_words_to_list(self, list_id: int, word_ids: Sequence[int]) -> int:
        """Add existing words to a list. Returns how many were not already in it."""
        return self._lists.add_words(list_id, word_ids)

    def remove_words_from_list(self, list_id: int, word_ids: Sequence[int]) -> tuple[int, int]:
        """Returns ``(removed, deleted)``; see :meth:`ListRepository.remove_words`."""
        return self._lists.remove_words(list_id, word_ids)

    def add_word(
        self,
        list_id: int,
        word: str,
        part_of_speech: str | None = None,
        cefr_level: str | None = None,
        definition: str | None = None,
        contexts: Sequence[str] = (),
    ) -> tuple[StoredWord, bool]:
        """Type a word into a list by hand: the word, its part of speech, CEFR
        level, definition and any number of contexts. Its length is worked
        out from the word.

        The word goes through exactly the same model and normalization as an
        imported one, in the list's language, so a manually added "ability"
        and an imported "ability" are the same vocabulary item. A word that
        was deleted can be added again: it starts afresh.

        Returns ``(word, added)``; ``added`` is False when it was already in
        the list (its new contexts are still added to it).
        """
        target = self._lists.require(list_id)
        normalized = normalize_word(word)
        if not normalized:
            raise ListError(
                f"“{word.strip()}” cannot be added: a word must contain "
                "letters, and may only use apostrophes, hyphens and spaces between them."
            )

        def clean(value: str | None) -> str | None:
            text = (value or "").strip()
            return text or None

        level = clean(cefr_level)
        entry = WordEntry(
            word=display_form(word),
            normalized_word=normalized,
            source_id=MANUAL_SOURCE.key,
            part_of_speech=clean(part_of_speech),
            cefr_level=level.upper() if level else None,
            definition=clean(definition),
            language=target.language,
            contexts=tuple(text for text in (clean_context(t) for t in contexts) if text),
        )
        with self._db.transaction():
            source = self._sources.upsert(MANUAL_SOURCE)
            assert source.id is not None
            result = self._words.add_entries([entry], source.id, target.language)
            word_id = result.word_ids[0]
            added = self._lists.add_words(list_id, [word_id]) == 1

        stored = self._words.get(word_id)
        assert stored is not None
        return stored, added

    # -- a word's definition and contexts -----------------------------------

    def contexts(self, word_id: int) -> tuple[WordContext, ...]:
        """The word's contexts, in the order they were added."""
        return self._contexts.for_word(word_id)

    def add_context(self, word_id: int, text: str) -> WordContext | None:
        """Add a context to a word. None when it already has that sentence."""
        return self._contexts.add(word_id, text)

    def delete_context(self, context_id: int) -> bool:
        return self._contexts.delete(context_id)

    def set_definition(self, word_id: int, definition: str) -> StoredWord:
        """Replace a word's definition: one text covering all its senses."""
        self._words.set_definition(word_id, definition)
        stored = self._words.get(word_id)
        if stored is None:
            raise WordError("That word no longer exists.")
        return stored

    def save_word(self, word_id: int, edit: WordEdit) -> StoredWord:
        """Save a definition and contexts edited together, in one transaction:
        either all of it is saved or none of it.

        Deletions go first, so a corrected sentence may take the text of one
        just deleted; a new sentence the word already has is not added twice.
        """
        with self._db.transaction():
            own = {context.id for context in self._contexts.for_word(word_id)}
            foreign = (set(edit.deleted) | set(edit.changed)) - own
            if foreign:
                raise WordError("Those contexts belong to another word.")
            if edit.definition is not None:
                self._words.set_definition(word_id, edit.definition)
            for context_id in edit.deleted:
                self._contexts.delete(context_id)
            for context_id, text in edit.changed.items():
                self._contexts.update(context_id, text)
            if edit.added:
                self._contexts.add_many(word_id, list(edit.added))
        stored = self._words.get(word_id)
        if stored is None:
            raise WordError("That word no longer exists.")
        return stored

    def delete_words(self, word_ids: Sequence[int]) -> int:
        """Delete words from LexiTrack altogether — from every list, with
        their contexts, status, card and answers. Returns how many."""
        deleted = self._words.delete(word_ids)
        log.info("Deleted %d words", deleted)
        return deleted

    # -- review ------------------------------------------------------------

    def start_review(self, list_id: int | None = None) -> ReviewSession:
        """A flashcard session with its own backward-navigation history."""
        if list_id is not None:
            self._lists.require(list_id)
        return ReviewSession(self._words, self._state, list_id)

    def get_next_word(self, list_id: int | None = None) -> StoredWord | None:
        """Return the next word awaiting review, or ``None`` when none remain.

        Derived from the database on every call, which is what makes the review
        position survive a restart without storing a cursor.
        """
        return self._words.next_unreviewed(list_id)

    def mark_known(self, word_id: int) -> None:
        self._state.set_status(word_id, ReviewStatus.KNOWN)

    def mark_unknown(self, word_id: int) -> None:
        self._state.set_status(word_id, ReviewStatus.UNKNOWN)

    def mark(self, word_id: int, known: bool) -> None:
        if known:
            self.mark_known(word_id)
        else:
            self.mark_unknown(word_id)

    def undo(self, word_id: int) -> None:
        """Return ``word_id`` to Not Reviewed. An explicit status change."""
        self._state.set_status(word_id, ReviewStatus.NOT_REVIEWED)

    def set_status(self, word_ids: Sequence[int], status: ReviewStatus) -> int:
        """Change the status of many words at once. Returns how many changed."""
        changed = self._state.set_status_many(word_ids, status)
        log.info("Set %d words to %s", changed, status.value)
        return changed

    def change_status(self, word_ids: Sequence[int], status: ReviewStatus) -> StatusChange:
        """Change the status of many words, remembering what each was so
        :meth:`undo_status_change` can put it back."""
        status = ReviewStatus(status)
        with self._db.transaction():
            before = self._state.states(word_ids)
            self._state.set_status_many(word_ids, status)
        changed = {word_id: was for word_id, was in before.items() if was[0] is not status}
        log.info("Set %d words to %s", len(changed), status.value)
        return StatusChange(status, changed)

    def undo_status_change(self, change: StatusChange) -> int:
        """Put back the statuses a :meth:`change_status` changed. Returns how
        many words changed back."""
        restored = self._state.restore(dict(change.before))
        log.info("Took back a change to %s for %d words", change.status.value, restored)
        return restored

    def get_word(self, word_id: int) -> StoredWord | None:
        return self._words.get(word_id)

    def get_words(self, word_ids: Sequence[int]) -> list[StoredWord]:
        """Words for ``word_ids``, in the order given; missing ids are skipped."""
        return self._words.get_many(word_ids)

    # -- progress ----------------------------------------------------------

    def get_progress(self, list_id: int | None = None) -> Progress:
        return self._state.progress(list_id)

    def list_sources(self) -> list[Source]:
        return self._sources.list_all()

    def list_unknown_words(self, list_id: int | None = None) -> list[StoredWord]:
        return self._words.list_by_status(ReviewStatus.UNKNOWN, list_id)

    def list_known_words(self, list_id: int | None = None) -> list[StoredWord]:
        return self._words.list_by_status(ReviewStatus.KNOWN, list_id)

    def is_empty(self) -> bool:
        """True when no vocabulary has been added yet."""
        return self._words.count() == 0 and self._lists.count() == 0

    def reset_progress(self) -> int:
        """Start over: every word not reviewed, and no schedule or history.

        Statuses and the study schedule are cleared together, in one
        transaction. Clearing only the statuses would leave cards scheduled
        for words the app now calls "not reviewed" — two answers to the same
        question. Words, lists, definitions and study plans are kept, and the
        daily backup from before the reset is still in ``data/backups``.
        """
        with self._db.transaction():
            count = self._state.reset_all()
            cards = CardRepository(self._db).clear_all()
        log.info("Progress reset: %d statuses, %d scheduled cards", count, cards)
        return count

    def unknown_count(self, list_id: int | None = None) -> int:
        if list_id is None:
            return self._state.count_with_status(ReviewStatus.UNKNOWN)
        return self._state.progress(list_id).unknown

    # -- export ------------------------------------------------------------

    def export_content_for_list(self, list_id: int) -> ExportContent:
        return self._export.list_content(list_id)

    def export_content_for_unknown(self, list_id: int | None = None) -> ExportContent:
        return self._export.unknown_content(list_id)

    def export_content_for_selection(
        self, word_ids: Sequence[int], list_id: int | None = None
    ) -> ExportContent:
        context = self._lists.get(list_id) if list_id is not None else None
        return self._export.selection_content(word_ids, context)

    def export_with_contexts(self, words: Sequence[StoredWord]) -> int:
        """How many of ``words`` have contexts to export."""
        return self._export.with_contexts(words)

    def export(self, content: ExportContent, path: Path | str, file_format: ExportFormat) -> Path:
        return self._export.write(content, Path(path), ExportFormat(file_format))

    def export_unknown_pdf(self, path: Path | str, list_id: int | None = None) -> Path:
        return self._export.export_unknown_pdf(Path(path), list_id)

    def export_unknown_csv(self, path: Path | str, list_id: int | None = None) -> Path:
        return self._export.export_unknown_csv(Path(path), list_id)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> VocabularyService:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
