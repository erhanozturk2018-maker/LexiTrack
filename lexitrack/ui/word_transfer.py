"""Copying and moving words between lists, from menus or the keyboard.

Two operations, named for what they do:

* **Copy to** — the words join the target list and stay in this one.
* **Move to** — the words join the target list, then leave this one. They are
  added before they are removed, so a move can never delete a word: at the
  moment it leaves the source list it is already in the target.

Both show their result in a toast with Undo instead of a dialog. Undo reverses
exactly what happened — only words that were newly added to the target are
taken out again, so words that were already there stay put.

Only lists that can accept the words are offered: a list in another language
never appears as a target (see DECISIONS §25).
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import LexiTrackError
from ..models.language import UNDETERMINED
from ..models.vocabulary_list import VocabularyList
from ..services.vocabulary_service import VocabularyService
from .components.toast import Toast
from .dialogs import ListDialog

#: Stands for "New List…" wherever a target list id is expected.
NEW_LIST_ID = -1


def compatible_lists(
    service: VocabularyService, word_ids: Sequence[int], exclude_id: int | None = None
) -> list[VocabularyList]:
    """Lists that can hold every one of ``word_ids``, other than ``exclude_id``."""
    languages = {word.language for word in service.get_words(word_ids)}
    return [
        lst
        for lst in service.lists()
        if lst.id != exclude_id
        and (lst.language == UNDETERMINED or languages <= {lst.language})
    ]


class ListPicker(QDialog):
    """A small popup: type to filter, arrows to choose, Enter to confirm.

    Used for C (copy to), M (move to) and Ctrl+L (switch list), so choosing a
    list never needs the mouse.
    """

    def __init__(
        self,
        title: str,
        lists: Sequence[VocabularyList],
        allow_new: bool = True,
        current_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("ListPicker")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.chosen_id: int | None = None
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Type to filter lists")
        self.filter.installEventFilter(self)
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)

        self.items = QListWidget()
        self.items.itemActivated.connect(lambda _item: self._accept_current())
        self.items.itemClicked.connect(lambda _item: self._accept_current())
        for lst in lists:
            label = lst.name
            if lst.language != UNDETERMINED:
                label += f"   ·   {lst.language_name}"
            if lst.id == current_id:
                label += "   (current)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, lst.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, lst.name.casefold())
            self.items.addItem(item)
        if allow_new:
            item = QListWidgetItem("+  New List…")
            item.setData(Qt.ItemDataRole.UserRole, NEW_LIST_ID)
            item.setData(Qt.ItemDataRole.UserRole + 1, "")
            self.items.addItem(item)
        if not lists and not allow_new:
            empty = QLabel("No lists can take these words.")
            empty.setObjectName("Faint")
            layout.addWidget(empty)
        layout.addWidget(self.items)
        self._apply_filter("")
        if current_id is not None:
            for row in range(self.items.count()):
                if self.items.item(row).data(Qt.ItemDataRole.UserRole) == current_id:
                    self.items.setCurrentRow(row)
        self.filter.setFocus()

    # -- choosing ----------------------------------------------------------

    def _visible_rows(self) -> list[int]:
        return [row for row in range(self.items.count()) if not self.items.item(row).isHidden()]

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.items.count()):
            item = self.items.item(row)
            name = item.data(Qt.ItemDataRole.UserRole + 1)
            is_new = item.data(Qt.ItemDataRole.UserRole) == NEW_LIST_ID
            item.setHidden(bool(needle) and not is_new and needle not in name)
        visible = self._visible_rows()
        if visible and self.items.currentRow() not in visible:
            self.items.setCurrentRow(visible[0])
        # Fit the list to what is showing, so filtering does not leave a gap.
        self.items.setFixedHeight(min(max(len(visible), 1), 8) * 34 + 8)
        self.adjustSize()

    def move_selection(self, step: int) -> None:
        visible = self._visible_rows()
        if not visible:
            return
        current = self.items.currentRow()
        position = visible.index(current) if current in visible else -1
        self.items.setCurrentRow(visible[max(0, min(len(visible) - 1, position + step))])

    def _accept_current(self) -> None:
        item = self.items.currentItem()
        if item is None or item.isHidden():
            return
        self.chosen_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.accept()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self.filter
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            key = event.key()
            if key == Qt.Key.Key_Down:
                self.move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self.move_selection(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_current()
                return True
        return False

    @classmethod
    def pick(
        cls,
        title: str,
        lists: Sequence[VocabularyList],
        anchor: QWidget,
        at: QPoint | None = None,
        allow_new: bool = True,
        current_id: int | None = None,
    ) -> int | None:
        picker = cls(title, lists, allow_new, current_id, anchor)
        point = at if at is not None else QPoint(0, anchor.height())
        picker.move(anchor.mapToGlobal(point))
        if picker.exec() != QDialog.DialogCode.Accepted:
            return None
        return picker.chosen_id


class WordTransfer(QObject):
    """Copy or move words between lists, with a toast and Undo."""

    #: Lists or memberships changed (including by Undo); redraw.
    changed = Signal()

    def __init__(self, service: VocabularyService, toast: Toast, parent: QWidget) -> None:
        super().__init__(parent)
        self._service = service
        self._toast = toast
        self._parent = parent

    # -- entry points ------------------------------------------------------

    def targets(self, word_ids: Sequence[int], exclude_id: int | None) -> list[VocabularyList]:
        return compatible_lists(self._service, word_ids, exclude_id)

    def pick_and_copy(
        self, word_ids: Sequence[int], source_id: int | None, anchor: QWidget
    ) -> None:
        target = ListPicker.pick(
            f"Copy {len(word_ids):,} to", self.targets(word_ids, source_id), anchor, _centre(anchor)
        )
        if target is not None:
            self.copy(word_ids, target)

    def pick_and_move(self, word_ids: Sequence[int], source_id: int, anchor: QWidget) -> None:
        target = ListPicker.pick(
            f"Move {len(word_ids):,} to", self.targets(word_ids, source_id), anchor, _centre(anchor)
        )
        if target is not None:
            self.move(word_ids, source_id, target)

    # -- operations --------------------------------------------------------

    def copy(self, word_ids: Sequence[int], target_id: int) -> bool:
        ids = list(dict.fromkeys(word_ids))
        target = self._resolve(target_id, ids)
        if target is None:
            return False
        newly_added = self._not_in(ids, target)
        try:
            self._service.add_words_to_list(target.id, ids)
        except LexiTrackError as exc:
            QMessageBox.warning(self._parent, "Could not copy words", exc.user_message)
            return False
        self.changed.emit()

        if not newly_added:
            self._toast.show_message(f"{_count(ids)} already in “{target.name}”.")
            return True
        message = f"Copied {_count(newly_added)} to “{target.name}”."
        if len(newly_added) < len(ids):
            message += f" {len(ids) - len(newly_added):,} were already there."
        self._toast.show_message(message, lambda: self._undo_copy(target.id, newly_added))
        return True

    def move(self, word_ids: Sequence[int], source_id: int, target_id: int) -> bool:
        ids = list(dict.fromkeys(word_ids))
        source = self._service.get_list(source_id)
        target = self._resolve(target_id, ids)
        if source is None or target is None:
            return False
        newly_added = self._not_in(ids, target)
        try:
            # Add first: a word already in the target can never be deleted as
            # an orphan when it leaves the source.
            self._service.add_words_to_list(target.id, ids)
            self._service.remove_words_from_list(source.id, ids)
        except LexiTrackError as exc:
            QMessageBox.warning(self._parent, "Could not move words", exc.user_message)
            self.changed.emit()
            return False
        self.changed.emit()
        self._toast.show_message(
            f"Moved {_count(ids)} to “{target.name}”.",
            lambda: self._undo_move(source.id, target.id, ids, newly_added),
        )
        return True

    # -- undo --------------------------------------------------------------

    def _undo_copy(self, target_id: int, newly_added: list[int]) -> None:
        try:
            self._service.remove_words_from_list(target_id, newly_added)
        except LexiTrackError as exc:
            QMessageBox.warning(self._parent, "Could not undo", exc.user_message)
        self.changed.emit()

    def _undo_move(
        self, source_id: int, target_id: int, ids: list[int], newly_added: list[int]
    ) -> None:
        try:
            self._service.add_words_to_list(source_id, ids)
            if newly_added:
                self._service.remove_words_from_list(target_id, newly_added)
        except LexiTrackError as exc:
            QMessageBox.warning(self._parent, "Could not undo", exc.user_message)
        self.changed.emit()

    # -- helpers -----------------------------------------------------------

    def _resolve(self, target_id: int, word_ids: list[int]) -> VocabularyList | None:
        if target_id != NEW_LIST_ID:
            target = self._service.get_list(target_id)
            if target is None:
                QMessageBox.warning(self._parent, "List not found", "That list no longer exists.")
            return target
        dialog = ListDialog(self._service, parent=self._parent)
        languages = {word.language for word in self._service.get_words(word_ids)}
        if len(languages) == 1:
            index = dialog.language_field.findData(next(iter(languages)))
            if index >= 0:
                dialog.language_field.setCurrentIndex(index)
        if dialog.exec() and dialog.result_list is not None:
            return dialog.result_list
        return None

    def _not_in(self, word_ids: list[int], target: VocabularyList) -> list[int]:
        return [
            word.id for word in self._service.get_words(word_ids) if target.name not in word.lists
        ]


def _count(ids: Sequence[int]) -> str:
    return f"{len(ids):,} {'word' if len(ids) == 1 else 'words'}"


def _centre(widget: QWidget) -> QPoint:
    return QPoint(max(widget.width() // 2 - 160, 0), 60)
