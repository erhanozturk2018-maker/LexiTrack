"""The details panel beside the vocabulary table.

Selecting a row shows the word next to the table, so reading a definition
never means opening and closing a window. The panel follows the current row
as the arrow keys move.

It shows a word as LexiTrack keeps it — the word, its length, CEFR level and
part of speech, its definition and its contexts — and, below that, where the
word stands in learning, with its history one click away; the status
buttons, which ask the table's owner to change the status exactly as
K / U / R do; and deleting the word altogether.

The definition and contexts change only in edit mode (Edit, or E on the
table, or typing in "Add a sentence"). Nothing is written until Save, and
Cancel throws everything away: a context marked with × is struck through,
not deleted, and only goes when Save is pressed — after a question naming
the sentences, since that cannot be undone. Edits that would be lost by
moving to another word are offered to be saved first.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QResizeEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.errors import LexiTrackError
from ...models.context import WordContext, clean_context, contains_word, same_context
from ...models.user_word_state import ReviewStatus
from ...repositories.word_repository import StoredWord
from ...services.vocabulary_service import WordEdit
from ..dialogs import ask_unsaved, confirm
from ..theme.palette import METRICS
from .status import StatusBadge
from .word_history import WordHistoryDialog, journey_summary

PANEL_WIDTH = 320

#: A context editor grows with its sentence up to this many lines, then scrolls.
_MAX_LINES = 4


class WordEditor(Protocol):
    """What the panel needs to change a word: the vocabulary service."""

    def contexts(self, word_id: int) -> tuple[WordContext, ...]: ...
    def save_word(self, word_id: int, edit: WordEdit) -> StoredWord: ...
    def delete_words(self, word_ids: list[int]) -> int: ...


class ContextEditor(QTextEdit):
    """One context being edited: as tall as its sentence, up to four lines.

    Plain text only. A QTextEdit rather than a QPlainTextEdit because its
    document reports its height in pixels at the editor's width, which is
    what growing with the sentence needs. A context is one sentence, so
    Enter does not break the line; it passes to the panel, where Ctrl+Enter
    saves.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.setObjectName("ContextEditor")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setPlaceholderText("A sentence using the word")
        self.setPlainText(text)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_args) -> None:
        # The border and padding around the text, as the stylesheet set them.
        margins = self.contentsMargins()
        chrome = margins.top() + margins.bottom()
        line = self.fontMetrics().lineSpacing()
        edge = int(2 * self.document().documentMargin())
        width = self.viewport().width()
        if width > 40:
            # Laid out on a copy at the editor's width: the editor's own
            # document lays out lazily and can still read as empty here.
            copy = self.document().clone(self)
            copy.setDefaultFont(self.font())
            copy.setTextWidth(width)
            text_height = int(copy.size().height() + 0.5)
            copy.deleteLater()
        else:
            text_height = line + edge
        self.setFixedHeight(min(max(text_height, line + edge), _MAX_LINES * line + edge) + chrome)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.ignore()
            return
        super().keyPressEvent(event)

    def set_struck(self, struck: bool) -> None:
        """Strike the sentence through, or not. A character format, because
        the stylesheet's font would override the widget's."""
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        style = QTextCharFormat()
        style.setFontStrikeOut(struck)
        self.blockSignals(True)
        cursor.mergeCharFormat(style)
        self.blockSignals(False)


