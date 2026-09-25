"""Home: continue where you left off, see where you stand, pick a list.

Three blocks, in order of how often they are wanted:

1. **Continue learning** — the current list, its progress and one button.
2. **Overview** — four numbers for the whole vocabulary; the Unknown tile
   opens the Unknown Words manager.
3. **Your lists** — a card per list. Click to make it current, double-click or
   Enter to open it, right-click (or the Menu key / Shift+F10) for everything
   else. Arrow keys move between cards; Up from the top row returns to
   Continue, Down from Continue enters the grid.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.language import UNDETERMINED
from ..services.vocabulary_service import VocabularyService
from .components.cards import ListCard, ModeSwitch, SegmentedProgress, StatTile
from .empty_state import WelcomeState
from .list_actions import ListActions
from .theme.palette import METRICS
from .widgets import PageColumn

_GRID_COLUMNS = 3


class HomePage(QWidget):
    """The start screen."""

    #: Open a list for review in a mode ("flashcard" / "list").
    open_list = Signal(int, str)
    #: The user picked a list as current without opening it.
    current_changed = Signal(int)
    show_unknown = Signal()

    def __init__(
        self, service: VocabularyService, actions: ListActions, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._actions = actions
        self._current_id: int | None = None
        self._cards: dict[int, ListCard] = {}
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self.welcome = WelcomeState()
        self.welcome.import_requested.connect(lambda: self._actions.import_into(None))
        self.welcome.create_list_requested.connect(self._actions.create_list)
        self._stack.addWidget(self.welcome)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(m.space_7, m.space_5, m.space_7, m.space_6)
        PageColumn(content, layout)
        layout.setSpacing(m.space_4)

        # 1. continue
        self.continue_panel = QFrame()
        self.continue_panel.setObjectName("Panel")
        self.continue_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel = QHBoxLayout(self.continue_panel)
        panel.setContentsMargins(m.space_5, m.space_4, m.space_5, m.space_4)
        panel.setSpacing(m.space_5)
        text = QVBoxLayout()
        text.setSpacing(6)
        text.addWidget(_label("CONTINUE LEARNING", "SectionTitle"))
        name_row = QHBoxLayout()
        self.current_name = _label("", "PageTitle")
        name_row.addWidget(self.current_name)
        self.current_language = _label("", "LanguageTag")
        name_row.addWidget(self.current_language, 0, Qt.AlignmentFlag.AlignVCenter)
        name_row.addStretch(1)
        text.addLayout(name_row)
        self.current_detail = _label("", "Muted")
        text.addWidget(self.current_detail)
        self.current_bar = SegmentedProgress()
        text.addWidget(self.current_bar)
        panel.addLayout(text, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(m.space_2)
        self.continue_button = QPushButton("Continue  →")
        self.continue_button.setProperty("variant", "primary")
        self.continue_button.setMinimumSize(170, 40)
        self.continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_button.clicked.connect(self._continue)
        self.continue_button.installEventFilter(self)
        buttons.addWidget(self.continue_button)
        self.mode_switch = ModeSwitch()
        buttons.addWidget(self.mode_switch, 0, Qt.AlignmentFlag.AlignHCenter)
        panel.addLayout(buttons)
        layout.addWidget(self.continue_panel)

        # 2. overview
        layout.addSpacing(m.space_2)
        layout.addWidget(_label("OVERVIEW", "SectionTitle"))

        tiles = QHBoxLayout()
        tiles.setSpacing(m.space_3)
        self.total_tile = StatTile("Total words")
        self.known_tile = StatTile("Known", "known")
        self.unknown_tile = StatTile("Unknown", "unknown", link="Open Unknown Words")
        self.unknown_tile.clicked.connect(self.show_unknown.emit)
        self.remaining_tile = StatTile("Not reviewed")
        for tile in (self.total_tile, self.known_tile, self.unknown_tile, self.remaining_tile):
            tiles.addWidget(tile)
        layout.addLayout(tiles)

        # 3. lists
        lists_header = QHBoxLayout()
        lists_header.addWidget(_label("YOUR LISTS", "SectionTitle"))
        lists_header.addStretch(1)
        new_list = QPushButton("New list")
        new_list.setToolTip("Create an empty list (Ctrl+N)")
        new_list.clicked.connect(self._actions.create_list)
        lists_header.addWidget(new_list)
        import_button = QPushButton("Import")
        import_button.setToolTip("Import PDF or JSON files (Ctrl+O)")
        import_button.clicked.connect(lambda: self._actions.import_into(None))
        lists_header.addWidget(import_button)
        layout.addSpacing(m.space_1)
        layout.addLayout(lists_header)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(m.space_3)
        self.grid.setVerticalSpacing(m.space_3)
        layout.addLayout(self.grid)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        self._stack.addWidget(scroll)

    # -- content -----------------------------------------------------------

    def refresh(self, current_id: int | None, mode: str) -> None:
        lists = self._service.lists()
        if not lists:
            self._stack.setCurrentWidget(self.welcome)
            return
        self._stack.setCurrentIndex(1)

        progress = self._service.get_progress()
        self.total_tile.set_value(progress.total)
        self.known_tile.set_value(progress.known)
        self.unknown_tile.set_value(progress.unknown)
        self.remaining_tile.set_value(progress.remaining)

        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                # Hidden now, deleted later: until the event loop runs, a card
                # waiting for deletion would still be painted under the new ones.
                item.widget().hide()
                item.widget().deleteLater()
        self._cards = {}
        for index, lst in enumerate(lists):
            card = ListCard(lst)
            card.clicked.connect(self._select)
            card.navigate.connect(lambda dx, dy, c=card: self._move_focus(c, dx, dy))
            card.activated.connect(
                lambda list_id: self.open_list.emit(list_id, self.mode_switch.mode)
            )
            card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, c=card: self._card_menu(c, pos)
            )
            self.grid.addWidget(card, index // _GRID_COLUMNS, index % _GRID_COLUMNS)
            self._cards[lst.id] = card
        for column in range(_GRID_COLUMNS):
            self.grid.setColumnStretch(column, 1)

        ids = [lst.id for lst in lists]
        self._current_id = current_id if current_id in ids else ids[0]
        self.mode_switch.set_mode(mode)
        self._show_current()
        if self.isVisible():
            self.continue_button.setFocus()

    def _show_current(self) -> None:
        current = self._service.get_list(self._current_id) if self._current_id else None
        for list_id, card in self._cards.items():
            card.set_current(list_id == self._current_id)
        if current is None:
            return
        p = current.progress
        self.current_name.setText(current.name)
        self.current_language.setVisible(current.language != UNDETERMINED)
        self.current_language.setText(current.language.upper())
        self.current_language.setToolTip(current.language_name)
        if p.total == 0:
            detail = "This list is empty. Add words or import a file into it."
            self.continue_button.setText("Open list  →")
        elif p.remaining == 0:
            detail = f"All {p.total:,} words reviewed · {p.known:,} known · {p.unknown:,} to learn"
            self.continue_button.setText("Open list  →")
        else:
            detail = (
                f"{p.reviewed:,} of {p.total:,} reviewed · {p.remaining:,} remaining · "
                f"{p.unknown:,} to learn"
            )
            self.continue_button.setText("Continue  →")
        self.current_detail.setText(detail)
        self.current_bar.set_progress(p)

    @property
    def current_id(self) -> int | None:
        return self._current_id

    # -- interaction -------------------------------------------------------

    def _move_focus(self, card: ListCard, dx: int, dy: int) -> None:
        cards = list(self._cards.values())
        index = cards.index(card)
        if dy < 0 and index < _GRID_COLUMNS:
            self.continue_button.setFocus()
            return
        target = index + dx + dy * _GRID_COLUMNS
        if 0 <= target < len(cards):
            cards[target].setFocus()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            watched is self.continue_button
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Down
            and self._cards
        ):
            current = self._cards.get(self._current_id) or next(iter(self._cards.values()))
            current.setFocus()
            return True
        return super().eventFilter(watched, event)

    def _select(self, list_id: int) -> None:
        self._current_id = list_id
        self._show_current()
        self.current_changed.emit(list_id)

    def _continue(self) -> None:
        if self._current_id is None:
            return
        current = self._service.get_list(self._current_id)
        mode = self.mode_switch.mode
        if current is not None and current.progress.remaining == 0:
            mode = ModeSwitch.LIST
        self.open_list.emit(self._current_id, mode)

    def _card_menu(self, card: ListCard, pos: QPoint) -> None:
        list_id = card.list_id
        menu = QMenu(self)
        menu.addAction(
            "Review as Flashcards", lambda: self.open_list.emit(list_id, ModeSwitch.FLASHCARD)
        )
        menu.addAction("Open as List", lambda: self.open_list.emit(list_id, ModeSwitch.LIST))
        menu.addSeparator()
        self._actions.fill_menu(menu, list_id)
        menu.exec(card.mapToGlobal(pos))


def _label(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    return label
