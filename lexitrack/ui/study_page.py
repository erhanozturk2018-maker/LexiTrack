"""Study: the one screen that answers "what do I do today?".

The page has three faces and shows exactly one of them:

1. **No plan yet** — one explanation and one button.
2. **The day** — built the way Home is built, so the two read as one app:
   a single *Today* panel at the top that makes the decision for you (the
   day's two steps, one progress line, one button whose label is the next
   thing to do), then sections whose titles sit outside their cards — the
   new words as chips grouped by level, the week as seven day tiles, the
   words you find hard, and the last thirty days as stat tiles.
3. **A review session** — the same card as Review's flashcards: the word,
   a chip for context, four answers inside the card with their keys, and
   the count in the card's corner.

Two decisions worth knowing:

* **There is one primary button, and its label changes.** Two panels with a
  button each made the user choose between them; the day has an order —
  learn the new words, then review — and the button follows it.
* **The answer buttons say when the word comes back**, and when all four say
  the same thing they say it once, underneath.

The page holds no learning logic: every number comes from one
:meth:`LearningService.daily_plan` call, and every action is a service method.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.srs import Rating
from ..models.word_entry import CEFR_ORDER
from ..services.learning_service import DailyPlan, LearningService
from ..services.study_flow import StudyFlow
from .components.cards import StatTile, repolish
from .components.chips import ChipFlow, DayProgress, WeekStrip, chip
from .theme import current_palette
from .theme.palette import METRICS
from .widgets import PageColumn

#: Which button style each answer gets. Amber for "not yet", green for
#: "solid", and Easy the solid green: the flashcards' own colour language.
_ANSWER_STYLE: dict[Rating, tuple[str, str | None]] = {
    Rating.AGAIN: ("1", "unknown"),
    Rating.HARD: ("2", None),
    Rating.GOOD: ("3", "known"),
    Rating.EASY: ("4", "known-solid"),
}

EMPTY, DAY, SESSION = "empty", "day", "session"


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _setup_step(number: str, title: str) -> tuple[QWidget, QVBoxLayout]:
    """One numbered question of the first-run setup."""
    step = QWidget()
    step.setObjectName("PanelBody")
    row = QHBoxLayout(step)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(METRICS.space_3)
    badge = _label(number, "SetupNumber")
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setFixedSize(24, 24)
    row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
    body = QVBoxLayout()
    body.setSpacing(METRICS.space_2)
    body.addWidget(_label(title, "SectionTitle"))
    row.addLayout(body, 1)
    return step, body


def _panel(name: str = "Panel") -> tuple[QFrame, QVBoxLayout]:
    m = METRICS
    frame = QFrame()
    frame.setObjectName(name)
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
    layout.setSpacing(m.space_3)
    return frame, layout


class _Section(QWidget):
    """A title outside, content below — Home's OVERVIEW / YOUR LISTS pattern."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space_2 + 2)
        self.header = QHBoxLayout()
        self.header.setSpacing(METRICS.space_2)
        self.title = _label(title, "SectionTitle")
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        layout.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(METRICS.space_2)
        layout.addLayout(self.body)

    def set_title(self, text: str) -> None:
        self.title.setText(text)