class _ContextRow(QWidget):
    """A context in edit mode: its number, its text, and × to delete it.

    × marks a saved context for deletion — struck through, restorable with
    the same button — and takes a new one away at once: it holds nothing
    that was saved.
    """

    changed = Signal()
    dropped = Signal(object)

    def __init__(self, number: int, context: WordContext | None, text: str = "") -> None:
        super().__init__()
        self.setObjectName("ContextRow")
        self.context = context
        self.removed = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space_2)
        self.number = QLabel(str(number))
        self.number.setObjectName("ContextNumber")
        self.number.setFixedWidth(14)
        layout.addWidget(self.number, 0, Qt.AlignmentFlag.AlignTop)
        self.editor = ContextEditor(context.text if context is not None else text)
        self.editor.setAccessibleName(f"Context {number}")
        self.editor.textChanged.connect(self.changed)
        layout.addWidget(self.editor, 1)
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("ContextRemove")
        self.remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_button.setFixedSize(28, 28)
        self.remove_button.clicked.connect(self.toggle_removed)
        layout.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)
        self._show_state()

    @property
    def text(self) -> str:
        return clean_context(self.editor.toPlainText())

    @property
    def is_new(self) -> bool:
        return self.context is None

    @property
    def is_edited(self) -> bool:
        return (
            self.context is not None and not self.removed and self.text != self.context.text
        )

    def toggle_removed(self) -> None:
        if self.is_new:
            self.dropped.emit(self)
            return
        self.removed = not self.removed
        self._show_state()
        self.changed.emit()

    def _show_state(self) -> None:
        self.editor.setReadOnly(self.removed)
        self.editor.setProperty("removed", "true" if self.removed else "")
        self.editor.set_struck(self.removed)
        self.editor.style().polish(self.editor)
        if self.removed:
            self.remove_button.setText("↶")
            self.remove_button.setToolTip("Keep this context")
            self.remove_button.setAccessibleName("Keep this context")
        else:
            self.remove_button.setText("×")
            tip = "Take this new context away" if self.is_new else (
                "Delete this context when you save"
            )
            self.remove_button.setToolTip(tip)
            self.remove_button.setAccessibleName(tip)


