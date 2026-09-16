"""The review screen.

This is where the user spends essentially all of their time, so the layout is
built around one question: how fast can someone answer a word and see the next
one? That leads to a few deliberate choices.

* One word, very large, vertically centred. It is the only thing competing for
  attention; part of speech and level sit below it in muted type.
* Two answer buttons of equal size and weight. Neither is the "right" answer,
  so neither is styled as primary.
* Both a mouse and a keyboard path, always. K and U answer, Enter repeats the
  last answer, Backspace undoes, and the hints are printed on the buttons so
  they are learned rather than memorised.
* No sense disambiguator is shown. Oxford lists ``bank (money)`` and
  ``bank (river)`` separately, but deduplication merges them into one item, so
  displaying a single sense would misrepresent what is being asked. The senses
  are still stored, for exports and for a future per-sense review mode.
* The card keeps a fixed minimum height so the buttons never move as words of
  different lengths come and go. A target that jumps is a target that gets
  mis-clicked.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..repositories.word_repository import StoredWord
from .theme.palette import METRICS

#: Keeps the answer buttons on the same line no matter how long the word is.
_CARD_MIN_HEIGHT = 340
#: Below this the two answer buttons start wrapping their labels.
_CARD_MIN_WIDTH = 480


class ReviewWidget(QWidget):
    """Shows one word and collects the user's answer."""

    #: Emitted with ``(word_id, known)`` when the user answers.
    answered = Signal(int, bool)
    #: Emitted when the user asks to undo the previous answer.
    undo_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._word: StoredWord | None = None
        self._last_answer: bool | None = None
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS

        outer = QVBoxLayout(self)
        outer.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_4)
        outer.setSpacing(m.space_4)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("ReviewCard")
        card.setMinimumHeight(_CARD_MIN_HEIGHT)
        card.setMinimumWidth(_CARD_MIN_WIDTH)
        card.setMaximumWidth(m.content_max_width)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(m.space_6, m.space_6, m.space_6, m.space_5)
        card_layout.setSpacing(0)

        # -- source chip, top left of the card
        self._source_label = QLabel()
        self._source_label.setObjectName("SourceChip")
        self._source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.addWidget(self._source_label, 0, Qt.AlignmentFlag.AlignLeft)
        chip_row.addStretch(1)
        card_layout.addLayout(chip_row)
        card_layout.addStretch(1)

        # -- the word itself
        self._word_label = QLabel()
        self._word_label.setObjectName("WordLabel")
        self._word_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._word_label.setWordWrap(True)
        self._word_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        card_layout.addWidget(self._word_label)

        card_layout.addSpacing(m.space_3)

        # -- metadata beneath, each line optional
        self._meta_label = QLabel()
        self._meta_label.setObjectName("MetaLabel")
        self._meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._meta_label)

        self._definition_label = QLabel()
        self._definition_label.setObjectName("DefinitionLabel")
        self._definition_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._definition_label.setWordWrap(True)
        card_layout.addWidget(self._definition_label)

        card_layout.addStretch(1)

        # -- answers
        self._known_button = _answer_button("I Know", "K", "known")
        self._unknown_button = _answer_button("I Don't Know", "U", "unknown")
        self._known_button.clicked.connect(lambda: self._answer(True))
        self._unknown_button.clicked.connect(lambda: self._answer(False))

        answers = QHBoxLayout()
        answers.setSpacing(m.space_3)
        answers.addWidget(self._known_button, 1)
        answers.addWidget(self._unknown_button, 1)
        card_layout.addLayout(answers)

        card_layout.addSpacing(m.space_3)

        # -- position and undo
        self._position_label = QLabel()
        self._position_label.setObjectName("PositionLabel")

        self._undo_button = QPushButton("Undo")
        self._undo_button.setObjectName("UndoButton")
        self._undo_button.setProperty("variant", "ghost")
        self._undo_button.setToolTip("Undo the previous answer (Backspace)")
        self._undo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._undo_button.setEnabled(False)
        self._undo_button.clicked.connect(self.undo_requested.emit)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self._position_label, 0, Qt.AlignmentFlag.AlignLeft)
        footer.addStretch(1)
        footer.addWidget(self._undo_button, 0, Qt.AlignmentFlag.AlignRight)
        card_layout.addLayout(footer)

        # Stretch factors rather than bare stretches: the card keeps the space
        # it needs and the margins absorb the rest, up to content_max_width.
        centred = QHBoxLayout()
        centred.addStretch(1)
        centred.addWidget(card, 10)
        centred.addStretch(1)
        outer.addLayout(centred)

        # -- shortcut hint, outside the card so it reads as ambient help
        self._hint_label = QLabel(
            "K — I Know     ·     U — I Don't Know     ·     "
            "Enter — repeat last answer     ·     Backspace — undo"
        )
        self._hint_label.setObjectName("ShortcutHint")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._hint_label)
        outer.addStretch(1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- content -----------------------------------------------------------

    def show_word(self, word: StoredWord, position: int, total: int) -> None:
        """Display ``word`` as item ``position`` of ``total``."""
        self._word = word
        self._word_label.setText(word.word)

        self._source_label.setText(word.source_label or "Imported")
        self._source_label.setVisible(bool(word.sources))

        self._set_optional(self._meta_label, _format_meta(word))
        self._set_optional(self._definition_label, word.definition)

        self._position_label.setText(f"{position:,} / {total:,}")

        for button in (self._known_button, self._unknown_button):
            button.setEnabled(True)
        self._known_button.setFocus()

    def set_undo_enabled(self, enabled: bool) -> None:
        self._undo_button.setEnabled(enabled)

    @staticmethod
    def _set_optional(label: QLabel, text: str | None) -> None:
        """Show ``label`` only when there is something to put in it.

        Reserving space for metadata that a source did not provide would leave
        a hole under every generic-parser word.
        """
        label.setText(text or "")
        label.setVisible(bool(text))

    # -- interaction -------------------------------------------------------

    def _answer(self, known: bool) -> None:
        if self._word is None:
            return
        word_id = self._word.id
        self._last_answer = known
        # Guard against a double click racing ahead of the next word.
        self._known_button.setEnabled(False)
        self._unknown_button.setEnabled(False)
        self.answered.emit(word_id, known)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_K, Qt.Key.Key_Left):
            self._answer(True)
        elif key in (Qt.Key.Key_U, Qt.Key.Key_Right):
            self._answer(False)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            # Enter repeats the previous answer, which makes long runs of
            # familiar words fast. Before the first answer it does nothing.
            if self._last_answer is not None:
                self._answer(self._last_answer)
        elif key == Qt.Key.Key_Backspace:
            if self._undo_button.isEnabled():
                self.undo_requested.emit()
        else:
            super().keyPressEvent(event)


def _answer_button(text: str, shortcut_key: str, variant: str) -> QPushButton:
    """Build one of the two answer buttons, with its shortcut printed on it."""
    button = QPushButton(f"{text}    {shortcut_key}")
    button.setProperty("variant", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(f"{text}  (shortcut: {shortcut_key})")
    button.setMinimumHeight(56)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return button


def _format_meta(word: StoredWord) -> str:
    """Return the ``verb · B2`` line, using whichever parts exist."""
    parts = [part for part in (word.part_of_speech, word.cefr_level) if part]
    return "  ·  ".join(parts)
