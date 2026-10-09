"""PDF export of vocabulary.

The output is LexiTrack's own study sheet, set like a page of a dictionary: a
quiet top (*LexiTrack · Word list* in small spaced capitals, the list's name,
how many words and when, and — with the Status column — how many are Known,
Unknown and Not reviewed), then the words. It takes ``StoredWord`` objects
and knows nothing about which parser produced them, so a list built from a
novel exports exactly as well as one built from Oxford — the fields it
cannot fill simply show a dash.

Without contexts (exporters/sheet.py) the sheet is a table: word, part of
speech, level, length, status and definition, as chosen. It has no vertical
lines and no shading — a rule under the header (repeated on every page) and a
hairline between rows are enough to follow a line across the page. With
contexts it becomes a list of entries, one per word: the word and its type
on a line with the status at the right, then the definition, then the
contexts in italic with the word in semibold. An entry never splits across
pages. Exported in CEFR order, either form starts each level with a heading,
and the rows under it do not repeat the level.

A status is a coloured dot and its name — green Known, amber Unknown, grey
Not reviewed — so a black-and-white print still says it.

The text is set in Source Serif 4 (SIL Open Font License), shipped in
``exporters/fonts`` and embedded in the file, so a sheet looks the same
everywhere — the phone's PDFs use the same font and layout — and covers the
Latin, Greek and Cyrillic alphabets. A sheet holding characters it lacks is
set in a system font that has them, where one is installed.

Every PDF also carries its words as an embedded file, ``lexitrack-words.json``
in the JSON word-list format, the same file the phone embeds. Importing the
PDF reads that file (parsers/lexitrack_pdf.py), so a sheet comes back whole —
contexts and all — whichever columns it prints.
"""

from __future__ import annotations

import io
import json
import logging
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import pymupdf
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFontFile
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from ..core.errors import ExportError
from ..models.context import find_word
from ..models.user_word_state import ReviewStatus
from ..parsers.lexitrack_pdf import WORD_LIST_FILE, WORD_LIST_FORMAT
from ..repositories.word_repository import StoredWord
from .json_exporter import build_json_document
from .sheet import STATUS_NAMES, ExportColumn, WordSheet

log = logging.getLogger(__name__)

#: Shown where a source provided no value for a column.
PLACEHOLDER = "—"

# The sheet's colours: near-black ink, two greys, a hairline, and the app's
# meaning colours for Known (green) and Unknown (amber), darkened for paper.
_INK = colors.HexColor("#1A1D21")
_MUTED = colors.HexColor("#6B717A")
_FAINT = colors.HexColor("#9AA0A8")
_RULE = colors.HexColor("#E2E4E8")

#: A status's dot and the colour of its name.
_STATUS_TONES: dict[ReviewStatus, tuple[str, str]] = {
    ReviewStatus.KNOWN: ("#0E7A57", "#0E7A57"),
    ReviewStatus.UNKNOWN: ("#A65D08", "#A65D08"),
    ReviewStatus.NOT_REVIEWED: ("#9AA0A8", "#6B717A"),
}

_SIDE = 56
_TOP = 58
_BOTTOM = 60

#: The table's columns: what each shows, its heading, and its share of the width.
#: The definition takes the slack because it is the only free-text column.
_TABLE_COLUMNS: tuple[tuple[ExportColumn | None, str, float], ...] = (
    (None, "Word", 1.9),
    (ExportColumn.PART_OF_SPEECH, "Type", 1.5),
    (ExportColumn.CEFR, "CEFR", 0.8),
    (ExportColumn.LENGTH, "Length", 0.9),
    (ExportColumn.STATUS, "Status", 1.6),
    (ExportColumn.DEFINITION, "Definition", 4.2),
)


