"""Study: the one screen that answers "what do I do today?".

The page has three faces and shows exactly one of them:

1. **No plan yet** — one explanation and one button.
2. **The day** — built the way Home is built, so the two read as one app:
   a single *Today* panel at the top that makes the decision for you (the
   day's two steps, one progress line, one button whose label is the next
   thing to do), then sections whose titles sit outside their cards — the
   new words as chips grouped by level, the week as seven day tiles, the
   words you find hard, and the last thirty days as stat tiles.
3. **A review session** — one card (``components/review_card.py``) that
   asks what :class:`~lexitrack.services.review_flow.ReviewFlow` says is
   next: type the word from its meaning or a context, choose it among four,
   see it taught again — or, for a word with nothing to ask from, the V1
   card with four answers.

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
from PySide6.QtGui import QKeyEvent, QKeySequence
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
from ..repositories import ContentRepository
from ..services.first_learning import choose_depth, estimate
from ..services.learning_service import DailyPlan, LearningService
from ..services.review_flow import Feedback, ReviewFlow, StepKind
from .components.chips import ChipFlow, DayProgress, WeekStrip, chip
from .components.review_card import ReviewCard, interval_text
from .theme.palette import METRICS
from .widgets import PageColumn

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
        self.flow = ReviewFlow(engine)
        self._content = ContentRepository(engine.database)
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
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _build_today(self) -> QWidget:
        """The decision card: the day's work in one line, and one button."""
        m = METRICS
        panel, layout = _panel()
        layout.setSpacing(m.space_2)
        row = QHBoxLayout()
        row.setSpacing(m.space_5)
        text = QVBoxLayout()
        text.setSpacing(m.space_1)
        self.headline = _label("", "TodayHeadline")
        text.addWidget(self.headline)
        self.detail = _label("", "TodayDetail", wrap=True)
        text.addWidget(self.detail)
        text.addSpacing(m.space_1)
        self.day_progress = DayProgress()
        text.addWidget(self.day_progress)
        row.addLayout(text, 1)
        self.primary_button = QPushButton()
        self.primary_button.setProperty("variant", "primary")
        self.primary_button.setMinimumWidth(190)
        self.primary_button.setMinimumHeight(40)
        self.primary_button.clicked.connect(self._primary_action)
        row.addWidget(self.primary_button, 0, Qt.AlignmentFlag.AlignVCenter)
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
        self.studied_button = QPushButton("Mark as studied")
        for button, tip in (
            (self.copy_button, "Copy the words and their meanings, one per line"),
            (self.export_button, "Save the words as PDF, CSV or JSON (Ctrl+E)"),
            (
                self.studied_button,
                "Studied them another way? Skip the practice: they are reviewed from "
                "tomorrow.",
            ),
        ):
            button.setProperty("variant", "ghost")
            button.setProperty("size", "small")
            button.setToolTip(tip)
            section.header.addWidget(button)
        self.copy_button.clicked.connect(self._copy_words)
        self.export_button.clicked.connect(self.export_requested.emit)
        self.studied_button.clicked.connect(self._introduce)
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
        context.addWidget(_label("TODAY'S SESSION", "ContextLabel"))
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

        card = ReviewCard()
        self.card = card
        card.submitted.connect(self._on_submitted)
        card.chosen.connect(self._on_chosen)
        card.graded.connect(self._on_graded)
        card.rated.connect(self._answer)
        card.reveal_requested.connect(self._reveal)
        card.continue_requested.connect(self._continue)
        card.undo_requested.connect(self.undo_last)
        # The card's parts, by the names the page has always had.
        self.session_line = card.session_line
        self.session_flag = card.session_flag
        self.word_label = card.word_label
        self.meta_label = card.meta_label
        self.reveal_button = card.reveal_button
        self.definition_label = card.definition_label
        self.note_label = card.note_label
        self.answer_buttons = card.answer_buttons
        self.answer_hint = card.answer_hint
        self.undo_button = card.undo_button
        self.session_progress = card.session_progress

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

    def _show_today(self, plan: DailyPlan) -> None:
        new_count = len(plan.new_words)
        learned = len(plan.introduced_today)
        due = plan.due_count
        done = plan.reviews_done_today
        language = self._engine.settings.learner_language
        depths = [
            choose_depth(self._content.teaching(word.id, language)).depth
            for word in plan.new_words
        ]
        day = estimate(due, depths)
        hard = sum(1 for item in self._engine.review_queue() if item.is_struggling)

        if day.words:
            noun = "word" if day.words == 1 else "words"
            self.headline.setText(f"{day.words} {noun} · about {day.minutes} min")
            parts = []
            if due:
                parts.append(f"{due} {'review' if due == 1 else 'reviews'}")
            if new_count:
                parts.append(f"{new_count} new")
            detail = ", ".join(parts)
            if hard:
                detail += f" · {hard} hard for you"
            if done or learned:
                detail += f" · done so far: {done} reviewed, {learned} learned"
            self.detail.setText(detail)
        else:
            self.headline.setText("All done for today")
            self.detail.setText(
                f"{done} reviewed and {learned} new learned today."
                if done or learned
                else "Nothing is waiting: " + (_no_words_reason(plan) or "see you tomorrow.")
            )

        # The day's progress: learning and reviewing as shares of all of it.
        total = new_count + learned + due + done
        self.day_progress.set_parts(
            learned / total if total else 0.0, done / total if total else 0.0
        )
        self.day_progress.setVisible(bool(total))

        if day.words:
            self._primary = "session"
            started = done or learned
            self.primary_button.setText("Continue session →" if started else "Start session →")
            self.primary_button.setToolTip(
                "Reviews first, then the new words: each taught, then asked."
            )
            self.primary_button.setEnabled(True)
        else:
            self._primary = None
            self.primary_button.setText("All done ✓")
            self.primary_button.setToolTip("Nothing is waiting. See you tomorrow.")
            self.primary_button.setEnabled(False)

        # Only the workload valve gets its own line: it is a reason, not a result.
        show_note = bool(plan.intake_note) and (plan.intake_paused or not learned)
        self.intake_note.setText(plan.intake_note or "")
        self.intake_note.setVisible(show_note)
        self.pool_label.setText(
            f"{plan.pool_remaining:,} words in the plan are still to come."
        )
        self.pool_label.setVisible(bool(plan.pool_remaining))

    def _primary_action(self) -> None:
        if self._primary == "session":
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
        self.studied_button.setVisible(bool(pending))

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

    def end_session(self) -> None:
        summary = self.flow.finish()
        if summary is not None and (summary.answered or summary.learned):
            parts = []
            if summary.answered:
                parts.append(
                    f"{summary.answered} {'word' if summary.answered == 1 else 'words'} reviewed"
                )
            if summary.learned:
                parts.append(
                    f"{summary.learned} new {'word' if summary.learned == 1 else 'words'} "
                    "learned, first review tomorrow"
                )
            text = "; ".join(parts) + "."
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
        step = self.flow.current
        if step is None:
            self.end_session()
            return
        self.card.show_step(step, revealed=self.flow.revealed, intervals=self.flow.intervals())
        self.card.set_progress(self.flow.position, self.flow.total)
        self._show_undo()
        if step.kind not in (StepKind.TYPE, StepKind.WRITE):
            self.setFocus()

    def _show_undo(self) -> None:
        possible = self.flow.can_undo()
        text = None
        if possible:
            word, rating = self.flow.last_answer
            text = f"\u21b6 Undo {rating.label} on \u201c{word}\u201d"
        self.card.set_undo(text)

    def undo_last(self) -> None:
        """Take back the last answer: in a session, show that word again."""
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
        self.card.show_intervals(preview)

    def _reveal(self) -> None:
        if self.flow.reveal():
            self.card.show_meaning(True)

    def _answer(self, rating: Rating) -> None:
        """A V1 answer to a word with nothing to ask from: no pause after it."""
        step = self.flow.current
        if step is None or step.kind is not StepKind.RECALL:
            return
        feedback = self.flow.rate(rating)
        self._after_rating(feedback)
        if feedback.finished:
            self.end_session()
            return
        self._show_card()

    def _on_submitted(self, text: str, response_ms: int, hinted: bool) -> None:
        self._show_feedback(self.flow.submit(text, response_ms, hinted))

    def _on_chosen(self, index: int, response_ms: int) -> None:
        self._show_feedback(self.flow.choose(index, response_ms))

    def _on_graded(self, used_well: bool, effortful: bool) -> None:
        self._show_feedback(self.flow.grade(used_well, effortful))

    def _show_feedback(self, feedback: Feedback) -> None:
        self._after_rating(feedback)
        self.card.show_feedback(feedback)
        self.card.set_progress(self.flow.position, self.flow.total)
        self._show_undo()

    def _after_rating(self, feedback: Feedback) -> None:
        outcome = feedback.outcome
        if outcome is not None and not outcome.duplicate and outcome.suggest_known:
            self._suggest_known(outcome.word)

    def _continue(self) -> None:
        """Enter after feedback or a teaching page: on to the next step."""
        if not self.card.waiting:
            return
        step = self.flow.current
        if step is not None and step.kind is StepKind.TEACH and self.card.step is step:
            self.flow.proceed()
        if self.flow.current is None:
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
        """The session's keys: they depend on what the card is asking."""
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
        enter = key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if self.card.waiting and enter:
            self._continue()
            return
        step = self.flow.current
        number = _NUMBER_KEYS.get(key)
        if step is not None and step.kind is StepKind.RECALL:
            if enter:
                if self.flow.revealed:
                    self._answer(Rating.GOOD)
                else:
                    self._reveal()
                return
            if number is not None:
                self._answer(Rating(number + 1))
                return
        if step is not None and number is not None and not self.card.waiting:
            if step.kind is StepKind.CHOOSE:
                self.card.choose(number)
                return
            if step.kind is StepKind.WRITE:
                self.card.grade(number)
                return
        super().keyPressEvent(event)


#: Keys 1–4, as indexes 0–3.
_NUMBER_KEYS = {
    Qt.Key.Key_1: 0,
    Qt.Key.Key_2: 1,
    Qt.Key.Key_3: 2,
    Qt.Key.Key_4: 3,
}


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


#: The interval wording, shared with the card.
_interval = interval_text
