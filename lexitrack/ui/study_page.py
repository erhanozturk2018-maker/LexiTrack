"""Study: the one screen that answers "what do I do today?".

The page has three faces and shows exactly one of them, because each answers a
different question and mixing them would make the first thing the user sees a
layout instead of an instruction:

1. **No plan yet** — one explanation and one button. A study plan is the only
   thing the engine needs to start, so nothing else is offered here.
2. **The day** — today's new words, today's reviews, and the week ahead. The
   two actions are a button each; everything else on the page is a number.
3. **A review session** — one word at a time and four answers. No navigation
   chrome: leaving is Escape or the End button, and both are visible.

Two decisions worth knowing:

* **The new words are shown, not just counted.** "25 words to study" with no
  words is an instruction to go and look somewhere else. The list is right
  there, and the confirmation button is under it.
* **The answer buttons say when the word comes back.** Four buttons labelled
  only Again / Hard / Good / Easy give the user no way to tell Hard from Good.
  The interval under each label is the actual scheduler's answer for this
  card, not a guess.

The page holds no learning logic at all: every number comes from one
:meth:`LearningService.daily_plan` call, and every action is a service method.
"""

from __future__ import annotations

from datetime import date
from html import escape

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.srs import Rating
from ..services.learning_service import DailyPlan, LearningService, StudyItem
from .components.cards import StatTile
from .theme import current_palette
from .theme.palette import METRICS
from .widgets import WrappedLabel

#: How many of today's new words are listed before the rest are summarised.
_WORDS_SHOWN = 30

#: Which button style each answer gets. Amber for "not yet", green for "solid":
#: the same language the flashcard answers already use, so the colours mean the
#: same thing on both screens.
_ANSWER_STYLE: dict[Rating, tuple[str, str | None]] = {
    Rating.AGAIN: ("1", "unknown"),
    Rating.HARD: ("2", None),
    Rating.GOOD: ("3", "primary"),
    Rating.EASY: ("4", "known"),
}

