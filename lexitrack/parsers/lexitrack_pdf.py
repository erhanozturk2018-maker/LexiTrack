"""Parser for PDFs that LexiTrack itself exported.

Every exported PDF, from the desktop or the phone, carries its words as an
embedded file, ``lexitrack-words.json``, in the JSON word-list format with
``"format": "lexitrack-words"``. This parser reads that file and nothing
else: never the page text, which is laid out for printing and may leave
fields out. So a sheet imports back with every word's part of speech, level,
definition and contexts, whichever columns it shows.

A PDF without that file — or with one that is not a LexiTrack word list — is
not this parser's, and goes on to the Oxford and generic parsers as before.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..core.errors import InvalidFileError
from ..models.word_entry import WordEntry
from .base import DocumentParser, ListMetadata, ProgressCallback
from .document import Document
from .json_document import JsonDocument
from .json_parser import JsonParser

log = logging.getLogger(__name__)

#: The name of the embedded file, the same on the desktop and the phone.
WORD_LIST_FILE = "lexitrack-words.json"
#: The ``format`` the embedded file states.
WORD_LIST_FORMAT = "lexitrack-words"


class LexiTrackPdfParser(DocumentParser):
    """Reads the word list a LexiTrack PDF carries inside it."""

    key = "lexitrack-pdf"
    name = "LexiTrack PDF"
    description = (
        "A PDF exported by LexiTrack. Imports the word list embedded in it, "
        "with every detail and context."
    )
    #: Above Oxford: a LexiTrack sheet of Oxford words is still a LexiTrack sheet.
    priority = 300

    def __init__(self) -> None:
        self._json = JsonParser()

    def can_parse(self, document: Document) -> bool:
        return _word_list(document) is not None

    # -- metadata ----------------------------------------------------------

    def source_key(self, document: Document) -> str:
        label = self._json.source_name(self._inner(document))
        slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
        return f"{self.key}:{slug or 'file'}"

    def source_name(self, document: Document) -> str:
        return self._json.source_name(self._inner(document))

    def list_metadata(self, document: Document) -> ListMetadata:
        return self._json.list_metadata(self._inner(document))

    # -- parsing -----------------------------------------------------------

    def parse(
        self, document: Document, progress: ProgressCallback | None = None
    ) -> list[WordEntry]:
        inner = self._inner(document)
        entries = self._json.parse(inner, progress)
        document.warnings.extend(inner.warnings)
        source_key = self.source_key(document)
        for entry in entries:
            entry.source_id = source_key
        log.info("LexiTrack PDF parser read %d entries from %s", len(entries), document.path.name)
        return entries

    def _inner(self, document: Document) -> JsonDocument:
        """The embedded word list as a JSON document named after the PDF."""
        data = _word_list(document)
        if data is None:
            # Only when the parser was chosen by hand for another PDF.
            raise InvalidFileError(
                f"{document.path.name} was not exported by LexiTrack: it carries no "
                "LexiTrack word list. Choose another parser to read its text."
            )
        return JsonDocument(data, document.path)


def _word_list(document: Document) -> dict[str, Any] | None:
    """The embedded LexiTrack word list, or ``None`` when there is none."""
    content = document.embedded_file(WORD_LIST_FILE)
    if content is None:
        return None
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("%s in %s is not valid JSON: %s", WORD_LIST_FILE, document.path.name, exc)
        return None
    if not isinstance(data, dict) or data.get("format") != WORD_LIST_FORMAT:
        return None
    return data
