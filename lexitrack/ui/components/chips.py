"""Chips, the week strip and the day's progress: the Study page's parts.

Each reuses the look of something the app already has — a chip is a pill in
the colours of the list tags, a day tile is a small stat tile, the progress
line is the list cards' segmented bar — so the Study page reads as part of the
same application as Home.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import current_palette
from .cards import repolish


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps onto new lines.

    Qt has no built-in flow layout; this is the standard one from the Qt
    examples, with ``heightForWidth`` so a scroll area gives it the height it
    actually needs.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def clear(self) -> None:
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _arrange(self, rect: QRect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line:
                x = rect.x()
                y += line + self._spacing
                line = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y()


class _ClickableChip(QLabel):
    """A chip that opens something: a word's history, on the Study page."""

    def __init__(self, text: str, on_click) -> None:
        super().__init__(text)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("clickable", "true")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)


def chip(
    text: str,
    tone: str | None = None,
    tooltip: str | None = None,
    on_click=None,
) -> QLabel:
    label = _ClickableChip(text, on_click) if on_click is not None else QLabel(text)
    label.setObjectName("Chip")
    if tone:
        label.setProperty("tone", tone)
    if tooltip:
        label.setToolTip(tooltip)
    return label


class ChipFlow(QWidget):
    """A wrapping row of chips that can be refilled."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.flow = FlowLayout(self)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_chips(self, chips: Iterable[QLabel]) -> None:
        self.flow.clear()
        for item in chips:
            self.flow.addWidget(item)
        self.updateGeometry()

    def texts(self) -> list[str]:
        return [
            item.widget().text()
            for item in (self.flow.itemAt(i) for i in range(self.flow.count()))
            if item is not None and item.widget() is not None
        ]


class DayProgress(QWidget):
    """The day's work as one thin bar: learned in green, reviewed in accent.

    The same segmented look as the progress bars on Home's list cards, so a
    finished day and a finished list look alike.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parts: tuple[float, float] = (0.0, 0.0)
        self.setFixedHeight(6)

    def set_parts(self, learned: float, reviewed: float) -> None:
        """Shares of the day, each between 0 and 1, together at most 1."""
        learned = min(max(learned, 0.0), 1.0)
        reviewed = min(max(reviewed, 0.0), 1.0 - learned)
        self._parts = (learned, reviewed)
        self.setAccessibleName(f"{round((learned + reviewed) * 100)} percent of today done")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        painter.fillPath(track, QColor(palette.surface_sunken))
        painter.setClipPath(track)
        learned, reviewed = self._parts
        width = rect.width()
        painter.fillRect(QRectF(0, 0, width * learned, rect.height()), QColor(palette.known))
        painter.fillRect(
            QRectF(width * learned, 0, width * reviewed, rect.height()), QColor(palette.accent)
        )
        painter.end()


class _MiniBar(QWidget):
    """The small column at the bottom of a day tile."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._share = 0.0
        self._tone = "normal"
        self.setFixedHeight(22)

    def set_share(self, share: float, tone: str) -> None:
        self._share = min(max(share, 0.0), 1.0)
        self._tone = tone
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._share <= 0:
            return
        palette = current_palette()
        colour = {
            "today": palette.accent,
            "over": palette.unknown,
        }.get(self._tone, palette.border_strong)
        height = max(self.height() * self._share, 3)
        width = min(self.width() * 0.3, 16)
        rect = QRectF((self.width() - width) / 2, self.height() - height, width, height)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        painter.fillPath(path, QColor(colour))
        painter.end()


class DayTile(QFrame):
    """One day of the week strip: its name, its count, a small bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DayTile")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(0)
        self.name = QLabel()
        self.name.setObjectName("DayName")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count = QLabel()
        self.count.setObjectName("DayCount")
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar = _MiniBar()
        layout.addWidget(self.name)
        layout.addWidget(self.count)
        layout.addSpacing(4)
        layout.addWidget(self.bar)

    def set_day(self, name: str, count: int, share: float, today: bool, over: bool) -> None:
        self.name.setText(name)
        self.count.setText(str(count))
        self.count.setProperty("empty", "true" if not count else "false")
        self.setProperty("today", "true" if today else "false")
        self.setProperty("over", "true" if over and not today else "false")
        tone = "today" if today else "over" if over else "normal"
        self.bar.set_share(share, tone)
        repolish(self)
        repolish(self.count)
        repolish(self.name)
        self.setAccessibleName(f"{name}: {count} reviews")


class WeekStrip(QWidget):
    """Seven day tiles. The number always sits in the same place."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.tiles = [DayTile() for _ in range(7)]
        for tile in self.tiles:
            layout.addWidget(tile, 1)

    def set_forecast(self, days: tuple[tuple[str, int], ...], capacity: int = 0) -> None:
        peak = max((count for _, count in days), default=0) or 1
        for index, tile in enumerate(self.tiles):
            if index >= len(days):
                tile.setVisible(False)
                continue
            tile.setVisible(True)
            day, count = days[index]
            name = "Today" if index == 0 else _weekday(day)
            tile.set_day(
                name,
                count,
                count / peak,
                today=index == 0,
                over=bool(capacity) and count > capacity,
            )


def _weekday(local_date: str) -> str:
    try:
        return date.fromisoformat(local_date).strftime("%a")
    except ValueError:
        return local_date[5:]
