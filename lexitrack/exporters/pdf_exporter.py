"""PDF export of vocabulary.

The output is LexiTrack's own study sheet, not an imitation of the Oxford
layout: a titled, paginated document designed to be printed and worked
through. It takes ``StoredWord`` objects and knows nothing about which parser
produced them, so a list built from a novel exports exactly as well as one
built from Oxford — the fields it cannot fill simply show a dash.

With only the dictionary's columns (exporters/sheet.py) the sheet is a table:
word, part of speech, level, and the definition with the word's note (a
sense, a UK/US variant) under it. With any teaching column it becomes a list
of entries, one per word — the word and its level on a line, then each chosen
field under a small label, examples with the word in bold and their
translations beneath — because meanings, patterns and examples do not fit in
table cells. Exported in CEFR order, either form starts each level with a
heading, so a printed list reads A1, then A2, and so on.

The text is set in Bitstream Vera, which ships with ReportLab and is embedded
in the file, so a sheet looks the same everywhere and covers the Latin
alphabets (Turkish, German, Spanish, French…). A sheet holding characters Vera
lacks — Cyrillic, Greek — is set in a system font that has them, where one is
installed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFontFile
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
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
from ..models.content import TARGET, WordContext
from ..models.language import language_name
from ..repositories.word_repository import StoredWord
from .sheet import ExportColumn, WordSheet

log = logging.getLogger(__name__)

#: Shown where a source provided no value for a column.
PLACEHOLDER = "—"

_INK = colors.HexColor("#1B1F24")
_MUTED = colors.HexColor("#6B7280")
_RULE = colors.HexColor("#D8DCE2")
_BAND = colors.HexColor("#F4F6F8")

_MARGIN = 18 * mm
#: The label column of an entry.
_LABEL_WIDTH = 30 * mm

#: The table's columns: what each shows, its heading, and its share of the width.
#: The definition takes the slack because it is the only free-text column.
_TABLE_COLUMNS: tuple[tuple[ExportColumn | None, str, float], ...] = (
    (None, "WORD", 0.22),
    (ExportColumn.PART_OF_SPEECH, "PART OF SPEECH", 0.20),
    (ExportColumn.CEFR, "CEFR", 0.10),
    (ExportColumn.DEFINITION, "DEFINITION", 0.48),
)


def export_words_pdf(
    words: Sequence[StoredWord],
    path: Path,
    title: str = "Unknown Words",
    subtitle: str | None = None,
    group_by_level: bool = False,
    sheet: WordSheet | None = None,
) -> Path:
    """Write ``words`` to ``path`` as a printable PDF and return the path.

    With ``group_by_level`` a heading starts each run of words sharing a CEFR
    level; pass the words already in level order.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _build(words, path, title, subtitle, group_by_level, sheet or WordSheet())
    except ExportError:
        raise
    except Exception as exc:
        log.exception("PDF export to %s failed", path)
        raise ExportError(f"The PDF file could not be written to {path}.") from exc

    log.info("Exported %d words to %s", len(words), path)
    return path


def _build(
    words: Sequence[StoredWord],
    path: Path,
    title: str,
    subtitle: str | None,
    group_by_level: bool,
    sheet: WordSheet,
) -> None:
    generated = datetime.now().strftime("%d %B %Y")
    footer_text = f"LexiTrack  ·  {title}  ·  {generated}"
    caption = subtitle or _default_subtitle(len(words))
    language_note = _language_note(sheet)
    fonts = fonts_for(_texts(words, sheet, (title, caption, footer_text, language_note)))

    document = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN + 6 * mm,
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
    )
    document.addPageTemplates(
        PageTemplate(id="page", frames=[frame], onPage=_make_footer(footer_text, fonts))
    )

    styles = _styles(fonts)
    story: list[object] = [Paragraph(_escape(title), styles["title"])]
    story.append(Paragraph(_escape(caption), styles["subtitle"]))
    if language_note:
        story.append(Paragraph(_escape(language_note), styles["subtitle"]))
    story.append(Spacer(1, 8 * mm))

    if words and group_by_level:
        for index, (level, run) in enumerate(_level_runs(words)):
            # Not keepWithNext: a level's table can be pages long, and keeping
            # the heading with all of it would leave a blank first page.
            story.append(CondPageBreak(40 * mm))
            if index:
                story.append(Spacer(1, 6 * mm))
            count = len(run)
            heading = f"{level or 'No level'}  <font size=10 color='#6B7280'>" \
                f"{count:,} {'word' if count == 1 else 'words'}</font>"
            story.append(Paragraph(heading, styles["level"]))
            story.extend(_body(run, sheet, styles, document.width))
    elif words:
        story.extend(_body(words, sheet, styles, document.width))
    else:
        story.append(Paragraph("There are no words to export.", styles["empty"]))

    document.build(story)


