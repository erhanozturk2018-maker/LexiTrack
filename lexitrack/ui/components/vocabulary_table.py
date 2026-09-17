"""The vocabulary table shared by List mode and the Unknown Words manager.

Looking at a word in this table never changes its learning status. Only an
explicit action — a toolbar button, or K / U / R on a selection — does. That
is the difference between *seeing* a word and *knowing* it.

The model holds ``StoredWord`` rows; a proxy handles search, status filtering
and sorting, so filtering thousands of words never touches the database.

Actions on a selection live in three places that always agree: the selection
bar, the right-click menu, and single keys on the table (K, U, R, C, M,
Delete). The table only asks; the page that owns it does the work.

The selection bar floats over the bottom of the table instead of taking a row
in the layout, so selecting a word never pushes the rows under the pointer
down. The details panel beside the table shows the current row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import IntEnum

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...models.language import language_name
from ...models.user_word_state import ReviewStatus
from ...models.word_entry import CEFR_ORDER
from ...repositories.word_repository import StoredWord
from ..theme.palette import METRICS
from .status import STATUS_NAMES, STATUS_ROLE, STATUS_SORT_ORDER, StatusDelegate, status_text
from .word_panel import WordPanel

#: Sort-key role, so text columns sort naturally and CEFR sorts A1 < C1.
SORT_ROLE = Qt.ItemDataRole.UserRole + 21
#: The word id of a row.
WORD_ID_ROLE = Qt.ItemDataRole.UserRole + 22

_PLACEHOLDER = "—"
#: Remembered between runs: whether the details panel is open.
_SETTINGS_DETAILS = "table/details"
#: Space between the floating selection bar and the bottom of the table.
_BAR_MARGIN = 14


class Column(IntEnum):
    ORDER = 0
    WORD = 1
    PART_OF_SPEECH = 2
    CEFR = 3
    STATUS = 4
    LISTS = 5
    LANGUAGE = 6


COLUMN_TITLES = {
    Column.ORDER: "#",
    Column.WORD: "WORD",
    Column.PART_OF_SPEECH: "PART OF SPEECH",
    Column.CEFR: "CEFR",
    Column.STATUS: "STATUS",
    Column.LISTS: "LISTS",
    Column.LANGUAGE: "LANGUAGE",
}


class VocabularyTableModel(QAbstractTableModel):
    """Rows of ``StoredWord``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._words: list[StoredWord] = []
        #: A list left out of the Lists column (the list being viewed).
        self.hidden_list_name: str | None = None
        self.titles = dict(COLUMN_TITLES)

    def set_words(self, words: Sequence[StoredWord]) -> None:
        self.beginResetModel()
        self._words = list(words)
        self.endResetModel()

    def update_words(self, words: Sequence[StoredWord]) -> None:
        """Replace rows in place (after a status change) without losing selection."""
        by_id = {word.id: word for word in words}
        for row, current in enumerate(self._words):
            if current.id in by_id:
                self._words[row] = by_id[current.id]
        if self._words:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._words) - 1, len(Column) - 1)
            )

    def remove_ids(self, word_ids: set[int]) -> None:
        self.set_words([word for word in self._words if word.id not in word_ids])

    def word_at(self, row: int) -> StoredWord:
        return self._words[row]

    @property
    def words(self) -> list[StoredWord]:
        return list(self._words)

    # -- Qt model interface -------------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._words)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(Column)

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.titles[Column(section)]
        return None

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if not index.isValid():
            return None
        word = self._words[index.row()]
        column = Column(index.column())

        if role == WORD_ID_ROLE:
            return word.id
        if role == STATUS_ROLE and column is Column.STATUS:
            return word.status.value
        if role == SORT_ROLE:
            return self._sort_key(word, column, index.row())
        if role == Qt.ItemDataRole.ToolTipRole:
            if column is Column.WORD and (word.definition or word.sources):
                parts = []
                if word.definition:
                    parts.append(word.definition)
                if word.sources:
                    parts.append(f"Source: {word.source_label}")
                return "\n".join(parts)
            if column is Column.LISTS and self._lists(word):
                return "\n".join(self._lists(word))
            return None
        if role == Qt.ItemDataRole.AccessibleTextRole and column is Column.STATUS:
            return STATUS_NAMES[word.status]
        if role == Qt.ItemDataRole.TextAlignmentRole and column is Column.ORDER:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if column is Column.ORDER:
            return f"{index.row() + 1:,}"
        if column is Column.WORD:
            return word.word
        if column is Column.PART_OF_SPEECH:
            return word.part_of_speech or _PLACEHOLDER
        if column is Column.CEFR:
            return word.cefr_level or _PLACEHOLDER
        if column is Column.STATUS:
            return status_text(word.status)
        if column is Column.LISTS:
            return ", ".join(self._lists(word)) or _PLACEHOLDER
        if column is Column.LANGUAGE:
            return language_name(word.language)
        return None

    def _lists(self, word: StoredWord) -> tuple[str, ...]:
        return tuple(name for name in word.lists if name != self.hidden_list_name)

    @staticmethod
    def _sort_key(word: StoredWord, column: Column, row: int):
        if column is Column.ORDER:
            return row
        if column is Column.WORD:
            return word.normalized_word
        if column is Column.PART_OF_SPEECH:
            return (word.part_of_speech is None, (word.part_of_speech or "").casefold())
        if column is Column.CEFR:
            level = word.cefr_level
            return CEFR_ORDER.index(level) if level in CEFR_ORDER else len(CEFR_ORDER)
        if column is Column.STATUS:
            return STATUS_SORT_ORDER[word.status]
        if column is Column.LISTS:
            return word.list_label.casefold()
        if column is Column.LANGUAGE:
            return language_name(word.language).casefold()
        return row


