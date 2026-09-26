"""A word's content in and out of LexiTrack: its definition and contexts.

LexiTrack never writes a definition or a context itself — nothing is
generated at review time. Contexts come from the learner (the word's page),
or from a JSON file prepared anywhere, for instance with an LLM:

    [
      {"word": "sleep in",
       "contexts": ["I don't have to work tomorrow, so I can sleep in.",
                    "I usually sleep in on Sundays."]},
      {"word": "acquire",
       "definition": "to get or gain something, such as a skill or a possession",
       "contexts": ["She acquired valuable experience in her first year."]}
    ]

Importing finds each word among the words already in LexiTrack and adds its
contexts to it — a word is never created here, and a context the word
already has is not added twice. A ``definition``, when given and different,
replaces the word's definition. The file is read first and what it would do
is shown (a preview); nothing is written until the import is confirmed.

The export writes the same shape, with each word's length, CEFR level and
part of speech besides, so an exported file can be filled in and imported
again. ``{"words": [...]}``, as a JSON word list is written, is read too.
See ``docs/formats/contexts.md``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import InvalidFileError
from ..database.connection import Database
from ..models.context import MAX_CONTEXT_LENGTH, clean_context, contains_word, same_context
from ..models.language import is_determined, normalize_language
from ..normalization.word_normalizer import normalize_word
from ..repositories import ContextRepository, WordRepository
from ..repositories.word_repository import StoredWord

log = logging.getLogger(__name__)

#: A contexts file larger than this is almost certainly something else.
_MAX_FILE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ContentStatus:
    """How much of the vocabulary has contexts."""

    words: int
    with_contexts: int
    contexts: int
    without_definition: int

    @property
    def without_contexts(self) -> int:
        return self.words - self.with_contexts


@dataclass(slots=True)
class EntryPlan:
    """What importing one entry of a file would do."""

    index: int
    #: The word as the file writes it.
    text: str
    word: StoredWord | None = None
    new_contexts: list[str] = field(default_factory=list)
    #: Contexts the word already has, or that the entry repeats.
    duplicates: int = 0
    #: The new definition, when the file gives a different one.
    definition: str | None = None
    #: Imported, but worth a look: a context that does not seem to contain
    #: the word, a context left out because it is empty or too long.
    warnings: list[str] = field(default_factory=list)
    #: Why the entry cannot be imported at all: not a word LexiTrack has, or
    #: not an entry.
    problem: str | None = None

    @property
    def changes_anything(self) -> bool:
        return self.problem is None and bool(self.new_contexts or self.definition)


@dataclass(frozen=True, slots=True)
class ContextImportPreview:
    path: Path
    entries: tuple[EntryPlan, ...]

    @property
    def words(self) -> int:
        """Entries that change a word."""
        return sum(1 for entry in self.entries if entry.changes_anything)

    @property
    def context_count(self) -> int:
        return sum(len(entry.new_contexts) for entry in self.entries if entry.problem is None)

    @property
    def duplicate_count(self) -> int:
        return sum(entry.duplicates for entry in self.entries)

    @property
    def definition_count(self) -> int:
        return sum(1 for entry in self.entries if entry.problem is None and entry.definition)

    @property
    def rejected(self) -> tuple[EntryPlan, ...]:
        """Entries that cannot be imported: words LexiTrack does not have,
        or entries that are not entries."""
        return tuple(entry for entry in self.entries if entry.problem is not None)

    @property
    def warning_count(self) -> int:
        return sum(len(entry.warnings) for entry in self.entries if entry.problem is None)

    @property
    def changes_anything(self) -> bool:
        return any(entry.changes_anything for entry in self.entries)


@dataclass(frozen=True, slots=True)
class ContextImportResult:
    words: int
    contexts_added: int
    definitions_changed: int
    #: Entries not imported: unknown words and malformed entries.
    rejected: int


class ContentService:
    """Contexts and definitions: counted, imported from and exported to JSON."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._words = WordRepository(database)
        self._contexts = ContextRepository(database)

    @property
    def database(self) -> Database:
        return self._db

    # -- how much there is -------------------------------------------------------

    def status(self, word_ids: Iterable[int] | None = None) -> ContentStatus:
        """How many of these words (all, by default) have contexts."""
        ids = list(word_ids) if word_ids is not None else self._words.all_ids()
        counts = self._contexts.counts(ids)
        words = self._words.get_many(ids) if ids else []
        return ContentStatus(
            words=len(ids),
            with_contexts=len(counts),
            contexts=sum(counts.values()),
            without_definition=sum(1 for word in words if not word.definition),
        )

    def all_word_ids(self) -> list[int]:
        return self._words.all_ids()

    def words_with_contexts(self, word_ids: Iterable[int]) -> set[int]:
        """Of ``word_ids``, those with at least one context."""
        return set(self._contexts.counts(word_ids))

    # -- export ------------------------------------------------------------------

    def document(self, word_ids: Sequence[int] | None = None) -> list[dict[str, Any]]:
        """The words, in order, as the export writes them."""
        ids = list(word_ids) if word_ids is not None else self._words.all_ids()
        words = self._words.get_many(ids)
        contexts = self._contexts.for_words(ids)
        items: list[dict[str, Any]] = []
        for word in words:
            item: dict[str, Any] = {"word": word.word, "length": word.length}
            if word.cefr_level:
                item["cefr_level"] = word.cefr_level
            if word.part_of_speech:
                item["part_of_speech"] = word.part_of_speech
            if word.definition:
                item["definition"] = word.definition
            item["contexts"] = [context.text for context in contexts.get(word.id, ())]
            items.append(item)
        return items

    def export(self, path: Path | str, word_ids: Sequence[int] | None = None) -> tuple[Path, int]:
        """Write the words (all, by default) with their contexts. Returns the
        path and how many words were written."""
        items = self.document(word_ids)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.info("Exported %d words with their contexts to %s", len(items), target)
        return target, len(items)

    # -- import ------------------------------------------------------------------

    def preview_import(self, path: Path | str) -> ContextImportPreview:
        """Read a contexts file and say what importing it would do. Writes nothing."""
        source = Path(path)
        items, file_language = _read(source)
        entries: list[EntryPlan] = []
        planned: dict[int, EntryPlan] = {}
        for index, item in enumerate(items, start=1):
            entry = self._plan(item, index, file_language, planned)
            entries.append(entry)
            if entry.word is not None and entry.problem is None:
                planned.setdefault(entry.word.id, entry)
        return ContextImportPreview(source, tuple(entries))

    def apply_import(self, preview: ContextImportPreview) -> ContextImportResult:
        """Import what the preview showed, in one transaction."""
        words = contexts = definitions = 0
        with self._db.transaction():
            for entry in preview.entries:
                if not entry.changes_anything or entry.word is None:
                    continue
                words += 1
                if entry.definition:
                    self._words.set_definition(entry.word.id, entry.definition)
                    definitions += 1
                if entry.new_contexts:
                    contexts += len(self._contexts.add_many(entry.word.id, entry.new_contexts))
        log.info(
            "Imported %d contexts and %d definitions for %d words from %s",
            contexts, definitions, words, preview.path,
        )
        return ContextImportResult(
            words=words,
            contexts_added=contexts,
            definitions_changed=definitions,
            rejected=len(preview.rejected),
        )

    def _plan(
        self,
        item: Any,
        index: int,
        file_language: str | None,
        planned: dict[int, EntryPlan],
    ) -> EntryPlan:
        if isinstance(item, str):
            # A bare word, as in a word list: nothing to add to it.
            return EntryPlan(index, item.strip(), problem=None)
        if not isinstance(item, dict):
            return EntryPlan(index, str(item)[:40], problem="not a word entry")
        text = item.get("word")
        if not isinstance(text, str) or not normalize_word(text):
            return EntryPlan(index, str(text or "")[:40], problem="no “word” to find")
        entry = EntryPlan(index, text.strip())
        language = file_language
        if isinstance(item.get("language"), str):
            code = normalize_language(item["language"])
            language = code if is_determined(code) else file_language
        word = self._words.find(normalize_word(text), language)
        if word is None:
            entry.problem = "not in your vocabulary (words are never created by this import)"
            return entry
        entry.word = word

        raw = item.get("contexts", [])
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            entry.problem = "“contexts” must be a list of sentences"
            return entry
        # The same word twice in one file: its contexts are compared with
        # those already planned for it too.
        earlier = planned.get(word.id)
        have = [c.text for c in self._contexts.for_word(word.id)]
        if earlier is not None:
            have += earlier.new_contexts
        for number, value in enumerate(raw, start=1):
            sentence = value.get("text") if isinstance(value, dict) else value
            if not isinstance(sentence, str) or not clean_context(sentence):
                entry.warnings.append(f"context {number} is empty or not text: left out")
                continue
            clean = clean_context(sentence)
            if len(clean) > MAX_CONTEXT_LENGTH:
                entry.warnings.append(
                    f"a context longer than {MAX_CONTEXT_LENGTH} characters left out"
                )
                continue
            if any(same_context(clean, other) for other in have + entry.new_contexts):
                entry.duplicates += 1
                continue
            if not contains_word(clean, word.word):
                entry.warnings.append(f"“{_short(clean)}” does not seem to contain “{word.word}”")
            entry.new_contexts.append(clean)

        definition = item.get("definition")
        if isinstance(definition, str) and clean_context(definition):
            new = clean_context(definition)
            if " ".join((word.definition or "").split()) != new:
                entry.definition = new
        elif definition is not None and not isinstance(definition, str):
            entry.warnings.append("“definition” is not text, so it was left out")
        return entry


def _read(path: Path) -> tuple[list[Any], str | None]:
    """The file's entries, and the language it states for all of them."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise InvalidFileError(f"{path.name} is too large to be a contexts file.")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise InvalidFileError(f"{path.name} could not be found.") from exc
    except UnicodeDecodeError as exc:
        raise InvalidFileError(f"{path.name} is not a UTF-8 text file.") from exc
    except json.JSONDecodeError as exc:
        raise InvalidFileError(
            f"{path.name} is not valid JSON (line {exc.lineno}, column {exc.colno})."
        ) from exc
    except OSError as exc:
        raise InvalidFileError(f"{path.name} could not be read: {exc}") from exc
    language = None
    if isinstance(data, dict):
        code = normalize_language(data.get("language")) if data.get("language") else None
        language = code if is_determined(code) else None
        data = data.get("words")
    if not isinstance(data, list):
        raise InvalidFileError(
            f"{path.name} must hold a list of words, each with its “contexts”: "
            '[{"word": "sleep in", "contexts": ["…"]}].'
        )
    if not data:
        raise InvalidFileError(f"{path.name} holds no words.")
    return data, language


def _short(text: str, limit: int = 50) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