def _body(
    words: Sequence[StoredWord], sheet: WordSheet, styles: dict[str, ParagraphStyle], width: float
) -> list[object]:
    if not sheet.teaches:
        return [_word_table(words, sheet, styles, width)]
    flowables: list[object] = []
    for index, word in enumerate(words):
        if index:
            flowables.append(HRFlowable(width="100%", thickness=0.25, color=_RULE,
                                        spaceBefore=5, spaceAfter=6))
        flowables.append(_entry(word, sheet, styles, width))
    return flowables


def _default_subtitle(count: int) -> str:
    word = "word" if count == 1 else "words"
    return f"{count:,} {word} · generated {datetime.now().strftime('%d %B %Y')}"


def _language_note(sheet: WordSheet) -> str:
    """Says which language meanings are in, when the sheet shows any."""
    if sheet.learner_language and any(column.needs_language for column in sheet.columns):
        return f"Meanings and translations in {language_name(sheet.learner_language)}."
    return ""


# -- fonts ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fonts:
    regular: str
    bold: str
    italic: str
    bold_italic: str


_STYLES = ("", "-Bold", "-Italic", "-BoldItalic")


def _bundled() -> tuple[str, tuple[Path, ...]]:
    folder = Path(reportlab.__file__).parent / "fonts"
    return "Vera", tuple(folder / name for name in ("Vera.ttf", "VeraBd.ttf", "VeraIt.ttf",
                                                    "VeraBI.ttf"))


def system_families() -> list[tuple[str, tuple[Path, ...]]]:
    """Fonts with wider coverage than Vera, where the system has them."""
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
    """Vera when it has every character of ``texts``, else a system font that does."""
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
    yield PLACEHOLDER
    for word in words:
        yield from (word.word, word.part_of_speech or "", word.cefr_level or "",
                    word.definition or "", word.note or "")
        if sheet.teaches:
            teaching = sheet.teaching_for(word.id)
            if teaching.content is not None:
                yield teaching.content.pattern or ""
                yield from teaching.content.collocations
            if teaching.localization is not None:
                local = teaching.localization
                yield from (local.core_meaning or "", local.nuance or "", local.usage_note or "")
            yield from (context.text for context in teaching.contexts)
            yield from teaching.translations.values()


# -- layout --------------------------------------------------------------------


def _styles(fonts: Fonts) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]

    def style(name: str, font: str, size: float, leading: float, color, **extra):
        return ParagraphStyle(name, parent=base, fontName=font, fontSize=size,
                              leading=leading, textColor=color, **extra)

    return {
        "title": style("LexiTitle", fonts.bold, 20, 24, _INK, spaceAfter=2),
        "subtitle": style("LexiSubtitle", fonts.regular, 9.5, 13, _MUTED),
        "header": style("LexiHeader", fonts.bold, 8.5, 11, _MUTED, alignment=TA_LEFT),
        "word": style("LexiWord", fonts.bold, 10, 13, _INK),
        "entry": style("LexiEntry", fonts.bold, 12, 16, _INK, spaceAfter=3),
        "label": style("LexiLabel", fonts.bold, 7, 12, _MUTED),
        "cell": style("LexiCell", fonts.regular, 9, 12, _INK),
        "muted": style("LexiMuted", fonts.regular, 9, 12, _MUTED),
        "level": style("LexiLevel", fonts.bold, 15, 19, _INK, spaceAfter=4),
        "note": style("LexiNote", fonts.italic, 8.5, 11, _MUTED),
        "empty": style("LexiEmpty", fonts.italic, 10, 13, _MUTED),
    }


def _word_table(
    words: Sequence[StoredWord],
    sheet: WordSheet,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> Table:
    shown = [spec for spec in _TABLE_COLUMNS if spec[0] is None or sheet.has(spec[0])]
    header = [Paragraph(name, styles["header"]) for _column, name, _share in shown]
    rows: list[list[object]] = [header]

    for word in words:
        cells: list[object] = []
        for column, _name, _share in shown:
            if column is None:
                cells.append(Paragraph(_escape(word.word), styles["word"]))
            elif column is ExportColumn.PART_OF_SPEECH:
                cells.append(_cell(word.part_of_speech, styles))
            elif column is ExportColumn.CEFR:
                cells.append(_cell(word.cefr_level, styles))
            else:
                cells.append(_definition_cell(word, styles))
        rows.append(cells)

    total = sum(share for _column, _name, share in shown)
    column_widths = [width * share / total for _column, _name, share in shown]
    table = Table(rows, colWidths=column_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                # A rule under the header, hairlines between rows: enough
                # structure to follow a line across the page, no more.
                ("LINEBELOW", (0, 0), (-1, 0), 0.9, _INK),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, _RULE),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _BAND]),
            ]
        )
    )
    return table


