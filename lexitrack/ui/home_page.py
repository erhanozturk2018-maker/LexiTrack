"""Lists: where your words stand, and every list, one click from its words.

Two blocks, as on the phone:

1. **Overview** — four numbers for the whole vocabulary; the Unknown tile
   opens the Unknown Words manager.
2. **Your lists** — a card per list. A click (or Enter) opens it: its words,
   and *Know or don't know?* to sort them. Right-click (or the Menu key /
   Shift+F10) for everything else. Arrow keys move between cards.

There is no "continue" banner: the day's learning is on Today, and sorting a
list is done inside it, so the page has one job — choosing a list.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
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

from ..services.vocabulary_service import VocabularyService
from .components.cards import ListCard, ModeSwitch, StatTile
from .empty_state import WelcomeState
from .list_actions import ListActions
from .theme.palette import METRICS
from .widgets import PageColumn

_GRID_COLUMNS = 3


class HomePage(QWidget):
    """The start screen."""

    #: Open a list in a mode ("list": its words; "flashcard": Know or don't know?).
    open_list = Signal(int, str)
    #: The list opened last, to come back to.
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

        title_row = QHBoxLayout()
        title_row.addWidget(_label("Lists", "PageTitle"))
        title_row.addStretch(1)
        new_list = QPushButton("New list")
        new_list.setToolTip("Create an empty list (Ctrl+N)")
        new_list.clicked.connect(self._actions.create_list)
        title_row.addWidget(new_list)
        import_button = QPushButton("Import")
        import_button.setToolTip("Import PDF, CSV or JSON files (Ctrl+O)")
        import_button.clicked.connect(lambda: self._actions.import_into(None))
        title_row.addWidget(import_button)
        layout.addLayout(title_row)

        # 1. overview
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

        # 2. lists
        layout.addSpacing(m.space_1)
        layout.addWidget(_label("YOUR LISTS", "SectionTitle"))

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
            card.clicked.connect(self._open)
            card.navigate.connect(lambda dx, dy, c=card: self._move_focus(c, dx, dy))
            card.activated.connect(self._open)
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
        for list_id, card in self._cards.items():
            card.set_current(list_id == self._current_id)

    @property
    def current_id(self) -> int | None:
        return self._current_id

    # -- interaction -------------------------------------------------------

    def _move_focus(self, card: ListCard, dx: int, dy: int) -> None:
        cards = list(self._cards.values())
        target = cards.index(card) + dx + dy * _GRID_COLUMNS
        if 0 <= target < len(cards):
            cards[target].setFocus()

    def _open(self, list_id: int, mode: str = ModeSwitch.LIST) -> None:
        """A list opens on its words; sorting them is one switch away."""
        self._current_id = list_id
        self.current_changed.emit(list_id)
        self.open_list.emit(list_id, mode)

    def _card_menu(self, card: ListCard, pos: QPoint) -> None:
        list_id = card.list_id
        menu = QMenu(self)
        menu.addAction("Open", lambda: self._open(list_id))
        menu.addAction("Know or don't know?", lambda: self._open(list_id, ModeSwitch.FLASHCARD))
        menu.addSeparator()
        self._actions.fill_menu(menu, list_id)
        menu.exec(card.mapToGlobal(pos))


def _label(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    return label