def export_words_pdf(
    words: Sequence[StoredWord],
    path: Path,
    title: str = "Unknown Words",
    subtitle: str | None = None,
    group_by_level: bool = False,
    sheet: WordSheet | None = None,
    word_list: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``words`` to ``path`` as a printable PDF and return the path.

    With ``group_by_level`` a heading starts each run of words sharing a CEFR
    level; pass the words already in level order. ``word_list`` is the JSON
    word list embedded in the file (see ``build_json_document``); without it,
    one is made from ``words`` alone, without contexts.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        _build(words, buffer, title, subtitle, group_by_level, sheet or WordSheet())
        embedded = word_list if word_list is not None else build_json_document(words, name=title)
        path.write_bytes(_with_word_list(buffer.getvalue(), embedded))
    except ExportError:
        raise
    except Exception as exc:
        log.exception("PDF export to %s failed", path)
        raise ExportError(f"The PDF file could not be written to {path}.") from exc

    log.info("Exported %d words to %s", len(words), path)
    return path


def _build(
    words: Sequence[StoredWord],
    target: io.BytesIO,
    title: str,
    subtitle: str | None,
    group_by_level: bool,
    sheet: WordSheet,
) -> None:
    caption = subtitle or _default_subtitle(len(words))
    footer_text = f"LexiTrack  ·  {title}"
    fonts = fonts_for(_texts(words, sheet, (title, caption)))
    document = BaseDocTemplate(
        target,
        pagesize=A4,
        leftMargin=_SIDE,
        rightMargin=_SIDE,
        topMargin=_TOP,
        bottomMargin=_BOTTOM,
        title=f"LexiTrack — {title}",
        author="LexiTrack",
        subject="Vocabulary export",
    )
    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="body",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    document.addPageTemplates(PageTemplate(id="page", frames=[frame]))

    styles = _styles(fonts)
    story: list[object] = [
        _Caps("LexiTrack  ·  Word list", fonts.bold, _FAINT),
        Spacer(1, 8),
        Paragraph(_escape(title), styles["title"]),
        Spacer(1, 4),
        # Paragraphs fold runs of spaces; the caption keeps its wide separators.
        Paragraph(_escape(caption).replace("  ", "&nbsp;&nbsp;"), styles["subtitle"]),
    ]
    if sheet.has(ExportColumn.STATUS) and words:
        story += [Spacer(1, 3), Paragraph(_tally(words), styles["subtitle"])]
    story += [Spacer(1, 14), HRFlowable(width="100%", thickness=0.8, color=_INK, spaceAfter=10)]

    if words and group_by_level:
        # Under a level heading the rows do not repeat the level.
        levelless = tuple(c for c in sheet.columns if c is not ExportColumn.CEFR)
        shown = replace(sheet, columns=levelless)
        for index, (level, run) in enumerate(_level_runs(words)):
            count = len(run)
            heading = Paragraph(
                f"{_escape(level or 'No level')}"
                f"<font name='{fonts.regular}' size=9.5 color='#9AA0A8'>"
                f"&nbsp;&nbsp;&nbsp;{count:,} {'word' if count == 1 else 'words'}</font>",
                styles["level"] if index else styles["first_level"],
            )
            body = _body(run, shown, styles, document.width, fonts)
            if sheet.has(ExportColumn.CONTEXTS):
                # A heading never ends a page: it keeps its first entry with it.
                first = _entry_parts(run[0], shown, styles, document.width)
                story.append(KeepTogether([heading, *first]))
                story.extend(body[1:])
            else:
                # Not keepWithNext: a level's table can be pages long, and
                # keeping the heading with all of it would leave a blank page.
                story.append(CondPageBreak(40 * mm))
                story.append(heading)
                story.extend(body)
    elif words:
        story.extend(_body(words, sheet, styles, document.width, fonts))
    else:
        story.append(Paragraph("There are no words to export.", styles["empty"]))

    document.build(story, canvasmaker=_numbered_canvas(footer_text, fonts))


def _with_word_list(pdf: bytes, word_list: Mapping[str, Any]) -> bytes:
    """``pdf`` with ``word_list`` embedded as ``lexitrack-words.json``."""
    content = {"format": WORD_LIST_FORMAT, "version": 1, **word_list}
    data = json.dumps(content, ensure_ascii=False, indent=1).encode("utf-8")
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        document.embfile_add(
            WORD_LIST_FILE,
            data,
            filename=WORD_LIST_FILE,
            desc="The words of this sheet, for importing it back into LexiTrack",
        )
        return document.tobytes(garbage=1, deflate=True)


def _body(
    words: Sequence[StoredWord],
    sheet: WordSheet,
    styles: dict[str, ParagraphStyle],
    width: float,
    fonts: Fonts,
) -> list[object]:
    if not sheet.has(ExportColumn.CONTEXTS):
        return [_word_table(words, sheet, styles, width, fonts)]
    return [_entry(word, sheet, styles, width) for word in words]


def _default_subtitle(count: int) -> str:
    word = "word" if count == 1 else "words"
    return f"{count:,} {word}  ·  {long_date(datetime.now())}"


def long_date(moment: datetime) -> str:
    """``5 October 2026``: the sheet is in English, whatever the system's locale."""
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"


_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)


