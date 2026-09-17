"""JSON export in the same format the JSON parser reads.

The output is meant to be opened in an editor, changed by hand and imported
again, so it follows three rules:

* **Only what a person would write.** No database ids, no timestamps, and no
  learning status — status belongs to the user's copy of LexiTrack, not to the
  word list, and importing never overwrites it.
* **Nothing empty.** A field with no value is left out rather than written as
  ``null``, so an exported list of bare words looks like a hand-written one.
* **Round-trips.** Importing an exported file reproduces the same words, in
  the same order, with the same details. A test holds this in place.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
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
        item: dict[str, str] = {"word": word.word}
        for field in ("part_of_speech", "cefr_level", "definition", "example", "note"):
            value = getattr(word, field)
            if value:
                item[field] = value
        # A mixed-language list keeps each word's own language, or a
        # round-trip would lose it.
        if word.language not in (list_language, UNDETERMINED, None):
            item["language"] = word.language
        items.append(item["word"] if len(item) == 1 else item)

    document["words"] = items
    return document


def export_words_json(
    words: Sequence[StoredWord],
    path: Path,
    name: str | None = None,
    language: str | None = None,
    description: str | None = None,
) -> Path:
    """Write ``words`` to ``path`` as a LexiTrack JSON word list."""
    document = build_json_document(words, name, language, description)
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
