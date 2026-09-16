"""Exporting words: choose what, choose a format, choose where.

The caller decides which scopes make sense where the user is — the words of
the list on screen, its unknown words, a table selection, every unknown word —
and which one is selected first. The dialog never offers a scope that does not
apply, so it stays two short questions.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..services.export_service import ExportContent, ExportFormat
from ..services.vocabulary_service import VocabularyService
from .dialogs import dialog_header, error_label, show_error
from .theme.palette import METRICS

FORMAT_HINTS = {
    ExportFormat.PDF: "A printable study sheet.",
    ExportFormat.CSV: "For Excel, Google Sheets or Anki.",
    ExportFormat.JSON: "LexiTrack's own format. Edit it and import it again.",
}


@dataclass(frozen=True, slots=True)
class ExportScope:
    label: str
    content: Callable[[], ExportContent]
    default_format: ExportFormat = ExportFormat.PDF


class ExportDialog(QDialog):
    def __init__(
        self,
        service: VocabularyService,
        scopes: Sequence[ExportScope],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._scopes = list(scopes)
        self.written: Path | None = None
        m = METRICS
        self.setWindowTitle("Export")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        layout.setSpacing(m.space_4)
        layout.addLayout(dialog_header("Export Words"))

        scope_box = QGroupBox("WORDS")
        scope_layout = QVBoxLayout(scope_box)
        self._scope_group = QButtonGroup(self)
        for index, scope in enumerate(self._scopes):
            radio = QRadioButton(scope.label)
            self._scope_group.addButton(radio, index)
            scope_layout.addWidget(radio)
        self._scope_group.button(0).setChecked(True)
        self._scope_group.idToggled.connect(self._on_scope)
        layout.addWidget(scope_box)

        format_box = QGroupBox("FORMAT")
        format_layout = QVBoxLayout(format_box)
        self._format_group = QButtonGroup(self)
        self._format_buttons: dict[ExportFormat, QRadioButton] = {}
        for index, file_format in enumerate(ExportFormat):
            radio = QRadioButton(file_format.label)
            radio.setToolTip(FORMAT_HINTS[file_format])
            self._format_group.addButton(radio, index)
            self._format_buttons[file_format] = radio
            row = QHBoxLayout()
            row.addWidget(radio)
            hint = QLabel(FORMAT_HINTS[file_format])
            hint.setObjectName("Faint")
            row.addWidget(hint, 1)
            format_layout.addLayout(row)
        layout.addWidget(format_box)
        self._on_scope(0, True)

        self.error = error_label()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        export = QPushButton("Export…")
        export.setProperty("variant", "primary")
        export.setDefault(True)
        export.clicked.connect(self._export)
        buttons.addWidget(export)
        layout.addLayout(buttons)

    def _on_scope(self, index: int, checked: bool) -> None:
        if checked:
            self._format_buttons[self._scopes[index].default_format].setChecked(True)

    def selected_format(self) -> ExportFormat:
        return list(ExportFormat)[self._format_group.checkedId()]

    def _export(self) -> None:
        scope = self._scopes[self._scope_group.checkedId()]
        file_format = self.selected_format()
        try:
            content = scope.content()
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return
        if not content.words:
            show_error(self.error, "There are no words to export in that selection.")
            return

        paths.ensure_data_dirs()
        suggested = paths.exports_dir() / f"{content.suggested_filename}.{file_format.value}"
        target, _ = QFileDialog.getSaveFileName(
            self, "Export Words", str(suggested), file_format.file_filter
        )
        if not target:
            return
        try:
            self.written = self._service.export(content, target, file_format)
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return

        count = len(content.words)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Export complete")
        box.setText(f"Exported {count:,} {'word' if count == 1 else 'words'} to:\n{self.written}")
        open_folder = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.written.parent)))
        self.accept()
