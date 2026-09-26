"""CSV export of vocabulary.

CSV is the interchange format: it opens in Excel, imports into Anki and is easy
to inspect when debugging. It is written with a UTF-8 BOM because Excel on
Windows otherwise misreads accented characters.

The word comes first, then the chosen columns (exporters/sheet.py), then the
sources. The contexts are one cell, one sentence to a line.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from pathlib import Path

from ..core.errors import ExportError
from ..repositories.word_repository import StoredWord
from .sheet import ExportColumn, WordSheet

log = logging.getLogger(__name__)

#: The header of an export with the default columns.
COLUMNS = ("Word", "Part of Speech", "CEFR", "Definition", "Sources")

_TITLES = {
    ExportColumn.PART_OF_SPEECH: "Part of Speech",
    ExportColumn.CEFR: "CEFR",
    ExportColumn.LENGTH: "Length",
    ExportColumn.DEFINITION: "Definition",
    ExportColumn.CONTEXTS: "Contexts",
}


def export_words_csv(
    words: Sequence[StoredWord], path: Path, sheet: WordSheet | None = None
) -> Path:
    """Write ``words`` to ``path`` as CSV and return the path.

    Missing metadata is written as an empty cell rather than a placeholder, so
    the file stays usable by other programs.
    """
    sheet = sheet or WordSheet()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header(sheet))
            for word in words:
                writer.writerow(row(word, sheet))
    except OSError as exc:
        log.exception("CSV export to %s failed", path)
        raise ExportError(f"The CSV file could not be written to {path}.") from exc

    log.info("Exported %d words to %s", len(words), path)
    return path


def _shown(sheet: WordSheet) -> list[ExportColumn]:
    return [column for column in ExportColumn if sheet.has(column)]


def header(sheet: WordSheet) -> list[str]:
    return ["Word", *(_TITLES[column] for column in _shown(sheet)), "Sources"]


def row(word: StoredWord, sheet: WordSheet) -> list[str]:
    cells = [word.word]
    for column in _shown(sheet):
        if column is ExportColumn.PART_OF_SPEECH:
            cells.append(word.part_of_speech or "")
        elif column is ExportColumn.CEFR:
            cells.append(word.cefr_level or "")
        elif column is ExportColumn.LENGTH:
            cells.append(str(word.length))
        elif column is ExportColumn.DEFINITION:
            cells.append(word.definition or "")
        elif column is ExportColumn.CONTEXTS:
            cells.append("\n".join(sheet.contexts_for(word.id)))
    cells.append(", ".join(word.sources))
    return cells
