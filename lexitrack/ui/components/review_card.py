"""The review card: one step of a session on screen.

The card draws what :class:`~lexitrack.services.review_flow.ReviewFlow` says
is next and reports what the learner did; it decides nothing. One card, five
faces, of which one shows at a time:

* **type** — a prompt (a meaning, a sentence with a gap, a phrase) and a box
  to type the word in, with a hint (the first letter) and Forgot; after a
  right answer, how it came: Effortful, Remembered or Instant;
* **choose** — the meaning and four words, keys 1–4;
* **write** — the word to use; after the sentence, examples, the pattern and
  collocations to check it against, and the four reports;
* **teach** — everything known about the word, before it is asked again;
* **recall** — a word with no meaning to ask from, and the four reports.

The reports are the same everywhere, keys 1–4: Forgot, Effortful,
Remembered, Instant. After an answer a line says what happened and Enter
moves on, so the right spelling is always seen before the next question.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QElapsedTimer, QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...models.attempt import SUCCESS_REPORTS, Phase, SelfReport
from ...models.srs import Rating
from ...services.review_flow import Feedback, Step, StepKind
from ...services.review_route import examples, hint_for
from ...services.review_wording import (
    feedback_text,
    interval_text,
    teaching_page,
    write_checklist,
)
from ..theme import current_palette
from ..theme.palette import METRICS
from .cards import repolish
from .chips import chip
from .word_label import WordLabel

#: Which button style each report gets. Amber for "not yet", green for
#: "solid", and Instant the solid green: the flashcards' own colour language.
REPORT_STYLE: dict[SelfReport, str | None] = {
    SelfReport.FORGOT: "unknown",
    SelfReport.EFFORTFUL: None,
    SelfReport.REMEMBERED: "known",
    SelfReport.INSTANT: "known-solid",
}

#: The rating each report on a shown word maps to, for the interval under it.
REPORT_RATING = {
    SelfReport.FORGOT: Rating.AGAIN,
    SelfReport.EFFORTFUL: Rating.HARD,
    SelfReport.REMEMBERED: Rating.GOOD,
    SelfReport.INSTANT: Rating.EASY,
}

CARD_WIDTH = 640
#: The teaching page's height before it scrolls inside the card.
TEACH_MAX_HEIGHT = 440


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
    """An answer: its key, its name and when the word comes back.

    Built from labels inside the button because a QPushButton cannot mix type
    sizes in its own text, and the key and the interval must be smaller and
    quieter than the answer itself.
    """

    def __init__(self, title: str, key: str, variant: str | None, tooltip: str = "") -> None:
        super().__init__()
        self.setObjectName("AnswerButton")
        if variant:
            self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"{title} ({key})")
        self.setAccessibleName(title)
        self.setFixedHeight(60)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(1)
        layout.addStretch(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addStretch(1)
        self.key = _label(key, "AnswerKey")
        self.key.setFixedHeight(18)
        self.key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = _label(title, "AnswerTitle")
        top.addWidget(self.key, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        layout.addLayout(top)
        self.sub = _label("", "AnswerSub")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.hide()
        layout.addWidget(self.sub)
        layout.addStretch(1)
        for child in (self.key, self.title, self.sub):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_interval(self, text: str) -> None:
        self.sub.setText(text)
        self.sub.setVisible(bool(text))

    def text(self) -> str:  # noqa: D102 - what a reader of the button sees
        interval = self.sub.text() if not self.sub.isHidden() else ""
        return f"{self.title.text()}\n{interval}" if interval else self.title.text()


class ReviewCard(QFrame):
    """One step of a review session; see the module docstring."""

    #: A typed answer: the text, milliseconds taken, whether the hint was used.
    submitted = Signal(str, int, bool)
    #: An option chosen: its index and the milliseconds taken.
    chosen = Signal(int, int)
    #: The learner's report (a SelfReport) on the step on screen.
    assessed = Signal(object)
    #: A sentence written for a WRITE step, before its report.
    written_sentence = Signal(str)
    #: "More about this word" on a new word's page.
    more_requested = Signal()
    #: Enter after feedback, or on a teaching page.
    continue_requested = Signal()
    undo_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(CARD_WIDTH)
        self.step: Step | None = None
        self.hinted = False
        self.written = False
        self.waiting = False
        #: A right typed answer shown, waiting for Effortful/Remembered/Instant.
        self.assessing = False
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

        # The word, for recall, teaching and writing.
        # As large as fits, never cut off: a phrase shrinks rather than clips.
        self.word_label = WordLabel()
        body.addWidget(self.word_label)
        self.meta_label = _centered("", "MetaLabel", wrap=False)
        body.addWidget(self.meta_label)

        # The prompt, for typing and choosing.
        self.prompt_label = _centered("", "PromptLabel")
        body.addWidget(self.prompt_label)
        self.prompt_detail = _centered("", "PromptDetail")
        body.addWidget(self.prompt_detail)

        self.recall_face = self._build_recall()
        self.type_face = self._build_type()
        self.choose_face = self._build_choose()
        self.write_face = self._build_write()
        self.teach_face = self._build_teach()
        for face in (self.recall_face, self.type_face, self.choose_face, self.write_face,
                     self.teach_face):
            body.addWidget(face)

        feedback = QHBoxLayout()
        feedback.setSpacing(m.space_3)
        self.feedback_label = _label("", "FeedbackLabel", wrap=True)
        feedback.addWidget(self.feedback_label, 1)
        self.continue_button = QPushButton("Continue   Enter")
        self.continue_button.setProperty("variant", "primary")
        self.continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_button.clicked.connect(self.continue_requested)
        feedback.addWidget(self.continue_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.feedback_row = QWidget()
        self.feedback_row.setObjectName("PanelBody")
        self.feedback_row.setLayout(feedback)
        body.addWidget(self.feedback_row)

        # After a right typed answer: how it came. The answer is already
        # seen, so forgetting it is not offered.
        self.report_row, self.report_buttons = self._report_row(SUCCESS_REPORTS)
        body.addWidget(self.report_row)

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

    def _report_row(
        self, reports: tuple[SelfReport, ...]
    ) -> tuple[QWidget, dict[SelfReport, AnswerButton]]:
        """A row of report buttons, keys 1–4 whatever the row holds."""
        row = QHBoxLayout()
        row.setSpacing(METRICS.space_2)
        buttons: dict[SelfReport, AnswerButton] = {}
        for report in reports:
            button = AnswerButton(report.label, report.key, REPORT_STYLE[report])
            button.clicked.connect(lambda _c=False, r=report: self.assess(r))
            row.addWidget(button, 1)
            buttons[report] = button
        holder = QWidget()
        holder.setObjectName("PanelBody")
        holder.setLayout(row)
        holder.hide()
        return holder, buttons

    def _build_recall(self) -> QWidget:
        m = METRICS
        face, layout = self._face()
        self.recall_note = _centered(
            "No meaning is stored for this word yet. Do you know it?", "SenseLabel"
        )
        layout.addWidget(self.recall_note)
        layout.addSpacing(m.space_2)
        self.recall_row, self.answer_buttons = self._report_row(tuple(SelfReport))
        self.recall_row.show()
        layout.addWidget(self.recall_row)
        return face

    def _build_type(self) -> QWidget:
        m = METRICS
        face, layout = self._face()
        layout.addSpacing(m.space_2)
        self.answer_input = QLineEdit()
        self.answer_input.setObjectName("AnswerInput")
        self.answer_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.answer_input.setPlaceholderText("Type the word")
        self.answer_input.setAccessibleName("Your answer")
        # Enter is taken here rather than through returnPressed: the line edit
        # passes Enter on to its parents, where it would also move past the
        # feedback it just produced.
        self.answer_input.installEventFilter(self)
        layout.addWidget(self.answer_input)
        self.hint_label = _centered("", "HintLabel", wrap=False)
        self.hint_label.hide()
        layout.addWidget(self.hint_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.hint_button = QPushButton("First letter   Ctrl+H")
        self.hint_button.setObjectName("FooterAction")
        self.hint_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hint_button.setToolTip("Show the first letter; the answer then counts as effortful")
        self.hint_button.clicked.connect(self.show_hint)
        row.addWidget(self.hint_button)
        self.dont_know_button = QPushButton("Forgot   Enter")
        self.dont_know_button.setObjectName("FooterAction")
        self.dont_know_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dont_know_button.setToolTip("Enter with nothing typed says the same")
        self.dont_know_button.clicked.connect(lambda: self._submit(give_up=True))
        row.addWidget(self.dont_know_button)
        row.addStretch(1)
        layout.addLayout(row)
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self.show_hint,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        return face

    def _build_choose(self) -> QWidget:
        m = METRICS
        face, layout = self._face()
        grid = QGridLayout()
        grid.setHorizontalSpacing(m.space_2)
        grid.setVerticalSpacing(m.space_2)
        self.choice_buttons: list[AnswerButton] = []
        for index in range(4):
            # The answers' own height: the stylesheet gives #AnswerButton a
            # minimum of 58 px, and a smaller fixed height would be overdrawn.
            button = AnswerButton("", str(index + 1), None)
            button.clicked.connect(lambda _c=False, i=index: self._choose(i))
            grid.addWidget(button, index // 2, index % 2)
            self.choice_buttons.append(button)
        layout.addLayout(grid)
        return face

    def _build_write(self) -> QWidget:
        face, layout = self._face()
        self.write_input = QLineEdit()
        self.write_input.setObjectName("SentenceInput")
        self.write_input.setPlaceholderText("Write a sentence with the word, then press Enter")
        self.write_input.setAccessibleName("Your sentence")
        self.write_input.installEventFilter(self)
        layout.addWidget(self.write_input)
        self.examples_label = _label("", "ExamplesLabel", wrap=True)
        layout.addWidget(self.examples_label)
        self.grade_row, grade_buttons = self._report_row(tuple(SelfReport))
        self.grade_buttons = list(grade_buttons.values())
        layout.addWidget(self.grade_row)
        return face

    def _build_teach(self) -> QWidget:
        face, layout = self._face()
        self.teach_label = _label("", "TeachText", wrap=True)
        self.teach_label.setTextFormat(Qt.TextFormat.RichText)
        # A full page can be taller than the window: it scrolls inside the
        # card rather than being cut off, and is only as tall as it needs.
        self.teach_scroll = QScrollArea()
        self.teach_scroll.setObjectName("TeachScroll")
        self.teach_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.teach_scroll.setWidgetResizable(True)
        self.teach_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.teach_scroll.setWidget(self.teach_label)
        layout.addWidget(self.teach_scroll)
        self.more_button = QPushButton("More about this word   M")
        self.more_button.setObjectName("FooterAction")
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.setToolTip("Everything stored about it: nuance, usage, register, related")
        self.more_button.clicked.connect(self.more_requested)
        self.more_button.hide()
        layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignLeft)
        return face

    def _refit_teaching(self) -> None:
        if self.step is not None and self.step.kind is StepKind.TEACH:
            self._fit_teaching()
            self._relayout()

    def _fit_teaching(self) -> None:
        # Measured at the width the text really has, once laid out; the
        # label's 12 px padding above and below is not in its height-for-width.
        width = self.teach_scroll.viewport().width()
        if width < 200:
            width = CARD_WIDTH - 2 * METRICS.space_6
        needed = self.teach_label.heightForWidth(width) + 26
        self.teach_scroll.setFixedHeight(min(max(needed, 60), TEACH_MAX_HEIGHT))
        self.teach_scroll.verticalScrollBar().setValue(0)

    def set_more(self, available: bool) -> None:
        self.more_button.setVisible(available)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Return, Qt.Key.Key_Enter
        ):
            if watched is self.answer_input:
                self._submit()
                return True
            if watched is self.write_input:
                self._written()
                return True
        return super().eventFilter(watched, event)

    # -- showing a step --------------------------------------------------------------

    def show_step(
        self,
        step: Step,
        *,
        intervals: dict[Rating, int] | None = None,
    ) -> None:
        self.step = step
        self.hinted = False
        self.written = False
        self.waiting = False
        self.assessing = False
        self.report_row.hide()
        kind = step.kind
        word = step.word
        self.session_flag.setVisible(step.is_struggling and step.phase is Phase.REVIEW)
        self.task_label.setText(step.label.upper())
        # Why this question: hover the task's name.
        self.task_label.setToolTip(step.reason or "")
        self.task_label.setCursor(
            Qt.CursorShape.WhatsThisCursor if step.reason else Qt.CursorShape.ArrowCursor
        )

        shows_word = kind in (StepKind.RECALL, StepKind.TEACH, StepKind.WRITE)
        self.word_label.setVisible(shows_word)
        self.meta_label.setVisible(shows_word)
        self.word_label.setText(word.word)
        meta = " · ".join(part for part in (word.part_of_speech, word.cefr_level) if part)
        self.meta_label.setText(meta)
        self.meta_label.setVisible(shows_word and bool(meta))

        asks = kind in (StepKind.TYPE, StepKind.CHOOSE)
        prompt = step.prompt
        self.prompt_label.setVisible(asks)
        self.prompt_label.setText(prompt.text if asks and prompt else "")
        detail = prompt.detail if prompt else None
        self.prompt_detail.setText(detail or "")
        self.prompt_detail.setVisible(asks and bool(detail))

        for face, face_kind in (
            (self.recall_face, StepKind.RECALL),
            (self.type_face, StepKind.TYPE),
            (self.choose_face, StepKind.CHOOSE),
            (self.write_face, StepKind.WRITE),
            (self.teach_face, StepKind.TEACH),
        ):
            face.setVisible(kind is face_kind)
        self.feedback_row.hide()
        self.continue_button.show()
        self.recall_row.setVisible(kind is StepKind.RECALL)
        self.answer_hint.hide()

        if kind is StepKind.RECALL:
            self.show_intervals(intervals or {})
        elif kind is StepKind.TYPE:
            self.answer_input.clear()
            self.answer_input.setPlaceholderText("Type the word")
            self.answer_input.setEnabled(True)
            self._set_result(self.answer_input, None)
            self.hint_label.hide()
            self.hint_button.show()
            self.dont_know_button.show()
            self.answer_input.setFocus()
        elif kind is StepKind.CHOOSE:
            for button, option in zip(self.choice_buttons, step.options, strict=False):
                button.title.setText(option.word)
                button.setAccessibleName(option.word)
                button.setToolTip(option.word)
                button.setEnabled(True)
                self._set_result(button, None)
            for index, button in enumerate(self.choice_buttons):
                button.setVisible(index < len(step.options))
        elif kind is StepKind.WRITE:
            self.write_input.clear()
            self.write_input.setEnabled(True)
            self.examples_label.hide()
            self.grade_row.hide()
            self.write_input.setFocus()
        elif kind is StepKind.TEACH:
            self.teach_label.setText(_teaching_html(step))
            self.feedback_label.setText("")
            self.feedback_row.show()
            self.waiting = True
        self._relayout()
        if kind is StepKind.TEACH:
            self._fit_teaching()
            self._relayout()
            # Shown for the first time, the page has its real width only once
            # the layout has settled: measured again then.
            QTimer.singleShot(0, self._refit_teaching)
        self._timer.restart()

    def show_intervals(self, preview: dict[Rating, int]) -> None:
        """Label the reports with when the word would come back.

        Early on, every answer lands tomorrow. Saying "tomorrow" four times
        looks like a bug, so then the buttons carry only their names and the
        card's footer says it once.
        """
        labels = {rating: interval_text(preview.get(rating)) for rating in Rating}
        uniform = len(set(labels.values())) <= 1
        for report, button in self.answer_buttons.items():
            button.set_interval("" if uniform else labels[REPORT_RATING[report]])
        self.answer_hint.setText(
            f"Every answer brings it back {labels[Rating.GOOD]}" if uniform else ""
        )
        self.answer_hint.setVisible(uniform)

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

    def show_hint(self) -> None:
        step = self.step
        if step is None or step.kind is not StepKind.TYPE or self.waiting or self.hinted:
            return
        self.hinted = True
        self.hint_label.setText(hint_for(step.prompt.answer))
        self.hint_label.show()
        self.hint_button.hide()
        self.answer_input.setFocus()

    def _submit(self, give_up: bool = False) -> None:
        if self.step is None or self.step.kind is not StepKind.TYPE or self.waiting:
            return
        text = "" if give_up else self.answer_input.text()
        self.submitted.emit(text, self.elapsed_ms(), self.hinted)

    def choose(self, index: int) -> None:
        self._choose(index)

    def _choose(self, index: int) -> None:
        if self.step is None or self.step.kind is not StepKind.CHOOSE or self.waiting:
            return
        if index >= len(self.step.options):
            return
        self._picked = index
        self.chosen.emit(index, self.elapsed_ms())

    def _written(self) -> None:
        step = self.step
        if step is None or step.kind is not StepKind.WRITE or self.written:
            return
        if not self.write_input.text().strip():
            return
        self.written_sentence.emit(self.write_input.text().strip())
        self.show_written()

    def show_written(self) -> None:
        """The written sentence beside what it is checked against, and the reports."""
        step = self.step
        if step is None or step.kind is not StepKind.WRITE:
            return
        self.written = True
        self.write_input.setEnabled(False)
        shown = examples(step.teaching) if step.teaching else []
        lines = []
        meaning = step.prompt.detail if step.prompt else None
        if meaning:
            lines.append(f"Meaning: {meaning}")
        # The reference the learner grades themselves against (level 5).
        checklist = write_checklist(step)
        if checklist:
            lines.append("Check your sentence against:")
            lines += [f"☐ {title}: {text}" for title, text in checklist]
        if shown:
            lines.append("Examples:")
            lines += [f"“{context.plain}”" for context in shown]
        fallback = "Compare it with how you have seen it used."
        self.examples_label.setText("\n".join(lines) or fallback)
        self.examples_label.show()
        self.grade_row.show()
        self.setFocus()

    def assess(self, report: SelfReport) -> None:
        """A report, when the card is asking for one; otherwise nothing."""
        step = self.step
        if step is None:
            return
        asking = (
            (step.kind is StepKind.TYPE and self.assessing and report.success)
            or (step.kind is StepKind.WRITE and self.written and not self.waiting)
            or (step.kind is StepKind.RECALL and not self.waiting)
        )
        if asking:
            self.assessing = False
            self.assessed.emit(report)

    def report_by_key(self, index: int) -> None:
        """Keys 1–4: Forgot, Effortful, Remembered, Instant."""
        if 0 <= index < len(SelfReport):
            self.assess(list(SelfReport)[index])

    # -- after an answer -------------------------------------------------------------

    def show_feedback(self, feedback: Feedback) -> None:
        """Say what the answer did, and wait for Enter — or, after a right
        typed answer, for how it came."""
        step = self.step
        self.waiting = not feedback.awaiting
        self.assessing = feedback.awaiting
        text, tone = feedback_text(step, feedback)
        if feedback.awaiting:
            text = f"{text} — how did it come?"
        self.feedback_label.setText(text)
        self.feedback_label.setProperty("tone", tone)
        repolish(self.feedback_label)
        self.continue_button.setVisible(not feedback.awaiting)
        self.report_row.setVisible(feedback.awaiting)
        if step is not None and step.kind is StepKind.TYPE:
            if not self.answer_input.text():
                self.answer_input.setPlaceholderText("No answer")
            self.answer_input.setEnabled(False)
            self._set_result(
                self.answer_input,
                "near" if feedback.near_miss else ("right" if feedback.correct else "wrong"),
            )
            self.hint_button.hide()
            self.dont_know_button.hide()
        if step is not None and step.kind is StepKind.CHOOSE:
            picked = getattr(self, "_picked", None)
            for index, (button, option) in enumerate(
                zip(self.choice_buttons, step.options, strict=False)
            ):
                button.setEnabled(False)
                if feedback.answer is not None and option.id == step.word.id:
                    self._set_result(button, "right")
                elif index == picked and not feedback.correct:
                    self._set_result(button, "wrong")
        if step is not None and step.kind is StepKind.WRITE:
            self.grade_row.hide()
        if step is not None and step.kind is StepKind.RECALL:
            self.recall_row.hide()
        self.feedback_row.show()
        self._relayout()
        if feedback.awaiting:
            self.setFocus()
        else:
            self.continue_button.setFocus()

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


def _teaching_html(step: Step) -> str:
    """The teaching page (services/review_wording.py) as rich text."""
    page = teaching_page(step)
    parts: list[str] = []

    def block(title: str, html: str) -> None:
        parts.append(
            f"<p style='margin:0 0 8px 0'><span style='font-size:11px;"
            f"font-weight:600;letter-spacing:0.6px'>{title.upper()}</span><br>{html}</p>"
        )

    # Content comes from imported files: shown as text, never as markup.
    for title, text in page.sections:
        block(title, escape(text))
    if page.note:
        parts.append(f"<p style='margin:0 0 8px 0'><i>{escape(page.note)}</i></p>")
    if page.examples:
        block("In use", "<br>".join(
            f"“{escape(sentence)}”" + (f"<br><i>{escape(translated)}</i>" if translated else "")
            for sentence, translated in page.examples
        ))
    return "".join(parts) or "<p>No more is stored about this word yet.</p>"