EMPTY, DAY, SESSION = "empty", "day", "session"


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class ForecastBars(QWidget):
    """The next seven days as bars, with today first.

    A number per day ("37, 12, 8…") reads as noise; the shape of the week is
    the thing worth seeing, and it is what tells the user a heavy day is
    coming before they meet it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: tuple[tuple[str, int], ...] = ()
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_forecast(self, days: tuple[tuple[str, int], ...]) -> None:
        self._days = days
        total = sum(count for _, count in days)
        self.setToolTip(
            "  ".join(f"{day[5:]}: {count}" for day, count in days) if days else ""
        )
        self.setAccessibleName(f"{total} reviews due over the next {len(days)} days")
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(420, 96)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._days:
            return
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())

        label_height = 18
        width = self.width()
        height = self.height() - label_height
        count = len(self._days)
        slot = width / count
        bar_width = min(slot - 8, 42)
        peak = max(count for _, count in self._days) or 1

        for index, (day, value) in enumerate(self._days):
            left = index * slot + (slot - bar_width) / 2
            # A zero day still gets a sliver, so the week reads as seven days
            # rather than as a gap.
            bar_height = max(round(height * value / peak), 2) if value else 2
            top = height - bar_height
            colour = QColor(palette.accent if index == 0 else palette.accent_soft)
            if index and value:
                colour = QColor(palette.accent)
                colour.setAlpha(110)
            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(int(left), int(top), int(bar_width), int(bar_height), 4, 4)

            painter.setPen(QColor(palette.text_faint if index else palette.text_muted))
            painter.drawText(
                int(index * slot),
                height + 2,
                int(slot),
                label_height,
                Qt.AlignmentFlag.AlignCenter,
                "Today" if index == 0 else day[8:10],
            )
            if value:
                # A tall bar leaves no room above it, and a number drawn at a
                # negative y is simply not drawn — so it moves inside the bar.
                inside = top < 16
                painter.setPen(
                    QColor(palette.text_on_accent if inside else palette.text_muted)
                )
                painter.drawText(
                    int(left) - 4,
                    int(top + 2) if inside else int(top) - 15,
                    int(bar_width) + 8,
                    14,
                    Qt.AlignmentFlag.AlignCenter,
                    str(value),
                )
        painter.end()


class StudyPage(QWidget):
    """Today's work, and the review session that clears it."""

    #: The user wants to create or change the study plan.
    manage_plan = Signal()
    #: Something changed that other pages show too (statuses, counts).
    data_changed = Signal()
    #: A short message for the window's toast.
    notify = Signal(str)
    #: The user wants today's words (or the hard ones) as a file.
    export_requested = Signal()

    def __init__(self, engine: LearningService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._queue: list[StudyItem] = []
        self._index = 0
        self._session_id: str | None = None
        self._revealed = False
        self._answered = 0
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
        m = METRICS
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(m.space_7, m.space_7, m.space_7, m.space_7)
        layout.setSpacing(m.space_3)
        layout.addStretch(1)
        glyph = _label("◎", "EmptyGlyph")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
        title = _label("No study plan yet", "EmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        # WrappedLabel rather than a wrapping QLabel: a centred wrapping label
        # stops Qt consulting heightForWidth and the last line gets clipped.
        body = WrappedLabel(
            "A study plan chooses the lists you are working through. LexiTrack "
            "then offers you a few new words each day and asks you about them "
            "on the days you are most likely to forget them."
        )
        body.setObjectName("EmptyBody")
        layout.addWidget(body, 0, Qt.AlignmentFlag.AlignHCenter)
        button = QPushButton("Create a Study Plan…")
        button.setProperty("variant", "primary")
        button.clicked.connect(self.manage_plan.emit)
        layout.addSpacing(m.space_2)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        return page

    def _build_day(self) -> QWidget:
        m = METRICS
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("PanelBody")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(m.space_7, m.space_5, m.space_7, m.space_6)
        layout.setSpacing(m.space_4)

        header = QHBoxLayout()
        header.setSpacing(m.space_3)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("Study", "PageTitle"))
        self.plan_label = _label("", "PageSubtitle")
        titles.addWidget(self.plan_label)
        header.addLayout(titles, 1)
        self.plan_button = QPushButton("Study Plan…")
        self.plan_button.setProperty("variant", "ghost")
        self.plan_button.setToolTip("Choose which lists you are working through")
        self.plan_button.clicked.connect(self.manage_plan.emit)
        header.addWidget(self.plan_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        layout.addWidget(self._build_intake_panel())
        layout.addWidget(self._build_review_panel())
        self._forecast_panel = self._build_forecast_panel()
        layout.addWidget(self._forecast_panel)
        self._hard_panel = self._build_hard_panel()
        layout.addWidget(self._hard_panel)
        self._stats_panel = self._build_stats_panel()
        layout.addWidget(self._stats_panel)
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _build_intake_panel(self) -> QWidget:
        m = METRICS
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
        layout.setSpacing(m.space_3)

        top = QHBoxLayout()
        top.setSpacing(m.space_3)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(_label("NEW WORDS TODAY", "SectionTitle"))
        self.intake_count = _label("", "StatValue")
        heading.addWidget(self.intake_count)
        top.addLayout(heading, 1)
        self.intake_button = QPushButton("I have studied these")
        self.intake_button.setProperty("variant", "primary")
        self.intake_button.setToolTip(
            "Confirm you have learned today's words. They will be reviewed from tomorrow."
        )
        self.intake_button.clicked.connect(self._introduce)
        top.addWidget(self.intake_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)

        self.intake_words = _label("", "DefinitionLabel", wrap=True)
        self.intake_words.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.intake_words)
        tools = QHBoxLayout()
        tools.setSpacing(METRICS.space_2)
        self.copy_button = QPushButton("Copy with meanings")
        self.copy_button.setProperty("variant", "ghost")
        self.copy_button.setProperty("size", "small")
        self.copy_button.setToolTip("Copy today's words and their meanings, one per line")
        self.copy_button.clicked.connect(self._copy_words)
        tools.addWidget(self.copy_button)
        self.export_button = QPushButton("Export\u2026")
        self.export_button.setProperty("variant", "ghost")
        self.export_button.setProperty("size", "small")
        self.export_button.setToolTip("Save today's words as PDF, CSV or JSON (Ctrl+E)")
        self.export_button.clicked.connect(self.export_requested.emit)
        tools.addWidget(self.export_button)
        tools.addStretch(1)
        layout.addLayout(tools)
        self.intake_note = _label("", "WarningText", wrap=True)
        layout.addWidget(self.intake_note)
        self.intake_pool = _label("", "Faint")
        layout.addWidget(self.intake_pool)
        return panel

    def _build_review_panel(self) -> QWidget:
        m = METRICS
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
        layout.setSpacing(m.space_3)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(_label("REVIEWS DUE", "SectionTitle"))
        self.due_count = _label("", "StatValue")
        heading.addWidget(self.due_count)
        self.done_label = _label("", "Muted")
        heading.addWidget(self.done_label)
        layout.addLayout(heading, 1)

        self.review_button = QPushButton("Start reviewing")
        self.review_button.setProperty("variant", "primary")
        self.review_button.clicked.connect(self.start_session)
        layout.addWidget(self.review_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return panel

    def _build_forecast_panel(self) -> QWidget:
        m = METRICS
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
        layout.setSpacing(m.space_3)
        layout.addWidget(_label("THE WEEK AHEAD", "SectionTitle"))
        self.forecast = ForecastBars()
        layout.addWidget(self.forecast)
        self.forecast_note = _label("", "Faint", wrap=True)
        layout.addWidget(self.forecast_note)
        return panel

    def _build_hard_panel(self) -> QWidget:
        """Words the engine has flagged. Shown only when there are some.

        Listed by name rather than counted, with how often each has been
        missed: the point is to look at them, and a number does not help
        with that.
        """
        m = METRICS
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
        layout.setSpacing(m.space_2)
        layout.addWidget(_label("WORDS YOU FIND HARD", "SectionTitle"))
        self.hard_words = _label("", "DefinitionLabel", wrap=True)
        self.hard_words.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.hard_words)
        self.hard_note = _label(
            "They come first in every session until they stick. Writing them in a "
            "sentence of your own tends to help more than another review.",
            "Faint",
            wrap=True,
        )
        layout.addWidget(self.hard_note)
        return panel

    def _build_stats_panel(self) -> QWidget:
        m = METRICS
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
        layout.setSpacing(m.space_3)
        layout.addWidget(_label("THE LAST 30 DAYS", "SectionTitle"))
        row = QHBoxLayout()
        row.setSpacing(m.space_3)
        self.stat_reviews = StatTile("reviews")
        self.stat_again = StatTile("answered Again")
        self.stat_introduced = StatTile("new words learned")
        self.stat_long_term = StatTile("in long-term memory", tone="known")
        for tile in (self.stat_reviews, self.stat_again, self.stat_introduced,
                     self.stat_long_term):
            row.addWidget(tile, 1)
        layout.addLayout(row)
        return panel

    def _build_session(self) -> QWidget:
        m = METRICS
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_5)
        outer.setSpacing(m.space_4)

        bar = QHBoxLayout()
        bar.setSpacing(m.space_3)
        self.session_progress = _label("", "ContextName")
        bar.addWidget(self.session_progress)
        self.session_flag = _label("", "HistoryBanner")
        self.session_flag.setVisible(False)
        bar.addWidget(self.session_flag)
        bar.addStretch(1)
        end_button = QPushButton("End session")
        end_button.setProperty("variant", "ghost")
        end_button.setToolTip("Stop here and keep what you have answered (Esc)")
        end_button.clicked.connect(self.end_session)
        bar.addWidget(end_button)
        outer.addLayout(bar)

        column = QVBoxLayout()
        column.setSpacing(m.space_4)
        card = QFrame()
        card.setObjectName("ReviewCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Tall enough that the buttons never move between words, but not
        # stretched: a card given the layout's spare height leaves one word
        # floating in the middle of an empty page.
        card.setMinimumHeight(260)
        card.setMaximumHeight(380)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(m.space_6, m.space_6, m.space_6, m.space_6)
        card_layout.setSpacing(m.space_3)
        card_layout.addStretch(1)

        self.word_label = _label("", "WordLabel", wrap=True)
        self.word_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.word_label)
        self.meta_label = _label("", "MetaLabel")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.meta_label)

        self.reveal_button = QPushButton("Show meaning")
        self.reveal_button.setProperty("variant", "ghost")
        self.reveal_button.setToolTip("Space")
        self.reveal_button.clicked.connect(self._reveal)
        card_layout.addWidget(self.reveal_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.definition_label = _label("", "DefinitionLabel", wrap=True)
        self.definition_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.definition_label)
        self.note_label = _label("", "SenseLabel", wrap=True)
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.note_label)
        card_layout.addStretch(1)
        column.addWidget(card)

        answers = QHBoxLayout()
        answers.setSpacing(m.space_2)
        self.answer_buttons: dict[Rating, QPushButton] = {}
        for rating in Rating:
            key, variant = _ANSWER_STYLE[rating]
            button = QPushButton(f"{rating.label}\n–")
            button.setObjectName("AnswerButton")
            if variant:
                button.setProperty("variant", variant)
            button.setToolTip(f"{rating.label} ({key})")
            # Fixed, not minimum: the coloured variants carry more padding in
            # the stylesheet, and four targets of different heights read as
            # four different kinds of button.
            button.setFixedHeight(56)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, r=rating: self._answer(r))
            answers.addWidget(button, 1)
            self.answer_buttons[rating] = button
        column.addLayout(answers)

        self.answer_hint = _label("", "Faint")
        self.answer_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.answer_hint)

        holder = QWidget()
        # A set width rather than a maximum: without it the column shrinks to
        # its content and the four answers end up different sizes.
        holder.setFixedWidth(560)
        holder.setLayout(column)
        outer.addStretch(1)
        outer.addWidget(holder, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)
        return page

    # -- the day -----------------------------------------------------------

    def refresh(self) -> None:
        """Re-read everything from the service. Called whenever shown."""
        if self._session_id is not None:
            return
        self._engine.refresh_settings()
        plan = self._engine.daily_plan()
        if not plan.has_plan:
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

        new_count = len(plan.new_words)
        done_today = len(plan.introduced_today)
        self.copy_button.setVisible(bool(new_count))
        self.export_button.setVisible(bool(new_count))
        if new_count:
            self.intake_count.setText(f"{new_count}")
            self.intake_words.setText(_word_list(plan))
            self.intake_button.setEnabled(True)
            self.intake_button.setText(
                f"I have studied these {new_count}"
                if new_count > 1
                else "I have studied this word"
            )
        else:
            self.intake_count.setText("0")
            self.intake_button.setEnabled(False)
            self.intake_button.setText("Nothing to confirm")
            self.intake_words.setText(
                f"{done_today} introduced today — they will be reviewed from tomorrow."
                if done_today
                else "No new words are being offered right now."
            )
        # A finished day is already stated above; repeating it as a warning
        # would be three sentences for one fact. Only the workload valve gets
        # its own line, because it is a reason rather than a result.
        show_note = bool(plan.intake_note) and (plan.intake_paused or not done_today)
        self.intake_note.setText(plan.intake_note or "")
        self.intake_note.setVisible(show_note)
        self.intake_pool.setText(
            f"{plan.pool_remaining:,} words in the plan have not been introduced yet."
            if plan.pool_remaining
            else "Every word in this plan has been introduced."
        )

        self.due_count.setText(f"{plan.due_count}")
        self.review_button.setEnabled(plan.due_count > 0)
        self.review_button.setText(
            "Start reviewing" if plan.due_count else "Nothing due today"
        )
        self.done_label.setText(
            f"{plan.reviews_done_today} answered today"
            if plan.reviews_done_today
            else "Not started today"
        )

        # Before anything has been introduced the chart is seven empty slots,
        # which is a lot of panel for no information.
        scheduled = sum(count for _, count in plan.forecast)
        self._forecast_panel.setVisible(bool(scheduled))
        self._show_hard_words()
        self._show_stats()
        self.forecast.set_forecast(plan.forecast)
        upcoming = [count for _, count in plan.forecast[1:]]
        busiest = max(upcoming, default=0)
        capacity = plan.review_capacity
        if capacity and busiest > capacity:
            self.forecast_note.setText(
                f"The busiest day ahead has {busiest} reviews, over the {capacity} "
                f"you set as a daily limit. New words will pause until it clears."
            )
        else:
            total = sum(count for _, count in plan.forecast)
            self.forecast_note.setText(f"{total} reviews due over the next 7 days.")

    def _show_hard_words(self) -> None:
        items = self._engine.struggling_words(limit=12)
        self._hard_panel.setVisible(bool(items))
        if not items:
            return
        muted = current_palette().text_muted
        parts = []
        for item in items:
            missed = item.card.lapse_count
            times = "once" if missed == 1 else f"{missed}&nbsp;times"
            # Non-breaking inside an item, so a line never ends on "missed 2"
            # and starts the next with "times".
            word = escape(item.word.word).replace(" ", "&nbsp;")
            parts.append(
                f"<span style='white-space:nowrap'><b>{word}</b>&nbsp;"
                f"<span style='color:{muted}'>missed&nbsp;{times}</span></span>"
            )
        self.hard_words.setText(" &nbsp;\u00b7&nbsp; ".join(parts))

    def _show_stats(self) -> None:
        """Four numbers for the month. Hidden until there is a month to show."""
        since = self._engine.clock.shift_days(-29)
        ratings = self._engine.rating_counts(since)
        answered = sum(ratings.values())
        introduced = sum(
            count for day, count in self._engine.introduced_per_day(30).items() if day >= since
        )
        self._stats_panel.setVisible(bool(answered or introduced))
        again = ratings.get(int(Rating.AGAIN), 0)
        self.stat_reviews.set_value(answered)
        self.stat_again.value_label.setText(
            f"{round(100 * again / answered)}%" if answered else "\u2013"
        )
        self.stat_introduced.set_value(introduced)
        self.stat_long_term.set_value(self._engine.state_counts().get("review", 0))

    def today_words(self) -> list:
        """The words on screen, for the window's export."""
        return list(self._engine.daily_plan().new_words)

    def _copy_words(self) -> None:
        words = self.today_words()
        lines = []
        for word in words:
            meaning = word.definition or ""
            if word.note:
                meaning = f"{meaning} ({word.note})".strip()
            lines.append(f"{word.word} \u2014 {meaning}" if meaning else word.word)
        QApplication.clipboard().setText("\n".join(lines))
        self.notify.emit(f"Copied {len(lines)} words with their meanings.")

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
        self._queue = self._engine.review_queue()
        if not self._queue:
            self.refresh()
            return
        self._session_id = self._engine.start_session().id
        self._index = 0
        self._answered = 0
        self._stack.setCurrentWidget(self._pages[SESSION])
        self._show_card()
        self.setFocus()

    def end_session(self) -> None:
        if self._session_id is not None:
            self._engine.finish_session(self._session_id)
            self._session_id = None
            if self._answered:
                word = "word" if self._answered == 1 else "words"
                self.notify.emit(f"{self._answered} {word} reviewed.")
                self.data_changed.emit()
        self._queue = []
        self.refresh()

    @property
    def in_session(self) -> bool:
        return self._session_id is not None

    def _current(self) -> StudyItem | None:
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    def _show_card(self) -> None:
        item = self._current()
        if item is None:
            self.end_session()
            return
        word = item.word
        self.session_progress.setText(f"{self._index + 1} of {len(self._queue)}")
        self.session_flag.setVisible(item.is_struggling)
        if item.is_struggling:
            self.session_flag.setText("You have been finding this one hard")

        self.word_label.setText(word.word)
        meta = " · ".join(part for part in (word.part_of_speech, word.cefr_level) if part)
        self.meta_label.setText(meta)
        self.meta_label.setVisible(bool(meta))

        self._revealed = not item.hide_meaning
        self._apply_reveal(word.definition, word.note)

        self._show_intervals(self._engine.preview_intervals(word.id))

    def _show_intervals(self, preview: dict[Rating, int]) -> None:
        """Label the answers with when the word would come back.

        Early on, every answer lands tomorrow — the learning step is a day, and
        a card with almost no stability cannot be pushed further out. Printing
        "tomorrow" four times then looks like a bug, so in that case the four
        buttons carry only their names and one line underneath says it once.
        Compared as the words the user reads, not as raw days: 0 and 1 are
        different numbers but the same sentence.
        """
        labels = {rating: _interval(preview.get(rating)) for rating in Rating}
        uniform = len(set(labels.values())) <= 1
        for rating, button in self.answer_buttons.items():
            button.setText(rating.label if uniform else f"{rating.label}\n{labels[rating]}")
        self.answer_hint.setText(
            f"Every answer brings this word back {labels[Rating.GOOD]}." if uniform else ""
        )
        self.answer_hint.setVisible(uniform)

    def _apply_reveal(self, definition: str | None, note: str | None) -> None:
        shown = self._revealed
        self.reveal_button.setVisible(not shown)
        self.definition_label.setVisible(shown)
        self.note_label.setVisible(shown and bool(note))
        if shown:
            self.definition_label.setText(definition or "No definition stored for this word.")
            self.note_label.setText(note or "")

    def _reveal(self) -> None:
        item = self._current()
        if item is None or self._revealed:
            return
        self._revealed = True
        self._apply_reveal(item.word.definition, item.word.note)

    def _answer(self, rating: Rating) -> None:
        item = self._current()
        if item is None:
            return
        outcome = self._engine.answer(
            item.word.id, rating, session_id=self._session_id
        )
        if outcome is not None and not outcome.duplicate:
            self._answered += 1
            if outcome.marked_known:
                self.notify.emit(f"“{outcome.word.word}” is now marked as known.")
        self._index += 1
        if self._index >= len(self._queue):
            self.end_session()
            return
        self._show_card()

    # -- keyboard ----------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Numbers answer, Space reveals, Escape leaves.

        Only during a session: the same keys on the day view would be a trap,
        because there is no card on screen for them to apply to.
        """
        if not self.in_session:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.end_session()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._revealed:
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


def _pretty_date(local_date: str) -> str:
    """``2026-09-17`` as ``Thursday 17 September``, so the day is unambiguous."""
    try:
        day = date.fromisoformat(local_date)
    except ValueError:
        return local_date
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"


def _word_list(plan: DailyPlan) -> str:
    """Today's words as one readable line, truncated if the count is high."""
    words = [word.word for word in plan.new_words]
    shown = words[:_WORDS_SHOWN]
    text = " · ".join(shown)
    if len(words) > len(shown):
        text += f" · and {len(words) - len(shown)} more"
    return text


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