class _Step(QWidget):
    """One line of the Today panel: a mark, what to do, how far along."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space_2 + 2)
        self.glyph = _label("", "StepGlyph")
        self.glyph.setFixedWidth(18)
        self.text = _label("", "StepText")
        self.meta = _label("", "StepMeta")
        layout.addWidget(self.glyph)
        layout.addWidget(self.text)
        layout.addWidget(self.meta)
        layout.addStretch(1)

    def set_step(self, text: str, meta: str, state: str) -> None:
        """``state`` is ``done``, ``active`` or ``waiting``."""
        self.glyph.setText({"done": "✓", "active": "●"}.get(state, "○"))
        for widget in (self.glyph, self.text):
            widget.setProperty("state", state)
            repolish(widget)
        self.text.setText(text)
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))
        self.setAccessibleName(f"{text}. {meta}")


class _SessionProgress(QWidget):
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


class _AnswerButton(QPushButton):
    """An answer: its key, its name and when the word comes back.

    Built from labels inside the button because a QPushButton cannot mix type
    sizes in its own text, and the key and the interval must be smaller and
    quieter than the answer itself.
    """

    def __init__(self, rating: Rating, key: str, variant: str | None) -> None:
        super().__init__()
        self.rating = rating
        self.setObjectName("AnswerButton")
        if variant:
            self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{rating.label} ({key})")
        self.setAccessibleName(rating.label)
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
        self.title = _label(rating.label, "AnswerTitle")
        top.addWidget(self.key, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        layout.addLayout(top)
        self.sub = _label("", "AnswerSub")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.sub)
        layout.addStretch(1)
        for child in (self.key, self.title, self.sub):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_interval(self, text: str) -> None:
        self.sub.setText(text)
        self.sub.setVisible(bool(text))

    def text(self) -> str:  # noqa: D102 - what a reader of the button sees
        interval = self.sub.text() if not self.sub.isHidden() else ""
        return f"{self.rating.label}\n{interval}" if interval else self.rating.label


class StudyPage(QWidget):
    """Today's work, and the review session that clears it."""

    #: The user wants to create or change the study plan.
    manage_plan = Signal()
    #: The setup asked for the Review tab, to sort a list first.
    show_review = Signal()
    #: The setup asked for How LexiTrack works.
    help_requested = Signal()
    #: Something changed that other pages show too (statuses, counts).
    data_changed = Signal()
    #: A short message for the window's toast.
    notify = Signal(str)
    #: A message whose Undo button calls the given function.
    notify_undo = Signal(str, object)
    #: A message with one action: ``(label, callback)``.
    notify_action = Signal(str, object)
    #: The user wants today's words (or the hard ones) as a file.
    export_requested = Signal()

    def __init__(self, engine: LearningService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        #: The session itself — queue, position, reveal, Undo — lives in the
        #: flow; this page only shows it and passes on what the user does.
        self.flow = StudyFlow(engine)
        self._plan: DailyPlan | None = None
        self._primary: str | None = None
        self._setup_pool = 0
        #: Opens a word's history; set by the main window.
        self.history_opener = None
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack)
        self._pages = {
            EMPTY: self._build_empty(),
            DAY: self._build_day(),
            SESSION: self._build_session(),
        }
        for widget in self._pages.values():
            self._stack.addWidget(widget)

    def _build_empty(self) -> QWidget:
        """No plan yet: a three-step setup that is also the introduction.

        Rather than a tour to click through, the first screen asks the three
        questions a plan needs, each with one sentence of why, and the button
        at the bottom starts learning. What is chosen here can be changed any
        time in the Study Plan window and in Settings.
        """
        m = METRICS
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("PanelBody")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(m.space_7, m.space_6, m.space_7, m.space_6)
        PageColumn(page, outer)
        outer.addStretch(1)
        column = QVBoxLayout()
        column.setSpacing(m.space_4)
        holder = QWidget()
        holder.setObjectName("PanelBody")
        # A fixed width, not a maximum: centred by alignment, the column is
        # given its size hint, and wrapped text measured at another width left
        # too little height for the choices below it.
        holder.setFixedWidth(640)
        holder.setLayout(column)
        outer.addWidget(holder, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)
        scroll.setWidget(page)

        column.addWidget(_label("Set up your study plan", "EmptyTitle"))
        column.addWidget(
            _label(
                "LexiTrack teaches the words you mark Unknown, a few new ones a day, "
                "and asks about each again on the day you are most likely to forget it. "
                "Words you already know are never offered.",
                "SetupIntro",
                wrap=True,
            )
        )

        panel, panel_layout = _panel()
        panel_layout.setSpacing(m.space_4)

        # 1. what to learn
        step, body = _setup_step("1", "WHAT TO LEARN")
        self.setup_all = QRadioButton()
        self.setup_all.setChecked(True)
        body.addWidget(self.setup_all)
        self.setup_lists = QRadioButton(
            "Only some of my lists: choose them in the Study Plan window"
        )
        body.addWidget(self.setup_lists)
        # The global radio style pads each option; its size hint here leaves
        # the descenders a pixel short, so the height is given outright.
        for radio in (self.setup_all, self.setup_lists):
            radio.setMinimumHeight(32)
        self.setup_none = _label("", "SetupWarning", wrap=True)
        body.addWidget(self.setup_none)
        self.setup_review = QPushButton("Go to Sort words \u2192")
        self.setup_review.setObjectName("LinkButton")
        self.setup_review.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_review.clicked.connect(self.show_review.emit)
        body.addWidget(self.setup_review, 0, Qt.AlignmentFlag.AlignLeft)
        panel_layout.addWidget(step)

        # 2. how many a day
        step, body = _setup_step("2", "HOW MANY A DAY")
        row = QHBoxLayout()
        row.setSpacing(m.space_3)
        self.setup_per_day = QSpinBox()
        self.setup_per_day.setRange(1, 200)
        self.setup_per_day.setSuffix(" words a day")
        self.setup_per_day.setFixedWidth(160)
        self.setup_per_day.valueChanged.connect(self._show_setup_pace)
        row.addWidget(self.setup_per_day)
        self.setup_pace = _label("", "Faint", wrap=True)
        row.addWidget(self.setup_pace, 1)
        body.addLayout(row)
        panel_layout.addWidget(step)

        # 3. on the phone
        step, body = _setup_step("3", "ON YOUR PHONE  \u00b7  OPTIONAL")
        body.addWidget(
            _label(
                "A Telegram bot can send the day's words each morning and run your "
                "reviews on your phone. Set it up whenever you like in Settings \u2192 "
                "Telegram.",
                "Faint",
                wrap=True,
            )
        )
        panel_layout.addWidget(step)
        column.addWidget(panel)

        actions = QHBoxLayout()
        actions.setSpacing(m.space_4)
        self.setup_start = QPushButton("Start learning")
        self.setup_start.setProperty("variant", "primary")
        self.setup_start.clicked.connect(self._start_from_setup)
        actions.addWidget(self.setup_start)
        how = QPushButton("How LexiTrack works \u2192")
        how.setObjectName("LinkButton")
        how.setCursor(Qt.CursorShape.PointingHandCursor)
        how.clicked.connect(self.help_requested.emit)
        actions.addWidget(how)
        actions.addStretch(1)
        column.addLayout(actions)
        return scroll

    def _show_setup(self) -> None:
        """Fill the setup with this vocabulary's numbers."""
        outlook = self._engine.selection_outlook([], all_lists=True)
        self._setup_pool = outlook.to_introduce
        self.setup_all.setText(
            f"All my Unknown words: {outlook.to_introduce:,}, from every list, "
            f"and any list I add later"
        )
        nothing = outlook.to_introduce == 0
        self.setup_none.setText(
            "You have no Unknown words yet. Sort a list on Sort words first: the "
            "words you mark I Don't Know are the ones LexiTrack teaches."
            if nothing
            else ""
        )
        self.setup_none.setVisible(nothing)
        self.setup_review.setVisible(nothing)
        self.setup_all.setEnabled(not nothing)
        self.setup_per_day.blockSignals(True)
        self.setup_per_day.setValue(max(self._engine.settings.new_words_per_day, 1))
        self.setup_per_day.blockSignals(False)
        self._show_setup_pace()

    def _show_setup_pace(self) -> None:
        per_day = self.setup_per_day.value()
        pool = self._setup_pool
        if not pool:
            self.setup_pace.setText("You can change this any time in Settings.")
            return
        days = -(-pool // per_day)
        self.setup_pace.setText(
            f"About {days:,} days for all of them. You can change this any time in Settings."
        )

    def _start_from_setup(self) -> None:
        if self.setup_lists.isChecked() or not self._setup_pool:
            self.manage_plan.emit()
            return
        from ..models.settings import Setting

        self._engine.save_settings({Setting.NEW_WORDS_PER_DAY: self.setup_per_day.value()})
        self._engine.create_plan("My Unknown words", list_ids=[], all_lists=True)
        self.notify.emit(
            f"Your study plan is ready: {self.setup_per_day.value()} new words a day."
        )
        self.data_changed.emit()
        self.refresh()

    def _build_day(self) -> QWidget:
        m = METRICS
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("PanelBody")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(m.space_7, m.space_5, m.space_7, m.space_6)
        PageColumn(page, layout)
        layout.setSpacing(m.space_5)

        header = QHBoxLayout()
        header.setSpacing(m.space_3)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("Today", "PageTitle"))
        self.plan_label = _label("", "PageSubtitle")
        titles.addWidget(self.plan_label)
        header.addLayout(titles, 1)
        self.plan_button = QPushButton("Study plan")
        self.plan_button.setProperty("variant", "ghost")
        self.plan_button.setToolTip("Choose which lists you are working through (Ctrl+P)")
        self.plan_button.clicked.connect(self.manage_plan.emit)
        header.addWidget(self.plan_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        layout.addSpacing(-m.space_2)

        layout.addWidget(self._build_today())
        self.words_section = self._build_words()
        layout.addWidget(self.words_section)
        self.week_section = self._build_week()
        layout.addWidget(self.week_section)
        self.hard_section = self._build_hard()
        layout.addWidget(self.hard_section)
        self.stats_section = self._build_stats()
        layout.addWidget(self.stats_section)
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _build_today(self) -> QWidget:
        """The decision panel: the day's two steps and one button."""
        m = METRICS
        panel, layout = _panel()
        layout.setSpacing(m.space_2)
        layout.addWidget(_label("TODAY", "SectionTitle"))
        row = QHBoxLayout()
        row.setSpacing(m.space_5)
        steps = QVBoxLayout()
        steps.setSpacing(m.space_2)
        self.step_new = _Step()
        self.step_review = _Step()
        steps.addWidget(self.step_new)
        steps.addWidget(self.step_review)
        steps.addSpacing(m.space_1)
        self.day_progress = DayProgress()
        steps.addWidget(self.day_progress)
        row.addLayout(steps, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(m.space_2)
        self.primary_button = QPushButton()
        self.primary_button.setProperty("variant", "primary")
        self.primary_button.setMinimumWidth(210)
        self.primary_button.setMinimumHeight(40)
        self.primary_button.clicked.connect(self._primary_action)
        buttons.addWidget(self.primary_button)
        self.secondary_button = QPushButton()
        self.secondary_button.setProperty("variant", "ghost")
        self.secondary_button.clicked.connect(self.start_session)
        buttons.addWidget(self.secondary_button)
        row.addLayout(buttons)
        layout.addLayout(row)

        self.intake_note = _label("", "WarningText", wrap=True)
        layout.addWidget(self.intake_note)
        self.pool_label = _label("", "Faint")
        layout.addWidget(self.pool_label)
        return panel

    def _build_words(self) -> _Section:
        section = _Section("NEW WORDS")
        self.copy_button = QPushButton("Copy")
        self.export_button = QPushButton("Export")
        for button, tip in (
            (self.copy_button, "Copy the words and their meanings, one per line"),
            (self.export_button, "Save the words as PDF, CSV or JSON (Ctrl+E)"),
        ):
            button.setProperty("variant", "ghost")
            button.setProperty("size", "small")
            button.setToolTip(tip)
            section.header.addWidget(button)
        self.copy_button.clicked.connect(self._copy_words)
        self.export_button.clicked.connect(self.export_requested.emit)
        self._level_rows = QVBoxLayout()
        self._level_rows.setSpacing(METRICS.space_2)
        section.body.addLayout(self._level_rows)
        return section

    def _build_week(self) -> _Section:
        section = _Section("THIS WEEK")
        self.week = WeekStrip()
        section.body.addWidget(self.week)
        self.week_note = _label("", "Faint", wrap=True)
        section.body.addWidget(self.week_note)
        return section

    def _build_hard(self) -> _Section:
        """Words the engine has flagged, by name, with how often they slipped."""
        section = _Section("WORDS YOU FIND HARD")
        self.hard_chips = ChipFlow()
        section.body.addWidget(self.hard_chips)
        section.body.addWidget(
            _label(
                "They come first in every session until they stick. Using one in a "
                "sentence of your own helps more than another review.",
                "Faint",
                wrap=True,
            )
        )
        return section

    def _build_stats(self) -> _Section:
        section = _Section("THE LAST 30 DAYS")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space_3)
        self.stat_reviews = StatTile("reviews")
        self.stat_again = StatTile("answered Again")
        self.stat_introduced = StatTile("new words learned")
        self.stat_long_term = StatTile("in long-term memory", tone="known")
        for tile in (
            self.stat_reviews,
            self.stat_again,
            self.stat_introduced,
            self.stat_long_term,
        ):
            row.addWidget(tile, 1)
        section.body.addLayout(row)
        return section

    def _build_session(self) -> QWidget:
        m = METRICS
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_5)
        outer.setSpacing(m.space_4)

        bar = QHBoxLayout()
        bar.setSpacing(m.space_3)
        context = QVBoxLayout()
        context.setSpacing(0)
        context.addWidget(_label("REVIEWING", "ContextLabel"))
        self.session_title = _label("", "ContextName")
        context.addWidget(self.session_title)
        bar.addLayout(context)
        bar.addStretch(1)
        end_button = QPushButton("End session")
        end_button.setProperty("variant", "ghost")
        end_button.setToolTip("Stop here and keep what you have answered (Esc)")
        end_button.clicked.connect(self.end_session)
        bar.addWidget(end_button, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(bar)

        card = QFrame()
        card.setObjectName("SessionCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setFixedWidth(600)
        card_outer = QVBoxLayout(card)
        card_outer.setContentsMargins(0, 0, 0, 0)
        card_outer.setSpacing(0)

        body = QVBoxLayout()
        body.setContentsMargins(m.space_6, m.space_5, m.space_6, m.space_4)
        body.setSpacing(m.space_3)
        self.session_line = _SessionProgress()
        body.addWidget(self.session_line)
        top = QHBoxLayout()
        self.session_flag = chip("Hard for you", tone="hard")
        self.session_flag.setToolTip("You have missed this word several times")
        top.addWidget(self.session_flag)
        top.addStretch(1)
        body.addLayout(top)
        body.addSpacing(m.space_3)

        self.word_label = _label("", "WordLabel", wrap=True)
        self.word_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.word_label)
        self.meta_label = _label("", "MetaLabel")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.meta_label)

        meaning = QVBoxLayout()
        meaning.setSpacing(m.space_1)
        self.reveal_button = QPushButton("Show meaning   Space")
        self.reveal_button.setProperty("variant", "ghost")
        self.reveal_button.setToolTip("Space")
        self.reveal_button.clicked.connect(self._reveal)
        meaning.addWidget(self.reveal_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.definition_label = _label("", "DefinitionLabel", wrap=True)
        self.definition_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meaning.addWidget(self.definition_label)
        self.note_label = _label("", "SenseLabel", wrap=True)
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meaning.addWidget(self.note_label)
        holder = QWidget()
        holder.setObjectName("PanelBody")
        holder.setLayout(meaning)
        # Room for a two-line meaning is reserved, so revealing it does not
        # push the answer buttons down under the pointer.
        holder.setMinimumHeight(64)
        body.addWidget(holder)
        body.addSpacing(m.space_2)

        answers = QHBoxLayout()
        answers.setSpacing(m.space_2)
        self.answer_buttons: dict[Rating, _AnswerButton] = {}
        for rating in Rating:
            key, variant = _ANSWER_STYLE[rating]
            button = _AnswerButton(rating, key, variant)
            button.clicked.connect(lambda _checked=False, r=rating: self._answer(r))
            answers.addWidget(button, 1)
            self.answer_buttons[rating] = button
        body.addLayout(answers)

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
        self.undo_button.clicked.connect(self.undo_last)
        self.undo_button.hide()
        footer.addWidget(self.session_progress)
        footer.addWidget(self.undo_button)
        footer.addStretch(1)
        footer.addWidget(self.answer_hint)
        footer.addStretch(1)
        footer.addWidget(escape_hint)
        body.addLayout(footer)
        card_outer.addLayout(body)

        outer.addStretch(1)
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)
        return page

    # -- the day -----------------------------------------------------------

    def refresh(self) -> None:
        """Re-read everything from the service. Called whenever shown."""
        if self.flow.active:
            return
        self._engine.refresh_settings()
        plan = self._engine.daily_plan()
        self._plan = plan
        if not plan.has_plan:
            self._show_setup()
            self._stack.setCurrentWidget(self._pages[EMPTY])
            return
        self._show_day(plan)

    def _show_day(self, plan: DailyPlan) -> None:
        self._stack.setCurrentWidget(self._pages[DAY])
        assert plan.plan is not None
        # The plan is usually named after the list it draws from, and printing
        # both then reads as a stutter ("Oxford 3000 · Oxford 3000").
        parts = [plan.plan.name]
        lists = plan.plan.list_label
        if lists and lists.casefold() != plan.plan.name.casefold():
            parts.append(lists)
        elif not lists:
            parts.append("no lists selected")
        parts.append(_pretty_date(plan.local_date))
        self.plan_label.setText(" · ".join(parts))

        self._show_today(plan)
        self._show_words(plan)
        self._show_week(plan)
        self._show_hard_words()
        self._show_stats()

    def _show_today(self, plan: DailyPlan) -> None:
        new_count = len(plan.new_words)
        learned = len(plan.introduced_today)
        due = plan.due_count
        done = plan.reviews_done_today

        # Step 1: the new words.
        if new_count:
            self.step_new.set_step(
                f"Learn {new_count} new {'word' if new_count == 1 else 'words'}",
                f"{learned} already confirmed" if learned else "study them, then confirm",
                "active",
            )
        elif learned:
            self.step_new.set_step(f"Learned {learned} new words", "done", "done")
        else:
            self.step_new.set_step("No new words today", _no_words_reason(plan), "waiting")

        # Step 2: the reviews.
        if due:
            meta = f"{done} answered" if done else "from earlier days"
            self.step_review.set_step(
                f"Review {due} {'word' if due == 1 else 'words'}",
                meta,
                "waiting" if new_count else "active",
            )
        elif done:
            self.step_review.set_step(f"Reviewed {done} words", "done", "done")
        else:
            self.step_review.set_step(
                "Nothing to review today", "new words come back tomorrow", "waiting"
            )

        # The day's progress: learning and reviewing as shares of all of it.
        total = new_count + learned + due + done
        self.day_progress.set_parts(
            learned / total if total else 0.0, done / total if total else 0.0
        )
        self.day_progress.setVisible(bool(total))

        # One primary button, labelled with the next thing to do.
        self.secondary_button.setVisible(False)
        if new_count:
            self._primary = "introduce"
            self.primary_button.setText(
                f"I studied these {new_count}" if new_count > 1 else "I studied this word"
            )
            self.primary_button.setToolTip(
                "Confirm you have learned the words below. They are reviewed from tomorrow."
            )
            self.primary_button.setEnabled(True)
            if due:
                self.secondary_button.setText(f"Review {due} first")
                self.secondary_button.setVisible(True)
        elif due:
            self._primary = "review"
            self.primary_button.setText(
                "Continue session →" if done else "Start session →"
            )
            self.primary_button.setToolTip(f"{due} words are waiting (1–4 to answer)")
            self.primary_button.setEnabled(True)
        else:
            self._primary = None
            self.primary_button.setText("All done for today ✓")
            self.primary_button.setToolTip("Nothing is waiting. See you tomorrow.")
            self.primary_button.setEnabled(False)

        # A finished day is already stated by the steps; only the workload
        # valve gets its own line, because it is a reason, not a result.
        show_note = bool(plan.intake_note) and (plan.intake_paused or not learned)
        self.intake_note.setText(plan.intake_note or "")
        self.intake_note.setVisible(show_note)
        # Only the remaining pool is news; an empty pool is already the reason
        # on the first step, and saying it twice reads as a warning.
        self.pool_label.setText(
            f"{plan.pool_remaining:,} words in the plan are still to come."
        )
        self.pool_label.setVisible(bool(plan.pool_remaining))

    def _primary_action(self) -> None:
        if self._primary == "introduce":
            self._introduce()
        elif self._primary == "review":
            self.start_session()

    def _show_words(self, plan: DailyPlan) -> None:
        """Today's words as chips, one row per CEFR level.

        Before confirming these are the words to study; afterwards the same
        chips stay, greyed, as the words learned today — the list you just
        worked through should not vanish the moment you finish it.
        """
        pending = list(plan.new_words)
        words = pending or list(plan.introduced_today)
        self.words_section.setVisible(bool(words))
        if not words:
            return
        title = "NEW WORDS" if pending else "LEARNED TODAY"
        self.words_section.set_title(f"{title} · {len(words)}")
        self.copy_button.setVisible(True)
        self.export_button.setVisible(bool(pending))

        while self._level_rows.count():
            item = self._level_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        groups: OrderedDict[str, list] = OrderedDict()
        for word in words:
            groups.setdefault(word.cefr_level or "–", []).append(word)
        order = {level: index for index, level in enumerate(CEFR_ORDER)}
        for level in sorted(groups, key=lambda value: order.get(value, len(order))):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(METRICS.space_3)
            level_label = _label(level, "LevelLabel")
            level_label.setFixedWidth(26)
            row_layout.addWidget(level_label, 0, Qt.AlignmentFlag.AlignTop)
            flow = ChipFlow()
            flow.set_chips(
                chip(
                    word.word,
                    None if pending else "done",
                    _meaning(word),
                    # Learned today has a history to show; a word still to
                    # be confirmed has none yet.
                    on_click=None if pending else self._history_for(word.id),
                )
                for word in groups[level]
            )
            row_layout.addWidget(flow, 1)
            self._level_rows.addWidget(row)

    def _history_for(self, word_id: int):
        if self.history_opener is None:
            return None
        return lambda: self.history_opener(word_id)

    def word_chips(self) -> list[str]:
        """The words shown as chips, in order. For tests and accessibility."""
        texts: list[str] = []
        for index in range(self._level_rows.count()):
            row = self._level_rows.itemAt(index).widget()
            if row is None:
                continue
            flow = row.findChild(ChipFlow)
            if flow is not None:
                texts.extend(flow.texts())
        return texts

    def _show_week(self, plan: DailyPlan) -> None:
        # Before anything has been introduced the week is seven zeros, which
        # is a lot of screen for no information.
        scheduled = sum(count for _, count in plan.forecast)
        self.week_section.setVisible(bool(scheduled))
        if not scheduled:
            return
        capacity = plan.review_capacity
        self.week.set_forecast(plan.forecast, capacity)
        busiest = max((count for _, count in plan.forecast[1:]), default=0)
        if capacity and busiest > capacity:
            self.week_note.setText(
                f"The busiest day has {busiest} reviews, over your limit of {capacity}. "
                "New words pause until it clears."
            )
        else:
            self.week_note.setText(f"{scheduled} reviews over the next 7 days.")

    def _show_hard_words(self) -> None:
        items = self._engine.struggling_words(limit=16)
        self.hard_section.setVisible(bool(items))
        if not items:
            return
        self.hard_chips.set_chips(
            chip(
                f"{item.word.word}  ×{item.card.lapse_count}",
                "hard",
                f"Missed {item.card.lapse_count} "
                f"{'time' if item.card.lapse_count == 1 else 'times'}"
                + (f"\n{_meaning(item.word)}" if _meaning(item.word) else "")
                + "\nClick for its history.",
                on_click=self._history_for(item.word.id),
            )
            for item in items
        )

    def _show_stats(self) -> None:
        """Four numbers for the month. Hidden until there is a month to show."""
        since = self._engine.clock.shift_days(-29)
        ratings = self._engine.rating_counts(since)
        answered = sum(ratings.values())
        introduced = sum(
            count for day, count in self._engine.introduced_per_day(30).items() if day >= since
        )
        self.stats_section.setVisible(bool(answered or introduced))
        again = ratings.get(int(Rating.AGAIN), 0)
        self.stat_reviews.set_value(answered)
        self.stat_again.value_label.setText(
            f"{round(100 * again / answered)}%" if answered else "–"
        )
        self.stat_introduced.set_value(introduced)
        self.stat_long_term.set_value(self._engine.state_counts().get("review", 0))

    def today_words(self) -> list:
        """The words on screen, for the window's export."""
        plan = self._plan or self._engine.daily_plan()
        return list(plan.new_words or plan.introduced_today)

    def copy_text(self) -> str:
        """Today's words and their meanings, one per line, as copied."""
        lines = []
        for word in self.today_words():
            meaning = _meaning(word)
            lines.append(f"{word.word} — {meaning}" if meaning else word.word)
        return "\n".join(lines)

    def _copy_words(self) -> None:
        text = self.copy_text()
        QApplication.clipboard().setText(text)
        count = len(text.splitlines())
        self.notify.emit(f"Copied {count} words with their meanings.")

    def _introduce(self) -> None:
        result = self._engine.introduce()
        if result.count:
            word = "word" if result.count == 1 else "words"
            self.notify.emit(
                f"{result.count} new {word} added — first review "
                f"{result.first_due_on or 'tomorrow'}."
            )
            self.data_changed.emit()
        self.refresh()

    # -- the session -------------------------------------------------------

    def start_session(self) -> None:
        if not self.flow.start():
            self.refresh()
            return
        plan = self._engine.active_plan()
        self.session_title.setText(plan.name if plan else "Today")
        self._stack.setCurrentWidget(self._pages[SESSION])
        self._show_card()
        self.setFocus()

    def end_session(self) -> None:
        summary = self.flow.finish()
        if summary is not None and summary.answered:
            word = "word" if summary.answered == 1 else "words"
            text = f"{summary.answered} {word} reviewed."
            if summary.can_undo:
                # The last card of a session is where a slip is most
                # likely and least visible: the page has moved on.
                self.notify_undo.emit(text, self.undo_last)
            else:
                self.notify.emit(text)
            self.data_changed.emit()
        self.refresh()

    @property
    def in_session(self) -> bool:
        return self.flow.active

    def _show_card(self) -> None:
        item = self.flow.current
        if item is None:
            self.end_session()
            return
        word = item.word
        total = self.flow.total
        self.session_progress.setText(f"{self.flow.position + 1} / {total}")
        self.session_line.set_share(self.flow.position / total if total else 0)
        self.session_flag.setVisible(item.is_struggling)

        self.word_label.setText(word.word)
        meta = " · ".join(part for part in (word.part_of_speech, word.cefr_level) if part)
        self.meta_label.setText(meta)
        self.meta_label.setVisible(bool(meta))

        self._apply_reveal(word.definition, word.note)
        self._show_intervals(self.flow.intervals())
        self._show_undo()

    def _show_undo(self) -> None:
        possible = self.flow.can_undo()
        if possible:
            word, rating = self.flow.last_answer
            self.undo_button.setText(f"\u21b6 Undo {rating.label} on \u201c{word}\u201d")
        self.undo_button.setVisible(possible)

    def undo_last(self) -> None:
        """Take back the last answer: in a session, show that card again."""
        word = self.flow.undo()
        if word is None:
            return
        self.notify.emit(f"Took back your answer to \u201c{word.word}\u201d.")
        self.data_changed.emit()
        if self.in_session:
            self._show_card()
        else:
            self.refresh()

    def _show_intervals(self, preview: dict[Rating, int]) -> None:
        """Label the answers with when the word would come back.

        Early on, every answer lands tomorrow — the learning step is a day, and
        a card with almost no stability cannot be pushed further out. Saying
        "tomorrow" four times looks like a bug, so then the buttons carry only
        their names and the card's footer says it once. Compared as the words
        the user reads, not as raw days: 0 and 1 are the same sentence.
        """
        labels = {rating: _interval(preview.get(rating)) for rating in Rating}
        uniform = len(set(labels.values())) <= 1
        for rating, button in self.answer_buttons.items():
            button.set_interval("" if uniform else labels[rating])
        self.answer_hint.setText(
            f"Every answer brings it back {labels[Rating.GOOD]}" if uniform else ""
        )
        self.answer_hint.setVisible(uniform)

    def _apply_reveal(self, definition: str | None, note: str | None) -> None:
        shown = self.flow.revealed
        self.reveal_button.setVisible(not shown)
        self.definition_label.setVisible(shown)
        self.note_label.setVisible(shown and bool(note))
        if shown:
            self.definition_label.setText(definition or "No definition stored for this word.")
            self.note_label.setText(note or "")

    def _reveal(self) -> None:
        if self.flow.reveal():
            item = self.flow.current
            self._apply_reveal(item.word.definition, item.word.note)

    def _answer(self, rating: Rating) -> None:
        if self.flow.current is None:
            return
        result = self.flow.answer(rating)
        outcome = result.outcome
        if outcome is not None and not outcome.duplicate and outcome.suggest_known:
            self._suggest_known(outcome.word)
        if result.finished:
            self.end_session()
            return
        self._show_card()

    def _suggest_known(self, word) -> None:
        """The word reached long-term memory: offer, never decide, Known."""
        def confirm() -> None:
            if self._engine.confirm_known([word.id]):
                self.notify.emit(f"“{word.word}” is marked Known.")
                self.data_changed.emit()

        self.notify_action.emit(
            f"“{word.word}” is in long-term memory.", ("Mark Known", confirm)
        )

    # -- keyboard ----------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Numbers answer, Space reveals, Escape leaves — during a session only."""
        if not self.in_session:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.end_session()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo_last()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.flow.revealed:
                self._answer(Rating.GOOD)
            else:
                self._reveal()
            return
        ratings = {
            Qt.Key.Key_1: Rating.AGAIN,
            Qt.Key.Key_2: Rating.HARD,
            Qt.Key.Key_3: Rating.GOOD,
            Qt.Key.Key_4: Rating.EASY,
        }
        if key in ratings:
            self._answer(ratings[key])
            return
        super().keyPressEvent(event)


def _meaning(word) -> str:
    meaning = word.definition or ""
    if word.note:
        meaning = f"{meaning} ({word.note})".strip()
    return meaning


def _no_words_reason(plan: DailyPlan) -> str:
    if plan.intake_paused:
        return "paused while reviews catch up"
    if not plan.pool_remaining:
        return "every word in the plan is introduced"
    if not plan.new_target:
        return "set to 0 a day in Settings"
    return ""


def _pretty_date(local_date: str) -> str:
    """``2026-09-17`` as ``Thursday 17 September``, so the day is unambiguous."""
    try:
        day = date.fromisoformat(local_date)
    except ValueError:
        return local_date
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"


def _interval(days: int | None) -> str:
    """The interval under an answer button, in the shortest honest words."""
    if days is None:
        return "–"
    if days <= 1:
        return "tomorrow"
    if days < 30:
        return f"{days} days"
    if days < 365:
        return f"{round(days / 30)} months"
    years = days / 365
    return "1 year" if round(years) == 1 else f"{years:.1f} years"
