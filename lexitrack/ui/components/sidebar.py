"""The navigation sidebar: where every page of the window is reached.

A column on the left rather than tabs across the top, for three reasons:

* The destinations are grouped. *Today* and *Progress* are about learning;
  *Lists*, *Sort words* and *Unknown* are the library the learning draws on;
  *Export and backup* and *Settings* sit at the foot, out of the way. A row
  of tabs can only list them.
* Each item can carry a count — words waiting today, unknown words — so the
  sidebar answers "is there anything to do?" from any page.
* There is room to grow. Pages to come (backups, the content workflow) are
  one more row, not a squeeze on the tab strip.

On a narrow window the sidebar folds to its icons; the labels move into
tooltips and nothing becomes unreachable.

Items are painted rather than styled, like the table's status pills, so the
icon, the label and the count change colour together and follow the theme.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPaintEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..theme import current_palette
from ..theme.palette import METRICS

#: Width of the sidebar, open and folded.
WIDE = 216
FOLDED = 60

#: Outline icons, 24-unit grid, drawn with the item's current colour.
ICONS: dict[str, str] = {
    "today": '<circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l5.5-3.5z"/>',
    "progress": '<path d="M4 20h16"/><path d="M7 16v-5"/><path d="M12 16V7"/>'
    '<path d="M17 16v-8"/>',
    "lists": '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/>'
    '<path d="M4.5 6h.01"/><path d="M4.5 12h.01"/><path d="M4.5 18h.01"/>',
    "sort": '<path d="M7 4v16"/><path d="M4 7l3-3 3 3"/><path d="M17 20V4"/>'
    '<path d="M14 17l3 3 3-3"/>',
    "unknown": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4'
    "c-.6.3-1 .8-1 1.5v.4\"/><path d=\"M12 16.8v.01\"/>",
    "export": '<path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>'
    '<path d="M7.5 10.5L12 15l4.5-4.5"/><path d="M12 4v11"/>',
    "settings": '<path d="M4 6h9"/><path d="M17 6h3"/><path d="M4 12h3"/><path d="M11 12h9"/>'
    '<path d="M4 18h11"/><path d="M19 18h1"/><circle cx="15" cy="6" r="2"/>'
    '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
}

_renderers: dict[tuple[str, str], QSvgRenderer] = {}


def _renderer(icon: str, color: str) -> QSvgRenderer:
    key = (icon, color)
    renderer = _renderers.get(key)
    if renderer is None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round">{ICONS[icon]}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        _renderers[key] = renderer
    return renderer


class NavItem(QAbstractButton):
    """One row: icon, label, and an optional count or key hint on the right."""

    HEIGHT = 36

    def __init__(
        self,
        text: str,
        icon: str,
        *,
        checkable: bool = True,
        shortcut: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(text)
        self._icon = icon
        self._badge = ""
        self._hint = ""
        self._compact = False
        self._shortcut = shortcut
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Tab reaches the items, a click does not leave a focus ring behind.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if shortcut:
            self.setShortcut(QKeySequence(shortcut))
        self._update_tooltip()

    # -- state -------------------------------------------------------------

    @property
    def badge(self) -> str:
        return self._badge

    def set_badge(self, value: int | str | None) -> None:
        """A count shown on the right; zero or None shows nothing."""
        if isinstance(value, int):
            value = f"{value:,}" if value else ""
        self._badge = value or ""
        self._update_tooltip()
        self.update()

    def set_hint(self, text: str) -> None:
        """Faint text on the right, such as a shortcut, when there is no count."""
        self._hint = text
        self.update()

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self._update_tooltip()
        self.updateGeometry()
        self.update()

    def _update_tooltip(self) -> None:
        parts = [self.text()]
        if self._compact and self._badge:
            parts.append(f"· {self._badge}")
        tip = " ".join(parts)
        if self._shortcut:
            tip += f" ({self._shortcut})"
        self.setToolTip(tip)

    # -- painting ----------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(FOLDED - 16 if self._compact else WIDE - 24, self.HEIGHT)

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = METRICS.radius_sm

        active = self.isChecked()
        hovered = self.underMouse() or self.isDown()
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p.accent_soft))
            painter.drawRoundedRect(rect, radius, radius)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p.surface_sunken))
            painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus():
            painter.setPen(QColor(p.focus_ring))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        color = p.accent if active else (p.text if hovered else p.text_muted)
        size = 18
        top = (self.height() - size) / 2
        left = (self.width() - size) / 2 if self._compact else 10
        _renderer(self._icon, color).render(painter, QRectF(left, top, size, size))

        if self._compact:
            if self._badge:
                # A dot says "something here"; the number is in the tooltip.
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(p.accent))
                painter.drawEllipse(QRectF(left + size - 3, top - 2, 7, 7))
            painter.end()
            return

        font = self.font()
        font.setPixelSize(14)
        font.setWeight(font.Weight.DemiBold if active else font.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(p.text if active or hovered else p.text_muted))
        text_left = left + size + 10
        right_text = self._badge or self._hint
        small = self.font()
        small.setPixelSize(12)
        right_width = 0
        if right_text:
            small.setWeight(font.Weight.DemiBold if self._badge else font.Weight.Normal)
            right_width = QFontMetrics(small).horizontalAdvance(right_text) + 12
        label_width = self.width() - text_left - right_width - 10
        label_rect = QRectF(text_left, 0, label_width, self.height())
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            painter.fontMetrics().elidedText(
                self.text(), Qt.TextElideMode.ElideRight, int(label_rect.width())
            ),
        )
        if right_text:
            painter.setFont(small)
            painter.setPen(QColor(p.accent if active and self._badge else p.text_faint))
            painter.drawText(
                QRectF(0, 0, self.width() - 10, self.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                right_text,
            )
        painter.end()


class Sidebar(QFrame):
    """The column of :class:`NavItem` rows, in groups, with a foot."""

    #: A checkable item was chosen: the page key.
    page_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        m = METRICS
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(m.space_3, m.space_4, m.space_3, m.space_3)
        self._layout.setSpacing(2)
        self.items: dict[str, NavItem] = {}
        self._sections: list[tuple[QLabel, QFrame]] = []
        self._foot_started = False
        self._compact = False

        self.title = QLabel("LexiTrack")
        self.title.setObjectName("AppTitle")
        self.title.setContentsMargins(10, 0, 0, m.space_3)
        self._layout.addWidget(self.title)
        self.setFixedWidth(WIDE)

    # -- building ----------------------------------------------------------

    def add_page(self, key: str, text: str, icon: str, shortcut: str) -> NavItem:
        item = NavItem(text, icon, shortcut=shortcut)
        item.clicked.connect(lambda _c=False, k=key: self.page_requested.emit(k))
        self.items[key] = item
        self._layout.addWidget(item)
        return item

    def add_action(self, text: str, icon: str, shortcut: str | None = None) -> NavItem:
        """A row that does something rather than showing a page."""
        item = NavItem(text, icon, checkable=False, shortcut=shortcut)
        self._layout.addWidget(item)
        return item

    def add_section(self, title: str) -> None:
        label = QLabel(title.upper())
        label.setObjectName("SidebarSection")
        label.setContentsMargins(10, METRICS.space_4, 0, METRICS.space_1)
        rule = QFrame()
        rule.setObjectName("SidebarRule")
        rule.setFixedHeight(1)
        rule.hide()
        self._sections.append((label, rule))
        self._layout.addWidget(label)
        self._layout.addWidget(rule)

    def add_spacing(self, size: int) -> None:
        self._layout.addSpacing(size)

    def start_foot(self) -> None:
        """Everything added after this sits at the bottom."""
        self._layout.addStretch(1)

    def add_widget(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    # -- state -------------------------------------------------------------

    def select(self, key: str) -> None:
        for name, item in self.items.items():
            item.setChecked(name == key)

    @property
    def compact(self) -> bool:
        return self._compact

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedWidth(FOLDED if compact else WIDE)
        if compact:
            # The app's icon stands for the name when there is no room for it.
            self.title.setPixmap(self.window().windowIcon().pixmap(24, 24))
            self.title.setToolTip("LexiTrack")
        else:
            self.title.setText("LexiTrack")
            self.title.setToolTip("")
        self.title.setAlignment(
            Qt.AlignmentFlag.AlignHCenter if compact else Qt.AlignmentFlag.AlignLeft
        )
        self.title.setContentsMargins(0 if compact else 10, 0, 0, METRICS.space_3)
        margin = METRICS.space_2 if compact else METRICS.space_3
        self._layout.setContentsMargins(margin, METRICS.space_4, margin, METRICS.space_3)
        for label, rule in self._sections:
            label.setVisible(not compact)
            rule.setVisible(compact)
        for item in self.findChildren(NavItem):
            item.set_compact(compact)
