"""Keyboard Shortcuts: every key in one place, and nowhere else.

Shortcuts are not printed around the app (under the flashcard, in buttons);
they are collected here and in the Ctrl+K palette. The window scrolls and is
sized to the screen, so it always fits, and a filter finds a key or an action
by name.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme.palette import METRICS

#: ``(section, [(keys, what it does), ...])``. Keys use " / " between
#: alternatives and "+" inside a combination.
Section = tuple[str, Sequence[tuple[str, str]]]

FLASHCARD_KEYS: Section = (
    "Flashcards",
    (
        ("K", "I Know"),
        ("U", "I Don't Know"),
        ("← / Backspace", "Previous word. Its status is not changed."),
        ("→", "Next word, after going back. Its status is not changed."),
        ("Enter / Space", "Repeat your last answer. On an earlier word: move forward without "
                          "changing it."),
        ("R", "Reset the word on screen to Not Reviewed"),
    ),
)

TABLE_KEYS: Section = (
    "List mode and Unknown Words",
    (
        ("↑ / ↓", "Move between words; the details panel follows"),
        ("Shift+↑ / Shift+↓", "Extend the selection"),
        ("Ctrl+A", "Select all visible words"),
        ("K / U / R", "Mark the selection Known / Unknown / Not Reviewed"),
        ("C / M", "Copy / move the selection to another list"),
        ("Menu / Right-click", "Every action for the selection"),
        ("Enter", "Open the details panel"),
        ("Delete", "Remove the selection from this list (asks first)"),
        ("Ctrl+F", "Search"),
        ("Esc", "Clear the selection"),
        ("Ctrl+Z", "Undo a copy or move while its message is showing"),
    ),
)

STUDY_KEYS: Section = (
    "Today's session",
    (
        ("Space", "Show the meaning; once it is showing, answer Good"),
        ("1 / 2 / 3 / 4", "Again / Hard / Good / Easy"),
        ("Esc", "End the session and keep what you answered"),
        ("Ctrl+Z", "Take back your last answer"),
    ),
)

HOME_KEYS: Section = (
    "Lists",
    (
        ("← ↑ → ↓", "Move between lists"),
        ("Enter", "Open the list"),
        ("Menu / Shift+F10", "Everything else for a list"),
    ),
)


class ShortcutsDialog(QDialog):
    def __init__(self, sections: Sequence[Section], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self._rows: list[tuple[QWidget, QWidget, str]] = []
        self._section_labels: list[tuple[QLabel, list[int]]] = []
        m = METRICS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_4)
        layout.setSpacing(m.space_3)

        title = QLabel("Keyboard Shortcuts")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Find a key or an action")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)

        content = QWidget()
        content.setObjectName("PanelBody")
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, m.space_3, 0)
        grid.setHorizontalSpacing(m.space_4)
        grid.setVerticalSpacing(m.space_2)
        grid.setColumnStretch(1, 1)
        row = 0
        for name, entries in sections:
            label = QLabel(name.upper())
            label.setObjectName("SectionTitle")
            if row:
                grid.setRowMinimumHeight(row, m.space_3)
                row += 1
            grid.addWidget(label, row, 0, 1, 2)
            row += 1
            indices = []
            for keys, what in entries:
                caps = _key_caps(keys)
                text = QLabel(what)
                text.setWordWrap(True)
                grid.addWidget(caps, row, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(text, row, 1)
                indices.append(len(self._rows))
                self._rows.append((caps, text, f"{keys} {what} {name}".casefold()))
                row += 1
            self._section_labels.append((label, indices))
        grid.setRowStretch(row, 1)

        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.resize(640, self._fitting_height(content))

    def _fitting_height(self, content: QWidget) -> int:
        screen = (self.parentWidget() or self).screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 800
        wanted = content.sizeHint().height() + 170
        return max(360, min(wanted, int(available * 0.8)))

    def _apply_filter(self, text: str) -> None:
        query = text.strip().casefold()
        for caps, label, haystack in self._rows:
            visible = not query or all(part in haystack for part in query.split())
            caps.setVisible(visible)
            label.setVisible(visible)
        for label, indices in self._section_labels:
            label.setVisible(any(not self._rows[i][1].isHidden() for i in indices))


def _key_caps(keys: str) -> QWidget:
    """``"Ctrl+Shift+Tab / Ctrl+Tab"`` as key caps with separators."""
    holder = QWidget()
    holder.setObjectName("PanelBody")
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    for index, alternative in enumerate(part.strip() for part in keys.split(" / ")):
        if index:
            slash = QLabel("/")
            slash.setObjectName("Faint")
            row.addWidget(slash)
        # A lone "+" is a key; otherwise "+" joins the keys of a combination.
        combination = alternative.split("+") if len(alternative) > 1 else [alternative]
        for position, key in enumerate(combination):
            if position:
                plus = QLabel("+")
                plus.setObjectName("Faint")
                row.addWidget(plus)
            cap = QLabel(key)
            cap.setObjectName("KeyCap")
            row.addWidget(cap)
    row.addStretch(1)
    holder.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    holder.setFixedWidth(230)
    return holder
