"""JSON export in the same format the JSON parser reads.

The output is meant to be opened in an editor, changed by hand and imported
again, so it follows three rules:

* **Only what a person would write.** No database ids, no timestamps, and no
  learning status — status belongs to the user's copy of LexiTrack, not to the
  word list, and importing never overwrites it.
* **Nothing empty.** A field with no value is left out rather than written as
  ``null``.
* **Round-trips.** Importing an exported file reproduces the same words, in
  the same order, with the same details and contexts. A test holds this in
  place.

Each word is written as the word model has it: ``word``, ``length`` (its
letters, for reading; an import works it out again), ``part_of_speech``,
``cefr_level``, ``definition`` and ``contexts``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.errors import ExportError
from ..models.language import UNDETERMINED, is_determined
from ..repositories.word_repository import StoredWord

log = logging.getLogger(__name__)


def build_json_document(
    words: Sequence[StoredWord],
    name: str | None = None,
    language: str | None = None,
    description: str | None = None,
    contexts: Mapping[int, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Return the exported structure without writing it (useful for tests)."""
    document: dict[str, Any] = {}
    if name:
        document["name"] = name
    list_language = language if is_determined(language) else None
    if list_language:
        document["language"] = list_language
    if description:
        document["description"] = description

    items: list[Any] = []
    for word in words:
        item: dict[str, Any] = {"word": word.word, "length": word.length}
        for field in ("part_of_speech", "cefr_level", "definition"):
            value = getattr(word, field)
            if value:
                item[field] = value
        sentences = list((contexts or {}).get(word.id, ()))
        if sentences:
            item["contexts"] = sentences
        # A mixed-language list keeps each word's own language, or a
        # round-trip would lose it.
        if word.language not in (list_language, UNDETERMINED, None):
            item["language"] = word.language
        items.append(item)

    document["words"] = items
    return document


def export_words_json(
    words: Sequence[StoredWord],
    path: Path,
    name: str | None = None,
    language: str | None = None,
    description: str | None = None,
    contexts: Mapping[int, Sequence[str]] | None = None,
) -> Path:
    """Write ``words`` to ``path`` as a LexiTrack JSON word list."""
    document = build_json_document(words, name, language, description, contexts)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        log.exception("JSON export to %s failed", path)
        raise ExportError(f"The JSON file could not be written to {path}.") from exc
    log.info("Exported %d words to %s", len(words), path)
    return path
