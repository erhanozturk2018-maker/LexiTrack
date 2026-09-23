"""Progress: what has been learned through the study plan, and how.

The Study page is about today; this page is about everything since the first
word was introduced. It keeps the two kinds of Known apart - words learned
here and words known before - and shows the record the engine keeps rather
than a summary of it: every studied word in a table, every answer in another,
each one opening the word's full history.

Top to bottom, in the order the questions come:

1. How much? Four tiles: learned here, in progress, marked Known by hand,
   known before the plan.
2. Where are the words now? One bar from "not answered yet" to "Known".
3. How fast, and does the schedule fit me? Running totals over time, and
   the scheduler's forecasts against the answers that followed them.
4. Which words, and which answers? Two tables, both complete.

Everything is read through :class:`ProgressService`; the page writes nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
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

from ..models.srs import Channel, Rating
from ..services.learning_service import LearningService
from ..services.progress import Calibration, Group, ProgressService, WordProgress
from .components.cards import StatTile
from .components.progress_charts import CalibrationChart, PipelineBar, TimelineChart
from .components.word_history import pretty_day, remembered_for
from .theme import current_palette
from .theme.palette import METRICS

ALL, HARD = "all", "hard"

#: The table filters, in the order they are offered.
_FILTERS = (
    (Group.LEARNED.value, "Learned here"),
    (Group.IN_PROGRESS.value, "In progress"),
    (Group.MARKED_KNOWN.value, "Marked Known"),
    (HARD, "Hard for you"),
    (ALL, "All"),
)


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
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setMinimumHeight(360)
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


class ProgressPage(QWidget):
    """Everything learned here, and every answer given."""

    #: The user asked for Settings, from the note about testing predictions.
    settings_requested = Signal()

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
        self._filter = Group.LEARNED.value
        #: Until the user picks a filter, the page opens on one with words in it.
        self._filter_chosen = False
        #: Opens a word's history; set by the main window.
        self.history_opener: Callable[[int], None] | None = None
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("PanelBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(m.space_6, m.space_5, m.space_6, m.space_6)
        layout.setSpacing(m.space_5)
        scroll.setWidget(body)
        outer.addWidget(scroll)

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

        # 1. how much
        tiles = QHBoxLayout()
        tiles.setSpacing(m.space_3)
        self.tile_learned = StatTile("learned here", tone="known")
        self.tile_progress = StatTile("in progress")
        self.tile_marked = StatTile("marked Known by hand")
        self.tile_before = StatTile("known before your plan")
        for tile in (self.tile_learned, self.tile_progress, self.tile_marked, self.tile_before):
            tiles.addWidget(tile, 1)
        tiles_holder = QVBoxLayout()
        tiles_holder.setSpacing(m.space_2)
        tiles_holder.addLayout(tiles)
        self.totals_line = _label("", "Faint")
        tiles_holder.addWidget(self.totals_line)
        layout.addLayout(tiles_holder)

        # 2. where the words are
        where, where_layout, _, _ = _section("WHERE YOUR WORDS ARE")
        panel, panel_layout = _panel()
        self.pipeline = PipelineBar()
        panel_layout.addWidget(self.pipeline)
        panel_layout.addWidget(
            _label(
                "How long each word is expected to be remembered, from its last answer. "
                "A word counts as Known once that passes the threshold in Settings.",
                "Faint",
                wrap=True,
            )
        )
        where_layout.addWidget(panel)
        layout.addWidget(where)

        # 3. how fast, and how well the forecasts fit
        pair = QHBoxLayout()
        pair.setSpacing(m.space_4)
        over, over_layout, _, _ = _section("OVER TIME")
        panel, panel_layout = _panel()
        self.timeline = TimelineChart()
        panel_layout.addWidget(self.timeline)
        over_layout.addWidget(panel, 1)
        pair.addWidget(over, 1)

        fit, fit_layout, _, _ = _section("DOES THE SCHEDULE FIT YOU?")
        panel, panel_layout = _panel()
        self.calibration = CalibrationChart()
        panel_layout.addWidget(self.calibration)
        self.calibration_note = _label("", None, wrap=True)
        self.calibration_note.setObjectName("CalibrationNote")
        panel_layout.addWidget(self.calibration_note)
        self.testing_note = _label("", "Faint", wrap=True)
        panel_layout.addWidget(self.testing_note)
        self.settings_link = QPushButton("Open Learning settings →")
        self.settings_link.setObjectName("LinkButton")
        self.settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_link.clicked.connect(self.settings_requested.emit)
        panel_layout.addWidget(self.settings_link, 0, Qt.AlignmentFlag.AlignLeft)
        fit_layout.addWidget(panel, 1)
        pair.addWidget(fit, 1)
        # Both columns take the height of the taller, so their titles line up.
        layout.addLayout(pair)

        # 4a. the words
        words, words_layout, words_header, self.words_title = _section("WORDS")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a word")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._fill_words)
        words_header.addWidget(self.search)
        filters = QFrame()
        filters.setObjectName("ModeSwitch")
        filters.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        filter_row = QHBoxLayout(filters)
        filter_row.setContentsMargins(3, 3, 3, 3)
        filter_row.setSpacing(2)
        self._filter_group = QButtonGroup(self)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key, text in _FILTERS:
            button = QPushButton(text)
            button.setObjectName("ModeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self._filter_group.addButton(button)
            filter_row.addWidget(button)
            self.filter_buttons[key] = button
        self.filter_buttons[self._filter].setChecked(True)
        words_layout.addWidget(filters, 0, Qt.AlignmentFlag.AlignLeft)
        self.words_table = _Table(
            ["Word", "CEFR", "Introduced", "Known", "Days to Known", "Answers", "Again",
             "Remembered for"],
            sort_column=2,
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

        # 4b. every answer
        answers, answers_layout, _, self.answers_title = _section("ALL ANSWERS")
        answers_layout.addWidget(
            _label(
                "Every answer from Study and Telegram, newest first, including answers "
                "taken back with Undo; those are kept for the record and left out of "
                "every count. The same list is in Settings → Data → Export history.",
                "Faint",
                wrap=True,
            )
        )
        self.answers_table = _Table(
            ["When", "Word", "Answer", "Change", "Remembered for", "Where", ""]
        )
        self.answers_table.open_word.connect(self._open)
        answers_layout.addWidget(self.answers_table)
        layout.addWidget(answers)
        layout.addStretch(1)

    # -- content -------------------------------------------------------------

    def refresh(self) -> None:
        rows = self._progress.words()
        self._rows = rows
        summary = self._progress.summary(rows)
        self.tile_learned.set_value(summary.learned)
        self.tile_progress.set_value(summary.in_progress)
        self.tile_marked.set_value(summary.marked_known)
        self.tile_before.set_value(summary.known_before)
        taken = (
            f" · {summary.taken_back:,} taken back" if summary.taken_back else ""
        )
        self.totals_line.setText(
            f"{summary.answers:,} answers given{taken} · "
            f"{summary.struggling:,} words you find hard"
        )
        self.pipeline.set_stages(self._progress.pipeline(rows))
        self.timeline.set_days(self._progress.timeline(rows))
        calibration = self._progress.calibration()
        self.calibration.set_calibration(calibration)
        self.calibration_note.setText(_verdict(calibration))
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
        self._fill_answers()

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._filter_chosen = True
        self._fill_words()

    def _opening_filter(self) -> str:
        """Learned here if anything is, else the first group that has words."""
        for key, _text in _FILTERS:
            if key == HARD:
                continue
            if key == ALL or any(row.group.value == key for row in self._rows):
                return key
        return ALL

    def _visible_rows(self) -> list[WordProgress]:
        text = self.search.text().strip().casefold()
        rows = self._rows
        if self._filter == HARD:
            rows = [row for row in rows if row.struggling]
        elif self._filter != ALL:
            rows = [row for row in rows if row.group.value == self._filter]
        if text:
            rows = [row for row in rows if text in row.word.word.casefold()]
        return rows

    def _fill_words(self) -> None:
        rows = self._visible_rows()
        table = self.words_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            word = _Item(row.word.word, row.word.word.casefold())
            word.setData(Qt.ItemDataRole.UserRole + 1, row.word.id)
            cells = [
                word,
                _Item(row.word.cefr_level or "—", row.word.cefr_level or "Z"),
                _Item(pretty_day(row.introduced_on), row.introduced_on),
                _Item(pretty_day(row.known_on) if row.known_on else "—",
                      row.known_on or "9999"),
                _Item(str(row.days_to_known) if row.days_to_known is not None else "—",
                      row.days_to_known if row.days_to_known is not None else 10**6),
                _Item(str(row.answers), row.answers),
                _Item(str(row.agains), row.agains),
                _Item(remembered_for(row.stability), row.stability or 0.0),
            ]
            for column, cell in enumerate(cells):
                table.setItem(index, column, cell)
            if row.agains:
                cells[6].setForeground(QColor(current_palette().unknown_text))
        table.setSortingEnabled(True)
        counts = {
            Group.LEARNED.value: sum(1 for r in self._rows if r.group is Group.LEARNED),
            Group.IN_PROGRESS.value: sum(1 for r in self._rows if r.group is Group.IN_PROGRESS),
            Group.MARKED_KNOWN.value: sum(1 for r in self._rows if r.group is Group.MARKED_KNOWN),
            HARD: sum(1 for r in self._rows if r.struggling),
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

    def _fill_answers(self) -> None:
        rows = self._progress.answers()
        table = self.answers_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        faint = QColor(current_palette().text_faint)
        tones = {
            Rating.AGAIN: current_palette().unknown_text,
            Rating.GOOD: current_palette().known_text,
            Rating.EASY: current_palette().known_text,
        }
        clock = self._engine.clock
        for index, row in enumerate(rows):
            entry = row.entry
            local = clock.to_local(entry.reviewed_at)
            when = _Item(f"{pretty_day(row.day)}  {local:%H:%M}", entry.reviewed_at.isoformat())
            word = _Item(row.word, row.word.casefold())
            word.setData(Qt.ItemDataRole.UserRole + 1, entry.word_id)
            when.setData(Qt.ItemDataRole.UserRole + 1, entry.word_id)
            change = ""
            if entry.state_before and entry.state_after and entry.state_before != entry.state_after:
                change = f"{entry.state_before.value} → {entry.state_after.value}"
            cells = [
                when,
                word,
                _Item(entry.rating.label, int(entry.rating)),
                _Item(change or "—", change),
                _Item(remembered_for(entry.stability_after), entry.stability_after or 0.0),
                _Item("Telegram" if entry.channel is Channel.TELEGRAM else "Desktop"),
                _Item("taken back" if entry.undone_at else "", 1 if entry.undone_at else 0),
            ]
            if entry.rating in tones and not entry.undone_at:
                cells[2].setForeground(QColor(tones[entry.rating]))
            if entry.undone_at:
                for cell in cells:
                    cell.setForeground(faint)
                    font = cell.font()
                    font.setStrikeOut(cell is not cells[6])
                    cell.setFont(font)
            for column, cell in enumerate(cells):
                table.setItem(index, column, cell)
        table.setSortingEnabled(True)
        self.answers_title.setText(f"ALL ANSWERS · {len(rows):,}")

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
        "schedule expects you to remember it for the threshold set in Settings.",
        Group.IN_PROGRESS.value: "Nothing in progress. Confirm the day's new words on the "
        "Study page to start.",
        Group.MARKED_KNOWN.value: "No word has been marked Known by hand after being "
        "introduced.",
        HARD: "No word is giving you trouble right now.",
        ALL: "No word has been studied here yet. Choose a study plan on the Study page.",
    }[group]