# -- fonts ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fonts:
    regular: str
    bold: str
    italic: str
    bold_italic: str


_STYLES = ("", "-Bold", "-Italic", "-BoldItalic")


def _bundled() -> tuple[str, tuple[Path, ...]]:
    """Source Serif 4, shipped with LexiTrack; its semibold serves as bold."""
    folder = Path(__file__).parent / "fonts"
    return "SourceSerif4", tuple(
        folder / f"SourceSerif4-{face}.ttf"
        for face in ("Regular", "Semibold", "Italic", "SemiboldItalic")
    )


def system_families() -> list[tuple[str, tuple[Path, ...]]]:
    """Fonts that may have what Source Serif lacks, where the system has them."""
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    mac = Path("/System/Library/Fonts/Supplemental")
    families = [
        ("Arial", ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"), windows),
        ("SegoeUI", ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"), windows),
        ("ArialMac", ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf",
                      "Arial Bold Italic.ttf"), mac),
        ("DejaVuSans", ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf",
                        "DejaVuSans-BoldOblique.ttf"), Path("/usr/share/fonts/truetype/dejavu")),
        ("DejaVuSansTTF", ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf",
                           "DejaVuSans-BoldOblique.ttf"), Path("/usr/share/fonts/TTF")),
        ("NotoSans", ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf", "NotoSans-Italic.ttf",
                      "NotoSans-BoldItalic.ttf"), Path("/usr/share/fonts/truetype/noto")),
    ]
    return [(name, tuple(folder / file for file in files)) for name, files, folder in families]


@cache
def _coverage(path: Path) -> frozenset[int]:
    return frozenset(TTFontFile(str(path)).charToGlyph)


def fonts_for(texts: Iterable[str]) -> Fonts:
    """Source Serif when it has every character of ``texts``, else a system font that does."""
    needed = {ord(char) for text in texts for char in text if not char.isspace()}
    bundled = _bundled()
    for name, files in (bundled, *system_families()):
        if all(file.exists() for file in files) and needed <= _coverage(files[0]):
            return _register(name, files)
    missing = "".join(sorted(chr(c) for c in needed - _coverage(bundled[1][0])))[:20]
    log.warning("No installed font has every character of this export (%s)", missing)
    return _register(*bundled)


@cache
def _register(name: str, files: tuple[Path, ...]) -> Fonts:
    names = tuple(f"Lexi{name}{style}" for style in _STYLES)
    for font_name, file in zip(names, files, strict=True):
        pdfmetrics.registerFont(TTFont(font_name, str(file)))
    # So <b> and <i> inside a paragraph find the right face.
    for (bold, italic), font_name in zip(
        ((0, 0), (1, 0), (0, 1), (1, 1)), names, strict=True
    ):
        addMapping(names[0], bold, italic, font_name)
    return Fonts(*names)


def _texts(
    words: Sequence[StoredWord], sheet: WordSheet, extra: Iterable[str]
) -> Iterable[str]:
    """Every string the sheet will set, for choosing a font that has them all."""
    yield from extra
    yield PLACEHOLDER + "•·"
    for word in words:
        yield from (word.word, word.part_of_speech or "", word.cefr_level or "",
                    word.definition or "")
        yield from sheet.contexts_for(word.id)


# -- layout --------------------------------------------------------------------


