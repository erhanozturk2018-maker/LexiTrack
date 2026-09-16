"""The PDF wrapper that parsers receive.

Parsers never open files themselves. They are handed a ``Document``, which
gives them the text and the document's name, and which has already answered
the awkward questions (is this really a PDF? is it scanned?) before any parser
sees it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

import pymupdf

from ..core.errors import DocumentError, EmptyDocumentError, ImageOnlyDocumentError

log = logging.getLogger(__name__)

#: How many leading pages are inspected when deciding whether a PDF has text.
_VALIDATION_PAGE_SAMPLE = 10


class Document:
    """An opened PDF, ready to be parsed.

    Use it as a context manager so the underlying file handle is always
    released::

        with Document.open(path) as document:
            entries = parser.parse(document)
    """

    def __init__(self, pdf: pymupdf.Document, path: Path) -> None:
        self._pdf = pdf
        self.path = path
        self._text_cache: dict[int, str] = {}

    # -- construction ------------------------------------------------------

    @classmethod
    def open(cls, path: Path | str) -> Document:
        """Open ``path`` as a PDF.

        Raises:
            DocumentError: the file is missing, not a PDF, or corrupt.
            EmptyDocumentError: the PDF has no pages.
            ImageOnlyDocumentError: the PDF has pages but no extractable text.
        """
        path = Path(path)
        if not path.exists():
            raise DocumentError(f"The file {path.name} could not be found.")
        if not path.is_file():
            raise DocumentError(f"{path.name} is not a file.")

        try:
            pdf = pymupdf.open(path)
        except Exception as exc:
            log.warning("Failed to open %s: %s", path, exc)
            raise DocumentError(
                f"{path.name} could not be opened. It may be corrupt or not a PDF."
            ) from exc

        if pdf.needs_pass:
            pdf.close()
            raise DocumentError(f"{path.name} is password protected.")

        document = cls(pdf, path)
        try:
            document._validate()
        except Exception:
            document.close()
            raise
        return document

    def _validate(self) -> None:
        if self.page_count == 0:
            raise EmptyDocumentError(f"{self.path.name} contains no pages.")

        # A scan is distinguished by having *no* extractable text, not by
        # having little of it. Rejecting sparse documents instead would refuse
        # a perfectly good one-page word list. A scan carrying nothing but
        # page numbers still gets through here, and is then reported by the
        # import service as producing no words — which is equally clear and
        # avoids guessing at a threshold.
        sample = min(self.page_count, _VALIDATION_PAGE_SAMPLE)
        has_text = any(
            any(character.isalpha() for character in self.page_text(index))
            for index in range(sample)
        )
        if not has_text:
            raise ImageOnlyDocumentError(
                f"No text could be extracted from {self.path.name}. It looks like a "
                "scanned document, and LexiTrack does not support OCR yet."
            )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if not self._pdf.is_closed:
            self._pdf.close()

    def __enter__(self) -> Document:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- metadata ----------------------------------------------------------

    @property
    def page_count(self) -> int:
        return self._pdf.page_count

    @property
    def name(self) -> str:
        """A human readable document name: the PDF title, else the file stem."""
        title = (self._pdf.metadata or {}).get("title") or ""
        title = re.sub(r"\s+", " ", title).strip()
        if title and len(title) > 2 and not title.lower().endswith(".pdf"):
            return title
        return self.path.stem.replace("_", " ").replace("-", " ").strip()

    # -- text --------------------------------------------------------------

    def page_text(self, page_number: int) -> str:
        """Return the plain text of one zero-based page."""
        if page_number not in self._text_cache:
            try:
                self._text_cache[page_number] = self._pdf[page_number].get_text("text")
            except Exception as exc:  # pragma: no cover - malformed page
                log.warning("Page %s of %s could not be read: %s", page_number, self.path, exc)
                self._text_cache[page_number] = ""
        return self._text_cache[page_number]

    def text(self, max_pages: int | None = None) -> str:
        """Return the plain text of the whole document, or its first pages."""
        limit = self.page_count if max_pages is None else min(max_pages, self.page_count)
        return "\n".join(self.page_text(i) for i in range(limit))

    def iter_page_text(self) -> Iterator[tuple[int, str]]:
        """Yield ``(page_number, text)`` for every page, one at a time."""
        for page_number in range(self.page_count):
            yield page_number, self.page_text(page_number)
