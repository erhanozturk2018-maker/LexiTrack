"""Progress: what has been learned through the study plan, and how.

The Today page is about today; this page is about everything since the first
word was introduced. Four tabs, in the order the questions come:

* **Overview** — how much has been learned, which words are ready to be
  marked Known (the engine only offers; the learner says yes), where the words
  stand in *memory* and in *skill* — two bars, because the engine keeps them
  apart — the evidence that is neither, and the last 30 days.
* **Words** — every studied word, its memory and its skill; each opens the
  word's full history.
* **Answers** — every answer, what it asked and what it showed, filtered and
  exported as CSV.
* **Scheduler** — does the schedule fit you: its forecasts against your
  answers, and which parameters are in use. Expert information, kept apart.

Everything is read through :class:`ProgressService`; the only thing the page
writes is a Known the learner confirms.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import SIGNAL, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..models.attempt import MemoryResult
from ..models.srs import Channel, Rating
from ..services.learning_service import LearningService
from ..services.maintenance import Maintenance
from ..services.optimizer import Personaliser
from ..services.progress import (
    AnswerRow,
    Calibration,
    Group,
    ProgressService,
    WordProgress,
)
from .components.cards import StatTile
from .components.progress_charts import (
    CalibrationChart,
    PipelineBar,
    TimelineChart,
    skill_colours,
)
from .components.word_history import pretty_day, remembered_for
from .theme import current_palette
from .theme.palette import METRICS
from .widgets import PageColumn

ALL, HARD, READY = "all", "hard", "ready"
AGAIN, TAKEN_BACK = "again", "taken_back"
OVERVIEW, WORDS, ANSWERS, SCHEDULER = "overview", "words", "answers", "scheduler"

_TABS = ((OVERVIEW, "Overview"), (WORDS, "Words"), (ANSWERS, "Answers"),
         (SCHEDULER, "Scheduler"))

#: The word filters, in the order they are offered.
_FILTERS = (
    (Group.LEARNED.value, "Learned here"),
    (Group.IN_PROGRESS.value, "In progress"),
    (Group.MARKED_KNOWN.value, "Marked Known"),
    (READY, "Ready for Known"),
    (HARD, "Hard for you"),
    (ALL, "All"),
)
_ANSWER_FILTERS = ((ALL, "All"), (AGAIN, "Again"), (TAKEN_BACK, "Taken back"))

#: Known suggestions listed one by one; the rest are counted.
_SUGGESTIONS_SHOWN = 5

_MEMORY_TONE = {
    MemoryResult.RECALLED: "known",
    MemoryResult.RECALLED_EFFORT: "known",
    MemoryResult.FORGOTTEN: "unknown",
}


class _Item(QTableWidgetItem):
    """A cell that sorts by a key, not by its text: 9 before 10, dates as dates."""

    def __init__(self, text: str, key: object | None = None) -> None:
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, text if key is None else key)
        self.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine = self.data(Qt.ItemDataRole.UserRole)
        theirs = other.data(Qt.ItemDataRole.UserRole)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


class _Table(QTableWidget):
    """A read-only, sortable table whose rows open a word's history."""

    open_word = Signal(int)

    def __init__(
        self,
        headers: list[str],
        sort_column: int = 0,
        stretch: tuple[int, ...] = (0,),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(True)
        # Newest first until the user clicks a header; Qt would otherwise
        # sort the first column backwards.
        self.sortItems(sort_column, Qt.SortOrder.DescendingOrder)
        self.verticalHeader().setDefaultSectionSize(40)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        # The text columns share the spare width; the rest fit their content,
        # so a short value never wraps into two lines.
        self.setWordWrap(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for column in stretch:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(72)
        self.setMinimumHeight(420)
        self.cellDoubleClicked.connect(lambda row, _column: self._open(row))

    def _open(self, row: int) -> None:
        item = self.item(row, 0)
        if item is not None and item.data(Qt.ItemDataRole.UserRole + 1) is not None:
            self.open_word.emit(int(item.data(Qt.ItemDataRole.UserRole + 1)))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentRow() >= 0:
            self._open(self.currentRow())
            return
        super().keyPressEvent(event)


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _section(title: str) -> tuple[QWidget, QVBoxLayout, QHBoxLayout, QLabel]:
    """A title outside, content below: the Home and Study section pattern."""
    widget = QWidget()
    # Transparent like the page, or the title row paints the window colour
    # as a band across the page.
    widget.setObjectName("PanelBody")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(METRICS.space_2 + 2)
    header = QHBoxLayout()
    header.setSpacing(METRICS.space_2)
    heading = _label(title, "SectionTitle")
    header.addWidget(heading)
    header.addStretch(1)
    layout.addLayout(header)
    return widget, layout, header, heading


def _panel() -> tuple[QFrame, QVBoxLayout]:
    m = METRICS
    frame = QFrame()
    frame.setObjectName("Panel")
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
    layout.setSpacing(m.space_3)
    return frame, layout


def _switch(
    options: tuple[tuple[str, str], ...], on_pick: Callable[[str], None], parent: QWidget
) -> tuple[QFrame, dict[str, QPushButton]]:
    """A segmented control: one of ``options``, the ModeSwitch look."""
    frame = QFrame()
    frame.setObjectName("ModeSwitch")
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    row = QHBoxLayout(frame)
    row.setContentsMargins(3, 3, 3, 3)
    row.setSpacing(2)
    group = QButtonGroup(parent)
    buttons: dict[str, QPushButton] = {}
    for key, text in options:
        button = QPushButton(text)
        button.setObjectName("ModeButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _c=False, k=key: on_pick(k))
        group.addButton(button)
        row.addWidget(button)
        buttons[key] = button
    return frame, buttons


def _page() -> tuple[QWidget, QVBoxLayout]:
    widget = QWidget()
    widget.setObjectName("PanelBody")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(METRICS.space_5)
    return widget, layout


class ProgressPage(QWidget):
    """Everything learned here, and every answer given."""

    #: The user asked for Settings, from the note about testing predictions.
    settings_requested = Signal()
    #: The learner confirmed words as Known: the rest of the window reloads.
    data_changed = Signal()
    #: A short message for the window's toast.
    notify = Signal(str)

    def __init__(
        self,
        progress: ProgressService,
        engine: LearningService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._progress = progress
        self._engine = engine
        self._rows: list[WordProgress] = []
        self._answers: list[AnswerRow] = []
        self._filter = Group.LEARNED.value
        self._answer_filter = ALL
        #: Until the user picks a filter, the page opens on one with words in it.
        self._filter_chosen = False
        self._tab = OVERVIEW
        #: Opens a word's history; set by the main window.
        self.history_opener: Callable[[int], None] | None = None
        self._suggestion_rows: list[QWidget] = []
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("PanelBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(m.space_6, m.space_5, m.space_6, m.space_6)
        PageColumn(body, layout)
        layout.setSpacing(m.space_5)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("Progress", "PageTitle"))
        titles.addWidget(
            _label(
                "Everything learned through your study plan, and every answer you have "
                "given. Words you knew before the plan are counted, not listed.",
                "PageSubtitle",
                wrap=True,
            )
        )
        layout.addLayout(titles)

        tabs, self.tab_buttons = _switch(_TABS, self.show_tab, self)
        layout.addWidget(tabs, 0, Qt.AlignmentFlag.AlignLeft)

        # The tabs share the column and only the one shown is visible: a hidden
        # widget takes no room, so the page is as tall as that tab and no
        # taller. (A stacked widget is as tall as its tallest page, which
        # spread a short tab down the whole of a long one.)
        self._pages = {
            OVERVIEW: self._build_overview(),
            WORDS: self._build_words(),
            ANSWERS: self._build_answers(),
            SCHEDULER: self._build_scheduler(),
        }
        for page in self._pages.values():
            layout.addWidget(page)
        layout.addStretch(1)
        self.show_tab(OVERVIEW)

    def _build_overview(self) -> QWidget:
        m = METRICS
        page, layout = _page()

        # How much
        tiles = QHBoxLayout()
        tiles.setSpacing(m.space_3)
        self.tile_learned = StatTile("learned here", tone="known")
        self.tile_progress = StatTile("in progress")
        self.tile_marked = StatTile("marked Known by hand")
        self.tile_before = StatTile("known before your plan")
        for tile in (self.tile_learned, self.tile_progress, self.tile_marked, self.tile_before):
            tiles.addWidget(tile, 1)
        holder = QVBoxLayout()
        holder.setSpacing(m.space_2)
        holder.addLayout(tiles)
        self.totals_line = _label("", "Faint")
        holder.addWidget(self.totals_line)
        layout.addLayout(holder)

        # Ready to mark Known
        self.suggestions, suggestions_layout, _, self.suggestions_title = _section(
            "READY TO MARK KNOWN"
        )
        panel, self._suggestions_layout = _panel()
        self.suggestions_note = _label("", "Faint", wrap=True)
        self._suggestions_layout.addWidget(self.suggestions_note)
        self._suggestion_list = QVBoxLayout()
        self._suggestion_list.setSpacing(0)
        self._suggestions_layout.addLayout(self._suggestion_list)
        footer = QHBoxLayout()
        self.suggestions_more = QPushButton()
        self.suggestions_more.setObjectName("LinkButton")
        self.suggestions_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.suggestions_more.clicked.connect(self._show_ready)
        footer.addWidget(self.suggestions_more)
        footer.addStretch(1)
        self.confirm_all = QPushButton()
        self.confirm_all.setProperty("variant", "primary")
        self.confirm_all.clicked.connect(self._confirm_all)
        footer.addWidget(self.confirm_all)
        self._suggestions_layout.addLayout(footer)
        suggestions_layout.addWidget(panel)
        layout.addWidget(self.suggestions)

        # Memory and skill
        where, where_layout, _, _ = _section("MEMORY AND SKILL")
        panel, panel_layout = _panel()
        panel_layout.addWidget(_label("MEMORY", "SubsectionTitle"))
        panel_layout.addWidget(
            _label("How long each word is expected to be remembered, from its last answer.",
                   "Faint", wrap=True)
        )
        self.pipeline = PipelineBar()
        panel_layout.addWidget(self.pipeline)
        panel_layout.addSpacing(m.space_2)
        panel_layout.addWidget(_label("SKILL", "SubsectionTitle"))
        panel_layout.addWidget(
            _label(
                "What your answers on later days have shown you can do with each word: "
                "recognise it, recall it from its meaning or a sentence, or use it.",
                "Faint",
                wrap=True,
            )
        )
        self.skill_bar = PipelineBar(skill_colours)
        panel_layout.addWidget(self.skill_bar)
        self.evidence = _label("", None, wrap=True)
        self.evidence.setObjectName("EvidenceLine")
        panel_layout.addWidget(self.evidence)
        self.skill_note = _label("", "Faint", wrap=True)
        panel_layout.addWidget(self.skill_note)
        where_layout.addWidget(panel)
        layout.addWidget(where)

        # The last 30 days
        recent, recent_layout, _, self.recent_title = _section("THE LAST 30 DAYS")
        row = QHBoxLayout()
        row.setSpacing(m.space_3)
        self.stat_answers = StatTile("answers")
        self.stat_again = StatTile("answered Again")
        self.stat_introduced = StatTile("new words learned")
        self.stat_long_term = StatTile("in long-term memory", tone="known")
        for tile in (self.stat_answers, self.stat_again, self.stat_introduced,
                     self.stat_long_term):
            row.addWidget(tile, 1)
        recent_layout.addLayout(row)
        layout.addWidget(recent)

        # Over time
        over, over_layout, _, _ = _section("OVER TIME")
        panel, panel_layout = _panel()
        self.timeline = TimelineChart()
        panel_layout.addWidget(self.timeline)
        over_layout.addWidget(panel)
        layout.addWidget(over)
        return page

    def _build_words(self) -> QWidget:
        page, layout = _page()
        words, words_layout, words_header, self.words_title = _section("WORDS")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a word")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._fill_words)
        words_header.addWidget(self.search)
        filters, self.filter_buttons = _switch(_FILTERS, self._set_filter, self)
        self.filter_buttons[self._filter].setChecked(True)
        words_layout.addWidget(filters, 0, Qt.AlignmentFlag.AlignLeft)
        self.words_table = _Table(
            ["Word", "CEFR", "Introduced", "Known", "Answers", "Again", "Remembered for",
             "Skill"],
            sort_column=2,
            stretch=(0, 7),
        )
        self.words_table.open_word.connect(self._open)
        words_layout.addWidget(self.words_table)
        self.words_empty = _label("", "Faint", wrap=True)
        words_layout.addWidget(self.words_empty)
        words_layout.addWidget(
            _label("Double-click a word, or select it and press Enter, for its history.",
                   "Faint")
        )
        layout.addWidget(words)
        return page

    def _build_answers(self) -> QWidget:
        page, layout = _page()
        answers, answers_layout, header, self.answers_title = _section("ALL ANSWERS")
        self.answer_search = QLineEdit()
        self.answer_search.setPlaceholderText("Find a word")
        self.answer_search.setClearButtonEnabled(True)
        self.answer_search.setFixedWidth(220)
        self.answer_search.textChanged.connect(self._fill_answers)
        header.addWidget(self.answer_search)
        self.export_answers = QPushButton("Export CSV…")
        self.export_answers.clicked.connect(self._export_answers)
        header.addWidget(self.export_answers)
        answers_layout.addWidget(
            _label(
                "Every answer from Today and Telegram, newest first: what it asked, "
                "your answer, and what it showed about the memory. Answers taken back "
                "with Undo are kept for the record and left out of every count.",
                "Faint",
                wrap=True,
            )
        )
        filters, self.answer_filter_buttons = _switch(
            _ANSWER_FILTERS, self._set_answer_filter, self
        )
        self.answer_filter_buttons[ALL].setChecked(True)
        answers_layout.addWidget(filters, 0, Qt.AlignmentFlag.AlignLeft)
        self.answers_table = _Table(
            ["When", "Word", "Asked", "Answer", "Showed", "Remembered for", "Where", ""],
            stretch=(1, 2),
        )
        self.answers_table.open_word.connect(self._open)
        answers_layout.addWidget(self.answers_table)
        self.answers_message = _label("", "Faint", wrap=True)
        answers_layout.addWidget(self.answers_message)
        layout.addWidget(answers)
        return page

    def _build_scheduler(self) -> QWidget:
        page, layout = _page()
        fit, fit_layout, _, _ = _section("DOES THE SCHEDULE FIT YOU?")
        panel, panel_layout = _panel()
        panel_layout.addWidget(
            _label(
                "Each answer is checked against the chance of remembering the schedule "
                "gave the word at the answer before. Of the words given a 90% chance, "
                "about 90% should have been remembered.",
                "Faint",
                wrap=True,
            )
        )
        self.calibration = CalibrationChart()
        panel_layout.addWidget(self.calibration)
        self.calibration_note = _label("", None, wrap=True)
        self.calibration_note.setObjectName("CalibrationNote")
        panel_layout.addWidget(self.calibration_note)
        self.parameters_note = _label("", "Faint", wrap=True)
        panel_layout.addWidget(self.parameters_note)
        self.testing_note = _label("", "Faint", wrap=True)
        panel_layout.addWidget(self.testing_note)
        self.settings_link = QPushButton("Open Learning settings →")
        self.settings_link.setObjectName("LinkButton")
        self.settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_link.clicked.connect(self.settings_requested.emit)
        panel_layout.addWidget(self.settings_link, 0, Qt.AlignmentFlag.AlignLeft)
        fit_layout.addWidget(panel)
        layout.addWidget(fit)
        return page

    # -- tabs ------------------------------------------------------------------

    def show_tab(self, key: str) -> None:
        self._tab = key
        self.tab_buttons[key].setChecked(True)
        for name, page in self._pages.items():
            page.setVisible(name == key)
        self._scroll.verticalScrollBar().setValue(0)

    @property
    def tab(self) -> str:
        return self._tab

    # -- content -------------------------------------------------------------

    def refresh(self) -> None:
        rows = self._progress.words()
        self._rows = rows
        summary = self._progress.summary(rows)
        self.tile_learned.set_value(summary.learned)
        self.tile_progress.set_value(summary.in_progress)
        self.tile_marked.set_value(summary.marked_known)
        self.tile_before.set_value(summary.known_before)
        taken = f" · {summary.taken_back:,} taken back" if summary.taken_back else ""
        self.totals_line.setText(
            f"{summary.answers:,} answers given{taken} · "
            f"{summary.struggling:,} words you find hard"
        )
        self._fill_suggestions()
        self.pipeline.set_stages(self._progress.pipeline(rows))
        self._fill_skill(rows)
        self._fill_recent(rows)
        self.timeline.set_days(self._progress.timeline(rows))

        calibration = self._progress.calibration()
        self.calibration.set_calibration(calibration)
        self.calibration_note.setText(_verdict(calibration))
        self.parameters_note.setText(self._parameters_text())
        keeps = self._engine.settings.review_known_words
        self.testing_note.setText(
            ""
            if keeps
            else "Words learned here leave the schedule once Known, so the forecast is "
            "never tested on the words it is most sure of. Turning on Keep reviewing "
            "words learned here lets it learn from them."
        )
        self.testing_note.setVisible(not keeps)
        self.settings_link.setVisible(not keeps)

        if not self._filter_chosen:
            self._filter = self._opening_filter()
            self.filter_buttons[self._filter].setChecked(True)
        self._fill_words()
        self._answers = self._progress.answers()
        self._fill_answers()

    def _fill_suggestions(self) -> None:
        suggestions = self._engine.known_suggestions()
        self._suggested = [word.id for word in suggestions]
        self.suggestions.setVisible(bool(suggestions))
        for widget in self._suggestion_rows:
            widget.hide()
            widget.deleteLater()
        self._suggestion_rows = []
        if not suggestions:
            return
        threshold = self._engine.settings.mastery_stability_days
        count = len(suggestions)
        self.suggestions_title.setText(f"READY TO MARK KNOWN · {count:,}")
        self.suggestions_note.setText(
            f"Long-term memory and productive evidence are strong: "
            f"{'this word has' if count == 1 else 'each of these has'} been used well in "
            f"two different ways and recalled after {threshold:g}+ days without a review. "
            "Consider marking it Known — it is your call; until you do, it keeps its "
            "place in your reviews."
        )
        stability = {row.word.id: row.stability for row in self._rows}
        for index, word in enumerate(suggestions[:_SUGGESTIONS_SHOWN]):
            row = QFrame()
            row.setObjectName("SuggestionRow")
            row.setProperty("first", "true" if index == 0 else "false")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, METRICS.space_1, 0, METRICS.space_1)
            line.setSpacing(METRICS.space_3)
            name = QPushButton(word.word)
            name.setObjectName("LinkButton")
            name.setCursor(Qt.CursorShape.PointingHandCursor)
            name.setToolTip("Open its history")
            name.clicked.connect(lambda _c=False, i=word.id: self._open(i))
            line.addWidget(name)
            line.addWidget(
                _label(f"remembered for {remembered_for(stability.get(word.id))}", "Faint")
            )
            line.addStretch(1)
            confirm = QPushButton("Mark Known")
            confirm.setProperty("compact", True)
            confirm.setAccessibleName(f"Mark {word.word} Known")
            confirm.clicked.connect(lambda _c=False, i=word.id: self._confirm([i]))
            line.addWidget(confirm)
            self._suggestion_list.addWidget(row)
            self._suggestion_rows.append(row)
        rest = count - _SUGGESTIONS_SHOWN
        self.suggestions_more.setText(f"and {rest:,} more: see them all in Words →")
        self.suggestions_more.setVisible(rest > 0)
        self.confirm_all.setText(
            "Mark it Known" if count == 1 else f"Mark all {count:,} Known"
        )

    def _confirm(self, word_ids: list[int]) -> None:
        marked = self._engine.confirm_known(word_ids)
        if marked:
            self.notify.emit(
                f"{marked:,} {'word' if marked == 1 else 'words'} marked Known."
            )
            # The window reloads the page on screen; alone, the page does it.
            if self.receivers(SIGNAL("data_changed()")):
                self.data_changed.emit()
            else:
                self.refresh()

    def _confirm_all(self) -> None:
        self._confirm(list(getattr(self, "_suggested", [])))

    def _show_ready(self) -> None:
        self._set_filter(READY)
        self.show_tab(WORDS)

    def _fill_skill(self, rows: list[WordProgress]) -> None:
        overview = self._progress.skill_overview(rows)
        self.skill_bar.set_stages(list(overview.stages))
        self.evidence.setText(
            f"<b>{overview.automatic:,}</b> retrieved instantly on several days · "
            f"<b>{overview.long_interval:,}</b> recalled after "
            f"{self._engine.settings.mastery_stability_days:g}+ days without a review · "
            f"<b>{overview.new_context:,}</b> recognised in a sentence they had not been "
            "seen in"
        )
        notes = []
        if overview.only_v1:
            notes.append(
                f"{overview.only_v1:,} {'word has' if overview.only_v1 == 1 else 'words have'} "
                "answers only from before this version, which asked for the meaning of "
                "the word; they count as recognised at most until asked again."
            )
        notes.append(
            "Memory comes from the schedule and skill from what each question asked, so "
            "one can be ahead of the other. Neither marks a word Known: you do."
        )
        self.skill_note.setText(" ".join(notes))

    def _fill_recent(self, rows: list[WordProgress]) -> None:
        recent = self._progress.recent(rows)
        self.recent_title.setText(f"THE LAST {recent.days} DAYS")
        self.stat_answers.set_value(recent.answers)
        rate = recent.again_rate
        self.stat_again.value_label.setText(
            f"{recent.agains:,}" + (f"  ({round(rate * 100)}%)" if rate is not None else "")
        )
        self.stat_introduced.set_value(recent.introduced)
        self.stat_long_term.set_value(recent.long_term)

    def _parameters_text(self) -> str:
        personaliser = Personaliser(self._progress.database, self._engine)
        note = personaliser.fit_note()
        if note is not None:
            when = note.get("on", "an earlier day")
            return f"In use: parameters fitted to your answers on {when}."
        ready = personaliser.readiness()
        if ready.enough:
            return (
                "In use: the published FSRS defaults. You have enough answers to fit "
                "them to your memory in Settings → Advanced."
            )
        return (
            f"In use: the published FSRS defaults. They can be fitted to your memory "
            f"after {ready.required:,} answers given on later days; {ready.usable:,} so far."
        )

    # -- words -----------------------------------------------------------------

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._filter_chosen = True
        self.filter_buttons[key].setChecked(True)
        self._fill_words()

    def _opening_filter(self) -> str:
        """Learned here if anything is, else the first group that has words."""
        for key, _text in _FILTERS:
            if key in (HARD, READY):
                continue
            if key == ALL or any(row.group.value == key for row in self._rows):
                return key
        return ALL

    def _visible_rows(self) -> list[WordProgress]:
        text = self.search.text().strip().casefold()
        rows = self._rows
        if self._filter == HARD:
            rows = [row for row in rows if row.struggling]
        elif self._filter == READY:
            rows = [row for row in rows if self._is_ready(row)]
        elif self._filter != ALL:
            rows = [row for row in rows if row.group.value == self._filter]
        if text:
            rows = [row for row in rows if text in row.word.word.casefold()]
        return rows

    def _is_ready(self, row: WordProgress) -> bool:
        """What is offered as Known: productive, and recalled after a long gap."""
        return row.word.id in getattr(self, "_suggested", ())

    def _fill_words(self) -> None:
        rows = self._visible_rows()
        table = self.words_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            word = _Item(row.word.word, row.word.word.casefold())
            word.setData(Qt.ItemDataRole.UserRole + 1, row.word.id)
            skill = row.skill
            cells = [
                word,
                _Item(row.word.cefr_level or "—", row.word.cefr_level or "Z"),
                _Item(pretty_day(row.introduced_on), row.introduced_on),
                _Item(pretty_day(row.known_on) if row.known_on else "—",
                      row.known_on or "9999"),
                _Item(str(row.answers), row.answers),
                _Item(str(row.agains), row.agains),
                _Item(remembered_for(row.stability), row.stability or 0.0),
                _Item(
                    (skill.stage.label + (" · automatic" if skill.automatic else ""))
                    if skill else "—",
                    (int(skill.stage), skill.automatic_days) if skill else (-1, 0),
                ),
            ]
            for column, cell in enumerate(cells):
                table.setItem(index, column, cell)
            if row.agains:
                cells[5].setForeground(QColor(current_palette().unknown_text))
        table.setSortingEnabled(True)
        counts = {
            Group.LEARNED.value: sum(1 for r in self._rows if r.group is Group.LEARNED),
            Group.IN_PROGRESS.value: sum(1 for r in self._rows if r.group is Group.IN_PROGRESS),
            Group.MARKED_KNOWN.value: sum(1 for r in self._rows if r.group is Group.MARKED_KNOWN),
            HARD: sum(1 for r in self._rows if r.struggling),
            READY: sum(1 for r in self._rows if self._is_ready(r)),
            ALL: len(self._rows),
        }
        for key, text in _FILTERS:
            self.filter_buttons[key].setText(f"{text}  {counts[key]:,}")
        self.words_title.setText(f"WORDS · {len(rows):,}")
        empty = not rows
        self.words_empty.setVisible(empty)
        table.setVisible(not empty)
        if empty:
            self.words_empty.setText(_empty_text(self._filter, bool(self.search.text())))

    # -- answers ---------------------------------------------------------------

    def _set_answer_filter(self, key: str) -> None:
        self._answer_filter = key
        self.answer_filter_buttons[key].setChecked(True)
        self._fill_answers()

    def _visible_answers(self) -> list[AnswerRow]:
        rows = self._answers
        if self._answer_filter == AGAIN:
            rows = [r for r in rows if r.entry.rating is Rating.AGAIN and not r.entry.undone_at]
        elif self._answer_filter == TAKEN_BACK:
            rows = [r for r in rows if r.entry.undone_at]
        text = self.answer_search.text().strip().casefold()
        if text:
            rows = [r for r in rows if text in r.word.casefold()]
        return rows

    def _fill_answers(self) -> None:
        rows = self._visible_answers()
        table = self.answers_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        palette = current_palette()
        faint = QColor(palette.text_faint)
        tones = {"known": palette.known_text, "unknown": palette.unknown_text}
        rating_tones = {Rating.AGAIN: "unknown", Rating.GOOD: "known", Rating.EASY: "known"}
        clock = self._engine.clock
        for index, row in enumerate(rows):
            entry = row.entry
            local = clock.to_local(entry.reviewed_at)
            when = _Item(f"{pretty_day(row.day)}  {local:%H:%M}", entry.reviewed_at.isoformat())
            word = _Item(row.word, row.word.casefold())
            word.setData(Qt.ItemDataRole.UserRole + 1, entry.word_id)
            when.setData(Qt.ItemDataRole.UserRole + 1, entry.word_id)
            result = row.memory_result
            cells = [
                when,
                word,
                _Item(row.asked.label if row.asked else "—", row.asked.value if row.asked else ""),
                _Item(entry.rating.label, int(entry.rating)),
                _Item(result.label if result else "—", result.value if result else ""),
                _Item(remembered_for(entry.stability_after), entry.stability_after or 0.0),
                _Item("Telegram" if entry.channel is Channel.TELEGRAM else "Desktop"),
                _Item("taken back" if entry.undone_at else "", 1 if entry.undone_at else 0),
            ]
            if entry.undone_at:
                for cell in cells:
                    cell.setForeground(faint)
                    font = cell.font()
                    font.setStrikeOut(cell is not cells[7])
                    cell.setFont(font)
            else:
                if entry.rating in rating_tones:
                    cells[3].setForeground(QColor(tones[rating_tones[entry.rating]]))
                if result in _MEMORY_TONE:
                    cells[4].setForeground(QColor(tones[_MEMORY_TONE[result]]))
            for column, cell in enumerate(cells):
                table.setItem(index, column, cell)
        table.setSortingEnabled(True)
        total = len(self._answers)
        shown = len(rows)
        self.answers_title.setText(
            f"ALL ANSWERS · {total:,}" if shown == total else f"ANSWERS · {shown:,} of {total:,}"
        )
        self.answers_message.setVisible(not rows)
        table.setVisible(bool(rows))
        self.answers_message.setText(
            "No answer matches." if total else "No answer has been given yet."
        )

    def _export_answers(self) -> None:
        maintenance = Maintenance(self._progress.database, self._engine.clock)
        default = paths.exports_dir() / f"answers-{self._engine.clock.today()}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Every Answer", str(default), "CSV files (*.csv)"
        )
        if path:
            rows = maintenance.export_review_log(Path(path))
            self.notify.emit(f"{rows:,} answers written to {Path(path).name}.")

    def _open(self, word_id: int) -> None:
        if self.history_opener is not None:
            self.history_opener(word_id)


def _verdict(calibration: Calibration) -> str:
    """What the calibration says, in one or two sentences, honest about size."""
    if calibration.reviews == 0 or calibration.predicted is None:
        return (
            "Nothing to compare yet. Each answer is checked against the forecast made "
            "at the answer before it, so this starts once words are answered twice."
        )
    predicted = round(calibration.predicted * 100)
    actual = round((calibration.actual or 0) * 100)
    if not calibration.enough:
        return (
            f"So far the schedule predicted {predicted}% and you remembered {actual}%, "
            f"but only {calibration.reviews:,} answers can be checked; about 100 are "
            f"needed before the difference means much."
        )
    difference = actual - predicted
    if abs(difference) <= 5:
        return (
            f"The schedule fits you: it predicted {predicted}% and you remembered "
            f"{actual}%, over {calibration.reviews:,} answers."
        )
    if difference < 0:
        return (
            f"You remember less than the schedule expects: {actual}% against "
            f"{predicted}% over {calibration.reviews:,} answers, so your reviews come "
            f"further apart than suits you."
        )
    return (
        f"You remember more than the schedule expects: {actual}% against {predicted}% "
        f"over {calibration.reviews:,} answers, so your reviews could come further apart."
    )


def _empty_text(group: str, searching: bool) -> str:
    if searching:
        return "No word here matches that search."
    return {
        Group.LEARNED.value: "No word has been learned here yet. A word counts once the "
        "schedule expects you to remember it for the threshold set in Settings, and you "
        "mark it Known.",
        Group.IN_PROGRESS.value: "Nothing in progress. Start a session on the Today page "
        "to learn the day's new words.",
        Group.MARKED_KNOWN.value: "No word has been marked Known by hand after being "
        "introduced.",
        HARD: "No word is giving you trouble right now.",
        READY: "No word is in long-term memory without being Known.",
        ALL: "No word has been studied here yet. Choose a study plan on the Today page.",
    }[group]
