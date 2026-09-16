"""PDF export of vocabulary.

The output is LexiTrack's own study sheet, not an imitation of the Oxford
layout: a titled, paginated table designed to be printed and worked through.
It takes ``StoredWord`` objects and knows nothing about which parser produced
them, so a list built from a novel exports exactly as well as one built from
Oxford — the columns it cannot fill simply show a dash.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from ..core.errors import ExportError
from ..repositories.word_repository import StoredWord

log = logging.getLogger(__name__)

#: Shown where a source provided no value for a column.
PLACEHOLDER = "—"

_INK = colors.HexColor("#1B1F24")
_MUTED = colors.HexColor("#6B7280")
_RULE = colors.HexColor("#D8DCE2")
_BAND = colors.HexColor("#F4F6F8")

_MARGIN = 18 * mm


def export_words_pdf(
    words: Sequence[StoredWord],
    path: Path,
    title: str = "Unknown Words",
    subtitle: str | None = None,
) -> Path:
    """Write ``words`` to ``path`` as a printable PDF and return the path."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _build(words, path, title, subtitle)
    except ExportError:
        raise
    except Exception as exc:
        log.exception("PDF export to %s failed", path)
        raise ExportError(f"The PDF file could not be written to {path}.") from exc

    log.info("Exported %d words to %s", len(words), path)
    return path


def _build(
    words: Sequence[StoredWord], path: Path, title: str, subtitle: str | None
) -> None:
    generated = datetime.now().strftime("%d %B %Y")
    footer_text = f"LexiTrack  ·  {title}  ·  {generated}"

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
        PageTemplate(id="page", frames=[frame], onPage=_make_footer(footer_text))
    )

    styles = _styles()
    story: list[object] = [Paragraph(title, styles["title"])]

    caption = subtitle or _default_subtitle(len(words))
    story.append(Paragraph(caption, styles["subtitle"]))
    story.append(Spacer(1, 8 * mm))

    if words:
        story.append(_word_table(words, styles, document.width))
    else:
        story.append(Paragraph("There are no words to export.", styles["empty"]))

    document.build(story)


def _default_subtitle(count: int) -> str:
    word = "word" if count == 1 else "words"
    return f"{count} {word} · generated {datetime.now().strftime('%d %B %Y')}"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    return {
        "title": ParagraphStyle(
            "LexiTitle",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=_INK,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "LexiSubtitle",
            parent=base,
            fontName="Helvetica",
            fontSize=9.5,
            leading=13,
            textColor=_MUTED,
        ),
        "header": ParagraphStyle(
            "LexiHeader",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=_MUTED,
            alignment=TA_LEFT,
        ),
        "word": ParagraphStyle(
            "LexiWord",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=_INK,
        ),
        "cell": ParagraphStyle(
            "LexiCell",
            parent=base,
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=_INK,
        ),
        "muted": ParagraphStyle(
            "LexiMuted",
            parent=base,
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=_MUTED,
        ),
        "empty": ParagraphStyle(
            "LexiEmpty",
            parent=base,
            fontName="Helvetica-Oblique",
            fontSize=10,
            textColor=_MUTED,
        ),
    }


def _word_table(
    words: Sequence[StoredWord], styles: dict[str, ParagraphStyle], width: float
) -> Table:
    header = [Paragraph(name, styles["header"]) for name in ("WORD", "PART OF SPEECH", "CEFR", "DEFINITION")]
    rows: list[list[object]] = [header]

    for word in words:
        rows.append(
            [
                Paragraph(_escape(word.word), styles["word"]),
                _cell(word.part_of_speech, styles),
                _cell(word.cefr_level, styles),
                _cell(word.definition, styles),
            ]
        )

    # Definition takes the slack because it is the only free-text column.
    column_widths = [width * 0.22, width * 0.20, width * 0.10, width * 0.48]
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


def _cell(value: str | None, styles: dict[str, ParagraphStyle]) -> Paragraph:
    if not value:
        return Paragraph(PLACEHOLDER, styles["muted"])
    return Paragraph(_escape(value), styles["cell"])


def _escape(text: str) -> str:
    """Escape the characters ReportLab treats as inline markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _make_footer(text: str):
    """Return an ``onPage`` handler drawing the footer and page number."""

    def draw(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
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
