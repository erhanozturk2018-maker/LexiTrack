"""The review card: one step of a session on screen.

The card draws what :class:`~lexitrack.services.review_flow.ReviewFlow` says
is next and reports what the learner did; it decides nothing. Two faces, one
at a time:

* **teach** — a new word whole: the word, its length, part of speech and
  level, its definition and its contexts;
* **question** — Definition → Word (the definition, four words side by side)
  or Context → Definition (a sentence with the word in bold, four
  definitions one under another). Keys 1–4 or A–D choose.

After an answer the right option turns green (and a wrong pick red) and a
panel says what happened, always with the right word and its definition.
A right answer to the day's question is followed by Again, Hard, Good and
Easy — keys 1–4, each with when the word would come back; anything else by
Continue (Enter).
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QElapsedTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...models.attempt import Phase, Task
from ...models.context import find_word
from ...models.srs import Rating
from ...services.review_flow import Feedback, Step, StepKind
from ...services.review_wording import feedback_text, interval_text
from ..theme import current_palette
from ..theme.palette import METRICS
from .cards import repolish
from .chips import chip
from .word_label import WordLabel

#: Which button style each rating gets: amber for Again, green for Good,
#: solid green for Easy — the flashcards' own colour language.
RATING_STYLE: dict[Rating, str | None] = {
    Rating.AGAIN: "unknown",
    Rating.HARD: None,
    Rating.GOOD: "known",
    Rating.EASY: "known-solid",
}

#: The option letters, as the card shows them.
LETTERS = "ABCD"

CARD_WIDTH = 640
#: The width of the card's content, inside its margins.
_INNER_WIDTH = CARD_WIDTH - 2 * METRICS.space_6
#: Qt's QWIDGETSIZE_MAX: no maximum height.
_NO_LIMIT = 16777215


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _centered(text: str, name: str, wrap: bool = True) -> QLabel:
    label = _label(text, name, wrap)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class SessionProgress(QWidget):
    """The thin line along the top edge of the session card."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._share = 0.0
        self.setFixedHeight(4)

    def set_share(self, share: float) -> None:
        self._share = min(max(share, 0.0), 1.0)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        painter.setBrush(QColor(palette.surface_sunken))
        painter.drawRoundedRect(self.rect(), radius, radius)
        width = round(self.width() * self._share)
        if width:
            painter.setBrush(QColor(palette.accent))
            painter.drawRoundedRect(0, 0, width, self.height(), radius, radius)
        painter.end()