class _Caps(Flowable):
    """A label in small spaced capitals: the quiet structure of the page."""

    def __init__(self, text: str, font: str, color, size: float = 7.5) -> None:
        super().__init__()
        self.text = text.upper()
        self.font = font
        self.color = color
        self.size = size

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return available_width, self.size * 1.35

    def draw(self) -> None:
        text = self.canv.beginText(0, self.size * 0.3)
        text.setFont(self.font, self.size)
        text.setCharSpace(1.0)
        text.setFillColor(self.color)
        text.textOut(self.text)
        self.canv.drawText(text)


def _styles(fonts: Fonts) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]

    def style(name: str, font: str, size: float, leading: float, color=_INK, **extra):
        spacing = {"spaceBefore": 0, "spaceAfter": 0, **extra}
        return ParagraphStyle(name, parent=base, fontName=font, fontSize=size,
                              leading=leading, textColor=color, **spacing)

    return {
        "title": style("LexiTitle", fonts.bold, 26, 31),
        "subtitle": style("LexiSubtitle", fonts.regular, 10, 14, _MUTED),
        "word": style("LexiWord", fonts.bold, 11, 14),
        "type": style("LexiType", fonts.italic, 10, 14, _MUTED),
        "quiet": style("LexiQuiet", fonts.regular, 9.5, 14, _MUTED),
        "missing": style("LexiMissing", fonts.regular, 10, 14, _FAINT),
        "status": style("LexiStatus", fonts.regular, 9.5, 14),
        "status_right": style("LexiStatusRight", fonts.regular, 9.5, 14, alignment=TA_RIGHT),
        "definition": style("LexiDefinition", fonts.regular, 10.5, 14),
        "entry": style("LexiEntry", fonts.bold, 14, 18),
        "context": style("LexiContext", fonts.italic, 10, 13.5, _MUTED, leftIndent=12),
        "first_level": style("LexiFirstLevel", fonts.bold, 15, 19, spaceAfter=8),
        "level": style("LexiLevel", fonts.bold, 15, 19, spaceBefore=16, spaceAfter=8),
        "empty": style("LexiEmpty", fonts.italic, 10.5, 14, _MUTED),
    }


def _word_table(
    words: Sequence[StoredWord],
    sheet: WordSheet,
    styles: dict[str, ParagraphStyle],
    width: float,
    fonts: Fonts,
) -> Table:
    shown = [spec for spec in _TABLE_COLUMNS if spec[0] is None or sheet.has(spec[0])]
    rows: list[list[object]] = [[_Caps(name, fonts.bold, _MUTED) for _c, name, _s in shown]]

    for word in words:
        cells: list[object] = []
        for column, _name, _share in shown:
            if column is None:
                cells.append(Paragraph(_escape(word.word), styles["word"]))
            elif column is ExportColumn.PART_OF_SPEECH:
                cells.append(_cell(word.part_of_speech, styles["type"], styles))
            elif column is ExportColumn.CEFR:
                cells.append(_cell(word.cefr_level, styles["quiet"], styles))
            elif column is ExportColumn.LENGTH:
                cells.append(_cell(str(word.length), styles["quiet"], styles))
            elif column is ExportColumn.STATUS:
                cells.append(Paragraph(_status_mark(word.status), styles["status"]))
            else:
                cells.append(_cell(word.definition, styles["definition"], styles))
        rows.append(cells)

    total = sum(share for _column, _name, share in shown)
    column_widths = [width * share / total for _column, _name, share in shown]
    table = Table(rows, colWidths=column_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                # No vertical lines, no shading: a rule under the header and a
                # hairline between rows are enough to follow a line across.
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, _MUTED),
                ("LINEBELOW", (0, 1), (-1, -2), 0.4, _RULE),
            ]
        )
    )
    return table


def _entry(
    word: StoredWord, sheet: WordSheet, styles: dict[str, ParagraphStyle], width: float
) -> object:
    """One word as a dictionary entry; never split across pages."""
    return KeepTogether(_entry_parts(word, sheet, styles, width))


