"""The details panel beside the vocabulary table.

Selecting a row shows everything about that word next to the table, so
reading a definition never means opening and closing a window. The panel
follows the current row as the arrow keys move, and its status buttons ask the
table's owner to change the status exactly as K / U / R do — looking at a word
here never changes it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...models.language import language_name
from ...models.user_word_state import ReviewStatus
from ...repositories.word_repository import StoredWord
from ..theme.palette import METRICS
from .status import StatusBadge
from .word_history import WordHistoryDialog, journey_summary

PANEL_WIDTH = 320


class WordPanel(QFrame):
    """One word's details, with explicit status buttons."""

    #: Set this word id to this status.
    status_requested = Signal(int, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WordPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(PANEL_WIDTH)
        self.word: StoredWord | None = None
        #: Supplies a word's learning history; set by the main window, so
        #: the panel itself needs no service.
        self._history_source: Callable[[int], object] | None = None
        self._build()

    def set_buttons_covered(self, covered: bool) -> None:
        """Hide the status buttons while the selection bar floats over them.

        The bar carries the same actions, for the whole selection, so nothing
        becomes unreachable; half-hidden buttons under it would only mislead.
        """
        self.button_row.setVisible(not covered)

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self.placeholder = QLabel("Select a word to see its details")
        self.placeholder.setObjectName("Faint")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self._stack.addWidget(self.placeholder)

        body = QWidget()
        body.setObjectName("PanelBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        layout.setSpacing(m.space_2)

        self.badge = StatusBadge()
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignLeft)
        self.title = QLabel()
        self.title.setObjectName("PanelWord")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.title)
        self.meta = _text("Muted")
        layout.addWidget(self.meta)

        layout.addSpacing(m.space_3)
        self.definition_title = _section("DEFINITION")
        layout.addWidget(self.definition_title)
        self.definition = _text()
        layout.addWidget(self.definition)
        self.note_title = _section("NOTE")
        layout.addWidget(self.note_title)
        self.note = _text("PanelNote")
        layout.addWidget(self.note)
        self.example_title = _section("EXAMPLE")
        layout.addWidget(self.example_title)
        self.example = _text("PanelExample")
        layout.addWidget(self.example)

        layout.addSpacing(m.space_3)
        self.lists = self._field(layout, "LISTS")
        self.source = self._field(layout, "SOURCE")
        self.language = self._field(layout, "LANGUAGE")
        self.learning_title = _section("LEARNING")
        layout.addWidget(self.learning_title)
        self.learning = _text()
        layout.addWidget(self.learning)
        self.history_button = QPushButton("Show history \u2192")
        self.history_button.setObjectName("LinkButton")
        self.history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_button.setToolTip("Every answer and every change of status")
        self.history_button.clicked.connect(self.open_history)
        layout.addWidget(self.history_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.learning_title.hide()
        self.learning.hide()
        self.history_button.hide()
        layout.addStretch(1)

        self.button_row = QWidget()
        self.button_row.setObjectName("PanelBody")
        buttons = QHBoxLayout(self.button_row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(m.space_2)
        self.buttons: dict[ReviewStatus, QPushButton] = {}
        for status, text, key in (
            (ReviewStatus.KNOWN, "✓  Known", "K"),
            (ReviewStatus.UNKNOWN, "?  Unknown", "U"),
            (ReviewStatus.NOT_REVIEWED, "↺  Reset", "R"),
        ):
            button = QPushButton(text)
            button.setObjectName("PanelStatusButton")
            button.setToolTip(f"Mark this word {text.split()[-1]} ({key})")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, s=status: self._request(s))
            buttons.addWidget(button, 1)
            self.buttons[status] = button
        layout.addWidget(self.button_row)

        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self._stack.addWidget(scroll)

    def _field(self, layout: QVBoxLayout, title: str) -> QLabel:
        layout.addWidget(_section(title))
        value = _text()
        layout.addWidget(value)
        layout.addSpacing(METRICS.space_1)
        return value

    # -- content -----------------------------------------------------------

    def show_word(self, word: StoredWord | None) -> None:
        self.word = word
        if word is None:
            self._stack.setCurrentIndex(0)
            return
        self._stack.setCurrentIndex(1)
        self.title.setText(word.word)
        self.badge.set_status(word.status)
        meta = "  ·  ".join(p for p in (word.part_of_speech, word.cefr_level) if p)
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))

        self.definition.setText(word.definition or "No definition yet")
        self.definition.setObjectName("" if word.definition else "Faint")
        self.definition.style().polish(self.definition)
        has_note = bool(word.note)
        self.note_title.setVisible(has_note)
        self.note.setVisible(has_note)
        self.note.setText(word.note or "")
        has_example = bool(word.example)
        self.example_title.setVisible(has_example)
        self.example.setVisible(has_example)
        self.example.setText(f"“{word.example}”" if has_example else "")

        self.lists.setText(word.list_label or "—")
        self.source.setText(word.source_label or "—")
        self.language.setText(language_name(word.language))
        for status, button in self.buttons.items():
            button.setEnabled(status is not word.status)
        self._show_learning(word)

    def set_history_source(self, source: Callable[[int], object]) -> None:
        self._history_source = source
        if self.word is not None:
            self._show_learning(self.word)

    def _show_learning(self, word: StoredWord) -> None:
        journey = self._history_source(word.id) if self._history_source else None
        visible = journey is not None
        self.learning_title.setVisible(visible)
        self.learning.setVisible(visible)
        self.history_button.setVisible(visible)
        if journey is not None:
            self.learning.setText(journey_summary(journey))

    def open_history(self) -> None:
        if self.word is None or self._history_source is None:
            return
        journey = self._history_source(self.word.id)
        if journey is not None:
            WordHistoryDialog(journey, parent=self.window()).exec()

    def hide_status(self, status: ReviewStatus) -> None:
        """Leave out a status that makes no sense where the panel is shown."""
        self.buttons[status].setVisible(False)

    def _request(self, status: ReviewStatus) -> None:
        if self.word is not None:
            self.status_requested.emit(self.word.id, status)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def _text(name: str | None = None) -> QLabel:
    label = QLabel()
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        label.setObjectName(name)
    return label
