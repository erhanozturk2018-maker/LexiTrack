"""Teaching content: reading it, and moving it in and out in batches.

The words in LexiTrack come with short definitions in their own language and
nothing else. Content is added over time, in batches, from outside the
application:

    export a batch of words that need content   (content_batch_001.json)
            -> generate it with any tool, e.g. an LLM, outside LexiTrack
            -> validate and preview the result here
            -> import, filling what is empty; replacing only what you choose

The desktop application never calls an LLM and works offline. A word with no
content still works: it is taught by the SHORT route.

Content has two parts (``models/content.py``): what is true of the word in
its **target language**, shared by every learner, and what explains it in a
**learner language**, one block per language. A batch file carries the
shared part once and any number of learner languages under
``localizations``, so English → Turkish and English → German content for the
same word live in one file, on one word record.

The format is ``lexitrack-content``, schema 2, described in
``docs/formats/content-enrichment.md``; schema 1 files are still read.
Words are matched by ``word_id`` *and* spelling, so a file can never write
one word's content onto another.
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
from ..core.errors import InvalidFileError
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
    WordLocalization,
    WordTeaching,
)
from ..models.language import is_determined, language_name, normalize_language
from ..normalization.word_normalizer import normalize_word
from ..repositories import ContentRepository, RuntimeRepository, WordRepository

log = logging.getLogger(__name__)

FORMAT = "lexitrack-content"
SCHEMA_VERSION = 2

#: The longest text accepted in any field, to catch a runaway generation.
_MAX_TEXT = 600
_MAX_ITEMS = 12

#: Schema 1 kept one learner language in the shared block, written for
#: Turkish-speaking learners; its fields are read into that localization.
_V1_LANGUAGE = "tr"
_V1_LOCALIZED = {
    "core_meaning_tr": "core_meaning",
    "nuance": "nuance",
    "encoding_type": "encoding_type",
    "encoding_cue": "encoding_cue",
}

_INSTRUCTIONS = """\
You are enriching a {target} vocabulary list for learners whose own language
is {learners}. The goal is to use each word naturally, not only to recognise
it.

For every entry in "words", fill "target", "contexts" and, for each learner
language listed in "localizations", that language's block. Keep word_id and
word exactly as given. Leave a field null (or an empty list) rather than
inventing something doubtful. Return the whole file as valid JSON, same shape.

