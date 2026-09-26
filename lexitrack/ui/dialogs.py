"""The small dialogs: create or edit a list, and add words.

Every dialog reports problems inline, next to the field, and keeps what the
user typed. A dialog that closes on a validation error and throws away the
input teaches people not to use it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import LexiTrackError
from ..models.context import contains_word, word_length
from ..models.language import LANGUAGE_NAMES, UNDETERMINED, language_name
from ..models.vocabulary_list import VocabularyList
from ..models.word_entry import CEFR_ORDER
from ..services.vocabulary_service import VocabularyService
from .theme.palette import METRICS

#: Offered in the part-of-speech box; anything else can still be typed.
COMMON_PARTS_OF_SPEECH = (
    "noun", "verb", "adjective", "adverb", "preposition", "pronoun",
    "determiner", "conjunction", "exclamation", "phrase",
)


def language_combo(selected: str = UNDETERMINED) -> QComboBox:
    combo = QComboBox()
    combo.setAccessibleName("Language")
    ordered = [code for code in LANGUAGE_NAMES if code != UNDETERMINED]
    combo.addItem("Unspecified (any language)", UNDETERMINED)
    for code in ordered:
        combo.addItem(f"{LANGUAGE_NAMES[code]} ({code})", code)
    index = combo.findData(selected)
    if index < 0 and selected:
        combo.addItem(f"{language_name(selected)} ({selected})", selected)
        index = combo.count() - 1
    combo.setCurrentIndex(max(index, 0))
    return combo


def error_label() -> QLabel:
    label = QLabel()
    label.setObjectName("ErrorText")
    label.setWordWrap(True)
    label.setVisible(False)
    return label


def show_error(label: QLabel, message: str | None) -> None:
    label.setText(message or "")
    label.setVisible(bool(message))


def dialog_header(title: str, hint: str | None = None) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(4)
    heading = QLabel(title)
    heading.setObjectName("DialogTitle")
    layout.addWidget(heading)
    if hint:
        text = QLabel(hint)
        text.setObjectName("DialogHint")
        text.setWordWrap(True)
        layout.addWidget(text)
    return layout


# -- list ------------------------------------------------------------------


class ListDialog(QDialog):
    """Create a list, or edit an existing one's name, language and description."""

    def __init__(
        self,
        service: VocabularyService,
        existing: VocabularyList | None = None,
        suggested_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._existing = existing
        self.result_list: VocabularyList | None = None
        m = METRICS

        editing = existing is not None
        self.setWindowTitle("Edit List" if editing else "New List")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        layout.setSpacing(m.space_4)
        layout.addLayout(
            dialog_header(
                "Edit List" if editing else "New List",
                None
                if editing
                else "A list is a set of words you want to learn. Words can belong to "
                "several lists; what you know is shared between them.",
            )
        )

        form = QFormLayout()
        form.setSpacing(m.space_3)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.name_field = QLineEdit(existing.name if existing else suggested_name)
        self.name_field.setPlaceholderText("e.g. German A1")
        self.name_field.setMaxLength(80)
        form.addRow("Name", self.name_field)

        self.language_field = language_combo(existing.language if existing else UNDETERMINED)
        form.addRow("Language", self.language_field)

        self.description_field = QPlainTextEdit(existing.description or "" if existing else "")
        self.description_field.setPlaceholderText("Optional")
        self.description_field.setFixedHeight(72)
        form.addRow("Description", self.description_field)
        layout.addLayout(form)

        language_hint = QLabel(
            "Words take the list's language. Choose Unspecified for a list that mixes "
            "languages, such as “My Difficult Words”."
        )
        language_hint.setObjectName("Faint")
        language_hint.setWordWrap(True)
        layout.addWidget(language_hint)

        self.error = error_label()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox()
        save = buttons.addButton(
            "Save" if editing else "Create list", QDialogButtonBox.ButtonRole.AcceptRole
        )
        save.setProperty("variant", "primary")
        save.setDefault(True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name_field.setFocus()
        self.name_field.selectAll()

    def _save(self) -> None:
        name = self.name_field.text()
        language = self.language_field.currentData()
        description = self.description_field.toPlainText()
        try:
            if self._existing is None:
                self.result_list = self._service.create_list(name, language, description)
            else:
                self.result_list = self._service.update_list(
                    self._existing.id, name=name, description=description, language=language
                )
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return
        self.accept()


# -- add word --------------------------------------------------------------


class AddWordDialog(QDialog):
    """Type words into a list by hand. Stays open for the next word.

    A word is its text, its length (worked out from it), part of speech, CEFR
    level, definition and contexts — one sentence per line. The definition is
    needed for it to be asked; nothing else is.
    """

    word_added = Signal(int)

    def __init__(
        self, service: VocabularyService, target: VocabularyList, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._target = target
        m = METRICS
        self.setWindowTitle("Add Word")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        layout.setSpacing(m.space_4)
        language = (
            "" if target.language == UNDETERMINED else f" · {target.language_name}"
        )
        layout.addLayout(
            dialog_header(
                f"Add Word to {target.name}",
                f"The word and its definition are needed; the rest helps{language}.",
            )
        )

        form = QFormLayout()
        form.setSpacing(m.space_3)
        self.word_field = QLineEdit()
        self.word_field.setPlaceholderText("Word or phrase")
        self.word_field.textChanged.connect(self._show_length)
        form.addRow("Word", self.word_field)

        self.length_label = QLabel("—")
        self.length_label.setObjectName("Muted")
        self.length_label.setToolTip("Letters, not counting spaces or hyphens")
        form.addRow("Length", self.length_label)

        self.pos_field = QComboBox()
        self.pos_field.setEditable(True)
        self.pos_field.addItem("")
        self.pos_field.addItems(COMMON_PARTS_OF_SPEECH)
        form.addRow("Part of speech", self.pos_field)

        self.cefr_field = QComboBox()
        self.cefr_field.addItem("—", "")
        for level in CEFR_ORDER:
            self.cefr_field.addItem(level, level)
        form.addRow("CEFR level", self.cefr_field)

        self.definition_field = QPlainTextEdit()
        self.definition_field.setPlaceholderText(
            "Every sense the word is learned in, in one text: “done on purpose; "
            "to think carefully before deciding”"
        )
        self.definition_field.setFixedHeight(64)
        self.definition_field.setTabChangesFocus(True)
        form.addRow("Definition", self.definition_field)

        self.contexts_field = QPlainTextEdit()
        self.contexts_field.setPlaceholderText(
            "Sentences using the word, one per line. Optional; with one, the word is "
            "also asked from a context."
        )
        self.contexts_field.setFixedHeight(88)
        self.contexts_field.setTabChangesFocus(True)
        form.addRow("Contexts", self.contexts_field)
        layout.addLayout(form)

        self.message = QLabel()
        self.message.setObjectName("Muted")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        self.add_button = QPushButton("Add word")
        self.add_button.setProperty("variant", "primary")
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self._add)
        buttons.addWidget(self.add_button)
        layout.addLayout(buttons)
        self.word_field.setFocus()

    def _show_length(self, text: str) -> None:
        length = word_length(text)
        letters = "letter" if length == 1 else "letters"
        self.length_label.setText(f"{length} {letters}" if length else "—")

    def _contexts(self) -> list[str]:
        return [line.strip() for line in self.contexts_field.toPlainText().splitlines()
                if line.strip()]

    def _add(self) -> None:
        text = self.word_field.text().strip()
        if not text:
            self.message.setText("Type a word first.")
            self.word_field.setFocus()
            return
        definition = self.definition_field.toPlainText().strip()
        if not definition:
            self.message.setText(
                "Give its definition too: both questions LexiTrack asks are built on it."
            )
            self.definition_field.setFocus()
            return
        contexts = self._contexts()
        try:
            word, added = self._service.add_word(
                self._target.id,
                text,
                part_of_speech=self.pos_field.currentText(),
                cefr_level=self.cefr_field.currentData(),
                definition=definition,
                contexts=contexts,
            )
        except LexiTrackError as exc:
            self.message.setText(exc.user_message)
            return

        missing = sum(1 for context in contexts if not contains_word(context, word.word))
        note = (
            f" {missing} of its contexts {'does' if missing == 1 else 'do'} not seem to "
            f"contain “{word.word}”; check {'it' if missing == 1 else 'them'} on its page."
            if missing else ""
        )
        if added:
            self.message.setText(f"Added “{word.word}”.{note} Type the next word, or close.")
            self.word_added.emit(word.id)
        else:
            self.message.setText(
                f"“{word.word}” is already in {self._target.name}; any new contexts were "
                f"added to it.{note}"
            )
        self.word_field.clear()
        self.definition_field.clear()
        self.contexts_field.clear()
        self.pos_field.setCurrentIndex(0)
        self.word_field.setFocus()


def confirm(
    parent: QWidget,
    title: str,
    text: str,
    action: str,
    destructive: bool = True,
) -> bool:
    """Ask before doing something that cannot be undone."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    go = box.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel)
    if destructive:
        go.setProperty("variant", "danger")
    box.exec()
    return box.clickedButton() is go