class WordPanel(QFrame):
    """One word's details, its contexts, and explicit status buttons."""

    #: Set this word id to this status.
    status_requested = Signal(int, object)
    #: The word shown was changed here (its definition or contexts): the new version.
    word_changed = Signal(object)
    #: The word shown was deleted here, by id.
    word_deleted = Signal(int)
    #: Edit mode began (True) or ended (False).
    editing_changed = Signal(bool)

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
        #: Asks before a save deletes contexts: the word and the sentences.
        self.confirm_deletions: Callable[[StoredWord, list[str]], bool] = self._ask_deletions
        #: Asks what to do with unsaved edits: "save", "discard" or None (keep).
        self.ask_unsaved: Callable[[StoredWord, bool], str | None] = (
            lambda word, allow_keep: ask_unsaved(self.window(), word.word, allow_keep)
        )
        #: The selection bar floats over the bottom of the panel.
        self._covered = False
        self._editing = False
        self._rows: list[_ContextRow] = []
        self._build()

    def set_buttons_covered(self, covered: bool) -> None:
        """Hide the status buttons while the selection bar floats over them.

        The bar carries the same actions, for the whole selection, so nothing
        becomes unreachable; half-hidden buttons under it would only mislead.
        """
        self._covered = covered
        self._show_actions()

    # -- building --------------------------------------------------------------

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

        top = QHBoxLayout()
        self.badge = StatusBadge()
        top.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        self.edit_button = QPushButton("✎  Edit")
        self.edit_button.setObjectName("PanelEdit")
        self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_button.setToolTip("Edit the definition and contexts (E)")
        self.edit_button.clicked.connect(lambda: self.start_edit())
        top.addWidget(self.edit_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # While editing, Cancel and Save take Edit's place: at the top, where
        # the selection bar floating over the panel's bottom never covers them.
        self.save_row = QWidget()
        self.save_row.setObjectName("PanelBody")
        save_row = QHBoxLayout(self.save_row)
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.setSpacing(m.space_1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setProperty("variant", "ghost")
        self.cancel_button.setProperty("compact", True)
        self.cancel_button.setToolTip("Throw the changes away (Esc)")
        self.cancel_button.clicked.connect(self.cancel_edit)
        save_row.addWidget(self.cancel_button)
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setProperty("compact", True)
        self.save_button.setToolTip("Save the definition and contexts (Ctrl+S)")
        self.save_button.clicked.connect(self.save_edit)
        save_row.addWidget(self.save_button)
        self.save_row.hide()
        top.addWidget(self.save_row, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)
        self.title = QLabel()
        self.title.setObjectName("PanelWord")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.title)
        self.meta = _text("Muted")
        layout.addWidget(self.meta)

        # The definition: read, or edited.
        layout.addSpacing(m.space_3)
        layout.addWidget(_section("DEFINITION"))
        self.definition = _text()
        layout.addWidget(self.definition)
        self.definition_field = QPlainTextEdit()
        self.definition_field.setObjectName("DefinitionField")
        self.definition_field.setFixedHeight(84)
        self.definition_field.setTabChangesFocus(True)
        self.definition_field.setAccessibleName("Definition")
        self.definition_field.setPlaceholderText("Every sense the word is learned in")
        self.definition_field.textChanged.connect(self._on_edited)
        self.definition_field.hide()
        layout.addWidget(self.definition_field)

        # The contexts: numbered sentences, or their editors.
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
        self.add_row = QWidget()
        self.add_row.setObjectName("PanelBody")
        add_row = QHBoxLayout(self.add_row)
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(m.space_2)
        # Typing here opens edit mode with the sentence as a new context:
        # it is saved with the rest, never on its own.
        self.context_field = QLineEdit()
        self.context_field.setPlaceholderText("Add a sentence using the word")
        self.context_field.setAccessibleName("New context")
        self.context_field.textEdited.connect(self._start_with_sentence)
        self.context_field.returnPressed.connect(self._start_with_sentence)
        add_row.addWidget(self.context_field, 1)
        layout.addWidget(self.add_row)
        self.add_context_button = _link("+  Add context", "Another sentence using the word")
        self.add_context_button.clicked.connect(lambda: self.add_context_row(focus=True))
        self.add_context_button.hide()
        layout.addWidget(self.add_context_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.context_message = _text("PanelMessage")
        self.context_message.hide()
        layout.addWidget(self.context_message)

        self.learning_title = _section("LEARNING")
        layout.addSpacing(m.space_3)
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
            (ReviewStatus.NOT_REVIEWED, "–  Not reviewed", "R"),
        ):
            button = QPushButton(text)
            button.setObjectName("PanelStatusButton")
            button.setToolTip(f"Mark this word {text.split('  ')[-1]} ({key})")
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
        """Show ``word``. The same word again (a refresh) keeps an edit in
        progress; another word ends it — the table asks first (see
        :meth:`can_leave`)."""
        same = word is not None and self.word is not None and word.id == self.word.id
        if not same:
            self.context_field.clear()
            self._end_edit()
        self.word = word
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
        for status, button in self.buttons.items():
            button.setEnabled(status is not word.status)
        self._show_learning(word)
        if self._editing:
            self._show_actions()
            return
        self._message(None)
        self.definition.setText(word.definition or "No definition yet")
        self.definition.setObjectName("" if word.definition else "Faint")
        self.definition.style().polish(self.definition)
        self._show_contexts()
        self._show_actions()

    def set_history_source(self, source: Callable[[int], object]) -> None:
        self._history_source = source
        if self.word is not None:
            self._show_learning(self.word)

    def set_editor(self, editor: WordEditor) -> None:
        """What reads and changes definitions and contexts; without one the
        panel only shows the definition."""
        self._editor = editor
        if self.word is not None and not self._editing:
            self.show_word(self.word)

    def context_texts(self) -> list[str]:
        """The contexts on screen, in order: their text, or while editing,
        what each editor holds. For tests and accessibility."""
        if self._editing:
            return [row.text for row in self._rows]
        texts = []
        for index in range(self.contexts_box.count()):
            row = self.contexts_box.itemAt(index).widget()
            label = row.findChild(QLabel, "ContextText") if row is not None else None
            if label is not None:
                texts.append(label.text())
        return texts

    def _clear_contexts(self) -> None:
        while self.contexts_box.count():
            item = self.contexts_box.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self._rows = []

    def _saved_contexts(self) -> tuple[WordContext, ...]:
        word = self.word
        return self._editor.contexts(word.id) if word is not None and self._editor else ()

    def _show_contexts(self) -> None:
        self._clear_contexts()
        contexts = self._saved_contexts()
        count = len(contexts)
        self.contexts_title.setText(f"CONTEXTS · {count}" if count else "CONTEXTS")
        self.no_contexts.setVisible(not count)
        for number, context in enumerate(contexts, start=1):
            self.contexts_box.addWidget(_context_line(number, context.text))

    def _show_learning(self, word: StoredWord) -> None:
        journey = self._history_source(word.id) if self._history_source else None
        visible = journey is not None and not self._editing
        self.learning_title.setVisible(visible)
        self.learning.setVisible(visible)
        self.history_button.setVisible(visible)
        if journey is not None:
            self.learning.setText(journey_summary(journey))

    def _show_actions(self) -> None:
        """What can be done now: read and act, or edit and save."""
        editable = self._editor is not None and self.word is not None
        editing = self._editing
        self.edit_button.setVisible(editable and not editing)
        self.definition.setVisible(not editing)
        self.definition_field.setVisible(editing)
        self.add_row.setVisible(editable and not editing)
        self.add_context_button.setVisible(editing)
        self.save_row.setVisible(editing)
        self.button_row.setVisible(not editing and not self._covered)
        self.delete_button.setVisible(editable and not editing and not self._covered)
        if self.word is not None:
            self._show_learning(self.word)

    def open_history(self) -> None:
        if self.word is None or self._history_source is None:
            return
        journey = self._history_source(self.word.id)
        if journey is not None:
            WordHistoryDialog(journey, parent=self.window()).exec()

    def hide_status(self, status: ReviewStatus) -> None:
        """Leave out a status that makes no sense where the panel is shown."""
        self.buttons[status].setVisible(False)

    # -- edit mode ---------------------------------------------------------

    @property
    def editing(self) -> bool:
        return self._editing

    def start_edit(self, new_context: str | None = None) -> None:
        """Edit the definition and contexts; ``new_context`` starts a new
        context with that text (it may be empty) and puts the cursor in it."""
        if self.word is None or self._editor is None:
            return
        if not self._editing:
            self._editing = True
            self.editing_changed.emit(True)
            self._message(None)
            self.definition_field.blockSignals(True)
            self.definition_field.setPlainText(self.word.definition or "")
            self.definition_field.blockSignals(False)
            self._clear_contexts()
            for context in self._saved_contexts():
                self._add_row(context)
            self.no_contexts.hide()
            self._show_actions()
        if new_context is not None:
            self.add_context_row(new_context, focus=True)
        elif self._rows and not self.word.definition:
            self.definition_field.setFocus()
        else:
            self.definition_field.setFocus()
        self._on_edited()

    def add_context_row(self, text: str = "", focus: bool = False) -> None:
        """A new, empty (or started) context in edit mode."""
        if not self._editing:
            self.start_edit(text)
            return
        row = self._add_row(None, text)
        if focus:
            row.editor.setFocus()
            cursor = row.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            row.editor.setTextCursor(cursor)
        self._on_edited()

    def _add_row(self, context: WordContext | None, text: str = "") -> _ContextRow:
        row = _ContextRow(len(self._rows) + 1, context, text)
        row.changed.connect(self._on_edited)
        row.dropped.connect(self._drop_row)
        self._rows.append(row)
        self.contexts_box.addWidget(row)
        return row

    def _drop_row(self, row: _ContextRow) -> None:
        self._rows.remove(row)
        self.contexts_box.removeWidget(row)
        row.hide()
        row.deleteLater()
        for number, other in enumerate(self._rows, start=1):
            other.number.setText(str(number))
            other.editor.setAccessibleName(f"Context {number}")
        self._on_edited()

    def _start_with_sentence(self, *_args) -> None:
        text = self.context_field.text()
        self.context_field.clear()
        self.start_edit(text)

    def _on_edited(self, *_args) -> None:
        if not self._editing:
            return
        self.save_button.setEnabled(self.is_dirty())
        kept = [row for row in self._rows if not row.removed]
        removed = len(self._rows) - len(kept)
        title = f"CONTEXTS · {len(kept)}" if kept else "CONTEXTS"
        if removed:
            title += f"  ·  {removed} TO DELETE"
        self.contexts_title.setText(title)

    def _edit(self) -> WordEdit:
        """What Save would write."""
        word = self.word
        assert word is not None
        definition = clean_context(self.definition_field.toPlainText())
        return WordEdit(
            definition=definition if definition != (word.definition or "") else None,
            changed={row.context.id: row.text for row in self._rows if row.is_edited},
            deleted=[row.context.id for row in self._rows if row.removed and row.context],
            added=[row.text for row in self._rows if row.is_new and row.text],
        )

    def is_dirty(self) -> bool:
        """Edit mode holds something Save would write."""
        if not self._editing or self.word is None:
            return False
        edit = self._edit()
        return bool(edit.definition is not None or edit.changed or edit.deleted or edit.added)

    def _problem(self) -> str | None:
        """Why Save cannot write what is on screen, if it cannot."""
        if not clean_context(self.definition_field.toPlainText()):
            return "The definition cannot be empty: it is what the word is asked from."
        kept = [row for row in self._rows if not row.removed and not (row.is_new and not row.text)]
        for number, row in enumerate(self._rows, start=1):
            if not row.removed and not row.is_new and not row.text:
                return f"Context {number} is empty. Delete it with × instead."
        texts = [row.text for row in kept]
        for index, text in enumerate(texts):
            if any(same_context(text, other) for other in texts[:index]):
                return f"“{_short(text)}” is there twice."
        return None

    def save_edit(self) -> bool:
        """Save the definition and contexts. True when saved (or when there
        was nothing to save); False when something stopped it."""
        word = self.word
        if not self._editing or word is None or self._editor is None:
            return True
        if not self.is_dirty():
            self._end_edit()
            self.show_word(word)
            return True
        problem = self._problem()
        if problem:
            self._message(problem, bad=True)
            return False
        doomed = [row.context.text for row in self._rows if row.removed and row.context]
        if doomed and not self.confirm_deletions(word, doomed):
            return False
        try:
            updated = self._editor.save_word(word.id, self._edit())
        except LexiTrackError as exc:
            self._message(exc.user_message, bad=True)
            return False
        self._end_edit()
        self.word = None
        self.show_word(updated)
        missing = [
            context.text for context in self._saved_contexts()
            if not contains_word(context.text, updated.word)
        ]
        if missing:
            self._message(
                f"Saved. “{_short(missing[0])}” does not seem to contain “{updated.word}”: "
                "the context question picks the word out in it."
            )
        else:
            self._message("Saved.")
        self.word_changed.emit(updated)
        return True

    def cancel_edit(self) -> None:
        """Throw the edits away and show the word as it is saved."""
        if not self._editing:
            return
        self._end_edit()
        if self.word is not None:
            word, self.word = self.word, None
            self.show_word(word)

    def can_leave(self, allow_keep: bool = True) -> bool:
        """Before another word is shown: unsaved edits are saved or thrown
        away as the user says. False when they chose to keep editing, or the
        save did not go through."""
        if not self._editing:
            return True
        if not self.is_dirty():
            self.cancel_edit()
            return True
        assert self.word is not None
        choice = self.ask_unsaved(self.word, allow_keep)
        if choice == "save":
            return self.save_edit()
        if choice == "discard":
            self.cancel_edit()
            return True
        return False

    def _end_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        self._clear_contexts()
        self._show_actions()
        self.editing_changed.emit(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """In edit mode: Ctrl+S or Ctrl+Enter saves; Esc cancels, asking
        first when there is something to lose."""
        if self._editing:
            key = event.key()
            enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            control = event.modifiers() & Qt.KeyboardModifier.ControlModifier
            if event.matches(QKeySequence.StandardKey.Save) or (enter and control):
                self.save_edit()
                return
            if key == Qt.Key.Key_Escape:
                if not self.is_dirty() or self.can_leave():
                    self.cancel_edit()
                return
        super().keyPressEvent(event)

    def _ask_deletions(self, word: StoredWord, sentences: list[str]) -> bool:
        count = len(sentences)
        noun = "context" if count == 1 else "contexts"
        listed = "\n".join(f"• {_short(sentence, 90)}" for sentence in sentences)
        return confirm(
            self.window(),
            "Delete contexts",
            f"Save your changes and delete {count} {noun} of “{word.word}”?\n\n{listed}",
            f"Save and delete {count} {noun}",
        )

    # -- the rest ------------------------------------------------------------

    def _request(self, status: ReviewStatus) -> None:
        if self.word is not None:
            self.status_requested.emit(self.word.id, status)

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


def _context_line(number: int, text: str) -> QWidget:
    """A saved context, read-only: its number and its sentence."""
    row = QWidget()
    row.setObjectName("ContextRow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(METRICS.space_2)
    index = QLabel(str(number))
    index.setObjectName("ContextNumber")
    index.setFixedWidth(14)
    layout.addWidget(index, 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(text)
    label.setObjectName("ContextText")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(label, 1)
    return row


def _short(text: str, limit: int = 50) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


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