def _entry(
    word: StoredWord, sheet: WordSheet, styles: dict[str, ParagraphStyle], width: float
) -> object:
    """One word as an entry: its line, then each chosen field that has a value."""
    meta = [
        _escape(value)
        for column, value in (
            (ExportColumn.PART_OF_SPEECH, word.part_of_speech),
            (ExportColumn.CEFR, word.cefr_level),
        )
        if sheet.has(column) and value
    ]
    head = _escape(word.word)
    if meta:
        head += f"   <font name='{styles['cell'].fontName}' size=9 color='#6B7280'>" \
            f"{'  ·  '.join(meta)}</font>"

    teaching = sheet.teaching_for(word.id)
    content, local = teaching.content, teaching.localization
    rows: list[list[object]] = []

    def add(label: str, value: object) -> None:
        rows.append([Paragraph(label, styles["label"]), value])

    if sheet.has(ExportColumn.DEFINITION) and (word.definition or word.note):
        add("DEFINITION", _definition_cell(word, styles))
    if sheet.has(ExportColumn.MEANING) and local and local.core_meaning:
        add("MEANING", Paragraph(_escape(local.core_meaning), styles["cell"]))
    if sheet.has(ExportColumn.NUANCE) and local:
        if local.nuance:
            add("NUANCE", Paragraph(_escape(local.nuance), styles["cell"]))
        if local.usage_note:
            add("USAGE", Paragraph(_escape(local.usage_note), styles["cell"]))
    if sheet.has(ExportColumn.PATTERN) and content and content.pattern:
        add("PATTERN", Paragraph(_escape(content.pattern), styles["cell"]))
    if sheet.has(ExportColumn.COLLOCATIONS) and content and content.collocations:
        add("COLLOCATIONS",
            Paragraph("  ·  ".join(_escape(c) for c in content.collocations), styles["cell"]))
    examples = _examples(teaching.contexts, teaching.translation, sheet, styles)
    if examples:
        add("EXAMPLES" if sheet.has(ExportColumn.EXAMPLES) else "TRANSLATIONS", examples)
    if not rows:
        rows.append(["", Paragraph(PLACEHOLDER, styles["muted"])])

    fields = Table(rows, colWidths=[_LABEL_WIDTH, width - _LABEL_WIDTH])
    fields.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                # The same inset as the table's cells: labels line up with the word.
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
    )
    return KeepTogether([Paragraph(head, styles["entry"]), fields])


def _examples(
    contexts: Sequence[WordContext],
    translation,
    sheet: WordSheet,
    styles: dict[str, ParagraphStyle],
) -> list[Paragraph]:
    """Each example with the word in bold, its translation under it when chosen."""
    show_text = sheet.has(ExportColumn.EXAMPLES)
    show_translation = sheet.has(ExportColumn.TRANSLATIONS)
    parts: list[Paragraph] = []
    for context in contexts:
        translated = translation(context) if show_translation else None
        if show_text:
            parts.append(Paragraph(_marked(context.text), styles["cell"]))
        if translated:
            parts.append(Paragraph(_escape(translated), styles["note"]))
        if show_text and translated:
            parts.append(Spacer(1, 2))
    return parts


def _marked(text: str) -> str:
    """A context's text with the target word in bold and the braces gone."""
    return TARGET.sub(lambda match: f"<b>{match.group(1)}</b>", _escape(text))


def _level_runs(words: Sequence[StoredWord]) -> list[tuple[str | None, list[StoredWord]]]:
    """Split ``words`` wherever the CEFR level changes."""
    runs: list[tuple[str | None, list[StoredWord]]] = []
    for word in words:
        if runs and runs[-1][0] == word.cefr_level:
            runs[-1][1].append(word)
        else:
            runs.append((word.cefr_level, [word]))
    return runs


def _definition_cell(word: StoredWord, styles: dict[str, ParagraphStyle]) -> object:
    """The definition, with the note under it; a dash when there is neither."""
    if not word.definition and not word.note:
        return Paragraph(PLACEHOLDER, styles["muted"])
    parts: list[Paragraph] = []
    if word.definition:
        parts.append(Paragraph(_escape(word.definition), styles["cell"]))
    if word.note:
        parts.append(Paragraph(_escape(word.note), styles["note"]))
    return parts


def _cell(value: str | None, styles: dict[str, ParagraphStyle]) -> Paragraph:
    if not value:
        return Paragraph(PLACEHOLDER, styles["muted"])
    return Paragraph(_escape(value), styles["cell"])


def _escape(text: str) -> str:
    """Escape the characters ReportLab treats as inline markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _make_footer(text: str, fonts: Fonts):
    """Return an ``onPage`` handler drawing the footer and page number."""

    def draw(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(fonts.regular, 8)
        canvas.setFillColor(_MUTED)
        baseline = _MARGIN - 2 * mm
        canvas.drawString(_MARGIN, baseline, text)
        canvas.drawRightString(
            document.pagesize[0] - _MARGIN, baseline, str(canvas.getPageNumber())
        )
        canvas.setStrokeColor(_RULE)
        canvas.setLineWidth(0.4)
        canvas.line(
            _MARGIN,
            baseline + 4 * mm,
            document.pagesize[0] - _MARGIN,
            baseline + 4 * mm,
        )
        canvas.restoreState()

    return draw
