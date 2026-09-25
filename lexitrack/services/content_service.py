"""Teaching content: reading it, and moving it in and out in batches.

The 6,825 words already in LexiTrack have short English definitions and
nothing else. Content — a Turkish core meaning, nuance, pattern,
collocations, an encoding cue, contexts — is added over time, in batches,
from outside the application:

    export a batch of words that need content   (content_batch_001.json)
            -> generate it with any tool, e.g. an LLM, outside LexiTrack
            -> validate and preview the result here
            -> import, filling what is empty; replacing only what you choose

The desktop application never calls an LLM and works offline. A word with no
content still works: it is taught by the SHORT route.

The file format is ``lexitrack-content``, schema 1, described in
``docs/formats/content-enrichment.md``. Words are matched by ``word_id`` *and*
spelling, so a file can never write one word's content onto another.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.errors import InvalidFileError, StorageError
from ..database.connection import Database
from ..models.content import (
    TARGET,
    ContentStatus,
    ContextKind,
    DepthHint,
    EncodingType,
    Related,
    WordContent,
    WordContext,
    WordTeaching,
)
from ..normalization.word_normalizer import normalize_word
from ..repositories import ContentRepository, WordRepository

log = logging.getLogger(__name__)

FORMAT = "lexitrack-content"
SCHEMA_VERSION = 1

#: The longest text accepted in any field, to catch a runaway generation.
_MAX_TEXT = 600
_MAX_ITEMS = 12

INSTRUCTIONS = """\
You are enriching an English vocabulary list for a Turkish-speaking learner
whose goal is to use each word naturally, not only to recognise it.

For every entry in "words", fill "content" and "contexts". Keep word_id and
word exactly as given. Leave a field null (or an empty list) rather than
inventing something doubtful. Return the whole file as valid JSON, same shape.

