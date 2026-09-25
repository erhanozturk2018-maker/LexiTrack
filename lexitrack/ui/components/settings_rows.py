"""Rows of settings: a titled card of rows, each a name, a hint and a control.

Shared by every window that lays out options as a column of explained rows:
Settings, and the word-content window. One look, described once.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..theme.palette import METRICS

#: Width of every number box and drop-down, so they line up down a page.
CONTROL_WIDTH = 160


class SettingsGroup(QWidget):
    """A titled card of settings rows, divided by hairlines.

    Each row is the setting's name and a one-line explanation on the left and
    its control on the right, so what a switch does is read next to the
    switch rather than found in a tooltip. The pattern of macOS System
    Settings and Linear, drawn in the app's own panel style.
    """

    def __init__(self, title: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(METRICS.space_2)
        if title:
            heading = QLabel(title)
            heading.setObjectName("GroupTitle")
            outer.addWidget(heading)
        self._card = QFrame()
        self._card.setObjectName("SettingsGroup")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._rows = QVBoxLayout(self._card)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)
        outer.addWidget(self._card)

    def _divider(self) -> None:
        if self._rows.count():
            line = QFrame()
            line.setObjectName("RowDivider")
            line.setFixedHeight(1)
            self._rows.addWidget(line)

    def add(self, title: str, hint: str | QLabel | None, control: QWidget) -> None:
        self._divider()
        row = QWidget()
        row.setObjectName("SettingRow")
        layout = QHBoxLayout(row)
        m = METRICS
        layout.setContentsMargins(m.space_4, m.space_3, m.space_4, m.space_3)
        layout.setSpacing(m.space_4)
        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(title)
        name.setObjectName("SettingTitle")
        text.addWidget(name)
        if isinstance(hint, QLabel):
            text.addWidget(hint)
        elif hint:
            label = QLabel(hint)
            label.setObjectName("SettingHint")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.addWidget(label)
        layout.addLayout(text, 1)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        control.setAccessibleName(title)
        name.setBuddy(control)
        self._rows.addWidget(row)

    def add_widget(self, widget: QWidget) -> None:
        self._divider()
        holder = QWidget()
        holder.setObjectName("SettingRow")
        layout = QVBoxLayout(holder)
        m = METRICS
        layout.setContentsMargins(m.space_4, m.space_3, m.space_4, m.space_3)
        layout.addWidget(widget)
        self._rows.addWidget(holder)


def switch() -> QCheckBox:
    """An on/off control whose label is the row's title."""
    box = QCheckBox()
    box.setCursor(Qt.CursorShape.PointingHandCursor)
    return box


def page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    m = METRICS
    page = QWidget()
    page.setObjectName("PanelBody")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
    layout.setSpacing(m.space_4)
    heading = QLabel(title)
    heading.setObjectName("PageTitle")
    layout.addWidget(heading)
    hint = QLabel(subtitle)
    hint.setObjectName("PageSubtitle")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    return page, layout


def scrolled(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll




def spin(minimum: int, maximum: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSuffix(suffix)
    # One width for every number, so the controls form a straight column.
    spin.setFixedWidth(CONTROL_WIDTH)
    return spin


def note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label
