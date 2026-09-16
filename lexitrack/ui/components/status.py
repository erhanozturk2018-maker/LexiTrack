"""How a learning status is shown.

Status is never communicated by colour alone. Every presentation pairs a
symbol with a word — ✓ Known, ? Unknown, – Not reviewed — so it survives
colour blindness, greyscale printing and a glance from across the room.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from ...models.user_word_state import ReviewStatus
from ..theme import current_palette

STATUS_SYMBOLS: dict[ReviewStatus, str] = {
    ReviewStatus.KNOWN: "✓",
    ReviewStatus.UNKNOWN: "?",
    ReviewStatus.NOT_REVIEWED: "–",
}

STATUS_NAMES: dict[ReviewStatus, str] = {
    ReviewStatus.KNOWN: "Known",
    ReviewStatus.UNKNOWN: "Unknown",
    ReviewStatus.NOT_REVIEWED: "Not reviewed",
}

#: Sort order used by tables: what still needs attention first.
STATUS_SORT_ORDER: dict[ReviewStatus, int] = {
    ReviewStatus.UNKNOWN: 0,
    ReviewStatus.NOT_REVIEWED: 1,
    ReviewStatus.KNOWN: 2,
}


def status_text(status: ReviewStatus) -> str:
    return f"{STATUS_SYMBOLS[status]}  {STATUS_NAMES[status]}"


class StatusBadge(QLabel):
    """A pill showing one status."""

    def __init__(
        self, status: ReviewStatus = ReviewStatus.NOT_REVIEWED, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(status)

    def set_status(self, status: ReviewStatus) -> None:
        self.status = status
        self.setText(status_text(status))
        self.setProperty("status", status.value)
        self.setAccessibleName(f"Status: {STATUS_NAMES[status]}")
        self.style().unpolish(self)
        self.style().polish(self)


#: Item-data role carrying a :class:`ReviewStatus` value for :class:`StatusDelegate`.
STATUS_ROLE = Qt.ItemDataRole.UserRole + 20


class StatusDelegate(QStyledItemDelegate):
    """Paints a status pill inside a table cell, in the current theme's colours."""

    _H_PADDING = 10
    _HEIGHT = 22

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        # Qt hands a StrEnum back as a plain str, so the role carries the value.
        value = index.data(STATUS_ROLE)
        try:
            status = ReviewStatus(value)
        except ValueError:
            super().paint(painter, option, index)
            return

        # Let the style draw selection and hover backgrounds first.
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        palette = current_palette()
        background, foreground = {
            ReviewStatus.KNOWN: (palette.known_soft, palette.known_text),
            ReviewStatus.UNKNOWN: (palette.unknown_soft, palette.unknown_text),
            ReviewStatus.NOT_REVIEWED: (palette.surface_sunken, palette.text_muted),
        }[status]

        text = status_text(status)
        font = QFont(option.font)
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 2 * self._H_PADDING
        rect = option.rect
        pill = QRectF(
            rect.left() + 10,
            rect.top() + (rect.height() - self._HEIGHT) / 2,
            width,
            self._HEIGHT,
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(pill, self._HEIGHT / 2, self._HEIGHT / 2)
        painter.fillPath(path, QColor(background))
        painter.setPen(QColor(foreground))
        painter.setFont(font)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        hint = super().sizeHint(option, index)
        return QSize(max(hint.width(), 130), max(hint.height(), self._HEIGHT + 12))
