"""The review card: one step of a session on screen.

The card draws what :class:`~lexitrack.services.review_flow.ReviewFlow` says
is next and reports what the learner did; it decides nothing. One card, five
faces, of which one shows at a time:

* **type** — a prompt (a meaning, a sentence with a gap, a phrase) and a box
  to type the word in, with a hint (the first letter) and "I don't know";
* **choose** — the meaning and four words, keys 1–4;
* **write** — the word to use; after the sentence, examples to compare it
  with and three grades;
* **teach** — everything known about the word, before it is asked again;
* **recall** — the V1 card: the word, its meaning behind Space, four answers.

After an answer a line says what happened and Enter moves on, so the right
spelling is always seen before the next question.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QElapsedTimer, QEvent, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...models.attempt import MemoryResult, Phase, Role
from ...models.srs import Rating
from ...services.review_flow import Feedback, Step, StepKind
from ...services.review_route import examples, hint_for
from ..theme import current_palette
from ..theme.palette import METRICS
from .cards import repolish
from .chips import chip

#: Which button style each answer gets. Amber for "not yet", green for
#: "solid", and Easy the solid green: the flashcards' own colour language.
ANSWER_STYLE: dict[Rating, tuple[str, str | None]] = {
    Rating.AGAIN: ("1", "unknown"),
    Rating.HARD: ("2", None),
    Rating.GOOD: ("3", "known"),
    Rating.EASY: ("4", "known-solid"),
}

#: The three grades of a written sentence: (key, label, used well, effortful).
GRADES = (
    ("1", "Couldn't use it", False, False),
    ("2", "With effort", True, True),
    ("3", "Used it well", True, False),
)

CARD_WIDTH = 640


def interval_text(days: int | None) -> str:
    """The interval under an answer button, in the shortest honest words."""
    if days is None:
        return "–"
    if days <= 1:
        return "tomorrow"
    if days < 30:
        return f"{days} days"
    if days < 365:
        months = round(days / 30)
        return f"{months} month" + ("s" if months != 1 else "")
    years = days / 365
    return "1 year" if round(years) == 1 else f"{years:.1f} years"


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
    #: A written sentence graded: used well, with effort.
    graded = Signal(bool, bool)
    #: A V1 answer.
    rated = Signal(object)
    reveal_requested = Signal()
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
        self.word_label = _centered("", "WordLabel")
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

    def _build_recall(self) -> QWidget:
        m = METRICS
        face, layout = self._face()
        meaning = QVBoxLayout()
        meaning.setSpacing(m.space_1)
        self.reveal_button = QPushButton("Show meaning   Space")
        self.reveal_button.setProperty("variant", "ghost")
        self.reveal_button.setToolTip("Space")
        self.reveal_button.clicked.connect(self.reveal_requested)
        meaning.addWidget(self.reveal_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.definition_label = _centered("", "DefinitionLabel")
        meaning.addWidget(self.definition_label)
        self.note_label = _centered("", "SenseLabel")
        meaning.addWidget(self.note_label)
        holder = QWidget()
        holder.setObjectName("PanelBody")
        holder.setLayout(meaning)
        # Room for a two-line meaning is reserved, so revealing it does not
        # push the answer buttons down under the pointer.
        holder.setMinimumHeight(64)
        layout.addWidget(holder)
        layout.addSpacing(m.space_2)
        answers = QHBoxLayout()
        answers.setSpacing(m.space_2)
        self.answer_buttons: dict[Rating, AnswerButton] = {}
        for rating in Rating:
            key, variant = ANSWER_STYLE[rating]
            button = AnswerButton(rating.label, key, variant)
            button.clicked.connect(lambda _c=False, r=rating: self.rated.emit(r))
            answers.addWidget(button, 1)
            self.answer_buttons[rating] = button
        layout.addLayout(answers)
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
        self.dont_know_button = QPushButton("I don't know   Enter")
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
        m = METRICS
        face, layout = self._face()
        self.write_input = QLineEdit()
        self.write_input.setObjectName("SentenceInput")
        self.write_input.setPlaceholderText("Write a sentence with the word, then press Enter")
        self.write_input.setAccessibleName("Your sentence")
        self.write_input.installEventFilter(self)
        layout.addWidget(self.write_input)
        self.examples_label = _label("", "ExamplesLabel", wrap=True)
        layout.addWidget(self.examples_label)
        grades = QHBoxLayout()
        grades.setSpacing(m.space_2)
        self.grade_buttons: list[AnswerButton] = []
        for key, title, used_well, effortful in GRADES:
            variant = "unknown" if not used_well else ("known" if effortful else "known-solid")
            button = AnswerButton(title, key, variant)
            button.clicked.connect(
                lambda _c=False, u=used_well, e=effortful: self._grade(u, e)
            )
            grades.addWidget(button, 1)
            self.grade_buttons.append(button)
        self.grade_row = QWidget()
        self.grade_row.setObjectName("PanelBody")
        self.grade_row.setLayout(grades)
        layout.addWidget(self.grade_row)
        return face

    def _build_teach(self) -> QWidget:
        face, layout = self._face()
        self.teach_label = _label("", "TeachText", wrap=True)
        self.teach_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.teach_label)
        return face

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
        revealed: bool = False,
        intervals: dict[Rating, int] | None = None,
    ) -> None:
        self.step = step
        self.hinted = False
        self.written = False
        self.waiting = False
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
        self.answer_hint.hide()

        if kind is StepKind.RECALL:
            self.show_meaning(revealed)
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
        self._timer.restart()

    def show_meaning(self, shown: bool) -> None:
        step = self.step
        word = step.word if step else None
        self.reveal_button.setVisible(not shown)
        self.definition_label.setVisible(shown)
        note = word.note if word else None
        self.note_label.setVisible(shown and bool(note))
        if shown and word is not None:
            self.definition_label.setText(word.definition or "No definition stored for this word.")
            self.note_label.setText(note or "")

    def show_intervals(self, preview: dict[Rating, int]) -> None:
        """Label the answers with when the word would come back.

        Early on, every answer lands tomorrow. Saying "tomorrow" four times
        looks like a bug, so then the buttons carry only their names and the
        card's footer says it once.
        """
        labels = {rating: interval_text(preview.get(rating)) for rating in Rating}
        uniform = len(set(labels.values())) <= 1
        for rating, button in self.answer_buttons.items():
            button.set_interval("" if uniform else labels[rating])
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
        self.written = True
        self.write_input.setEnabled(False)
        shown = examples(step.teaching) if step.teaching else []
        lines = [f"“{context.plain}”" for context in shown]
        meaning = step.prompt.detail if step.prompt else None
        if meaning:
            lines.insert(0, f"Meaning: {meaning}")
        fallback = "Compare it with how you have seen it used."
        self.examples_label.setText("\n".join(lines) or fallback)
        self.examples_label.show()
        self.grade_row.show()
        self.setFocus()

    def grade(self, index: int) -> None:
        if self.step is None or self.step.kind is not StepKind.WRITE or not self.written:
            return
        if self.waiting or not 0 <= index < len(GRADES):
            return
        _key, _title, used_well, effortful = GRADES[index]
        self._grade(used_well, effortful)

    def _grade(self, used_well: bool, effortful: bool) -> None:
        if self.waiting or not self.written:
            return
        self.graded.emit(used_well, effortful)

    # -- after an answer -------------------------------------------------------------

    def show_feedback(self, feedback: Feedback) -> None:
        """Say what the answer did, and wait for Enter."""
        step = self.step
        self.waiting = True
        text, tone = _feedback_text(step, feedback)
        self.feedback_label.setText(text)
        self.feedback_label.setProperty("tone", tone)
        repolish(self.feedback_label)
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
        self.feedback_row.show()
        self._relayout()
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


def _feedback_text(step: Step | None, feedback: Feedback) -> tuple[str, str]:
    """The line under an answer, and its tone: good, bad or neutral."""
    if feedback.answer is None:
        return "Not quite. One more question about this word.", "bad"
    answer = feedback.answer
    outcome = feedback.outcome
    back = ""
    if outcome is not None and not outcome.duplicate:
        back = f"Back {_when(outcome.interval_days)}"
    later = ""
    if feedback.resolution is not None and feedback.resolution.follow_up.value != "none":
        later = "; you'll go over it again in a moment"
    if step is not None and step.phase is not Phase.REVIEW:
        # Practice after teaching: never rated, so nothing about when.
        if feedback.correct:
            return f"✓ “{answer}”", "good"
        return f"It is “{answer}”.", "bad"
    probe = step is not None and step.role is Role.PROBE
    if feedback.correct and not probe:
        slip = " — accepted; mind the spelling" if feedback.near_miss else ""
        return f"✓ “{answer}”{slip} · {back.lower()}" if back else f"✓ “{answer}”{slip}", "good"
    memory = feedback.resolution.memory if feedback.resolution else None
    tail = f" {back}{later}." if back else ""
    if memory is MemoryResult.RECALLED or memory is MemoryResult.RECALLED_EFFORT:
        return f"It is “{answer}” — the word itself you knew.{tail}", "neutral"
    if memory is MemoryResult.RECOGNIZED:
        return f"It is “{answer}” — you recognised it.{tail}", "neutral"
    return f"It is “{answer}”.{tail}", "bad"


def _when(days: int) -> str:
    text = interval_text(days)
    return text if text == "tomorrow" else f"in {text}"


def _teaching_html(step: Step) -> str:
    """Everything known about the word, as a short page."""
    word = step.word
    teaching = step.teaching
    content = teaching.content if teaching else None
    parts: list[str] = []

    def line(title: str, text: str | None, raw: bool = False) -> None:
        # Content comes from imported files: shown as text, never as markup.
        if text and not raw:
            text = escape(text)
        if text:
            parts.append(
                f"<p style='margin:0 0 8px 0'><span style='font-size:11px;"
                f"font-weight:600;letter-spacing:0.6px'>{title.upper()}</span><br>{text}</p>"
            )

    localization = teaching.localization if teaching else None
    line("Meaning", teaching.core_meaning if teaching else None)
    line("Definition", word.definition)
    if content:
        line("Pattern", content.pattern)
        if content.collocations:
            line("Goes with", " · ".join(content.collocations))
    if localization:
        line("Nuance", localization.nuance)
        line("How it is used", localization.usage_note)
        line("To remember", localization.encoding_cue)
        line("Note", localization.notes)
    if teaching and teaching.contexts:
        examples = []
        for context in teaching.contexts[:2]:
            translated = teaching.translation(context)
            examples.append(
                f"“{escape(context.plain)}”"
                + (f"<br><i>{escape(translated)}</i>" if translated else "")
            )
        line("In use", "<br>".join(examples), raw=True)
    return "".join(parts) or "<p>No more is stored about this word yet.</p>"
