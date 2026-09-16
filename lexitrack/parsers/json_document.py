"""JSON files as documents, and choosing between PDF and JSON by file.

A JSON vocabulary file is opened and syntax-checked here, before any parser is
chosen, in the same way :class:`~lexitrack.parsers.document.Document` checks a
PDF. Whether the JSON has the *shape* of a word list is the parser's concern.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import TracebackType
from typing import Any

from ..core.errors import DocumentError, InvalidFileError
from .document import Document

log = logging.getLogger(__name__)

#: A JSON file larger than this is almost certainly not a word list.
MAX_JSON_BYTES = 50 * 1024 * 1024

SUPPORTED_EXTENSIONS = (".pdf", ".json")


class JsonDocument:
    """A parsed JSON file, ready for a parser."""

    def __init__(self, data: Any, path: Path) -> None:
        self.data = data
        self.path = path
        #: Non-fatal problems found while parsing, shown in the import preview.
        self.warnings: list[str] = []

    @classmethod
    def open(cls, path: Path | str) -> JsonDocument:
        path = Path(path)
        if not path.is_file():
            raise DocumentError(f"The file {path.name} could not be found.")
        try:
            if path.stat().st_size > MAX_JSON_BYTES:
                raise InvalidFileError(f"{path.name} is too large to be a vocabulary file.")
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidFileError(
                f"{path.name} is not a UTF-8 text file, so it cannot be read as JSON."
            ) from exc
        except OSError as exc:
            raise DocumentError(f"{path.name} could not be opened.") from exc

        if not text.strip():
            raise InvalidFileError(f"{path.name} is empty.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidFileError(
                f"{path.name} is not valid JSON: {exc.msg} "
                f"(line {exc.lineno}, column {exc.colno})."
            ) from exc
        return cls(data, path)

    # Parsers ask a document for these, whatever its type.

    @property
    def name(self) -> str:
        return _pretty_stem(self.path)

    @property
    def page_count(self) -> int:
        return 1

    def close(self) -> None:
        """Nothing to release; present so all documents share a lifecycle."""

    def __enter__(self) -> JsonDocument:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


#: Anything a parser can be handed.
AnyDocument = Document | JsonDocument


def open_document(path: Path | str) -> AnyDocument:
    """Open ``path`` as whichever document type its content is.

    The extension decides for ``.pdf`` and ``.json``. Anything else is sniffed
    from its first bytes, so a correctly formed file with the wrong extension
    still imports.
    """
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix == ".json":
        return JsonDocument.open(path)
    if suffix == ".pdf":
        return Document.open(path)

    if not path.is_file():
        raise DocumentError(f"The file {path.name} could not be found.")
    try:
        with path.open("rb") as handle:
            head = handle.read(1024)
    except OSError as exc:
        raise DocumentError(f"{path.name} could not be opened.") from exc

    if head.startswith(b"%PDF"):
        return Document.open(path)
    if head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"["):
        return JsonDocument.open(path)
    raise DocumentError(
        f"{path.name} is not a supported file. LexiTrack can import PDF and JSON files."
    )


def _pretty_stem(path: Path) -> str:
    """``german_a1.json`` -> ``German A1``; mixed-case stems are left alone."""
    stem = re.sub(r"[_\-]+", " ", path.stem).strip()
    stem = re.sub(r"\s+", " ", stem)
    if stem and stem == stem.lower():
        stem = " ".join(part[:1].upper() + part[1:] for part in stem.split(" "))
    return stem or path.name
