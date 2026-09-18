"""The Unknown Words manager.

Every word the user has marked unknown, across all lists, in one table. It is
a working list, not an archive: marking a word Known or resetting it takes it
off this page, because this page *is* "words whose status is Unknown". Nothing
here deletes vocabulary — the word stays in its lists with its new status.

Typical uses: collect the hardest words into "My Difficult Words" (Copy to,
or C), reset a batch to Not Reviewed so they come round again in flashcards,
or export them to study on paper.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import LexiTrackError
from ..models.user_word_state import ReviewStatus
from ..services.export_service import ExportFormat
from ..services.vocabulary_service import VocabularyService
from .components.toast import Toast
from .components.vocabulary_table import Column, VocabularyTable
from .empty_state import EmptyState
from .export_dialog import ExportDialog, ExportScope
from .theme.palette import METRICS
from .word_transfer import WordTransfer


class UnknownPage(QWidget):
    """Unknown words across every list, with bulk status actions."""

    def __init__(self, service: VocabularyService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._build()

    def _build(self) -> None:
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_6, m.space_5, m.space_6, m.space_4)
        layout.setSpacing(m.space_4)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Unknown Words")
        title.setObjectName("PageTitle")
        titles.addWidget(title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(lambda: self.export())
        header.addWidget(self.export_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.table = VocabularyTable(
            # No status column: every row here is Unknown by definition.
            columns=(Column.WORD, Column.PART_OF_SPEECH, Column.CEFR, Column.LISTS),
            allow_remove=False,
            # Words here come from every list, so there is no single list to
            # move them out of; copying is the only transfer that makes sense.
            allow_move=False,
            noun="unknown words",
        )
        # Every row here is Unknown by definition; filtering by status or
        # marking Unknown again would only be noise.
        self.table.status_filter.setVisible(False)
        self.table.hide_status_action(ReviewStatus.UNKNOWN)

        self.list_filter = QComboBox()
        self.list_filter.setAccessibleName("Filter by list")
        self.list_filter.currentIndexChanged.connect(self._reload)
        self.table.extra_filters.addWidget(self.list_filter)

        self.table.status_requested.connect(self._set_status)
        self.table.copy_requested.connect(self._copy_to)
        self.table.pick_requested.connect(self._pick_copy)
        self.table.set_target_provider(
            lambda ids: [(lst.id, lst.name) for lst in self.transfer.targets(ids, None)]
        )
        self.table.export_requested.connect(lambda ids: self.export(ids))
        self.stack.addWidget(self.table)

        self.empty = EmptyState(
            title="No unknown words",
            body="Words you answer “I Don't Know” collect here, across every list, "
            "so you can study, export or organise them.",
        )
        self.stack.addWidget(self.empty)

        self.toast = Toast(self)
        self.toast.avoid(self.table.selection_bar)
        self.transfer = WordTransfer(self._service, self.toast, self)
        self.transfer.changed.connect(self._reload_keeping_selection)

    # -- content -----------------------------------------------------------

    def refresh(self) -> None:
        current = self.list_filter.currentData()
        self.list_filter.blockSignals(True)
        self.list_filter.clear()
        self.list_filter.addItem("All lists", None)
        for lst in self._service.lists():
            self.list_filter.addItem(f"{lst.name} ({lst.progress.unknown:,})", lst.id)
        index = self.list_filter.findData(current)
        self.list_filter.setCurrentIndex(index if index >= 0 else 0)
        self.list_filter.blockSignals(False)
        self._reload()

    def _reload(self) -> None:
        list_id = self.list_filter.currentData()
        words = self._service.list_unknown_words(list_id)
        total = self._service.unknown_count()
        noun = "word" if total == 1 else "words"
        self.subtitle.setText(f"{total:,} {noun} you marked as unknown, across all lists")
        self.export_button.setEnabled(total > 0)
        self.stack.setCurrentWidget(self.table if total else self.empty)
        self.table.set_words(words)

    def _reload_keeping_selection(self) -> None:
        self.table.refresh_words(self._service.get_words([w.id for w in self.table.model.words]))

    # -- actions -----------------------------------------------------------

    def _set_status(self, word_ids: list[int], status: ReviewStatus) -> None:
        status = ReviewStatus(status)
        if status is ReviewStatus.UNKNOWN:
            return
        try:
            self._service.set_status(word_ids, status)
        except LexiTrackError as exc:
            QMessageBox.warning(self, "Could not change status", exc.user_message)
            return
        # They are no longer unknown, so they leave this page.
        self.table.remove_word_ids(word_ids)
        self._reload_counts()

    def _reload_counts(self) -> None:
        total = self._service.unknown_count()
        self.subtitle.setText(
            f"{total:,} {'word' if total == 1 else 'words'} "
            "you marked as unknown, across all lists"
        )
        if total == 0:
            self.stack.setCurrentWidget(self.empty)
            self.export_button.setEnabled(False)

    def _copy_to(self, word_ids: list[int], target_id: int) -> None:
        self.transfer.copy(word_ids, target_id)

    def _pick_copy(self, kind: str) -> None:
        ids = self.table.selected_ids()
        if ids and kind == "copy":
            self.transfer.pick_and_copy(ids, None, self.table.view)

    def export(self, selected_ids: list[int] | None = None) -> None:
        scopes: list[ExportScope] = []
        if selected_ids:
            ids = list(selected_ids)
            scopes.append(
                ExportScope(
                    f"Selected words ({len(ids):,})",
                    lambda: self._service.export_content_for_selection(ids),
                )
            )
        list_id = self.list_filter.currentData()
        if list_id is not None:
            current = self._service.get_list(list_id)
            if current is not None:
                scopes.append(
                    ExportScope(
                        f"Unknown words in {current.name} ({current.progress.unknown:,})",
                        lambda: self._service.export_content_for_unknown(list_id),
                    )
                )
        scopes.append(
            ExportScope(
                f"All unknown words ({self._service.unknown_count():,})",
                lambda: self._service.export_content_for_unknown(None),
                ExportFormat.PDF,
            )
        )
        ExportDialog(self._service, scopes, parent=self).exec()
