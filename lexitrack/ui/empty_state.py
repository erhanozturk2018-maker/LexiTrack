"""Screens shown when there is nothing to review.

An empty screen is a chance to explain the next step, not a dead end, so each
state names what happened and offers the action that follows from it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .theme.palette import METRICS
from .widgets import WrappedLabel


class EmptyState(QWidget):
    """A centred glyph, title, explanation and up to two actions."""

    def __init__(
        self,
        title: str,
        body: str,
        primary_text: str | None = None,
        secondary_text: str | None = None,
        glyph: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        m = METRICS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_6, m.space_6, m.space_6, m.space_6)
        layout.setSpacing(m.space_3)
        layout.addStretch(1)

        if glyph:
            glyph_label = QLabel(glyph)
            glyph_label.setObjectName("EmptyGlyph")
            glyph_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(glyph_label)
            layout.addSpacing(m.space_2)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("EmptyTitle")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label)

        self._body_label = WrappedLabel(body, width=460)
        self._body_label.setObjectName("EmptyBody")
        layout.addWidget(self._body_label, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addSpacing(m.space_4)

        actions = QHBoxLayout()
        actions.setSpacing(m.space_2)
        actions.addStretch(1)

        self.primary_button: QPushButton | None = None
        if primary_text:
            self.primary_button = QPushButton(primary_text)
            self.primary_button.setProperty("variant", "primary")
            self.primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.primary_button.setMinimumWidth(170)
            self.primary_button.setMinimumHeight(40)
            actions.addWidget(self.primary_button)

        self.secondary_button: QPushButton | None = None
        if secondary_text:
            self.secondary_button = QPushButton(secondary_text)
            self.secondary_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.secondary_button.setMinimumWidth(170)
            self.secondary_button.setMinimumHeight(40)
            actions.addWidget(self.secondary_button)

        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)

    def set_body(self, text: str) -> None:
        self._body_label.setText(text)


class WelcomeState(EmptyState):
    """Shown before any list exists."""

    import_requested = Signal()
    create_list_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            title="No vocabulary yet",
            body=(
                "Import a word list to get started — a PDF such as the Oxford 3000, "
                "any text-based PDF, or a LexiTrack JSON file. Or create an empty list "
                "and type your own words."
            ),
            primary_text="Import…",
            secondary_text="New List",
            parent=parent,
        )
        if self.primary_button is not None:
            self.primary_button.clicked.connect(self.import_requested.emit)
        if self.secondary_button is not None:
            self.secondary_button.clicked.connect(self.create_list_requested.emit)
