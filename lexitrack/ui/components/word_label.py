"""The headword of a card, as large as it fits.

Most words are one short word, set large. Some entries are phrases —
"departures and arrivals board" — or one long word, and at the full size they
would wrap onto three lines or run past the card. The label steps its size
down until the text fits on at most two lines, and asks the layout for the
height those lines need, so nothing is ever cut off.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QLabel, QWidget

#: Pixel sizes tried in turn; the first is the one the stylesheet sets.
SIZES = (58, 50, 44, 38, 32, 28, 24)
MAX_LINES = 2


class WordLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WordLabel")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self._size: int | None = None

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self._fit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    @property
    def pixel_size(self) -> int | None:
        """The size the text is set in now; None before it has a width."""
        return self._size

    def _fit(self) -> None:
        width = self.contentsRect().width()
        text = self.text()
        if width <= 0 or not text:
            return
        font = QFont(self.font())
        flags = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter)
        for size in SIZES:
            font.setPixelSize(size)
            metrics = QFontMetrics(font)
            needed = metrics.boundingRect(QRect(0, 0, width, 100_000), flags, text)
            longest = max(metrics.horizontalAdvance(part) for part in text.split())
            if needed.height() <= metrics.lineSpacing() * MAX_LINES and longest <= width:
                break
        if size != self._size:
            self._size = size
            # Over the stylesheet's size only; family, weight and colour stay.
            self.setStyleSheet(f"font-size: {size}px;")
        height = needed.height() + 4
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
