"""How LexiTrack works, inside the app.

For the user who opens the app and never the documentation. The text is a
Markdown file packaged with the application (``lexitrack/help``), and its
central section is the README's *How a word is learned*, copied verbatim - a
test fails if the two drift apart - so there is one explanation, not two.
"""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextFrameFormat, QTextLength, QTextTable
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QTextBrowser, QVBoxLayout

from .theme import current_palette
from .theme.palette import METRICS

HELP_FILE = "how_lexitrack_works.md"


def help_text() -> str:
    return resources.files("lexitrack.help").joinpath(HELP_FILE).read_text(encoding="utf-8")


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("How LexiTrack Works")
        self.setMinimumSize(720, 680)
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.browser = QTextBrowser()
        self.browser.setObjectName("HelpText")
        self.browser.setOpenExternalLinks(False)
        # The text wraps; a horizontal bar would only ever scroll the margin.
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        palette = current_palette()
        # Tables and headings styled to the app, not Qt's defaults.
        self.browser.document().setDefaultStyleSheet(
            f"h1 {{ font-size: 22px; margin-bottom: 8px; }}"
            f"h2 {{ font-size: 16px; margin-top: 18px; }}"
            f"th {{ text-align: left; color: {palette.text_muted}; padding: 6px 10px; }}"
            f"td {{ padding: 6px 10px; border-top: 1px solid {palette.border}; }}"
            f"table {{ margin: 6px 0; }}"
        )
        self.browser.document().setDocumentMargin(m.space_5)
        self.browser.setMarkdown(help_text())
        self._style_tables(palette)
        layout.addWidget(self.browser, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(m.space_5, m.space_3, m.space_5, m.space_4)
        footer.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _style_tables(self, palette) -> None:
        """Thin single rules in the app's border colour, as in its own tables.

        Markdown tables arrive with Qt's default double grey frame, and the
        document's style sheet does not reach them, so each table's format
        is set directly.
        """
        frame = self.browser.document().rootFrame()
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                fmt = child.format()
                fmt.setBorderCollapse(True)
                fmt.setBorder(1)
                fmt.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
                fmt.setBorderBrush(QColor(palette.border))
                fmt.setCellPadding(6)
                fmt.setCellSpacing(0)
                fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 99))
                child.setFormat(fmt)
