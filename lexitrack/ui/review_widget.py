"""The flashcard review screen.

This is where the user spends most of their time, so the layout is built
around one question: how fast can someone answer a word and see the next one?

* One word, very large, centred. Part of speech and level sit below it in
  muted type; where the word came from is a quiet line at the top of the card.
  It is labelled "Source" on purpose — the *list* being reviewed is shown by
  the page around the card, and the two are never presented as the same thing.
* Two answer buttons of equal size and weight. Neither is the "right" answer.
* A keyboard path for everything. Arrows move; letters act — so an arrow key
  can never change an answer by accident. The keys are listed in Keyboard
  Shortcuts (F1) and the Ctrl+K palette rather than printed under the card:

  ======================  ==============================================
  K                       I Know
  U                       I Don't Know
  ← or Backspace          previous word (status unchanged)
  →                       next word, on a word you went back to (status
                          unchanged); does nothing on an unanswered word
  Enter or Space          repeat the last answer; on an earlier word, move
                          forward keeping its status
  R                       reset the word on screen to Not Reviewed
  ======================  ==============================================

* Moving back is navigation, never an undo. Stepping back shows the earlier word
  *with* its status, in a banner that says so; changing it takes an explicit
  answer or R. See ``services/review_session.py``.
* The card keeps a fixed minimum height so the buttons never move between
  words. A target that jumps is a target that gets mis-clicked.
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

from ..models.user_word_state import ReviewStatus
from ..repositories.word_repository import StoredWord
from ..services.review_session import ReviewItem
from .components.status import StatusBadge
from .components.word_label import WordLabel
from .theme.palette import METRICS

#: Keeps the answer buttons on the same line no matter how long the word is.
_CARD_MIN_HEIGHT = 360
#: Below this the two answer buttons start wrapping their labels.
_CARD_MIN_WIDTH = 480


class ReviewWidget(QWidget):
    """Shows one flashcard and collects the user's answer."""

    #: Emitted with ``known`` when the user answers.
    answered = Signal(bool)
    #: ← or Backspace: step back one word.
    back_requested = Signal()
    #: →: step forward through words already answered.
    forward_requested = Signal()
    #: Enter / Space: repeat the last answer, or move forward in history.
    repeat_requested = Signal()
    #: R: reset the word on screen to Not Reviewed.
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._word: StoredWord | None = None
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS

        outer = QVBoxLayout(self)
        outer.setContentsMargins(m.space_5, m.space_3, m.space_5, m.space_4)
        outer.setSpacing(m.space_4)
        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("ReviewCard")
        card.setMinimumHeight(_CARD_MIN_HEIGHT)
        card.setMinimumWidth(_CARD_MIN_WIDTH)
        card.setMaximumWidth(m.content_max_width)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(m.space_6, m.space_5, m.space_6, m.space_5)
        card_layout.setSpacing(0)

        # -- top row: provenance on the left, status on the right
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._source_label = QLabel()
        self._source_label.setObjectName("SourceChip")
        top.addWidget(self._source_label, 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch(1)
        self._status_badge = StatusBadge()
        top.addWidget(self._status_badge, 0, Qt.AlignmentFlag.AlignRight)
        card_layout.addLayout(top)

        card_layout.addSpacing(m.space_2)
        self._history_banner = QLabel()
        self._history_banner.setObjectName("HistoryBanner")
        self._history_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._history_banner.setVisible(False)
        card_layout.addWidget(self._history_banner, 0, Qt.AlignmentFlag.AlignHCenter)
        card_layout.addStretch(1)

        # -- the word
        self._word_label = WordLabel()
        self._word_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(self._word_label)
        card_layout.addSpacing(m.space_3)

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

        # -- footer: position, back, reset
        self._position_label = QLabel()
        self._position_label.setObjectName("PositionLabel")

        self._back_button = _ghost_button("\u2190 Back", "Previous word (\u2190 or Backspace)")
        self._back_button.clicked.connect(self.back_requested.emit)
        self._back_button.setEnabled(False)

        self._next_button = _ghost_button(
            "Next \u2192", "Step forward without changing it (\u2192)"
        )
        self._next_button.clicked.connect(self.forward_requested.emit)
        self._next_button.setVisible(False)

        self._reset_button = _ghost_button("Reset", "Reset this word to Not Reviewed (R)")
        self._reset_button.clicked.connect(self.reset_requested.emit)
        self._reset_button.setVisible(False)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self._position_label, 0, Qt.AlignmentFlag.AlignLeft)
        footer.addStretch(1)
        footer.addWidget(self._reset_button)
        footer.addWidget(self._back_button)
        footer.addWidget(self._next_button)
        card_layout.addLayout(footer)

        centred = QHBoxLayout()
        centred.addStretch(1)
        centred.addWidget(card, 10)
        centred.addStretch(1)
        outer.addLayout(centred)
        outer.addStretch(1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- content -----------------------------------------------------------

    def show_item(self, item: ReviewItem, can_go_back: bool) -> None:
        """Display a review item from a :class:`ReviewSession`."""
        word = item.word
        self._word = word
        self._word_label.setText(word.word)

        if word.sources:
            self._source_label.setText(f"Source: {word.source_label}")
            self._source_label.setVisible(True)
        else:
            self._source_label.setVisible(False)

        self._status_badge.set_status(word.status)
        # A live word is by definition not reviewed; the badge would only
        # repeat that on every card. It appears when it says something.
        self._status_badge.setVisible(
            item.is_history or word.status is not ReviewStatus.NOT_REVIEWED
        )

        if item.is_history:
            steps = "1 word back" if item.steps_back == 1 else f"{item.steps_back} words back"
            self._history_banner.setText(
                f"Earlier word · {steps} · its answer is kept unless you change it"
            )
            self._history_banner.setVisible(True)
            self._position_label.setText("Reviewing an earlier answer")
        else:
            self._history_banner.setVisible(False)
            self._position_label.setText(f"{item.position:,} / {item.progress.total:,}")

        self._set_optional(self._meta_label, _format_meta(word))
        self._set_optional(self._definition_label, word.definition)

        self._back_button.setEnabled(can_go_back)
        self._next_button.setVisible(item.is_history)
        self._reset_button.setVisible(word.status is not ReviewStatus.NOT_REVIEWED)

        for button in (self._known_button, self._unknown_button):
            button.setEnabled(True)
        self.setFocus()

    @staticmethod
    def _set_optional(label: QLabel, text: str | None) -> None:
        """Show ``label`` only when there is something to put in it."""
        label.setText(text or "")
        label.setVisible(bool(text))

    # -- interaction -------------------------------------------------------

    def _answer(self, known: bool) -> None:
        if self._word is None:
            return
        # Guard against a double click racing ahead of the next word.
        self._known_button.setEnabled(False)
        self._unknown_button.setEnabled(False)
        self.answered.emit(known)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        ):
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_K:
            if self._known_button.isEnabled():
                self._answer(True)
        elif key == Qt.Key.Key_U:
            if self._unknown_button.isEnabled():
                self._answer(False)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.repeat_requested.emit()
        elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_Left):
            if self._back_button.isEnabled():
                self.back_requested.emit()
        elif key == Qt.Key.Key_Right:
            # Forward only through answered words: an unanswered word must be
            # answered, not skipped.
            if not self._next_button.isHidden():
                self.forward_requested.emit()
        elif key == Qt.Key.Key_R:
            # isHidden, not isVisible: the latter is false whenever the window is.
            if not self._reset_button.isHidden():
                self.reset_requested.emit()
        else:
            super().keyPressEvent(event)


def _answer_button(text: str, shortcut_key: str, variant: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(f"{text}  (shortcut: {shortcut_key})")
    button.setMinimumHeight(56)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


def _ghost_button(text: str, tooltip: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", "ghost")
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


def _format_meta(word: StoredWord) -> str:
    """Return the ``verb · B2`` line, using whichever parts exist."""
    parts = [part for part in (word.part_of_speech, word.cefr_level) if part]
    return "  ·  ".join(parts)