content:
  core_meaning_tr  The core meaning in Turkish, short (one line). The idea the
                   word names, not a list of every translation.
  nuance           When this word and not a near synonym; tone. English or
                   Turkish, one or two sentences.
  pattern          The grammatical pattern(s), e.g. "reluctant to do sth".
  collocations     2-5 common collocations, e.g. ["make a decision"].
  register         "formal", "informal", "neutral", "technical"... or null.
  encoding_type    One of IMAGE, SCENE, ACTION, CONTRAST, RELATION, SOUND, NONE.
                   Concrete words: IMAGE, SCENE or ACTION. Abstract words:
                   CONTRAST or RELATION. Never force an image on an abstract word.
  encoding_cue     One short sentence that gives the word an extra route into
                   memory, of the type chosen. null for NONE.
  related          Up to 3 {"word": ..., "relation": "synonym|antonym|contrast|
                   family|confusable"}.
  depth_hint       "deep" for abstract words or words with tricky usage,
                   otherwise "light".

contexts: 2-3 items, each
  {"kind": "sentence" or "situation", "text": ..., "translation_tr": ...}
  text      A natural sentence using the word, with the word (as it appears,
            inflected or not) marked like {{reluctant}}. A "situation" is a
            short description of a moment the word fits, still marking it.
            Each context shows a different use; none repeats the definition.
  translation_tr  A Turkish translation of the sentence, or null.
"""


# -- the result of reading a file --------------------------------------------


@dataclass(slots=True)
class WordPlan:
    """What importing one entry would do."""

    word_id: int
    word: str
    fills: dict[str, Any] = field(default_factory=dict)
    conflicts: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    new_contexts: list[WordContext] = field(default_factory=list)
    duplicate_contexts: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def changes_anything(self) -> bool:
        return bool(self.fills or self.conflicts or self.new_contexts)


@dataclass(slots=True)
class ContentImportPreview:
    path: Path
    batch: str | None
    plans: list[WordPlan] = field(default_factory=list)
    #: Entries that cannot be imported at all, with why.
    rejected: list[str] = field(default_factory=list)

    @property
    def conflict_count(self) -> int:
        return sum(len(plan.conflicts) for plan in self.plans)

    @property
    def fill_count(self) -> int:
        return sum(len(plan.fills) for plan in self.plans)

    @property
    def context_count(self) -> int:
        return sum(len(plan.new_contexts) for plan in self.plans)

    @property
    def warning_count(self) -> int:
        return sum(len(plan.warnings) for plan in self.plans)


@dataclass(frozen=True, slots=True)
class ContentImportResult:
    words: int
    fields_filled: int
    fields_replaced: int
    contexts_added: int


class ContentService:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._content = ContentRepository(database)
        self._words = WordRepository(database)

    # -- reading -------------------------------------------------------------

    def teaching(self, word_id: int) -> WordTeaching:
        return self._content.teaching(word_id)

    def status(self, word_id: int) -> ContentStatus:
        return self._content.statuses([word_id])[word_id]

    def statuses(self, word_ids: Iterable[int]) -> dict[int, ContentStatus]:
        return self._content.statuses(word_ids)

    def needing_content(self, word_ids: Sequence[int]) -> list[int]:
        """Of ``word_ids``, in order, those whose content is not complete."""
        statuses = self._content.statuses(word_ids)
        return [
            word_id for word_id in word_ids if statuses.get(word_id) is not ContentStatus.COMPLETE
        ]

    # -- export a batch ------------------------------------------------------

    def build_batch(self, word_ids: Sequence[int], batch: str) -> dict[str, Any]:
        """The file for a batch: each word with what it has and what it lacks."""
        words = {w.id: w for w in self._words.get_many(word_ids)}
        entries = []
        for word_id in word_ids:
            word = words.get(int(word_id))
            if word is None:
                continue
            teaching = self._content.teaching(word.id)
            content = teaching.content or WordContent(word_id=word.id)
            entries.append(
                {
                    "word_id": word.id,
                    "word": word.word,
                    "language": word.language,
                    "cefr": word.cefr_level,
                    "part_of_speech": word.part_of_speech,
                    "definition": word.definition,
                    "needs": _needs(teaching),
                    "content": _content_to_json(content),
                    "contexts": [_context_to_json(c) for c in teaching.contexts],
                }
            )
        return {
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            "app_version": __version__,
            "batch": batch,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "instructions": INSTRUCTIONS,
            "encoding_types": [e.value for e in EncodingType],
            "words": entries,
        }

    def export_batch(self, word_ids: Sequence[int], path: Path | str, batch: str) -> Path:
        target = Path(path)
        document = self.build_batch(word_ids, batch)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        log.info("Exported content batch %s with %d words", batch, len(document["words"]))
        return target

    # -- import --------------------------------------------------------------

    def preview_import(self, path: Path | str) -> ContentImportPreview:
        """Read and validate a content file. Writes nothing."""
        source = Path(path)
        try:
            root = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise InvalidFileError(f"{source.name} is not readable JSON: {exc}") from exc
        if not isinstance(root, dict) or root.get("format") != FORMAT:
            raise InvalidFileError(
                f"{source.name} is not a LexiTrack content file "
                f"(it needs \"format\": \"{FORMAT}\")."
            )
        if int(root.get("schema_version", 0) or 0) > SCHEMA_VERSION:
            raise InvalidFileError(
                f"{source.name} was written for a newer LexiTrack. Update LexiTrack first."
            )
        items = root.get("words")
        if not isinstance(items, list):
            raise InvalidFileError(f"{source.name} has no \"words\" list.")

        preview = ContentImportPreview(path=source, batch=_text(root.get("batch")))
        seen: set[int] = set()
        for index, item in enumerate(items, start=1):
            try:
                plan = self._plan(item, index)
            except _Rejected as problem:
                preview.rejected.append(str(problem))
                continue
            if plan.word_id in seen:
                preview.rejected.append(f"Entry {index}: word {plan.word} appears twice")
                continue
            seen.add(plan.word_id)
            preview.plans.append(plan)
        return preview

    def apply_import(
        self,
        preview: ContentImportPreview,
        *,
        replace_fields: Iterable[tuple[int, str]] = (),
        replace_all: bool = False,
    ) -> ContentImportResult:
        """Import a previewed file, in one transaction.

        Empty fields are filled. A field that already has a different value
        is replaced only if it is named in ``replace_fields`` (word id, field)
        or ``replace_all`` is set: nothing is overwritten silently.
        """
        allowed = set(replace_fields)
        source = preview.batch or preview.path.name
        filled = replaced = contexts = words = 0
        try:
            with self._db.transaction():
                for plan in preview.plans:
                    if not plan.changes_anything:
                        continue
                    current = self._content.content(plan.word_id) or WordContent(
                        word_id=plan.word_id
                    )
                    updates = dict(plan.fills)
                    for name, (_old, new) in plan.conflicts.items():
                        if replace_all or (plan.word_id, name) in allowed:
                            updates[name] = new
                            replaced += 1
                    filled += len(plan.fills)
                    if updates:
                        self._content.save_content(replace(current, **updates, source=source))
                    if plan.new_contexts:
                        self._content.add_contexts(
                            [replace(c, source=c.source or source) for c in plan.new_contexts]
                        )
                        contexts += len(plan.new_contexts)
                    words += 1
        except StorageError:
            raise
        log.info(
            "Imported content from %s: %d words, %d filled, %d replaced, %d contexts",
            preview.path.name, words, filled, replaced, contexts,
        )
        return ContentImportResult(words, filled, replaced, contexts)

    # -- one entry -------------------------------------------------------------

    def _plan(self, item: Any, index: int) -> WordPlan:
        if not isinstance(item, dict):
            raise _Rejected(f"Entry {index} is not an object")
        try:
            word_id = int(item.get("word_id"))
        except (TypeError, ValueError):
            raise _Rejected(f"Entry {index} has no word_id") from None
        spelling = _text(item.get("word"))
        stored = self._words.get(word_id)
        if stored is None:
            raise _Rejected(f"Entry {index}: no word with id {word_id} in this vocabulary")
        # The id must name the word the file thinks it names.
        if not spelling or normalize_word(spelling) != stored.normalized_word:
            raise _Rejected(
                f"Entry {index}: id {word_id} is “{stored.word}” here, but the file says "
                f"“{spelling}”"
            )
        plan = WordPlan(word_id=word_id, word=stored.word)
        current = self._content.content(word_id) or WordContent(word_id=word_id)
        incoming = _read_content(item.get("content") or {}, plan.warnings)
        for name, value in incoming.items():
            existing = getattr(current, name)
            if not value or value == existing:
                continue
            if existing:
                plan.conflicts[name] = (existing, value)
            else:
                plan.fills[name] = value

        known = {_fingerprint(c.text) for c in self._content.contexts(word_id)}
        for position, raw in enumerate(item.get("contexts") or [], start=1):
            context = _read_context(raw, word_id, position, stored.word, plan.warnings)
            if context is None:
                continue
            key = _fingerprint(context.text)
            if key in known:
                plan.duplicate_contexts += 1
                continue
            known.add(key)
            plan.new_contexts.append(context)
        return plan


class _Rejected(Exception):
    pass


# -- reading and writing the JSON shape ------------------------------------------


def _needs(teaching: WordTeaching) -> list[str]:
    content = teaching.content or WordContent(word_id=0)
    missing = [name for name in WordContent.FIELDS if not getattr(content, name)]
    if len(teaching.contexts) < 2:
        missing.append("contexts")
    return missing


def _content_to_json(content: WordContent) -> dict[str, Any]:
    return {
        "core_meaning_tr": content.core_meaning_tr,
        "nuance": content.nuance,
        "pattern": content.pattern,
        "collocations": list(content.collocations),
        "register": content.register,
        "encoding_type": content.encoding_type.value if content.encoding_type else None,
        "encoding_cue": content.encoding_cue,
        "related": [{"word": r.word, "relation": r.relation} for r in content.related],
        "depth_hint": content.depth_hint.value if content.depth_hint else None,
    }


def _context_to_json(context: WordContext) -> dict[str, Any]:
    return {
        "kind": context.kind.value,
        "text": context.text,
        "translation_tr": context.translation_tr,
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _bounded(value: Any, name: str, warnings: list[str]) -> str | None:
    text = _text(value)
    if text and len(text) > _MAX_TEXT:
        warnings.append(f"{name} is longer than {_MAX_TEXT} characters and was cut")
        text = text[:_MAX_TEXT]
    return text


def _read_content(raw: Any, warnings: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        warnings.append("content is not an object and was ignored")
        return {}
    values: dict[str, Any] = {}
    for name in ("core_meaning_tr", "nuance", "pattern", "register", "encoding_cue"):
        values[name] = _bounded(raw.get(name), name, warnings)

    collocations = raw.get("collocations") or []
    if isinstance(collocations, list):
        values["collocations"] = tuple(
            text for text in (_bounded(c, "collocation", warnings) for c in collocations) if text
        )[:_MAX_ITEMS]
    else:
        warnings.append("collocations is not a list and was ignored")

    kind = _text(raw.get("encoding_type"))
    if kind:
        try:
            values["encoding_type"] = EncodingType(kind.upper())
        except ValueError:
            warnings.append(f"encoding_type “{kind}” is not one of the allowed types")
    depth = _text(raw.get("depth_hint"))
    if depth:
        try:
            values["depth_hint"] = DepthHint(depth.casefold())
        except ValueError:
            warnings.append(f"depth_hint “{depth}” is not light or deep")

    related = raw.get("related") or []
    if isinstance(related, list):
        values["related"] = tuple(
            Related(_text(r.get("word")) or "", _text(r.get("relation")) or "related")
            for r in related
            if isinstance(r, dict) and _text(r.get("word"))
        )[:_MAX_ITEMS]
    else:
        warnings.append("related is not a list and was ignored")
    return {name: value for name, value in values.items() if value}


def _read_context(
    raw: Any, word_id: int, position: int, word: str, warnings: list[str]
) -> WordContext | None:
    if not isinstance(raw, dict):
        warnings.append(f"context {position} is not an object and was skipped")
        return None
    text = _bounded(raw.get("text"), f"context {position}", warnings)
    if not text:
        warnings.append(f"context {position} has no text and was skipped")
        return None
    if not TARGET.search(text):
        warnings.append(
            f"context {position} does not mark the word as {{{{{word}}}}} and was skipped"
        )
        return None
    try:
        kind = ContextKind((_text(raw.get("kind")) or "sentence").casefold())
    except ValueError:
        warnings.append(f"context {position} has an unknown kind; kept as a sentence")
        kind = ContextKind.SENTENCE
    return WordContext(
        word_id=word_id,
        text=text,
        kind=kind,
        translation_tr=_bounded(raw.get("translation_tr"), "translation", warnings),
    )


def _fingerprint(text: str) -> str:
    """Two contexts are the same if they differ only in case, spacing or marking."""
    plain = TARGET.sub(lambda m: m.group(1), text)
    return " ".join(plain.casefold().split())