target: what is true of the word in {target}, whoever learns it
  pattern         The grammatical pattern(s), e.g. "reluctant to do sth".
  collocations    2-5 common collocations, e.g. ["make a decision"].
  register        "formal", "informal", "neutral", "technical"... or null.
  related         Up to 3 {{"word": ..., "relation": "synonym|antonym|contrast|
                  family|confusable"}}, words of {target}.
  depth_hint      "deep" for abstract words or words with tricky usage,
                  otherwise "light".

contexts: 2-3 items, each {{"kind": ..., "text": ..., "translations": {{...}}}}
  kind            "sentence", or "situation" for a short description of a
                  moment the word fits.
  text            In {target}, with the word (as it appears, inflected or
                  not) marked like {{{{reluctant}}}}. Each context shows a
                  different use; none repeats the definition.
  translations    The text in each learner language, keyed by its code.

localizations: one block per learner language, keyed by its code
  core_meaning    The core idea in that language, one line: the idea the word
                  names, not a list of every translation.
  nuance          When this word and not a near synonym, and its tone,
                  explained in that language.
  usage_note      How it is used, explained for a speaker of that language.
  encoding_type   One of IMAGE, SCENE, ACTION, CONTRAST, RELATION, SOUND, NONE.
                  Concrete words: IMAGE, SCENE or ACTION. Abstract words:
                  CONTRAST or RELATION. Never force an image on an abstract word.
  encoding_cue    One short sentence, in that language, that gives the word an
                  extra route into memory, of the type chosen. null for NONE.
  notes           Anything else for a speaker of that language: a false
                  friend, a typical confusion. Or null.
"""


# -- the result of reading a file --------------------------------------------


@dataclass(slots=True)
class WordPlan:
    """What importing one entry would do.

    Field keys name where a value goes: ``target.pattern`` for the shared
    content, ``tr.core_meaning`` for a learner language's.
    """

    word_id: int
    word: str
    fills: dict[str, Any] = field(default_factory=dict)
    conflicts: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    #: New contexts, each with its translations by learner language.
    new_contexts: list[tuple[WordContext, dict[str, str]]] = field(default_factory=list)
    #: Translations for contexts already stored: (context id, language, text).
    new_translations: list[tuple[int, str, str]] = field(default_factory=list)
    duplicate_contexts: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def changes_anything(self) -> bool:
        return bool(self.fills or self.conflicts or self.new_contexts or self.new_translations)


@dataclass(slots=True)
class ContentImportPreview:
    path: Path
    batch: str | None
    #: Learner languages the file has content for.
    learner_languages: list[str] = field(default_factory=list)
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
    def translation_count(self) -> int:
        return sum(
            len(plan.new_translations) + sum(len(t) for _c, t in plan.new_contexts)
            for plan in self.plans
        )

    @property
    def warning_count(self) -> int:
        return sum(len(plan.warnings) for plan in self.plans)


@dataclass(frozen=True, slots=True)
class OpenBatch:
    """A batch exported and not yet imported."""

    name: str
    word_ids: tuple[int, ...]
    exported_on: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ContentImportResult:
    words: int
    fields_filled: int
    fields_replaced: int
    contexts_added: int
    translations_added: int = 0


class ContentService:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._content = ContentRepository(database)
        self._words = WordRepository(database)
        self._runtime = RuntimeRepository(database)

    # -- batches in flight ----------------------------------------------------
    #
    # Enrichment is resumable: a batch exported and not yet imported is
    # remembered, its words are left out of the next batch, and importing it
    # closes it. A batch that will never come back can be forgotten.

    def open_batches(self) -> list[OpenBatch]:
        raw = self._runtime.get(RuntimeRepository.CONTENT_BATCHES)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return [
            OpenBatch(
                name,
                tuple(int(i) for i in entry.get("words", [])),
                str(entry.get("exported_on", "")),
                entry.get("path"),
            )
            for name, entry in sorted(data.items())
            if isinstance(entry, dict)
        ]

    def _save_batches(self, batches: list[OpenBatch]) -> None:
        data = {
            b.name: {"words": list(b.word_ids), "exported_on": b.exported_on, "path": b.path}
            for b in batches
        }
        self._runtime.set(RuntimeRepository.CONTENT_BATCHES, json.dumps(data))

    def forget_batch(self, name: str) -> None:
        """Stop waiting for a batch: its words can be exported again."""
        self._save_batches([b for b in self.open_batches() if b.name != name])

    def next_batch_name(self) -> str:
        """``batch_001``, ``batch_002``…: one more than any batch seen so far."""
        seen = [b.name for b in self.open_batches()]
        seen += [
            row[0]
            for row in self._db.connection.execute(
                "SELECT source FROM word_content WHERE source LIKE 'batch_%' "
                "UNION SELECT source FROM word_localizations WHERE source LIKE 'batch_%'"
            )
            if row[0]
        ]
        numbers = [int(name[6:]) for name in seen if name[6:].isdigit()]
        return f"batch_{max(numbers, default=0) + 1:03d}"

    def batch_candidates(
        self,
        word_ids: Sequence[int],
        size: int,
        learner_languages: Sequence[str] = (),
    ) -> list[int]:
        """The next ``size`` words of ``word_ids``, in order, that still need
        content for any of ``learner_languages`` and are not in an open batch."""
        waiting = {i for b in self.open_batches() for i in b.word_ids}
        candidates = [int(i) for i in word_ids if int(i) not in waiting]
        languages: list[str | None] = list(_languages(learner_languages)) or [None]
        chosen: list[int] = []
        for start in range(0, len(candidates), 500):
            chunk = candidates[start : start + 500]
            needing: set[int] = set()
            for language in languages:
                needing |= set(self.needing_content(chunk, language))
            chosen += [i for i in chunk if i in needing]
            if len(chosen) >= size:
                break
        return chosen[:size]

    # -- reading -------------------------------------------------------------

    def teaching(self, word_id: int, learner_language: str | None = None) -> WordTeaching:
        return self._content.teaching(word_id, learner_language)

    def status(self, word_id: int, learner_language: str | None = None) -> ContentStatus:
        return self._content.statuses([word_id], learner_language)[word_id]

    def statuses(
        self, word_ids: Iterable[int], learner_language: str | None = None
    ) -> dict[int, ContentStatus]:
        return self._content.statuses(word_ids, learner_language)

    def needing_content(
        self, word_ids: Sequence[int], learner_language: str | None = None
    ) -> list[int]:
        """Of ``word_ids``, in order, those whose content is not complete."""
        statuses = self._content.statuses(word_ids, learner_language)
        return [
            word_id for word_id in word_ids if statuses.get(word_id) is not ContentStatus.COMPLETE
        ]

    # -- export a batch ------------------------------------------------------

    def build_batch(
        self, word_ids: Sequence[int], batch: str, learner_languages: Sequence[str] = ()
    ) -> dict[str, Any]:
        """The file for a batch: each word with what it has and what it lacks,
        asking for a block in each of ``learner_languages``."""
        languages = _languages(learner_languages)
        words = {w.id: w for w in self._words.get_many(word_ids)}
        entries = []
        targets: set[str] = set()
        for word_id in word_ids:
            word = words.get(int(word_id))
            if word is None:
                continue
            targets.add(word.language)
            content = self._content.content(word.id) or WordContent(word_id=word.id)
            contexts = self._content.contexts(word.id)
            stored = self._content.localizations(word.id)
            entries.append(
                {
                    "word_id": word.id,
                    "word": word.word,
                    "target_language": word.language,
                    "cefr": word.cefr_level,
                    "part_of_speech": word.part_of_speech,
                    "source_definition": word.definition,
                    "needs": _needs(content, contexts, stored, languages),
                    "target": _content_to_json(content),
                    "contexts": [
                        _context_to_json(context, self._content.context_translations(context.id))
                        for context in contexts
                    ],
                    "localizations": {
                        language: _localization_to_json(
                            stored.get(language)
                            or WordLocalization(word_id=word.id, learner_language=language)
                        )
                        for language in sorted(set(languages) | set(stored))
                    },
                }
            )
        target_names = ", ".join(sorted(language_name(code) for code in targets)) or "a"
        learner_names = ", ".join(language_name(code) for code in languages) or "not given"
        return {
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            "app_version": __version__,
            "batch": batch,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "learner_languages": languages,
            "instructions": _INSTRUCTIONS.format(target=target_names, learners=learner_names),
            "encoding_types": [e.value for e in EncodingType],
            "words": entries,
        }

    def export_batch(
        self,
        word_ids: Sequence[int],
        path: Path | str,
        batch: str,
        learner_languages: Sequence[str] = (),
    ) -> Path:
        target = Path(path)
        document = self.build_batch(word_ids, batch, learner_languages)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        batches = [b for b in self.open_batches() if b.name != batch]
        batches.append(
            OpenBatch(
                batch,
                tuple(entry["word_id"] for entry in document["words"]),
                datetime.now(UTC).date().isoformat(),
                str(target),
            )
        )
        self._save_batches(batches)
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
        version = int(root.get("schema_version", 0) or 0)
        if version > SCHEMA_VERSION:
            raise InvalidFileError(
                f"{source.name} was written for a newer LexiTrack. Update LexiTrack first."
            )
        items = root.get("words")
        if not isinstance(items, list):
            raise InvalidFileError(f"{source.name} has no \"words\" list.")

        preview = ContentImportPreview(path=source, batch=_text(root.get("batch")))
        seen: set[int] = set()
        languages: set[str] = set()
        for index, item in enumerate(items, start=1):
            try:
                plan = self._plan(item if version >= 2 else _from_v1(item), index)
            except _Rejected as problem:
                preview.rejected.append(str(problem))
                continue
            if plan.word_id in seen:
                preview.rejected.append(f"Entry {index}: word {plan.word} appears twice")
                continue
            seen.add(plan.word_id)
            preview.plans.append(plan)
            languages |= {key.split(".", 1)[0] for key in (*plan.fills, *plan.conflicts)}
            languages |= {lang for _c, t in plan.new_contexts for lang in t}
            languages |= {lang for _c, lang, _t in plan.new_translations}
        preview.learner_languages = sorted(languages - {"target"})
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
        is replaced only if it is named in ``replace_fields`` (word id, key
        such as ``target.pattern`` or ``de.core_meaning``) or ``replace_all``
        is set: nothing is overwritten silently.
        """
        allowed = set(replace_fields)
        source = preview.batch or preview.path.name
        filled = replaced = contexts = translations = words = 0
        with self._db.transaction():
            for plan in preview.plans:
                if not plan.changes_anything:
                    continue
                updates: dict[str, Any] = dict(plan.fills)
                for key, (_old, new) in plan.conflicts.items():
                    if replace_all or (plan.word_id, key) in allowed:
                        updates[key] = new
                        replaced += 1
                filled += len(plan.fills)
                self._write_fields(plan.word_id, updates, source)
                if plan.new_contexts:
                    ids = self._content.add_contexts(
                        [replace(c, source=c.source or source) for c, _t in plan.new_contexts]
                    )
                    contexts += len(ids)
                    for context_id, (_c, texts) in zip(ids, plan.new_contexts, strict=True):
                        for language, text in texts.items():
                            self._content.save_translation(context_id, language, text, source)
                            translations += 1
                for context_id, language, text in plan.new_translations:
                    self._content.save_translation(context_id, language, text, source)
                    translations += 1
                words += 1
        if preview.batch:
            # Imported: the batch is no longer waiting for its words.
            self.forget_batch(preview.batch)
        log.info(
            "Imported content from %s: %d words, %d filled, %d replaced, %d contexts, "
            "%d translations",
            preview.path.name, words, filled, replaced, contexts, translations,
        )
        return ContentImportResult(words, filled, replaced, contexts, translations)

    def _write_fields(self, word_id: int, updates: dict[str, Any], source: str) -> None:
        by_part: dict[str, dict[str, Any]] = {}
        for key, value in updates.items():
            part, name = key.split(".", 1)
            by_part.setdefault(part, {})[name] = value
        if "target" in by_part:
            current = self._content.content(word_id) or WordContent(word_id=word_id)
            self._content.save_content(replace(current, **by_part.pop("target"), source=source))
        for language, values in by_part.items():
            current = self._content.localization(word_id, language) or WordLocalization(
                word_id=word_id, learner_language=language
            )
            self._content.save_localization(replace(current, **values, source=source))

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
        target = normalize_language(_text(item.get("target_language")))
        if is_determined(target) and is_determined(stored.language) and target != stored.language:
            raise _Rejected(
                f"Entry {index}: “{stored.word}” is {language_name(stored.language)} here, "
                f"but the file says {language_name(target)}"
            )
        plan = WordPlan(word_id=word_id, word=stored.word)

        current = self._content.content(word_id) or WordContent(word_id=word_id)
        incoming = _read_target(item.get("target") or {}, plan.warnings)
        self._compare(plan, "target", current, incoming)

        raw = item.get("localizations") or {}
        if not isinstance(raw, dict):
            plan.warnings.append("localizations is not an object and was ignored")
            raw = {}
        for code, block in raw.items():
            language = _learner_language(code)
            if language is None:
                plan.warnings.append(f"“{code}” is not a language code; its block was skipped")
                continue
            localization = self._content.localization(word_id, language) or WordLocalization(
                word_id=word_id, learner_language=language
            )
            self._compare(plan, language, localization, _read_localization(block, plan.warnings))

        existing = {_fingerprint(c.text): c for c in self._content.contexts(word_id)}
        for position, raw_context in enumerate(item.get("contexts") or [], start=1):
            read = _read_context(raw_context, word_id, position, stored.word, plan.warnings)
            if read is None:
                continue
            context, texts = read
            key = _fingerprint(context.text)
            if key in existing:
                plan.duplicate_contexts += 1
                known = existing[key]
                have = self._content.context_translations(known.id) if known.id else {}
                for language, text in texts.items():
                    if language not in have:
                        plan.new_translations.append((known.id, language, text))
                continue
            placeholder = WordContext(word_id=word_id, text=context.text)
            existing[key] = placeholder
            plan.new_contexts.append((context, texts))
        return plan

    @staticmethod
    def _compare(plan: WordPlan, part: str, current: Any, incoming: dict[str, Any]) -> None:
        for name, value in incoming.items():
            existing = getattr(current, name)
            if not value or value == existing:
                continue
            key = f"{part}.{name}"
            if existing:
                plan.conflicts[key] = (existing, value)
            else:
                plan.fills[key] = value


class _Rejected(Exception):
    pass


# -- reading and writing the JSON shape ------------------------------------------


def _learner_language(value: Any) -> str | None:
    code = normalize_language(_text(value)) if _text(value) else None
    return code if is_determined(code) else None


def _languages(values: Sequence[str]) -> list[str]:
    found = []
    for value in values:
        code = _learner_language(value)
        if code and code not in found:
            found.append(code)
    return found


def _needs(
    content: WordContent,
    contexts: Sequence[WordContext],
    stored: dict[str, WordLocalization],
    languages: Sequence[str],
) -> dict[str, list[str]]:
    needs = {"target": [name for name in WordContent.FIELDS if not getattr(content, name)]}
    if len(contexts) < 2:
        needs["target"].append("contexts")
    for language in languages:
        localization = stored.get(language)
        needs[language] = [
            name
            for name in WordLocalization.FIELDS
            if localization is None or not getattr(localization, name)
        ]
    return needs


def _content_to_json(content: WordContent) -> dict[str, Any]:
    return {
        "pattern": content.pattern,
        "collocations": list(content.collocations),
        "register": content.register,
        "related": [{"word": r.word, "relation": r.relation} for r in content.related],
        "depth_hint": content.depth_hint.value if content.depth_hint else None,
    }


def _localization_to_json(localization: WordLocalization) -> dict[str, Any]:
    return {
        "core_meaning": localization.core_meaning,
        "nuance": localization.nuance,
        "usage_note": localization.usage_note,
        "encoding_type": localization.encoding_type.value if localization.encoding_type else None,
        "encoding_cue": localization.encoding_cue,
        "notes": localization.notes,
    }


def _context_to_json(context: WordContext, translations: dict[str, str]) -> dict[str, Any]:
    return {"kind": context.kind.value, "text": context.text, "translations": translations}


def _from_v1(item: Any) -> Any:
    """A schema 1 entry in schema 2's shape: its learner fields are Turkish."""
    if not isinstance(item, dict):
        return item
    content = item.get("content") or {}
    if not isinstance(content, dict):
        content = {}
    target = {k: v for k, v in content.items() if k not in _V1_LOCALIZED}
    localized = {new: content.get(old) for old, new in _V1_LOCALIZED.items()}
    contexts = []
    for raw in item.get("contexts") or []:
        if isinstance(raw, dict):
            translation = raw.get("translation_tr")
            contexts.append({
                "kind": raw.get("kind"),
                "text": raw.get("text"),
                "translations": {_V1_LANGUAGE: translation} if translation else {},
            })
        else:
            contexts.append(raw)
    return {
        "word_id": item.get("word_id"),
        "word": item.get("word"),
        "target_language": item.get("language"),
        "target": target,
        "contexts": contexts,
        "localizations": {_V1_LANGUAGE: localized} if any(localized.values()) else {},
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


def _read_target(raw: Any, warnings: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        warnings.append("target is not an object and was ignored")
        return {}
    values: dict[str, Any] = {
        "pattern": _bounded(raw.get("pattern"), "pattern", warnings),
        "register": _bounded(raw.get("register"), "register", warnings),
    }
    collocations = raw.get("collocations") or []
    if isinstance(collocations, list):
        values["collocations"] = tuple(
            text for text in (_bounded(c, "collocation", warnings) for c in collocations) if text
        )[:_MAX_ITEMS]
    else:
        warnings.append("collocations is not a list and was ignored")
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


def _read_localization(raw: Any, warnings: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        warnings.append("a localization is not an object and was ignored")
        return {}
    values: dict[str, Any] = {
        name: _bounded(raw.get(name), name, warnings)
        for name in ("core_meaning", "nuance", "usage_note", "encoding_cue", "notes")
    }
    kind = _text(raw.get("encoding_type"))
    if kind:
        try:
            values["encoding_type"] = EncodingType(kind.upper())
        except ValueError:
            warnings.append(f"encoding_type “{kind}” is not one of the allowed types")
    return {name: value for name, value in values.items() if value}


def _read_context(
    raw: Any, word_id: int, position: int, word: str, warnings: list[str]
) -> tuple[WordContext, dict[str, str]] | None:
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
    texts: dict[str, str] = {}
    raw_translations = raw.get("translations") or {}
    if isinstance(raw_translations, dict):
        for code, value in raw_translations.items():
            language = _learner_language(code)
            translated = _bounded(value, f"context {position} translation", warnings)
            if language and translated:
                texts[language] = translated
            elif translated:
                warnings.append(f"context {position}: “{code}” is not a language code")
    else:
        warnings.append(f"context {position}: translations is not an object and was ignored")
    return WordContext(word_id=word_id, text=text, kind=kind), texts


def _fingerprint(text: str) -> str:
    """Two contexts are the same if they differ only in case, spacing or marking."""
    plain = TARGET.sub(lambda m: m.group(1), text)
    return " ".join(plain.casefold().split())
