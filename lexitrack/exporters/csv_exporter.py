"""CSV export of vocabulary.

CSV is the interchange format: it opens in Excel, imports into Anki and is easy
to inspect when debugging. It is written with a UTF-8 BOM because Excel on
Windows otherwise misreads accented characters.

The word comes first, then the chosen columns (exporters/sheet.py). The
dictionary's definition is three cells — definition, example, note — and the
sources always follow the dictionary's fields. A cell holding several items
puts collocations on one line, separated by semicolons, and examples one to
a line, their translations on the same lines of their own cell.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from pathlib import Path

from ..core.errors import ExportError
from ..models.language import language_name
from ..repositories.word_repository import StoredWord
from .sheet import ExportColumn, WordSheet

log = logging.getLogger(__name__)

#: The header of an export with the default columns.
COLUMNS = ("Word", "Part of Speech", "CEFR", "Definition", "Example", "Note", "Sources")


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


def header(sheet: WordSheet) -> list[str]:
    language = f" ({language_name(sheet.learner_language)})" if sheet.learner_language else ""
    names = ["Word"]
    if sheet.has(ExportColumn.PART_OF_SPEECH):
        names.append("Part of Speech")
    if sheet.has(ExportColumn.CEFR):
        names.append("CEFR")
    if sheet.has(ExportColumn.DEFINITION):
        names += ["Definition", "Example", "Note"]
    names.append("Sources")
    for column, titles in (
        (ExportColumn.MEANING, [f"Meaning{language}"]),
        (ExportColumn.NUANCE, [f"Nuance{language}", f"Usage Note{language}"]),
        (ExportColumn.PATTERN, ["Pattern"]),
        (ExportColumn.COLLOCATIONS, ["Collocations"]),
        (ExportColumn.EXAMPLES, ["Examples"]),
        (ExportColumn.TRANSLATIONS, [f"Example Translations{language}"]),
    ):
        if sheet.has(column):
            names += titles
    return names


def row(word: StoredWord, sheet: WordSheet) -> list[str]:
    cells = [word.word]
    if sheet.has(ExportColumn.PART_OF_SPEECH):
        cells.append(word.part_of_speech or "")
    if sheet.has(ExportColumn.CEFR):
        cells.append(word.cefr_level or "")
    if sheet.has(ExportColumn.DEFINITION):
        cells += [word.definition or "", word.example or "", word.note or ""]
    cells.append(", ".join(word.sources))
    if not sheet.teaches:
        return cells
    teaching = sheet.teaching_for(word.id)
    content, local = teaching.content, teaching.localization
    if sheet.has(ExportColumn.MEANING):
        cells.append((local.core_meaning if local else None) or "")
    if sheet.has(ExportColumn.NUANCE):
        cells += [local.nuance or "", local.usage_note or ""] if local else ["", ""]
    if sheet.has(ExportColumn.PATTERN):
        cells.append((content.pattern if content else None) or "")
    if sheet.has(ExportColumn.COLLOCATIONS):
        cells.append("; ".join(content.collocations) if content else "")
    if sheet.has(ExportColumn.EXAMPLES):
        cells.append("\n".join(context.plain for context in teaching.contexts))
    if sheet.has(ExportColumn.TRANSLATIONS):
        # Line for line with the examples, so the two cells read side by side.
        lines = [teaching.translation(context) or "" for context in teaching.contexts]
        cells.append("\n".join(lines) if any(lines) else "")
    return cells
