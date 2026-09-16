"""The PDF wrapper that parsers receive.

Parsers never open files themselves. They are handed a ``Document``, which
gives them plain text, layout-aware lines and document metadata, and which has
already answered the awkward questions (is this really a PDF? is it scanned?)
before any parser sees it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

import pymupdf

from ..core.errors import DocumentError, EmptyDocumentError, ImageOnlyDocumentError

log = logging.getLogger(__name__)

#: Below this many characters per page the PDF is treated as scanned images.
_MIN_CHARS_PER_PAGE = 20
#: Vertical tolerance, in points, for deciding two spans share a line.
_LINE_TOLERANCE = 3.0


@dataclass(slots=True)
class TextSpan:
    """A run of text with its position on the page."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float = 0.0
    font: str = ""

    @property
    def is_bold(self) -> bool:
        return "bold" in self.font.lower() or "black" in self.font.lower()


@dataclass(slots=True)
class TextLine:
    """Spans that sit on the same baseline, ordered left to right.

    Oxford's word lists are laid out in columns, and a naive text dump
    interleaves them. Rebuilding lines from span geometry is what keeps
    ``abandon v. B2`` together instead of merging two columns into nonsense.
    """

    spans: list[TextSpan] = field(default_factory=list)
    page_number: int = 0

    @property
    def text(self) -> str:
        return " ".join(span.text for span in self.spans).strip()

    @property
    def y(self) -> float:
        return min(span.y0 for span in self.spans) if self.spans else 0.0

    @property
    def x0(self) -> float:
        return min(span.x0 for span in self.spans) if self.spans else 0.0


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
        self._line_cache: dict[int, list[TextLine]] = {}

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

        sample = min(self.page_count, 5)
        characters = sum(len(self.page_text(i).strip()) for i in range(sample))
        if characters < _MIN_CHARS_PER_PAGE * sample:
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

    @property
    def metadata(self) -> dict[str, str]:
        return {k: v for k, v in (self._pdf.metadata or {}).items() if v}

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

    # -- layout ------------------------------------------------------------

    def page_lines(self, page_number: int) -> list[TextLine]:
        """Return the layout-aware lines of one page.

        Spans are grouped by baseline and then sorted by column (left edge) and
        by vertical position, which reconstructs multi-column reading order.
        """
        if page_number in self._line_cache:
            return self._line_cache[page_number]

        spans = self._page_spans(page_number)
        lines = _group_spans_into_lines(spans, page_number)
        self._line_cache[page_number] = lines
        return lines

    def iter_lines(self, max_pages: int | None = None) -> Iterator[TextLine]:
        """Yield every layout-aware line in the document."""
        limit = self.page_count if max_pages is None else min(max_pages, self.page_count)
        for page_number in range(limit):
            yield from self.page_lines(page_number)

    def _page_spans(self, page_number: int) -> list[TextSpan]:
        try:
            raw = self._pdf[page_number].get_text("dict")
        except Exception as exc:  # pragma: no cover - malformed page
            log.warning("Layout of page %s could not be read: %s", page_number, exc)
            return []

        spans: list[TextSpan] = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:  # 0 == text, 1 == image
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                    spans.append(
                        TextSpan(
                            text=text,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                            size=span.get("size", 0.0),
                            font=span.get("font", ""),
                        )
                    )
        return spans


def _group_spans_into_lines(spans: Sequence[TextSpan], page_number: int) -> list[TextLine]:
    """Group ``spans`` into visual lines, respecting columns."""
    if not spans:
        return []

    columns = _detect_columns(spans)
    lines: list[TextLine] = []

    for column_spans in columns:
        buckets: list[TextLine] = []
        for span in sorted(column_spans, key=lambda s: (s.y0, s.x0)):
            for bucket in reversed(buckets):
                if abs(bucket.y - span.y0) <= _LINE_TOLERANCE:
                    bucket.spans.append(span)
                    break
            else:
                buckets.append(TextLine(spans=[span], page_number=page_number))
        for bucket in buckets:
            bucket.spans.sort(key=lambda s: s.x0)
        lines.extend(buckets)

    return lines


def _detect_columns(spans: Sequence[TextSpan]) -> list[list[TextSpan]]:
    """Split ``spans`` into columns, left to right.

    A vertical gap that no span crosses and that is wider than a typical word
    gap is treated as a column boundary. Documents with a single column come
    back as one group, which is the common case and costs almost nothing.
    """
    page_left = min(span.x0 for span in spans)
    page_right = max(span.x1 for span in spans)
    width = page_right - page_left
    if width <= 0:
        return [list(spans)]

    # Occupancy histogram across the page width, one bucket per 4 points.
    bucket_width = 4.0
    bucket_count = max(int(width / bucket_width) + 1, 1)
    occupied = [False] * bucket_count
    for span in spans:
        start = int((span.x0 - page_left) / bucket_width)
        end = int((span.x1 - page_left) / bucket_width)
        for index in range(max(start, 0), min(end + 1, bucket_count)):
            occupied[index] = True

    # A gutter must be at least 5% of the page wide to count as one.
    min_gap_buckets = max(int(width * 0.05 / bucket_width), 3)
    boundaries: list[float] = []
    run_start: int | None = None
    for index, is_occupied in enumerate(occupied):
        if not is_occupied:
            run_start = index if run_start is None else run_start
            continue
        if run_start is not None:
            if index - run_start >= min_gap_buckets and run_start > 0:
                boundaries.append(page_left + (run_start + index) / 2 * bucket_width)
            run_start = None

    if not boundaries:
        return [list(spans)]

    groups: list[list[TextSpan]] = [[] for _ in range(len(boundaries) + 1)]
    for span in spans:
        centre = (span.x0 + span.x1) / 2
        index = sum(1 for boundary in boundaries if centre > boundary)
        groups[index].append(span)

    return [group for group in groups if group]
