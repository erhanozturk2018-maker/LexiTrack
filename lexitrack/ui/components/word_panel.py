"""The details panel beside the vocabulary table.

Selecting a row shows the word next to the table, so reading a definition
never means opening and closing a window. The panel follows the current row
as the arrow keys move.

It shows a word as LexiTrack keeps it — the word, its length, CEFR level and
part of speech, its definition and its contexts — and lets both be edited:
the definition changed, a context added or deleted. Below that, where the
word stands in learning, with its history one click away; the status
buttons, which ask the table's owner to change the status exactly as
K / U / R do; and deleting the word altogether.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.errors import LexiTrackError
from ...models.context import WordContext, contains_word
from ...models.user_word_state import ReviewStatus
from ...repositories.word_repository import StoredWord
from ..theme.palette import METRICS
from .status import StatusBadge
from .word_history import WordHistoryDialog, journey_summary

PANEL_WIDTH = 320


class WordEditor(Protocol):
    """What the panel needs to change a word: the vocabulary service."""

    def contexts(self, word_id: int) -> tuple[WordContext, ...]: ...
    def add_context(self, word_id: int, text: str) -> WordContext | None: ...
    def delete_context(self, context_id: int) -> bool: ...
    def set_definition(self, word_id: int, definition: str) -> StoredWord: ...
    def delete_words(self, word_ids: list[int]) -> int: ...


class WordPanel(QFrame):
    """One word's details, its contexts, and explicit status buttons."""

    #: Set this word id to this status.
    status_requested = Signal(int, object)
    #: The word shown was changed here (its definition): the new version.
    word_changed = Signal(object)
    #: The word shown was deleted here, by id.
    word_deleted = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WordPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(PANEL_WIDTH)
        self.word: StoredWord | None = None
        #: Supplies a word's learning history; set by the main window, so
        #: the panel itself needs no service.
        self._history_source: Callable[[int], object] | None = None
        #: Reads and changes definitions and contexts; set by the main window.
        self._editor: WordEditor | None = None
        #: Asks before deleting a word; set by the owner, defaults to yes.
        self.confirm_delete: Callable[[StoredWord], bool] = lambda _word: True
        #: The selection bar floats over the bottom of the panel.
        self._covered = False
        self._build()

    def set_buttons_covered(self, covered: bool) -> None:
        """Hide the status buttons while the selection bar floats over them.

        The bar carries the same actions, for the whole selection, so nothing
        becomes unreachable; half-hidden buttons under it would only mislead.
        """
        self._covered = covered
        self.button_row.setVisible(not covered)
        self.delete_button.setVisible(not covered and self._editor is not None)

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

        # The definition, and editing it in place.
        layout.addSpacing(m.space_3)
        header = QHBoxLayout()
        header.addWidget(_section("DEFINITION"))
        header.addStretch(1)
        self.edit_button = _link("Edit", "Change the definition: one text covering all its senses")
        self.edit_button.clicked.connect(self._edit_definition)
        header.addWidget(self.edit_button)
        layout.addLayout(header)
        self.definition = _text()
        layout.addWidget(self.definition)
        self.definition_field = QPlainTextEdit()
        self.definition_field.setObjectName("DefinitionField")
        self.definition_field.setFixedHeight(84)
        self.definition_field.setAccessibleName("Definition")
        self.definition_field.hide()
        layout.addWidget(self.definition_field)
        self.definition_buttons = QWidget()
        self.definition_buttons.setObjectName("PanelBody")
        row = QHBoxLayout(self.definition_buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("variant", "ghost")
        cancel.setProperty("compact", True)
        cancel.clicked.connect(self._cancel_definition)
        row.addWidget(cancel)
        save = QPushButton("Save")
        save.setProperty("variant", "primary")
        save.setProperty("compact", True)
        save.clicked.connect(self._save_definition)
        row.addWidget(save)
        self.definition_buttons.hide()
        layout.addWidget(self.definition_buttons)

        # The contexts: each deletable, and a line to add another.
        layout.addSpacing(m.space_3)
        self.contexts_title = _section("CONTEXTS")
        layout.addWidget(self.contexts_title)
        self.contexts_box = QVBoxLayout()
        self.contexts_box.setSpacing(m.space_1)
        layout.addLayout(self.contexts_box)
        self.no_contexts = _text("Faint")
        self.no_contexts.setText("No contexts yet. A sentence using the word lets it be "
                                 "asked from a context as well as its definition.")
        layout.addWidget(self.no_contexts)
        add_row = QHBoxLayout()
        add_row.setSpacing(m.space_2)
        self.context_field = QLineEdit()
        self.context_field.setPlaceholderText("Add a sentence using the word")
        self.context_field.setAccessibleName("New context")
        self.context_field.returnPressed.connect(self._add_context)
        add_row.addWidget(self.context_field, 1)
        self.add_button = QPushButton("Add")
        self.add_button.setProperty("compact", True)
        self.add_button.clicked.connect(self._add_context)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)
        self.context_message = _text("PanelMessage")
        self.context_message.hide()
        layout.addWidget(self.context_message)

        layout.addSpacing(m.space_3)
        self.learning_title = _section("LEARNING")
        layout.addWidget(self.learning_title)
        self.learning = _text()
        layout.addWidget(self.learning)
        self.history_button = _link("Show history →", "Every answer and every change of status")
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
        self.delete_button = _link(
            "Delete word…",
            "Remove it from LexiTrack: every list, its contexts, status and history",
        )
        self.delete_button.setObjectName("DangerLink")
        self.delete_button.clicked.connect(self._delete_word)
        layout.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignLeft)

        scroll = QScrollArea()
        scroll.setObjectName("PanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self._stack.addWidget(scroll)

    # -- content -----------------------------------------------------------

    def show_word(self, word: StoredWord | None) -> None:
        if word is not None and self.word is not None and word.id != self.word.id:
            self.context_field.clear()
        self.word = word
        self._cancel_definition()
        self._message(None)
        if word is None:
            self._stack.setCurrentIndex(0)
            return
        self._stack.setCurrentIndex(1)
        self.title.setText(word.word)
        self.badge.set_status(word.status)
        letters = "letter" if word.length == 1 else "letters"
        meta = "  ·  ".join(
            p for p in (f"{word.length} {letters}", word.cefr_level, word.part_of_speech) if p
        )
        self.meta.setText(meta)
        self.definition.setText(word.definition or "No definition yet")
        self.definition.setObjectName("" if word.definition else "Faint")
        self.definition.style().polish(self.definition)
        self.edit_button.setText("Edit" if word.definition else "Add")
        editable = self._editor is not None
        for widget in (self.edit_button, self.context_field, self.add_button):
            widget.setVisible(editable)
        self.delete_button.setVisible(editable and not self._covered)
        for status, button in self.buttons.items():
            button.setEnabled(status is not word.status)
        self._show_contexts()
        self._show_learning(word)

    def set_history_source(self, source: Callable[[int], object]) -> None:
        self._history_source = source
        if self.word is not None:
            self._show_learning(self.word)

    def set_editor(self, editor: WordEditor) -> None:
        """What reads and changes definitions and contexts; without one the
        panel only shows the definition."""
        self._editor = editor
        if self.word is not None:
            self.show_word(self.word)

    def context_texts(self) -> list[str]:
        """The contexts on screen, in order. For tests and accessibility."""
        texts = []
        for index in range(self.contexts_box.count()):
            row = self.contexts_box.itemAt(index).widget()
            label = row.findChild(QLabel, "ContextText") if row is not None else None
            if label is not None:
                texts.append(label.text())
        return texts

    def _show_contexts(self) -> None:
        while self.contexts_box.count():
            item = self.contexts_box.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        word = self.word
        contexts = self._editor.contexts(word.id) if word is not None and self._editor else ()
        count = len(contexts)
        self.contexts_title.setText(f"CONTEXTS · {count}" if count else "CONTEXTS")
        self.no_contexts.setVisible(not count)
        for number, context in enumerate(contexts, start=1):
            self.contexts_box.addWidget(self._context_row(number, context))

    def _context_row(self, number: int, context: WordContext) -> QWidget:
        row = QWidget()
        row.setObjectName("ContextRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space_2)
        index = QLabel(str(number))
        index.setObjectName("ContextNumber")
        index.setFixedWidth(14)
        layout.addWidget(index, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(context.text)
        text.setObjectName("ContextText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(text, 1)
        remove = QPushButton("×")
        remove.setObjectName("FooterAction")
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setToolTip("Delete this context")
        remove.setAccessibleName(f"Delete context {number}")
        remove.clicked.connect(lambda _c=False, i=context.id: self._delete_context(i))
        layout.addWidget(remove, 0, Qt.AlignmentFlag.AlignTop)
        return row

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

    # -- changing it -------------------------------------------------------

    def _request(self, status: ReviewStatus) -> None:
        if self.word is not None:
            self.status_requested.emit(self.word.id, status)

    def _add_context(self) -> None:
        word = self.word
        text = self.context_field.text().strip()
        if word is None or self._editor is None or not text:
            return
        try:
            added = self._editor.add_context(word.id, text)
        except LexiTrackError as exc:
            self._message(exc.user_message, bad=True)
            return
        if added is None:
            self._message("It already has that sentence.", bad=True)
            return
        self.context_field.clear()
        self._show_contexts()
        if not contains_word(added.text, word.word):
            self._message(
                f"Added. The sentence does not seem to contain “{word.word}”: the "
                "context question picks the word out in it."
            )
        else:
            self._message(None)

    def _delete_context(self, context_id: int | None) -> None:
        if context_id is None or self._editor is None:
            return
        self._editor.delete_context(context_id)
        self._message(None)
        self._show_contexts()

    def _edit_definition(self) -> None:
        if self.word is None:
            return
        self.definition_field.setPlainText(self.word.definition or "")
        self.definition.hide()
        self.edit_button.hide()
        self.definition_field.show()
        self.definition_buttons.show()
        self.definition_field.setFocus()

    def _cancel_definition(self) -> None:
        self.definition_field.hide()
        self.definition_buttons.hide()
        self.definition.show()
        self.edit_button.setVisible(self._editor is not None)

    def _save_definition(self) -> None:
        word = self.word
        if word is None or self._editor is None:
            return
        try:
            updated = self._editor.set_definition(word.id, self.definition_field.toPlainText())
        except LexiTrackError as exc:
            self._message(exc.user_message, bad=True)
            return
        self.show_word(updated)
        self.word_changed.emit(updated)

    def _delete_word(self) -> None:
        word = self.word
        if word is None or self._editor is None or not self.confirm_delete(word):
            return
        self._editor.delete_words([word.id])
        self.show_word(None)
        self.word_deleted.emit(word.id)

    def _message(self, text: str | None, bad: bool = False) -> None:
        self.context_message.setText(text or "")
        self.context_message.setProperty("tone", "bad" if bad else "")
        self.context_message.style().polish(self.context_message)
        self.context_message.setVisible(bool(text))


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


def _link(text: str, tooltip: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("LinkButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(tooltip)
    return button
