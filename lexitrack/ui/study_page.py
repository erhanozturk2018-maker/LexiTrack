"""Study: the one screen that answers "what do I do today?".

The page has three faces and shows exactly one of them:

1. **No plan yet** — one explanation and one button.
2. **The day** — built the way Home is built, so the two read as one app:
   the day's two sessions side by side — **Reviews** and **New words**, each
   with what is left and its own button — then sections whose titles sit
   outside their cards: the new words as chips grouped by level, the week as
   seven day tiles, the words you find hard.
3. **A session** — one card (``components/review_card.py``) that shows what
   :class:`~lexitrack.services.review_flow.ReviewFlow` says is next: a new
   word to read, or a question — Definition → Word or Context → Definition —
   with four options, then Again / Hard / Good / Easy after a right answer.

Two decisions worth knowing (DECISIONS §87):

* **Reviews and new words are two sessions with a button each.** A review
  session never shows a new word; a new word counts as learned only once it
  is shown and practised. The day's order — reviews, then new words — is
  kept by which button is the primary one, not by joining them.
* **The rating buttons say when the word comes back**, and when all four say
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

from ..models.srs import Channel, Rating
from ..models.word_entry import CEFR_ORDER
from ..services.first_learning import estimate
from ..services.learning_service import DailyPlan, LearningService
from ..services.review_flow import Feedback, ReviewFlow, SessionKind, StepKind
from ..services.review_wording import interval_text
from .components.chips import ChipFlow, WeekStrip, chip
from .components.review_card import ReviewCard
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


class _TaskCard(QFrame):
    """One of the day's two sessions: what it has left, and its button."""

    def __init__(self, title: str, action) -> None:
        super().__init__()
        m = METRICS
        self.setObjectName("Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
        layout.setSpacing(m.space_1)
        layout.addWidget(_label(title, "SectionTitle"))
        self.headline = _label("", "TodayHeadline")
        layout.addWidget(self.headline)
        self.detail = _label("", "TodayDetail", wrap=True)
        layout.addWidget(self.detail)
        layout.addStretch(1)
        layout.addSpacing(m.space_2)
        self.button = QPushButton()
        self.button.setMinimumHeight(38)
        self.button.clicked.connect(action)
        layout.addWidget(self.button)

    def show_work(self, headline: str, detail: str, button: str) -> None:
        self.headline.setText(headline)
        self.detail.setText(detail)
        self.button.setText(button)
        self.button.setEnabled(True)

    def show_done(self, detail: str) -> None:
        """The day's work of this kind is finished."""
        self.headline.setText("Done ✓")
        self.detail.setText(detail)
        self.button.setText("Done ✓")
        self.button.setEnabled(False)

    def show_nothing(self, headline: str, detail: str) -> None:
        """There was none of this kind today: nothing to call done."""
        self.headline.setText(headline)
        self.detail.setText(detail)
        self.button.setText("Nothing today")
        self.button.setEnabled(False)

    def set_primary(self, primary: bool) -> None:
        """The next thing to do has the one primary button of the page."""
        self.button.setProperty("variant", "primary" if primary else "")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)


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
        self._plan: DailyPlan | None = None
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
        """The day's two sessions side by side, reviews first: what each has
        left and a button for each. The next thing to do has the primary one."""
        m = METRICS
        holder = QWidget()
        holder.setObjectName("PanelBody")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(m.space_2)
        row = QHBoxLayout()
        row.setSpacing(m.space_4)
        self.review_task = _TaskCard("REVIEWS", self.start_review)
        self.learn_task = _TaskCard("NEW WORDS", self.start_learning)
        row.addWidget(self.review_task, 1)
        row.addWidget(self.learn_task, 1)
        layout.addLayout(row)
        self.intake_note = _label("", "WarningText", wrap=True)
        layout.addWidget(self.intake_note)
        self.pool_label = _label("", "Faint")
        layout.addWidget(self.pool_label)
        return holder

    def _build_words(self) -> _Section:
        section = _Section("NEW WORDS")
        self.copy_button = QPushButton("Copy")
        self.export_button = QPushButton("Export")
        for button, tip in (
            (self.copy_button, "Copy the words and their meanings, one per line"),
            (self.export_button, "Save the words as PDF, CSV or JSON (Ctrl+E)"),
        ):
            button.setProperty("variant", "ghost")
            # Text-sized, like the section title beside them.
            button.setProperty("compact", True)
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
        self.session_kind_label = _label("REVIEWS", "ContextLabel")
        context.addWidget(self.session_kind_label)
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
        card.chosen.connect(self._on_chosen)
        card.rated.connect(self._on_rated)
        card.continue_requested.connect(self._continue)
        card.undo_requested.connect(self.undo_last)
        # The card's parts, by the names the page has always had.
        self.session_line = card.session_line
        self.session_flag = card.session_flag
        self.word_label = card.word_label
        self.meta_label = card.meta_label
        self.answer_buttons = card.rating_buttons
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
        hard = sum(1 for item in self._engine.review_queue() if item.is_struggling)

        # Reviews: what is due, or that the day's are done.
        if due:
            minutes = estimate(due, 0).minutes
            detail = f"about {minutes} min"
            if hard:
                detail += f" · {hard} hard for you"
            if done:
                detail += f" · {done} done so far"
            self.review_task.show_work(
                f"{due} due", detail, "Continue reviews →" if done else "Start reviews →"
            )
        elif done:
            self.review_task.show_done(f"{done} reviewed today.")
        else:
            self.review_task.show_nothing("Nothing due", "No reviews today.")

        # New words: what is waiting, or that the day's are learned.
        if new_count:
            detail = f"about {estimate(0, new_count).minutes} min · shown, then practised"
            if learned:
                detail = f"{learned} learned so far · " + detail
            self.learn_task.show_work(
                f"{new_count} new", detail,
                "Continue new words →" if learned else "Learn new words →",
            )
        elif learned:
            self.learn_task.show_done(f"{learned} learned today — first review tomorrow.")
        else:
            reason = _no_words_reason(plan) or "none today."
            self.learn_task.show_nothing("None today", f"No new words: {reason}")

        # The day's order: reviews first, then the new words. The next thing
        # to do has the primary button; the other stays one click away.
        self.review_task.set_primary(bool(due))
        self.learn_task.set_primary(not due and bool(new_count))

        # Only the workload valve gets its own line: it is a reason, not a result.
        show_note = bool(plan.intake_note) and (plan.intake_paused or not learned)
        notes = [plan.intake_note] if show_note and plan.intake_note else []
        if plan.due_left_over:
            notes.append(
                f"{plan.due_left_over} more due words wait for another day: over your "
                f"limit of {plan.review_capacity}. The most fragile and those you are "
                "most likely to have forgotten come first."
            )
        self.intake_note.setText(" ".join(notes))
        self.intake_note.setVisible(bool(notes))
        pool = f"{plan.pool_remaining:,} words in the plan are still to come."
        waiting = plan.without_definition
        if waiting:
            pool += (
                f" {waiting:,} more have no definition yet: they are offered once they "
                "have one."
            )
        self.pool_label.setText(pool)
        self.pool_label.setVisible(bool(plan.pool_remaining or waiting))

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

    # -- the session -------------------------------------------------------

    def start_review(self) -> None:
        self.start_session(SessionKind.REVIEW)

    def start_learning(self) -> None:
        self.start_session(SessionKind.LEARN)

    def start_session(self, kind: SessionKind = SessionKind.REVIEW) -> None:
        """A session of ``kind``: the one left open (the app closed in the
        middle) as it stood, if it is of that kind; otherwise a new one."""
        kind = SessionKind(kind)
        resumed = self._resume(kind)
        if not resumed:
            self.flow = ReviewFlow(self._engine, kind=kind)
            if not self.flow.start():
                self.refresh()
                return
        plan = self._engine.active_plan()
        self.session_title.setText(plan.name if plan else "Today")
        self.session_kind_label.setText(
            "NEW WORDS" if self.flow.kind is SessionKind.LEARN else "REVIEWS"
        )
        self._stack.setCurrentWidget(self._pages[SESSION])
        self._show_card()

    def _resume(self, kind: SessionKind) -> bool:
        """The desktop's open session, if it is of ``kind``. One of the other
        kind is closed: its answers are already saved."""
        existing = self._engine.open_session(Channel.DESKTOP)
        if existing is None or self.flow.active:
            return False
        flow = ReviewFlow.restore(self._engine, existing.id)
        if flow is None or flow.current is None or flow.kind is not kind:
            # Nothing left in it, the other kind, or saved by an older
            # version: closed.
            self._engine.finish_session(existing.id)
            return False
        self.flow = flow
        return True

    def end_session(self) -> None:
        kind = self.flow.kind
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
            waiting = len(self._engine.daily_plan().new_words)
            if kind is SessionKind.REVIEW and waiting:
                # The next step, named: its card on the page is the primary.
                text += f" Next: {waiting} new words."
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
        self.card.show_step(step)
        self.card.set_progress(self.flow.position, self.flow.total)
        self._show_undo()
        # A right answer waiting for its rating before the app closed comes
        # back as it stood.
        pending = self.flow.pending_feedback()
        if pending is not None:
            self.card.show_feedback(pending, self.flow.intervals())
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

    def _on_chosen(self, index: int, response_ms: int) -> None:
        if self.flow.current is None or self.flow.awaiting:
            return
        feedback = self.flow.choose(index, response_ms)
        self._after_rating(feedback)
        self.card.show_feedback(feedback, self.flow.intervals())
        self.card.set_progress(self.flow.position, self.flow.total)
        self._show_undo()

    def _on_rated(self, rating: Rating) -> None:
        """Again, Hard, Good or Easy for a right answer: recorded, and on to
        the next step — the word and its definition were just on screen."""
        if not self.flow.awaiting:
            return
        feedback = self.flow.rate(rating)
        self._after_rating(feedback)
        if feedback.finished:
            self.end_session()
            return
        self._show_card()

    def _after_rating(self, feedback: Feedback) -> None:
        outcome = feedback.outcome
        if outcome is not None and not outcome.duplicate and outcome.suggest_known:
            self._suggest_known(outcome.word)

    def _continue(self) -> None:
        """Enter after an answer or on a new word's page: on to the next step."""
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
        """Right after a long gap: offer, never decide, Known."""
        def confirm() -> None:
            if self._engine.confirm_known([word.id]):
                self.notify.emit(f"“{word.word}” is marked Known.")
                self.data_changed.emit()

        days = self._engine.settings.mastery_stability_days
        self.notify_action.emit(
            f"“{word.word}”: right after {days:g}+ days without a review. "
            "Consider marking it Known.",
            ("Mark Known", confirm),
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
        number = _NUMBER_KEYS.get(key)
        if self.card.rating and number is not None:
            # After a right answer: 1 Again … 4 Easy.
            self.card.rate_by_key(number)
            return
        choice = number if number is not None else _LETTER_KEYS.get(key)
        step = self.flow.current
        if (
            step is not None
            and step.kind is StepKind.QUESTION
            and choice is not None
            and not self.card.waiting
            and not self.card.rating
        ):
            self.card.choose(choice)
            return
        super().keyPressEvent(event)


#: Keys 1–4, as indexes 0–3.
_NUMBER_KEYS = {
    Qt.Key.Key_1: 0,
    Qt.Key.Key_2: 1,
    Qt.Key.Key_3: 2,
    Qt.Key.Key_4: 3,
}
#: Keys A–D, as the options are lettered, as indexes 0–3.
_LETTER_KEYS = {
    Qt.Key.Key_A: 0,
    Qt.Key.Key_B: 1,
    Qt.Key.Key_C: 2,
    Qt.Key.Key_D: 3,
}


def _meaning(word) -> str:
    return word.definition or ""


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