class AnswerButton(QPushButton):
    """A button with its key, its title and, optionally, a line under it.

    Built from labels inside the button because a QPushButton cannot mix type
    sizes in its own text. ``wrap`` lays it out for a long title — a
    definition — left-aligned beside its key, as tall as the text needs.
    """

    def __init__(
        self, title: str, key: str, variant: str | None, tooltip: str = "", wrap: bool = False
    ) -> None:
        super().__init__()
        self.setObjectName("AnswerButton")
        if variant:
            self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"{title} ({key})")
        self.setAccessibleName(title)
        self._wrap = wrap
        self.key = _label(key, "AnswerKey")
        self.key.setFixedHeight(18)
        self.key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = _label(title, "OptionText" if wrap else "AnswerTitle", wrap=wrap)
        self.sub = _label("", "AnswerSub")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.hide()
        if wrap:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(10)
            layout.addWidget(self.key, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(self.title, 1)
        else:
            self.setFixedHeight(60)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(1)
            layout.addStretch(1)
            top = QHBoxLayout()
            top.setSpacing(6)
            top.addStretch(1)
            top.addWidget(self.key, 0, Qt.AlignmentFlag.AlignVCenter)
            top.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
            top.addStretch(1)
            layout.addLayout(top)
            layout.addWidget(self.sub)
            layout.addStretch(1)
        for child in (self.key, self.title, self.sub):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_title(self, text: str, width: int | None = None) -> None:
        self.title.setText(text)
        self.setAccessibleName(text)
        self.setToolTip(text)
        if self._wrap and width:
            # The key, the gaps and the margins beside the text.
            text_width = width - 24 - 10 - 26
            text_height = self.title.heightForWidth(text_width)
            self.setFixedHeight(max(52, text_height + 22))
            # The key beside the first line; beside the text when it is one line.
            one_line = text_height <= self.title.fontMetrics().lineSpacing() + 2
            self.layout().setAlignment(
                self.key,
                Qt.AlignmentFlag.AlignVCenter if one_line else Qt.AlignmentFlag.AlignTop,
            )

    def set_interval(self, text: str) -> None:
        self.sub.setText(text)
        self.sub.setVisible(bool(text))

    def text(self) -> str:  # noqa: D102 - what a reader of the button sees
        interval = self.sub.text() if not self.sub.isHidden() else ""
        return f"{self.title.text()}\n{interval}" if interval else self.title.text()


class ReviewCard(QFrame):
    """One step of a review session; see the module docstring."""

    #: An option chosen: its index and the milliseconds taken.
    chosen = Signal(int, int)
    #: Again, Hard, Good or Easy (a Rating) for a right answer.
    rated = Signal(object)
    #: Enter after an answer, or on a new word's page.
    continue_requested = Signal()
    undo_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(CARD_WIDTH)
        self.step: Step | None = None
        #: An answer or a new word is on screen: Enter moves on.
        self.waiting = False
        #: A right answer is on screen, waiting for Again / Hard / Good / Easy.
        self.rating = False
        self._picked: int | None = None
        self._timer = QElapsedTimer()
        self._build()

    # -- building --------------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        body = QVBoxLayout(self)
        body.setContentsMargins(m.space_6, m.space_5, m.space_6, m.space_4)
        body.setSpacing(m.space_3)
        self.session_line = SessionProgress()
        body.addWidget(self.session_line)

        top = QHBoxLayout()
        self.session_flag = chip("Hard for you", tone="hard")
        self.session_flag.setToolTip("You have missed this word several times")
        top.addWidget(self.session_flag)
        top.addStretch(1)
        self.task_label = _label("", "TaskLabel")
        top.addWidget(self.task_label)
        body.addLayout(top)
        body.addSpacing(m.space_2)

        self.teach_face = self._build_teach()
        self.question_face = self._build_question()
        body.addWidget(self.teach_face)
        body.addWidget(self.question_face)

        # After an answer: the right word and its definition, always.
        self.result_panel = QFrame()
        self.result_panel.setObjectName("ResultPanel")
        self.result_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        result = QVBoxLayout(self.result_panel)
        result.setContentsMargins(m.space_4, m.space_3, m.space_4, m.space_3)
        result.setSpacing(4)
        self.result_title = _label("", "ResultTitle")
        result.addWidget(self.result_title)
        self.result_lines = _label("", "ResultLine", wrap=True)
        self.result_lines.setTextFormat(Qt.TextFormat.RichText)
        self.result_lines.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result.addWidget(self.result_lines)
        self.result_note = _label("", "ResultNote", wrap=True)
        result.addWidget(self.result_note)
        self.result_panel.hide()
        body.addWidget(self.result_panel)

        # Right: how it went. Wrong, or a new word: Continue.
        rating_row = QHBoxLayout()
        rating_row.setSpacing(m.space_2)
        self.rating_buttons: dict[Rating, AnswerButton] = {}
        for rating in Rating:
            button = AnswerButton(rating.label, str(int(rating)), RATING_STYLE[rating])
            button.clicked.connect(lambda _c=False, r=rating: self.rate(r))
            rating_row.addWidget(button, 1)
            self.rating_buttons[rating] = button
        self.rating_row = QWidget()
        self.rating_row.setObjectName("PanelBody")
        self.rating_row.setLayout(rating_row)
        self.rating_row.hide()
        body.addWidget(self.rating_row)

        continue_row = QHBoxLayout()
        continue_row.addStretch(1)
        self.continue_button = QPushButton("Continue   Enter")
        self.continue_button.setProperty("variant", "primary")
        self.continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_button.clicked.connect(self.continue_requested)
        continue_row.addWidget(self.continue_button)
        self.continue_holder = QWidget()
        self.continue_holder.setObjectName("PanelBody")
        self.continue_holder.setLayout(continue_row)
        self.continue_holder.hide()
        body.addWidget(self.continue_holder)

        footer = QHBoxLayout()
        self.session_progress = _label("", "CardFooter")
        self.answer_hint = _label("", "CardFooter")
        self.answer_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        escape_hint = _label("Esc to stop", "CardFooter")
        # Names the answer it would take back, so a slip is recognised
        # before it is undone rather than after.
        self.undo_button = QPushButton("")
        self.undo_button.setObjectName("FooterAction")
        self.undo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_button.setToolTip("Take back your last answer (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo_requested)
        self.undo_button.hide()
        footer.addWidget(self.session_progress)
        footer.addWidget(self.undo_button)
        footer.addStretch(1)
        footer.addWidget(self.answer_hint)
        footer.addStretch(1)
        footer.addWidget(escape_hint)
        body.addLayout(footer)

    def _face(self) -> tuple[QWidget, QVBoxLayout]:
        face = QWidget()
        face.setObjectName("PanelBody")
        layout = QVBoxLayout(face)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space_2)
        return face, layout

    def _build_teach(self) -> QWidget:
        face, layout = self._face()
        # As large as fits, never cut off: a phrase shrinks rather than clips.
        self.word_label = WordLabel()
        layout.addWidget(self.word_label)
        self.meta_label = _centered("", "MetaLabel", wrap=False)
        layout.addWidget(self.meta_label)
        layout.addSpacing(METRICS.space_2)
        self.teach_label = _label("", "TeachText", wrap=True)
        self.teach_label.setTextFormat(Qt.TextFormat.RichText)
        self.teach_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.teach_label)
        return face

    def _build_question(self) -> QWidget:
        m = METRICS
        face, layout = self._face()
        self.prompt_label = _centered("", "PromptLabel")
        self.prompt_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.prompt_label)
        self.prompt_detail = _centered("", "PromptDetail")
        layout.addWidget(self.prompt_detail)
        layout.addSpacing(m.space_2)
        # Words side by side, two by two.
        grid = QGridLayout()
        grid.setHorizontalSpacing(m.space_2)
        grid.setVerticalSpacing(m.space_2)
        self.word_buttons: list[AnswerButton] = []
        for index in range(4):
            button = AnswerButton("", LETTERS[index], None)
            button.clicked.connect(lambda _c=False, i=index: self.choose(i))
            grid.addWidget(button, index // 2, index % 2)
            self.word_buttons.append(button)
        self.word_grid = QWidget()
        self.word_grid.setObjectName("PanelBody")
        self.word_grid.setLayout(grid)
        layout.addWidget(self.word_grid)
        # Definitions one under another, as long as they need.
        column = QVBoxLayout()
        column.setSpacing(m.space_2)
        self.definition_buttons: list[AnswerButton] = []
        for index in range(4):
            button = AnswerButton("", LETTERS[index], None, wrap=True)
            button.clicked.connect(lambda _c=False, i=index: self.choose(i))
            column.addWidget(button)
            self.definition_buttons.append(button)
        self.definition_list = QWidget()
        self.definition_list.setObjectName("PanelBody")
        self.definition_list.setLayout(column)
        layout.addWidget(self.definition_list)
        return face

    @property
    def option_buttons(self) -> list[AnswerButton]:
        """The four options of the question on screen."""
        step = self.step
        if step is not None and step.task is Task.CONTEXT_TO_DEFINITION:
            return self.definition_buttons
        return self.word_buttons

    # -- showing a step --------------------------------------------------------------

    def show_step(self, step: Step) -> None:
        self.step = step
        self.waiting = False
        self.rating = False
        self._picked = None
        self.session_flag.setVisible(step.is_struggling and step.phase is Phase.REVIEW)
        self.task_label.setText(step.label.upper())
        # Why this step: hover its name.
        self.task_label.setToolTip(step.reason or "")
        self.task_label.setCursor(
            Qt.CursorShape.WhatsThisCursor if step.reason else Qt.CursorShape.ArrowCursor
        )
        teach = step.kind is StepKind.TEACH
        self.teach_face.setVisible(teach)
        self.question_face.setVisible(not teach)
        self.result_panel.hide()
        self.rating_row.hide()
        self.answer_hint.hide()
        if teach:
            self._show_word(step)
            self.continue_holder.show()
            self.waiting = True
            self.continue_button.setFocus()
        else:
            self.continue_holder.hide()
            self._show_question(step)
        self._relayout()
        self._timer.restart()

    def _show_word(self, step: Step) -> None:
        word = step.word
        self.word_label.setText(word.word)
        letters = "letter" if word.length == 1 else "letters"
        meta = " · ".join(
            part for part in (f"{word.length} {letters}", word.part_of_speech, word.cefr_level)
            if part
        )
        self.meta_label.setText(meta)
        parts = [_block("Definition", escape(word.definition or "No definition yet."))]
        if step.contexts:
            items = "<br>".join(f"“{_marked(c.text, word.word)}”" for c in step.contexts)
            title = "Context" if len(step.contexts) == 1 else "Contexts"
            parts.append(_block(title, items))
        self.teach_label.setText("".join(parts))
        _fit(self.teach_label, _INNER_WIDTH)

    def _show_question(self, step: Step) -> None:
        question = step.question
        assert question is not None
        context = question.task is Task.CONTEXT_TO_DEFINITION
        if context:
            self.prompt_label.setObjectName("ContextPrompt")
            self.prompt_label.setText(f"“{_marked(question.prompt, step.word.word)}”")
            self.prompt_detail.setText("Which definition fits the word in bold?")
        else:
            self.prompt_label.setObjectName("PromptLabel")
            self.prompt_label.setText(escape(question.prompt))
            self.prompt_detail.setText("Which word is it?")
        repolish(self.prompt_label)
        _fit(self.prompt_label, _INNER_WIDTH)
        _fit(self.prompt_detail, _INNER_WIDTH)
        self.word_grid.setVisible(not context)
        self.definition_list.setVisible(context)
        buttons = self.option_buttons
        for index, button in enumerate(buttons):
            visible = index < len(question.options)
            button.setVisible(visible)
            if visible:
                button.set_title(question.options[index].text, _INNER_WIDTH if context else None)
                button.setEnabled(True)
                self._set_result(button, None)
        self.setFocus()

    def set_progress(self, done: int, total: int) -> None:
        self.session_progress.setText(f"{min(done + 1, total)} / {total}")
        self.session_line.set_share(done / total if total else 0)

    def set_undo(self, text: str | None) -> None:
        if text:
            self.undo_button.setText(text)
        self.undo_button.setVisible(bool(text))

    def elapsed_ms(self) -> int:
        return int(self._timer.elapsed()) if self._timer.isValid() else 0

    # -- the learner's actions -------------------------------------------------------

    def choose(self, index: int) -> None:
        """Choose an option, when a question is waiting for one."""
        step = self.step
        if step is None or step.kind is not StepKind.QUESTION or self.waiting or self.rating:
            return
        if step.question is None or not 0 <= index < len(step.question.options):
            return
        self._picked = index
        self.chosen.emit(index, self.elapsed_ms())

    def rate(self, rating: Rating) -> None:
        """Again, Hard, Good or Easy, when a right answer is waiting for one."""
        if self.rating:
            self.rating = False
            self.rated.emit(Rating(int(rating)))

    def rate_by_key(self, index: int) -> None:
        """Keys 1–4: Again, Hard, Good, Easy."""
        ratings = list(Rating)
        if 0 <= index < len(ratings):
            self.rate(ratings[index])

    # -- after an answer -------------------------------------------------------------

    def show_feedback(self, feedback: Feedback, intervals: dict[Rating, int] | None = None) -> None:
        """Mark the options, show the right word and its definition, and ask
        how it went (a right answer to the day's question) or wait for Enter."""
        step = self.step
        if step is None:
            return
        self._picked = feedback.picked
        for index, button in enumerate(self.option_buttons):
            button.setEnabled(False)
            if index == feedback.answer:
                self._set_result(button, "right")
            elif index == feedback.picked and not feedback.correct:
                self._set_result(button, "wrong")
        text = feedback_text(step, feedback)
        mark = "✓" if feedback.correct else "✗"
        self.result_title.setText(f"{mark}  {text.title}")
        self.result_panel.setProperty("tone", text.tone)
        repolish(self.result_panel)
        repolish(self.result_title)
        self.result_lines.setText(
            "<br>".join(
                f"<span style='font-weight:600'>{escape(label)}:</span> "
                + (f"<b>{escape(value)}</b>" if label in ("Word", "Correct answer")
                   else escape(value))
                for label, value in text.lines
            )
        )
        self.result_note.setText(text.note or "")
        self.result_note.setVisible(bool(text.note))
        panel_width = _INNER_WIDTH - 2 * METRICS.space_4
        _fit(self.result_lines, panel_width)
        _fit(self.result_note, panel_width)
        self.result_panel.show()
        self.rating = feedback.awaiting
        self.waiting = not feedback.awaiting
        self.rating_row.setVisible(feedback.awaiting)
        self.continue_holder.setVisible(not feedback.awaiting)
        if feedback.awaiting:
            self.show_intervals(intervals or {})
            self.setFocus()
        else:
            self.answer_hint.hide()
            self.continue_button.setFocus()
        self._relayout()

    def show_intervals(self, preview: dict[Rating, int]) -> None:
        """Label the ratings with when the word would come back.

        Early on, every answer lands tomorrow. Saying "tomorrow" four times
        looks like a bug, so then the buttons carry only their names and the
        card's footer says it once.
        """
        labels = {rating: interval_text(preview.get(rating)) for rating in Rating}
        uniform = len(set(labels.values())) <= 1
        for rating, button in self.rating_buttons.items():
            button.set_interval("" if uniform else labels[rating])
        self.answer_hint.setText(
            f"Every answer brings it back {labels[Rating.GOOD]}" if uniform else ""
        )
        self.answer_hint.setVisible(uniform)

    @staticmethod
    def _set_result(widget: QWidget, result: str | None) -> None:
        widget.setProperty("result", result or "")
        repolish(widget)
        # Rules like "[result=right] #AnswerTitle" style the labels inside;
        # Qt only re-reads them when the labels themselves are polished again.
        for child in widget.findChildren(QLabel):
            repolish(child)

    def _relayout(self) -> None:
        """Lay out now, not on the next pass, inside the card and around it.

        A face shown for the first time would otherwise be drawn once at the
        card's old height, its bottom row cut off by the footer.
        """
        self.layout().activate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().activate()


def _fit(label: QLabel, width: int) -> None:
    """Make a wrapped label exactly as tall as its text at the card's width.

    Left to itself, Qt sizes a wrapped label from a narrow hint: a prompt that
    wrapped to two lines squeezed the options under it until they overlapped,
    and a short one got room for three lines. The card's width is fixed, so
    the right height is known. The last question's height is cleared first:
    Qt clamps what the label measures to it.
    """
    label.setMinimumHeight(0)
    label.setMaximumHeight(_NO_LIMIT)
    label.setFixedHeight(label.heightForWidth(width))


def _block(title: str, html: str) -> str:
    return (
        f"<p style='margin:0 0 10px 0'><span style='font-size:11px;font-weight:600;"
        f"letter-spacing:0.6px'>{title.upper()}</span><br>{html}</p>"
    )


def _marked(text: str, word: str) -> str:
    """A context as rich text, the word in bold where it can be found.

    Content comes from imported files: shown as text, never as markup.
    """
    span = find_word(text, word)
    if span is None:
        return escape(text)
    start, end = span
    return f"{escape(text[:start])}<b>{escape(text[start:end])}</b>{escape(text[end:])}"
