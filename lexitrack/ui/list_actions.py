"""List operations shared by every screen that offers them.

Home (list cards) and Review (the list menu) both let the user create, edit,
delete, import into and export a list. Keeping the flows here means the same
confirmation text and the same error handling everywhere.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMenu, QMessageBox, QWidget

from ..core.errors import LexiTrackError
from ..models.vocabulary_list import VocabularyList
from ..services.export_service import ExportFormat
from ..services.vocabulary_service import VocabularyService
from .dialogs import AddWordDialog, ListDialog, confirm
from .export_dialog import ExportDialog, ExportScope
from .import_dialog import ImportDialog


class ListActions(QObject):
    """Dialog-driven list operations. Emits ``changed`` after anything is written."""

    #: Lists or their contents changed; screens should re-read.
    changed = Signal()
    #: A list the user should now be looking at (just created or imported into).
    focus_list = Signal(int)
    #: A list was deleted (id).
    deleted = Signal(int)

    def __init__(self, service: VocabularyService, parent_widget: QWidget) -> None:
        super().__init__(parent_widget)
        self._service = service
        self._parent = parent_widget

    # -- operations --------------------------------------------------------

    def create_list(self) -> VocabularyList | None:
        dialog = ListDialog(self._service, parent=self._parent)
        if dialog.exec() and dialog.result_list is not None:
            self.focus_list.emit(dialog.result_list.id)
            self.changed.emit()
            return dialog.result_list
        return None

    def edit_list(self, list_id: int) -> None:
        current = self._service.get_list(list_id)
        if current is None:
            self._missing()
            return
        dialog = ListDialog(self._service, existing=current, parent=self._parent)
        if dialog.exec():
            self.changed.emit()

    def delete_list(self, list_id: int) -> bool:
        current = self._service.get_list(list_id)
        if current is None:
            self._missing()
            return False
        exclusive = self._service.exclusive_word_count(list_id)
        shared = current.progress.total - exclusive
        lines = [f"Delete “{current.name}”?"]
        if exclusive:
            noun = "word is" if exclusive == 1 else "words are"
            lines.append(
                f"{exclusive:,} {noun} only in this list and will be deleted, "
                "along with whether you know them."
            )
        if shared:
            noun = "word is" if shared == 1 else "words are"
            lines.append(f"{shared:,} {noun} also in other lists and will be kept.")
        lines.append("This cannot be undone.")
        if not confirm(self._parent, "Delete list", "\n\n".join(lines), "Delete list"):
            return False
        try:
            self._service.delete_list(list_id)
        except LexiTrackError as exc:
            QMessageBox.warning(self._parent, "Could not delete the list", exc.user_message)
            return False
        self.deleted.emit(list_id)
        self.changed.emit()
        return True

    def add_words(self, list_id: int) -> None:
        current = self._service.get_list(list_id)
        if current is None:
            self._missing()
            return
        dialog = AddWordDialog(self._service, current, parent=self._parent)
        added: list[int] = []
        dialog.word_added.connect(added.append)
        dialog.exec()
        if added:
            self.changed.emit()

    def import_into(self, list_id: int | None = None) -> None:
        dialog = ImportDialog(self._service, parent=self._parent, target_list_id=list_id)
        dialog.exec()
        if not dialog.imported_anything:
            return
        if list_id is None:
            # Bring the user to the list the first imported file went into.
            names = next((o.result.list_names for o in dialog.results if o.result), ())
            by_name = {lst.name: lst.id for lst in self._service.lists()}
            if names and names[0] in by_name:
                self.focus_list.emit(by_name[names[0]])
        self.changed.emit()

    def export_list(self, list_id: int, selected_ids: list[int] | None = None) -> None:
        current = self._service.get_list(list_id)
        if current is None:
            self._missing()
            return
        scopes: list[ExportScope] = []
        if selected_ids:
            ids = list(selected_ids)
            scopes.append(
                ExportScope(
                    f"Selected words ({len(ids):,})",
                    lambda: self._service.export_content_for_selection(ids, list_id),
                    ExportFormat.JSON,
                )
            )
        scopes.append(
            ExportScope(
                f"All words in {current.name} ({current.progress.total:,})",
                lambda: self._service.export_content_for_list(list_id),
                ExportFormat.JSON,
            )
        )
        scopes.append(
            ExportScope(
                f"Unknown words in {current.name} ({current.progress.unknown:,})",
                lambda: self._service.export_content_for_unknown(list_id),
                ExportFormat.PDF,
            )
        )
        ExportDialog(self._service, scopes, parent=self._parent).exec()

    # -- menus -------------------------------------------------------------

    def fill_menu(self, menu: QMenu, list_id: int) -> None:
        """Add the standard list actions to ``menu``."""
        menu.addAction("Add Words…", lambda: self.add_words(list_id))
        menu.addAction("Import into This List…", lambda: self.import_into(list_id))
        menu.addAction("Export…", lambda: self.export_list(list_id))
        menu.addSeparator()
        menu.addAction("Edit List…", lambda: self.edit_list(list_id))
        menu.addAction("Delete List…", lambda: self.delete_list(list_id))

    def _missing(self) -> None:
        QMessageBox.information(self._parent, "List not found", "That list no longer exists.")
        self.changed.emit()
