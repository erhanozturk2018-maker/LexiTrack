"""CSV export of vocabulary.

CSV is the interchange format: it opens in Excel, imports into Anki and is easy
to inspect when debugging. It is written with a UTF-8 BOM because Excel on
Windows otherwise misreads accented characters.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from pathlib import Path

from ..core.errors import ExportError
from ..repositories.word_repository import StoredWord

log = logging.getLogger(__name__)

COLUMNS = ("Word", "Part of Speech", "CEFR", "Definition", "Example", "Note", "Sources")


def export_words_csv(words: Sequence[StoredWord], path: Path) -> Path:
    """Write ``words`` to ``path`` as CSV and return the path.

    Missing metadata is written as an empty cell rather than a placeholder, so
    the file stays usable by other programs.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for word in words:
                writer.writerow(
                    [
                        word.word,
                        word.part_of_speech or "",
                        word.cefr_level or "",
                        word.definition or "",
                        word.example or "",
                        word.note or "",
                        ", ".join(word.sources),
                    ]
                )
    except OSError as exc:
        log.exception("CSV export to %s failed", path)
        raise ExportError(f"The CSV file could not be written to {path}.") from exc

    log.info("Exported %d words to %s", len(words), path)
    return path
