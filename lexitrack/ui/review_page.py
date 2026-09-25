"""Review: one list, as flashcards or as a table.

The strip across the top always answers "which list am I reviewing?" — the
list's name is the largest text on it, and it doubles as the control for
switching lists. Where the words came from is not shown here; that is
provenance, and it lives on the card ("Source: …") and in the details panel.

Switching between Flashcard and List never changes the list, and never loses
the flashcard session's Backspace history: the session belongs to the page,
not to the mode.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import LexiTrackError
from ..models.language import UNDETERMINED
from ..models.user_word_state import ReviewStatus
from ..services.review_session import ReviewSession
from ..services.vocabulary_service import VocabularyService
from .components.cards import ModeSwitch
from .components.toast import Toast
from .components.vocabulary_table import Column, VocabularyTable
from .dialogs import confirm
from .empty_state import EmptyState
from .list_actions import ListActions
from .progress_widget import StatsBar
from .review_widget import ReviewWidget
from .theme.palette import METRICS
from .word_transfer import ListPicker, WordTransfer


class FinishedState(EmptyState):
    """The end of a list. ← or Backspace still steps back into it."""

    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            title="List complete",
            body="",
            primary_text="Open as list",
            secondary_text="Export",
            glyph="✓",
            parent=parent,
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Left):
            self.back_requested.emit()
            return
        super().keyPressEvent(event)


class ReviewPage(QWidget):
    """Flashcard and List modes for the current list."""

    #: The list being reviewed no longer exists.
    list_missing = Signal()
    #: The user switched list or mode here; remember it.
    context_changed = Signal(int, str)

    def __init__(
        self, service: VocabularyService, actions: ListActions, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._actions = actions
        self.list_id: int | None = None
        self.session: ReviewSession | None = None
        #: The list whose words the table currently holds.
        self._table_list_id: int | None = None
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        strip = QFrame()
        row = QHBoxLayout(strip)
        row.setContentsMargins(m.space_6, m.space_4, m.space_6, m.space_2)
        row.setSpacing(m.space_3)

        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(_label("SORTING", "ContextLabel"))
        self.list_button = QPushButton()
        self.list_button.setObjectName("ContextListButton")
        self.list_button.setToolTip("Switch list (Ctrl+L)")
        self.list_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_menu = QMenu(self.list_button)
        self.list_menu.aboutToShow.connect(self._fill_list_menu)
        self.list_button.setMenu(self.list_menu)
        names.addWidget(self.list_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addLayout(names)

        self.language_tag = _label("", "LanguageTag")
        row.addWidget(self.language_tag, 0, Qt.AlignmentFlag.AlignBottom)
        row.addSpacing(m.space_2)

        self.actions_button = QPushButton("List actions")
        self.actions_button.setProperty("variant", "ghost")
        self.actions_button.setProperty("size", "small")
        self.actions_menu = QMenu(self.actions_button)
        self.actions_menu.aboutToShow.connect(self._fill_actions_menu)
        self.actions_button.setMenu(self.actions_menu)
        row.addWidget(self.actions_button, 0, Qt.AlignmentFlag.AlignBottom)

        row.addStretch(1)
        self.mode_switch = ModeSwitch()
        self.mode_switch.mode_changed.connect(self.set_mode)
        row.addWidget(self.mode_switch, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(strip)

        # Two things in the app are called reviewing. This one sorts; the
        # Study tab schedules. Saying so here is what stops an answer on
        # this page being mistaken for a spaced-repetition review.
        self.purpose = _label(
            "Sort words into Known and Unknown. The words you mark Unknown are "
            "the ones Today teaches; nothing on this page is scheduled.",
            "Faint",
        )
        self.purpose.setWordWrap(True)
        self.purpose.setContentsMargins(m.space_6, 0, m.space_6, m.space_2)
        layout.addWidget(self.purpose)

        self.modes = QStackedWidget()
        layout.addWidget(self.modes, 1)

        # Flashcard mode
        self.flashcard_stack = QStackedWidget()
        self.flashcard = ReviewWidget()
        self.flashcard.answered.connect(lambda known: self._step(lambda s: s.answer(known)))
        self.flashcard.back_requested.connect(lambda: self._step(ReviewSession.back))
        self.flashcard.forward_requested.connect(lambda: self._step(ReviewSession.forward))
        self.flashcard.repeat_requested.connect(
            lambda: self._step(ReviewSession.repeat_last_answer)
        )
        self.flashcard.reset_requested.connect(lambda: self._step(ReviewSession.reset_current))
        self.flashcard_stack.addWidget(self.flashcard)

        self.finished = FinishedState()
        self.finished.back_requested.connect(lambda: self._step(ReviewSession.back))
        if self.finished.primary_button is not None:
            self.finished.primary_button.clicked.connect(lambda: self.set_mode(ModeSwitch.LIST))
        if self.finished.secondary_button is not None:
            self.finished.secondary_button.clicked.connect(self._export)
        self.flashcard_stack.addWidget(self.finished)

        self.empty = EmptyState(
            title="This list has no words yet",
            body="Add words by typing them in, or import a PDF or JSON file into this list.",
            primary_text="Add words",
            secondary_text="Import into list",
        )
        if self.empty.primary_button is not None:
            self.empty.primary_button.clicked.connect(self._add_words)
        if self.empty.secondary_button is not None:
            self.empty.secondary_button.clicked.connect(self._import_into)
        self.flashcard_stack.addWidget(self.empty)
        self.modes.addWidget(self.flashcard_stack)

        # List mode
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(m.space_6, m.space_2, m.space_6, m.space_4)
        self.table = VocabularyTable(
            columns=(
                Column.ORDER, Column.WORD, Column.PART_OF_SPEECH, Column.CEFR, Column.STATUS,
                Column.LISTS,
            )
        )
        add_words = QPushButton("Add words")
        add_words.clicked.connect(self._add_words)
        self.table.extra_filters.addWidget(add_words)
        self.table.status_requested.connect(self._set_status)
        self.table.copy_requested.connect(self._copy_to)
        self.table.move_requested.connect(self._move_to)
        self.table.pick_requested.connect(self._pick_transfer)
        self.table.set_target_provider(self._transfer_targets)
        self.table.remove_requested.connect(self._remove_from_list)
        self.table.export_requested.connect(self._export_selection)
        holder_layout.addWidget(self.table)
        self.modes.addWidget(holder)

        self.toast = Toast(holder)
        self.toast.avoid(self.table.selection_bar)
        self.transfer = WordTransfer(self._service, self.toast, self)
        self.transfer.changed.connect(self._after_transfer)

        self.stats = StatsBar()
        layout.addWidget(self.stats)

    # -- context -----------------------------------------------------------

    def set_list(self, list_id: int, mode: str | None = None) -> None:
        """Show ``list_id``. A new list starts a new flashcard session."""
        if list_id != self.list_id or self.session is None:
            self.list_id = list_id
            try:
                self.session = self._service.start_review(list_id)
            except LexiTrackError:
                self.session = None
                self.list_missing.emit()
                return
            self._table_list_id = None
        if mode is not None:
            self.mode_switch.set_mode(mode)
        self.refresh()

    @property
    def mode(self) -> str:
        return self.mode_switch.mode

    def set_mode(self, mode: str) -> None:
        self.mode_switch.set_mode(mode)
        self.refresh()
        if self.list_id is not None:
            self.context_changed.emit(self.list_id, mode)

    def refresh(self, reload_table: bool = False) -> None:
        """Re-read the list from the database and redraw whichever mode is shown."""
        if self.list_id is None:
            return
        current = self._service.get_list(self.list_id)
        if current is None:
            self.list_missing.emit()
            return

        self.list_button.setText(f"{current.name}  ▾")
        self.table.set_hidden_list(current.name, "ALSO IN")
        self.language_tag.setVisible(current.language != UNDETERMINED)
        self.language_tag.setText(current.language.upper())
        self.language_tag.setToolTip(current.language_name)
        self.stats.update_progress(current.progress)

        if self.mode_switch.mode == ModeSwitch.LIST:
            self.modes.setCurrentIndex(1)
            if reload_table or self._table_list_id != self.list_id:
                self.table.set_words(self._service.list_words(self.list_id))
                self._table_list_id = self.list_id
            else:
                self.table.refresh_words(self._service.list_words(self.list_id))
            self.table.set_remove_allowed(True)
            self.table.view.setFocus()
        else:
            self.modes.setCurrentIndex(0)
            self._show_flashcard(current.progress.total)

    def _show_flashcard(self, total: int) -> None:
        if self.session is None:
            return
        if total == 0:
            self.flashcard_stack.setCurrentWidget(self.empty)
            return
        item = self.session.current()
        if item is None:
            progress = self._service.get_progress(self.list_id)
            current = self._service.get_list(self.list_id)
            name = current.name if current else "this list"
            self.finished.set_body(
                f"You have reviewed every word in {name}: {progress.known:,} known, "
                f"{progress.unknown:,} to learn. Open it as a list to change any answer"
                + (", or press \u2190 to step back." if self.session.can_go_back else ".")
            )
            self.flashcard_stack.setCurrentWidget(self.finished)
            self.finished.setFocus()
            return
        self.flashcard.show_item(item, self.session.can_go_back)
        self.flashcard_stack.setCurrentWidget(self.flashcard)

    # -- flashcard ---------------------------------------------------------

    def _step(self, action: Callable[[ReviewSession], object]) -> None:
        if self.session is None:
            return
        try:
            action(self.session)
        except LexiTrackError as exc:
            QMessageBox.warning(self, "Could not save your answer", exc.user_message)
        self.refresh()

    # -- list mode ---------------------------------------------------------

    def _set_status(self, word_ids: list[int], status: ReviewStatus) -> None:
        try:
            self._service.set_status(word_ids, ReviewStatus(status))
        except LexiTrackError as exc:
            QMessageBox.warning(self, "Could not change status", exc.user_message)
            return
        self.table.refresh_words(self._service.get_words(word_ids))
        self._after_change()

    def _after_transfer(self) -> None:
        """Redraw after a copy, move or undo, keeping whatever is still selected.

        After a copy the words are all still here, so the selection survives and
        can go on to another list; after a move the moved rows are simply gone.
        """
        selected = self.table.selected_ids()
        self._after_change(reload=True)
        self.table.select_ids(selected)

    def _transfer_targets(self, word_ids: list[int]) -> list[tuple[int, str]]:
        return [(lst.id, lst.name) for lst in self.transfer.targets(word_ids, self.list_id)]

    def _copy_to(self, word_ids: list[int], target_id: int) -> None:
        self.transfer.copy(word_ids, target_id)

    def _move_to(self, word_ids: list[int], target_id: int) -> None:
        if self.list_id is not None:
            self.transfer.move(word_ids, self.list_id, target_id)

    def _pick_transfer(self, kind: str) -> None:
        ids = self.table.selected_ids()
        if not ids or self.list_id is None:
            return
        if kind == "move":
            self.transfer.pick_and_move(ids, self.list_id, self.table.view)
        else:
            self.transfer.pick_and_copy(ids, self.list_id, self.table.view)

    def pick_list(self) -> None:
        """Ctrl+L: switch list from the keyboard."""
        chosen = ListPicker.pick(
            "Switch list",
            self._service.lists(),
            self.list_button,
            allow_new=False,
            current_id=self.list_id,
        )
        if chosen is not None and chosen != self.list_id:
            self._switch_list(chosen)

    def _remove_from_list(self, word_ids: list[int]) -> None:
        if self.list_id is None:
            return
        current = self._service.get_list(self.list_id)
        exclusive = self._service.exclusive_word_count(self.list_id, word_ids)
        count = len(word_ids)
        text = f"Remove {count:,} {'word' if count == 1 else 'words'} from “{current.name}”?"
        if exclusive:
            text += (
                f"\n\n{exclusive:,} of them {'is' if exclusive == 1 else 'are'} in no other list, "
                "so they will be deleted along with whether you know them."
            )
        if not confirm(self, "Remove words", text, "Remove"):
            return
        try:
            self._service.remove_words_from_list(self.list_id, word_ids)
        except LexiTrackError as exc:
            QMessageBox.warning(self, "Could not remove words", exc.user_message)
            return
        self.table.remove_word_ids(word_ids)
        self._after_change()

    def _export_selection(self, word_ids: list[int]) -> None:
        if self.list_id is not None:
            self._actions.export_list(self.list_id, word_ids)

    def _after_change(self, reload: bool = False) -> None:
        if self.list_id is not None:
            current = self._service.get_list(self.list_id)
            if current is not None:
                self.stats.update_progress(current.progress)
        if reload:
            self.refresh(reload_table=True)

    # -- menus -------------------------------------------------------------

    def _fill_list_menu(self) -> None:
        self.list_menu.clear()
        for lst in self._service.lists():
            action = self.list_menu.addAction(
                f"{lst.name}   ·   {int(lst.progress.percent_complete)}%"
            )
            action.setCheckable(True)
            action.setChecked(lst.id == self.list_id)
            action.triggered.connect(lambda _c=False, i=lst.id: self._switch_list(i))
        self.list_menu.addSeparator()
        self.list_menu.addAction("New List…", self._actions.create_list)

    def _switch_list(self, list_id: int) -> None:
        self.set_list(list_id)
        self.context_changed.emit(list_id, self.mode)

    def _fill_actions_menu(self) -> None:
        self.actions_menu.clear()
        if self.list_id is not None:
            self._actions.fill_menu(self.actions_menu, self.list_id)

    def _add_words(self) -> None:
        if self.list_id is not None:
            self._actions.add_words(self.list_id)

    def _import_into(self) -> None:
        if self.list_id is not None:
            self._actions.import_into(self.list_id)

    def _export(self) -> None:
        if self.list_id is not None:
            self._actions.export_list(self.list_id)


def _label(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    return label