def _entry_parts(
    word: StoredWord, sheet: WordSheet, styles: dict[str, ParagraphStyle], width: float
) -> list[object]:
    """An entry's lines: the word, its type and status, definition, contexts."""
    head = _escape(word.word)
    if sheet.has(ExportColumn.PART_OF_SPEECH) and word.part_of_speech:
        head += (f"&nbsp;&nbsp;&nbsp;<font name='{styles['type'].fontName}' size=10.5 "
                 f"color='#6B717A'>{_escape(word.part_of_speech)}</font>")
    details = [
        value
        for column, value in (
            (ExportColumn.CEFR, word.cefr_level),
            (ExportColumn.LENGTH, f"{word.length} letters"),
        )
        if sheet.has(column) and value
    ]
    if details:
        head += (f"&nbsp;&nbsp;&nbsp;<font name='{styles['quiet'].fontName}' size=9.5 "
                 f"color='#9AA0A8'>{_escape(' · '.join(details))}</font>")

    line: object = Paragraph(head, styles["entry"])
    if sheet.has(ExportColumn.STATUS):
        line = Table(
            [[line, Paragraph(_status_mark(word.status), styles["status_right"])]],
            colWidths=[width * 0.75, width * 0.25],
        )
        line.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )

    parts: list[object] = [line, Spacer(1, 2)]
    if sheet.has(ExportColumn.DEFINITION) and word.definition:
        parts.append(Paragraph(_escape(word.definition), styles["definition"]))
    for text in sheet.contexts_for(word.id):
        parts.append(Paragraph(_marked(text, word.word), styles["context"]))
    parts.append(Spacer(1, 11))
    return parts


def _status_mark(status: ReviewStatus) -> str:
    """A dot in the status's colour, then its name: never colour alone."""
    dot, ink = _STATUS_TONES[status]
    name = STATUS_NAMES[status].replace(" ", "&nbsp;")
    return f"<font color='{dot}'>•</font>&nbsp;<font color='{ink}'>{name}</font>"


def _tally(words: Sequence[StoredWord]) -> str:
    """``• Known 12   • Unknown 30   • Not reviewed 8``."""
    counts = Counter(word.status for word in words)
    return ("&nbsp;" * 6).join(
        f"{_status_mark(status)} {counts[status]:,}"
        for status in (ReviewStatus.KNOWN, ReviewStatus.UNKNOWN, ReviewStatus.NOT_REVIEWED)
    )


def _marked(text: str, word: str) -> str:
    """A context with the word in semibold italic and ink, where it can be found."""
    span = find_word(text, word)
    if span is None:
        return _escape(text)
    start, end = span
    return (
        _escape(text[:start]) + "<b><font color='#1A1D21'>" + _escape(text[start:end])
        + "</font></b>" + _escape(text[end:])
    )


def _level_runs(words: Sequence[StoredWord]) -> list[tuple[str | None, list[StoredWord]]]:
    """Split ``words`` wherever the CEFR level changes."""
    runs: list[tuple[str | None, list[StoredWord]]] = []
    for word in words:
        if runs and runs[-1][0] == word.cefr_level:
            runs[-1][1].append(word)
        else:
            runs.append((word.cefr_level, [word]))
    return runs


def _cell(value: str | None, style: ParagraphStyle, styles: dict[str, ParagraphStyle]) -> Paragraph:
    if not value:
        return Paragraph(PLACEHOLDER, styles["missing"])
    return Paragraph(_escape(value), style)


def _escape(text: str) -> str:
    """Escape the characters ReportLab treats as inline markup, and keep line
    breaks (a definition's numbered senses are one per line)."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return escaped.replace("\n", "<br/>")


def _numbered_canvas(text: str, fonts: Fonts):
    """A canvas that ends each page with the footer: ``text`` and *page / pages*."""

    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []

        def showPage(self) -> None:  # noqa: N802 - ReportLab's name
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._pages)
            for page in self._pages:
                self.__dict__.update(page)
                self._footer(total)
                super().showPage()
            super().save()

        def _footer(self, total: int) -> None:
            self.saveState()
            self.setFont(fonts.regular, 8)
            self.setFillColor(_FAINT)
            baseline = 34
            self.drawString(_SIDE, baseline, text)
            self.drawRightString(
                self._pagesize[0] - _SIDE, baseline, f"{self._pageNumber} / {total}"
            )
            self.restoreState()

    return NumberedCanvas