class VocabularyFilterProxy(QSortFilterProxyModel):
    """Search and status filtering over :class:`VocabularyTableModel`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._search = ""
        self._status: ReviewStatus | None = None
        self._levels: frozenset[str] = frozenset()
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(False)

    def set_search(self, text: str) -> None:
        self._search = text.strip().casefold()
        self.invalidateFilter()

    def set_status(self, status: ReviewStatus | str | None) -> None:
        # Combo-box item data comes back from Qt as a plain str.
        self._status = ReviewStatus(status) if status else None
        self.invalidateFilter()

    def set_levels(self, levels: set[str] | frozenset[str]) -> None:
        """Show only these CEFR levels; an empty set shows every level."""
        self._levels = frozenset(levels)
        self.invalidateFilter()

    def filterAcceptsRow(  # noqa: N802
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        model = self.sourceModel()
        if not isinstance(model, VocabularyTableModel):
            return True
        word = model.word_at(source_row)
        if self._status is not None and word.status != self._status:
            return False
        if self._levels and word.cefr_level not in self._levels:
            return False
        if not self._search:
            return True
        haystack = (word.normalized_word, (word.definition or "").casefold())
        return any(self._search in text for text in haystack)

    def lessThan(  # noqa: N802
        self,
        left: QModelIndex | QPersistentModelIndex,
        right: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        try:
            if a == b:
                return left.row() < right.row()
            return a < b
        except TypeError:
            return str(a) < str(b)


class VocabularyTable(QWidget):
    """Search and filters, the table, a floating selection bar and a details panel."""

    #: The user asked to set a status on these word ids.
    status_requested = Signal(list, object)
    #: Copy these word ids to a list id (``NEW_LIST_ID`` for a new list).
    copy_requested = Signal(list, int)
    #: Move these word ids to a list id (``NEW_LIST_ID`` for a new list).
    move_requested = Signal(list, int)
    #: C or M pressed: open the keyboard list picker ("copy" or "move").
    pick_requested = Signal(str)
    #: "Remove from list" for these word ids.
    remove_requested = Signal(list)
    #: "Export selection…" for these word ids.
    export_requested = Signal(list)
    #: The visible row count changed (after filtering or loading).
    count_changed = Signal(int, int)

    def __init__(
        self,
        columns: Sequence[Column] = (
            Column.ORDER, Column.WORD, Column.PART_OF_SPEECH, Column.CEFR, Column.STATUS,
        ),
        allow_remove: bool = True,
        allow_move: bool = True,
        noun: str = "words",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._allow_remove = allow_remove
        self._allow_move = allow_move
        self._noun = noun
        #: Supplies ``(list_id, name)`` targets for the given word ids.
        self._targets: Callable[[list[int]], list[tuple[int, str]]] = lambda _ids: []
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(m.space_3)

        # Filters row
        filters = QHBoxLayout()
        filters.setSpacing(m.space_2)

        self.search = QLineEdit()
        self.search.setObjectName("SearchField")
        self.search.setPlaceholderText("Search words and definitions")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(200)
        self.search.textChanged.connect(self._on_search)
        filters.addWidget(self.search, 2)

        self.status_filter = QComboBox()
        self.status_filter.setAccessibleName("Filter by status")
        self.status_filter.addItem("All statuses", None)
        for status in (ReviewStatus.NOT_REVIEWED, ReviewStatus.UNKNOWN, ReviewStatus.KNOWN):
            self.status_filter.addItem(STATUS_NAMES[status], status.value)
        self.status_filter.currentIndexChanged.connect(self._on_status_filter)
        filters.addWidget(self.status_filter, 0)

        #: Callers may insert extra filter widgets here (e.g. a list selector).
        self.extra_filters = QHBoxLayout()
        self.extra_filters.setSpacing(m.space_2)
        filters.addLayout(self.extra_filters)

        # CEFR level chips, one per level present in the words shown.
        self.level_chips: dict[str, QPushButton] = {}
        self._levels_row = QHBoxLayout()
        self._levels_row.setSpacing(4)
        filters.addSpacing(m.space_1)
        filters.addLayout(self._levels_row)

        filters.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setObjectName("Faint")
        filters.addWidget(self.count_label)

        self.details_button = QPushButton("Details")
        self.details_button.setObjectName("DetailsToggle")
        self.details_button.setCheckable(True)
        self.details_button.setProperty("size", "small")
        self.details_button.setToolTip("Show or hide word details")
        self.details_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_button.toggled.connect(self._on_details_toggled)
        filters.addWidget(self.details_button)
        layout.addLayout(filters)

        # Table and details panel
        body = QHBoxLayout()
        body.setSpacing(m.space_3)

        self.model = VocabularyTableModel(self)
        self.proxy = VocabularyFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.view = _KeyboardTableView(self)
        self.view.setModel(self.proxy)
        self.view.setItemDelegateForColumn(Column.STATUS, StatusDelegate(self.view))
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(Column.ORDER, Qt.SortOrder.AscendingOrder)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(38)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.view.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        widths = {
            Column.ORDER: 56, Column.WORD: 210, Column.PART_OF_SPEECH: 160, Column.CEFR: 64,
            Column.STATUS: 130, Column.LISTS: 120, Column.LANGUAGE: 100,
        }
        for column in Column:
            self.view.setColumnHidden(column, column not in self._columns)
            self.view.setColumnWidth(column, widths[column])
        # Keep the chosen order of columns.
        for position, column in enumerate(self._columns):
            header.moveSection(header.visualIndex(column), position)

        self.view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.view.selectionModel().currentRowChanged.connect(self._on_current_changed)
        self.view.doubleClicked.connect(self._on_open)
        self.view.key_status.connect(self._request_status)
        self.view.key_open.connect(lambda: self._on_open(self.view.currentIndex()))
        self.view.key_remove.connect(self._on_key_remove)
        self.view.key_pick.connect(self._on_key_pick)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        # The table sits in a frame with room at the bottom that opens while
        # the selection bar floats there, so no row is ever hidden under it.
        self.table_frame = QFrame()
        self.table_frame.setObjectName("TableFrame")
        self.table_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame_layout = QVBoxLayout(self.table_frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.setSpacing(0)
        frame_layout.addWidget(self.view, 1)
        self._bar_space = QWidget()
        self._bar_space.setObjectName("PanelBody")
        self._bar_space.setFixedHeight(0)
        frame_layout.addWidget(self._bar_space)
        body.addWidget(self.table_frame, 1)

        self.panel = WordPanel()
        self.panel.status_requested.connect(
            lambda word_id, status: self.status_requested.emit([word_id], status)
        )
        body.addWidget(self.panel)
        layout.addLayout(body, 1)

        self._build_selection_bar()
        self.table_frame.installEventFilter(self)

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self.view, activated=self.clear_selection)

        visible = QSettings().value(_SETTINGS_DETAILS, True) in (True, "true", "1", 1)
        self.details_button.setChecked(visible)
        self.panel.setVisible(visible)

    def _build_selection_bar(self) -> None:
        """The floating bar: count · status · transfer · more · close."""
        self.selection_bar = QFrame(self)
        self.selection_bar.setObjectName("SelectionBar")
        self.selection_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar = QHBoxLayout(self.selection_bar)
        bar.setContentsMargins(18, 6, 6, 6)
        bar.setSpacing(2)
        self.selection_count = QLabel()
        self.selection_count.setObjectName("SelectionCount")
        bar.addWidget(self.selection_count)
        bar.addSpacing(8)
        bar.addWidget(_divider())

        self.mark_known_button = _bar_button("✓  Known", "Mark Known (K)")
        self.mark_unknown_button = _bar_button("?  Unknown", "Mark Unknown (U)")
        self.reset_button = _bar_button("↺  Reset", "Reset to Not Reviewed (R)")
        self.mark_known_button.clicked.connect(lambda: self._request_status(ReviewStatus.KNOWN))
        self.mark_unknown_button.clicked.connect(
            lambda: self._request_status(ReviewStatus.UNKNOWN)
        )
        self.reset_button.clicked.connect(lambda: self._request_status(ReviewStatus.NOT_REVIEWED))
        for button in (self.mark_known_button, self.mark_unknown_button, self.reset_button):
            bar.addWidget(button)
        bar.addWidget(_divider())

        self.copy_button = _bar_button("Copy to  ▾", "Copy to another list (C)")
        self.copy_button.setMenu(self._target_menu(self.copy_button, self.copy_requested))
        bar.addWidget(self.copy_button)
        self.move_button = _bar_button("Move to  ▾", "Move to another list (M)")
        self.move_button.setMenu(self._target_menu(self.move_button, self.move_requested))
        self.move_button.setVisible(self._allow_move)
        bar.addWidget(self.move_button)

        self.more_button = _bar_button("More  ▾", "Export or remove the selection")
        more = QMenu(self.more_button)
        self.export_action = more.addAction(
            "Export Selection…", lambda: self._emit_selection(self.export_requested)
        )
        self.remove_action = more.addAction(
            "Remove from This List…", lambda: self._emit_selection(self.remove_requested)
        )
        self.remove_action.setVisible(self._allow_remove)
        self.more_button.setMenu(more)
        bar.addWidget(self.more_button)

        self.clear_button = _bar_button("✕", "Clear selection (Esc)")
        self.clear_button.setAccessibleName("Clear selection")
        self.clear_button.clicked.connect(self.clear_selection)
        bar.addWidget(self.clear_button)
        self.selection_bar.hide()

    # -- content -----------------------------------------------------------

    def set_words(self, words: Sequence[StoredWord]) -> None:
        self.model.set_words(words)
        self._fit_order_column()
        self._rebuild_level_chips()
        self._update_counts()
        self._on_current_changed()

    def _fit_order_column(self) -> None:
        """Size the # column to its widest number.

        A fixed width fits "1,013" but not "1,020": digits differ in width, and
        Qt elides what does not fit ("1,0…"). Measuring the largest number with
        a wide digit in every place keeps every row readable.
        """
        count = max(self.model.rowCount(), 1)
        widest = f"{int('8' * len(str(count))):,}"
        needed = self.view.fontMetrics().horizontalAdvance(widest) + order_cell_margin(self.view)
        self.view.setColumnWidth(Column.ORDER, max(needed, 56))

    def _rebuild_level_chips(self) -> None:
        present = {w.cefr_level for w in self.model.words if w.cefr_level in CEFR_ORDER}
        levels = [level for level in CEFR_ORDER if level in present]
        if len(levels) < 2:
            levels = []
        if levels == list(self.level_chips):
            self._on_levels()
            return
        checked = {level for level, chip in self.level_chips.items() if chip.isChecked()}
        for chip in self.level_chips.values():
            self._levels_row.removeWidget(chip)
            chip.deleteLater()
        self.level_chips = {}
        # A single level filters nothing, so chips appear only when there is a choice.
        for level in levels:
            chip = QPushButton(level)
            chip.setObjectName("LevelChip")
            chip.setCheckable(True)
            chip.setChecked(level in checked)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Show only {level} words. Choose more levels to combine them.")
            chip.toggled.connect(self._on_levels)
            self._levels_row.addWidget(chip)
            self.level_chips[level] = chip
        self._on_levels()

    def refresh_words(self, words: Sequence[StoredWord]) -> None:
        """Update rows in place, keeping selection and scroll position."""
        self.model.update_words(words)
        self._update_counts()
        self._on_current_changed()

    def remove_word_ids(self, word_ids: Sequence[int]) -> None:
        self.model.remove_ids(set(word_ids))
        self._update_counts()
        self._on_current_changed()

    def set_remove_allowed(self, allowed: bool) -> None:
        self._allow_remove = allowed
        self.remove_action.setVisible(allowed)

    def hide_status_action(self, status: ReviewStatus) -> None:
        """Leave out a status action that means nothing where the table is used."""
        {
            ReviewStatus.KNOWN: self.mark_known_button,
            ReviewStatus.UNKNOWN: self.mark_unknown_button,
            ReviewStatus.NOT_REVIEWED: self.reset_button,
        }[status].setVisible(False)
        self.panel.hide_status(status)

    def set_target_provider(self, provider: Callable[[list[int]], list[tuple[int, str]]]) -> None:
        """Tell the table which lists the selection may be copied or moved to."""
        self._targets = provider

    def set_hidden_list(self, name: str | None, title: str = "LISTS") -> None:
        """Leave ``name`` out of the Lists column, and title the column."""
        self.model.hidden_list_name = name
        self.model.titles[Column.LISTS] = title
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal, Column.LISTS, Column.LISTS)

    def selected_ids(self) -> list[int]:
        rows = self.view.selectionModel().selectedRows()
        return [int(index.data(WORD_ID_ROLE)) for index in sorted(rows, key=lambda i: i.row())]

    def select_all(self) -> None:
        self.view.selectAll()

    def clear_selection(self) -> None:
        self.view.clearSelection()

    def select_ids(self, word_ids: Sequence[int]) -> None:
        wanted = set(word_ids)
        selection = self.view.selectionModel()
        selection.clearSelection()
        first = None
        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, Column.WORD)
            if index.data(WORD_ID_ROLE) in wanted:
                selection.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                if first is None:
                    first = index
        if first is not None:
            selection.setCurrentIndex(first, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.view.scrollTo(first)

    def show_details(self, visible: bool = True) -> None:
        self.details_button.setChecked(visible)

    # -- internals ---------------------------------------------------------

    def _on_search(self, text: str) -> None:
        self.proxy.set_search(text)
        self._update_counts()

    def _on_status_filter(self) -> None:
        self.proxy.set_status(self.status_filter.currentData())
        self._update_counts()

    def _on_levels(self, *_args) -> None:
        self.proxy.set_levels(
            {level for level, chip in self.level_chips.items() if chip.isChecked()}
        )
        self._update_counts()

    def _update_counts(self) -> None:
        visible, total = self.proxy.rowCount(), self.model.rowCount()
        noun = self._noun if total != 1 else self._noun.rstrip("s")
        self.count_label.setText(
            f"{total:,} {noun}" if visible == total else f"{visible:,} of {total:,} {noun}"
        )
        self.count_changed.emit(visible, total)
        self._on_selection_changed()

    def _on_selection_changed(self, *_args) -> None:
        count = len(self.view.selectionModel().selectedRows())
        self.selection_count.setText(f"{count:,} selected")
        showing = count > 0
        self.selection_bar.setVisible(showing)
        if showing:
            self._place_selection_bar()
        # Keep the last rows reachable above the bar instead of under it.
        self._bar_space.setFixedHeight(
            self.selection_bar.sizeHint().height() + 2 * _BAR_MARGIN if showing else 0
        )

    def _place_selection_bar(self) -> None:
        bar = self.selection_bar
        bar.adjustSize()
        area = self.table_frame.geometry()
        x = area.left() + (area.width() - bar.width()) // 2
        # In a narrow window the bar may be wider than the table; keep it on screen.
        x = min(max(x, 0), max(self.width() - bar.width(), 0))
        y = area.bottom() - bar.height() - _BAR_MARGIN + 1
        bar.move(x, max(y, 0))
        bar.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.table_frame and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Move
        ):
            if not self.selection_bar.isHidden():
                self._place_selection_bar()
        return super().eventFilter(watched, event)

    def _on_current_changed(self, *_args) -> None:
        index = self.view.currentIndex()
        if not index.isValid():
            rows = self.view.selectionModel().selectedRows()
            index = rows[0] if rows else index
        if not index.isValid():
            self.panel.show_word(None)
            return
        source = self.proxy.mapToSource(index)
        self.panel.show_word(self.model.word_at(source.row()))

    def _on_details_toggled(self, visible: bool) -> None:
        self.panel.setVisible(visible)
        QSettings().setValue(_SETTINGS_DETAILS, visible)

    def _request_status(self, status: ReviewStatus) -> None:
        ids = self.selected_ids()
        if ids:
            self.status_requested.emit(ids, status)

    def _on_open(self, index: QModelIndex) -> None:
        if index.isValid():
            self.show_details(True)

    def _on_key_remove(self) -> None:
        ids = self.selected_ids()
        if ids and self._allow_remove:
            self.remove_requested.emit(ids)

    def _on_key_pick(self, kind: str) -> None:
        if not self.selected_ids() or (kind == "move" and not self._allow_move):
            return
        self.pick_requested.emit(kind)

    def _emit_selection(self, signal) -> None:
        ids = self.selected_ids()
        if ids:
            signal.emit(ids)

    def _target_menu(self, owner: QWidget, signal) -> QMenu:
        menu = QMenu(owner)
        menu.aboutToShow.connect(lambda: self.fill_targets(menu, signal))
        return menu

    def fill_targets(self, menu: QMenu, signal) -> None:
        """Fill ``menu`` with the lists the selection can go to, plus New List."""
        from ..word_transfer import NEW_LIST_ID

        menu.clear()
        ids = self.selected_ids()
        targets = self._targets(ids) if ids else []
        for list_id, name in targets:
            menu.addAction(name, lambda i=list_id: signal.emit(self.selected_ids(), i))
        if not targets:
            menu.addAction("No other list can take these words").setEnabled(False)
        menu.addSeparator()
        menu.addAction("New List…", lambda: signal.emit(self.selected_ids(), NEW_LIST_ID))

    def _show_context_menu(self, pos: QPoint) -> None:
        index = self.view.indexAt(pos)
        if not index.isValid():
            return
        # Right-clicking outside the selection acts on that row alone, as in a
        # file manager; inside the selection it acts on the whole selection.
        if not self.view.selectionModel().isRowSelected(index.row(), index.parent()):
            self.view.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.view.setCurrentIndex(index)
        self.build_context_menu().exec(self.view.viewport().mapToGlobal(pos))

    def build_context_menu(self) -> QMenu:
        menu = QMenu(self.view)
        for status, text, key, button in (
            (ReviewStatus.KNOWN, "Mark Known", "K", self.mark_known_button),
            (ReviewStatus.UNKNOWN, "Mark Unknown", "U", self.mark_unknown_button),
            (ReviewStatus.NOT_REVIEWED, "Reset to Not Reviewed", "R", self.reset_button),
        ):
            # isHidden is the button's own flag: switched off for this table,
            # regardless of whether the bar around it is showing.
            if button.isHidden():
                continue
            action = menu.addAction(text, lambda s=status: self._request_status(s))
            action.setShortcut(QKeySequence(key))
        menu.addSeparator()
        copy_menu = menu.addMenu("Copy to")
        copy_menu.aboutToShow.connect(lambda: self.fill_targets(copy_menu, self.copy_requested))
        if self._allow_move:
            move_menu = menu.addMenu("Move to")
            move_menu.aboutToShow.connect(
                lambda: self.fill_targets(move_menu, self.move_requested)
            )
        if self._allow_remove:
            remove = menu.addAction(
                "Remove from This List…", lambda: self._emit_selection(self.remove_requested)
            )
            remove.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        menu.addAction("Export…", lambda: self._emit_selection(self.export_requested))
        if len(self.selected_ids()) == 1:
            menu.addSeparator()
            details = menu.addAction("Word Details", lambda: self.show_details(True))
            details.setShortcut(QKeySequence(Qt.Key.Key_Return))
        return menu


def order_cell_margin(view: QTableView) -> int:
    """Horizontal space a cell takes from its text.

    The stylesheet pads cells by 10px each side, and the style adds its own
    focus-frame margin on top. Leaving the latter out is what made "1,020"
    look as if it fitted when it did not. A little extra leaves room for the
    header's sort arrow.
    """
    frame = view.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, view) + 1
    return 2 * 10 + 2 * frame + 12


class _KeyboardTableView(QTableView):
    """A table that turns single keys into actions on the selection.

    K / U / R set status, C / M copy or move, Enter opens, Delete removes.
    Arrows, Shift+arrows, Page Up/Down, Home/End and Ctrl+A keep their
    standard Qt behaviour.
    """

    key_status = Signal(object)
    key_open = Signal()
    key_remove = Signal()
    key_pick = Signal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if modifiers == Qt.KeyboardModifier.NoModifier:
            key = event.key()
            if key == Qt.Key.Key_K:
                self.key_status.emit(ReviewStatus.KNOWN)
                return
            if key == Qt.Key.Key_U:
                self.key_status.emit(ReviewStatus.UNKNOWN)
                return
            if key == Qt.Key.Key_R:
                self.key_status.emit(ReviewStatus.NOT_REVIEWED)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.key_open.emit()
                return
            if key == Qt.Key.Key_Delete:
                self.key_remove.emit()
                return
            if key == Qt.Key.Key_C:
                self.key_pick.emit("copy")
                return
            if key == Qt.Key.Key_M:
                self.key_pick.emit("move")
                return
        super().keyPressEvent(event)


def _bar_button(text: str, tooltip: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("BarButton")
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    return button


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("BarDivider")
    line.setFixedSize(1, 18)
    return line
