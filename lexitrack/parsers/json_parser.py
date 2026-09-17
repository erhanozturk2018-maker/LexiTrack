"""Parser for LexiTrack's JSON vocabulary format.

The format is meant to be written by hand, so it asks for as little as
possible. The whole of a valid file can be::

    {"words": ["Haus", "gehen", "kommen"]}

and everything else is optional::

    {
      "name": "German A1",
      "language": "de",
      "description": "German A1 vocabulary",
      "source": "Goethe-Institut A1 word list",
      "words": [
        "Haus",
        {"word": "gehen", "part_of_speech": "verb", "definition": "to go",
         "example": "Wir gehen nach Hause.", "cefr_level": "A1"}
      ]
    }

A bare array of words is accepted as a shorthand for an object with only
``words``. See ``docs/formats/json-import-export.md`` for the full reference.

Two kinds of problem are treated differently:

* **The file has the wrong shape** — ``words`` is missing or not a list, an
  item is neither text nor an object, a field has the wrong type. The import
  stops and the message says where, because silently importing half of a
  broken file is worse than importing none of it.
* **An item is well-formed but is not a usable word** — an empty string, or
  text such as ``"42"`` that normalization rejects. The item is skipped and a
  warning is shown in the import preview, so one stray entry does not block an
  otherwise good list.

The parser never invents metadata. A file without ``source`` has no stated
source, and is recorded as coming from its file name; a file without
``language`` has an undetermined language.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..core.errors import InvalidFileError, NoWordsFoundError
from ..models.language import UNDETERMINED, is_determined, normalize_language
from ..models.word_entry import CEFR_ORDER, WordEntry
from ..normalization.word_normalizer import display_form, normalize_word
from .base import DocumentParser, ListMetadata, ProgressCallback
from .json_document import JsonDocument

log = logging.getLogger(__name__)

#: Word-object fields and the aliases accepted for them.
WORD_FIELDS: dict[str, tuple[str, ...]] = {
    "part_of_speech": ("part_of_speech", "pos"),
    "cefr_level": ("cefr_level", "cefr", "level"),
    "definition": ("definition", "meaning", "translation"),
    "example": ("example",),
    "note": ("note", "notes"),
    "language": ("language",),
}

_MAX_REPORTED_PROBLEMS = 3
_MAX_TEXT_LENGTH = 2_000


class JsonParser(DocumentParser):
    key = "json"
    name = "JSON word list"
    description = "LexiTrack's JSON format: a list of words with optional details."
    priority = 200
    document_types = (JsonDocument,)

    def can_parse(self, document: JsonDocument) -> bool:  # type: ignore[override]
        # Structure is validated in parse(), where problems can be explained.
        # Rejecting here would only produce a vaguer "no parser" message.
        return isinstance(document, JsonDocument)

    # -- metadata ----------------------------------------------------------

    def source_key(self, document: JsonDocument) -> str:  # type: ignore[override]
        root = _root(document)
        label = _optional_text(root, "source") or document.path.name
        slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
        return f"json:{slug or 'file'}"

    def source_name(self, document: JsonDocument) -> str:  # type: ignore[override]
        # Provenance is what the file claims, or else the file itself. Never a
        # guess: a file called oxford.json is not thereby "Oxford 3000".
        return _optional_text(_root(document), "source") or document.path.name

    def list_metadata(self, document: JsonDocument) -> ListMetadata:  # type: ignore[override]
        root = _root(document)
        return ListMetadata(
            name=_optional_text(root, "name") or document.name,
            language=_file_language(root),
            description=_optional_text(root, "description"),
        )

    # -- parsing -----------------------------------------------------------

    def parse(  # type: ignore[override]
        self, document: JsonDocument, progress: ProgressCallback | None = None
    ) -> list[WordEntry]:
        root = _root(document)
        file_language = _file_language(root)
        source_key = self.source_key(document)

        if "words" not in root:
            raise InvalidFileError(
                f"{document.path.name} has no “words” list, so there is "
                "nothing to import."
            )
        items = root["words"]
        if not isinstance(items, list):
            raise InvalidFileError(
                f"In {document.path.name}, “words” must be a list, "
                f"not {_type_name(items)}."
            )
        if not items:
            raise NoWordsFoundError(f"{document.path.name} contains no words.")

        problems: list[str] = []
        skipped: list[str] = []
        entries: list[WordEntry] = []

        for index, item in enumerate(items, start=1):
            try:
                entry = _parse_item(item, index, source_key, file_language)
            except _ItemProblem as problem:
                problems.append(str(problem))
                continue
            except _SkippedItem as skip:
                skipped.append(str(skip))
                continue
            entries.append(entry)

        if problems:
            shown = "\n".join(f"• {p}" for p in problems[:_MAX_REPORTED_PROBLEMS])
            more = len(problems) - _MAX_REPORTED_PROBLEMS
            suffix = f"\n• …and {more} more." if more > 0 else ""
            raise InvalidFileError(
                f"{document.path.name} could not be imported:\n{shown}{suffix}"
            )

        if skipped:
            detail = "; ".join(skipped[:_MAX_REPORTED_PROBLEMS])
            more = len(skipped) - _MAX_REPORTED_PROBLEMS
            reason = (
                "1 item was skipped because it is not a word"
                if len(skipped) == 1
                else f"{len(skipped)} items were skipped because they are not words"
            )
            document.warnings.append(
                f"{reason}: {detail}" + (f", and {more} more." if more > 0 else ".")
            )

        if not entries:
            raise NoWordsFoundError(
                f"None of the entries in {document.path.name} could be used as words."
            )

        if progress is not None:
            progress(1, 1)
        log.info("JSON parser read %d entries from %s", len(entries), document.path.name)
        return entries


# -- helpers ---------------------------------------------------------------


class _ItemProblem(Exception):
    """An item has the wrong shape. Stops the import."""


class _SkippedItem(Exception):
    """An item is well-formed but not a usable word. Skipped with a warning."""


def _root(document: JsonDocument) -> dict[str, Any]:
    """The top-level object, accepting a bare array as shorthand."""
    data = document.data
    if isinstance(data, list):
        return {"words": data}
    if not isinstance(data, dict):
        raise InvalidFileError(
            f"{document.path.name} must contain a JSON object with a "
            f"“words” list, not {_type_name(data)}."
        )
    for key in ("name", "language", "description", "source"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise InvalidFileError(
                f"In {document.path.name}, “{key}” must be text, "
                f"not {_type_name(value)}."
            )
    language = data.get("language")
    if isinstance(language, str) and language.strip() and normalize_language(language) is None:
        raise InvalidFileError(
            f"In {document.path.name}, “{language}” is not a language "
            "LexiTrack recognises. Use a code such as “en” or “de”."
        )
    return data


def _file_language(root: dict[str, Any]) -> str | None:
    code = normalize_language(root.get("language"))
    return code if is_determined(code) else None


def _optional_text(root: dict[str, Any], key: str) -> str | None:
    value = root.get(key)
    if isinstance(value, str):
        clean = " ".join(value.split())
        return clean or None
    return None


def _parse_item(
    item: Any, index: int, source_key: str, file_language: str | None
) -> WordEntry:
    if isinstance(item, str):
        return _entry(item, index, source_key, file_language, {})
    if isinstance(item, dict):
        if "word" not in item:
            raise _ItemProblem(f"Word {index} has no “word” field.")
        text = item["word"]
        if not isinstance(text, str):
            raise _ItemProblem(
                f"Word {index}: “word” must be text, not {_type_name(text)}."
            )
        return _entry(text, index, source_key, file_language, item)
    raise _ItemProblem(
        f"Word {index} must be text or an object with a “word” field, "
        f"not {_type_name(item)}."
    )


def _entry(
    text: str,
    index: int,
    source_key: str,
    file_language: str | None,
    fields: dict[str, Any],
) -> WordEntry:
    normalized = normalize_word(text)
    if not normalized:
        shown = text.strip()
        raise _SkippedItem(f"#{index} “{shown}”" if shown else f"#{index} (empty)")

    values: dict[str, str | None] = {}
    for field_name, aliases in WORD_FIELDS.items():
        values[field_name] = None
        for alias in aliases:
            if alias not in fields or fields[alias] is None:
                continue
            value = fields[alias]
            if not isinstance(value, str):
                raise _ItemProblem(
                    f"Word {index} (“{text.strip()}”): “{alias}” must be "
                    f"text, not {_type_name(value)}."
                )
            clean = value.strip()[:_MAX_TEXT_LENGTH]
            values[field_name] = clean or None
            break

    language = file_language
    if values["language"]:
        code = normalize_language(values["language"])
        if code is None:
            raise _ItemProblem(
                f"Word {index} (“{text.strip()}”): “{values['language']}” "
                "is not a recognised language."
            )
        language = code if code != UNDETERMINED else file_language

    level = values["cefr_level"]
    if level and level.upper() in CEFR_ORDER:
        level = level.upper()

    return WordEntry(
        word=display_form(text),
        normalized_word=normalized,
        source_id=source_key,
        part_of_speech=values["part_of_speech"],
        cefr_level=level,
        definition=values["definition"],
        example=values["example"],
        metadata={"note": values["note"]} if values["note"] else {},
        language=language,
    )


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__
