"""The Import PDF dialog.

Parsing a 12-page word list takes a moment, and a novel takes considerably
longer, so the work runs on a :class:`QThread` and the dialog reports what it
is doing. The user can cancel; because nothing is written to the database until
parsing finishes, cancelling leaves no half-import behind.

The dialog also lets the user override parser detection. Auto is right almost
always, but "almost" is why the override exists.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import LexiTrackError
from ..parsers.registry import AUTO
from ..repositories.word_repository import ImportResult
from ..services.vocabulary_service import VocabularyService
from .theme.palette import METRICS

log = logging.getLogger(__name__)


class ImportWorker(QObject):
    """Runs one import on a worker thread."""

    progress = Signal(str, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, service: VocabularyService, path: Path, parser_key: str) -> None:
        super().__init__()
        self._service = service
        self._path = path
        self._parser_key = parser_key
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            result = self._service.import_document(
                self._path,
                parser_key=self._parser_key,
                progress=lambda message, percent: self.progress.emit(message, percent),
                should_cancel=lambda: self._cancelled,
            )
        except LexiTrackError as exc:
            # Expected, explainable failures: show the message as written.
            log.warning("Import of %s failed: %s", self._path, exc)
            self.failed.emit(exc.user_message)
        except Exception:
            # Anything else is a bug; the user gets a plain sentence and the
            # traceback goes to the log.
            log.exception("Unexpected error importing %s", self._path)
            self.failed.emit(
                f"{self._path.name} could not be imported because of an "
                "unexpected error. See the log file for details."
            )
        else:
            self.finished.emit(result)


class ImportDialog(QDialog):
    """Collects a file and a parser choice, then runs the import."""

    def __init__(self, service: VocabularyService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._path: Path | None = None
        self._thread: QThread | None = None
        self._worker: ImportWorker | None = None
        self.result_data: ImportResult | None = None

        self.setWindowTitle("Import PDF")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        layout.setSpacing(m.space_4)

        title = QLabel("Import PDF")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        hint = QLabel(
            "Choose a PDF to add to your vocabulary. Words you have already "
            "reviewed are kept as they are."
        )
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # -- file chooser
        self._path_label = QLabel("No file selected")
        self._path_label.setObjectName("FilePathLabel")
        self._path_label.setProperty("state", "empty")

        browse_button = QPushButton("Choose File…")
        browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_button.clicked.connect(self._choose_file)

        file_row = QHBoxLayout()
        file_row.setSpacing(m.space_2)
        file_row.addWidget(self._path_label, 1)
        file_row.addWidget(browse_button, 0)
        layout.addLayout(file_row)

        # -- parser choice
        layout.addWidget(self._build_parser_group())

        # -- progress, hidden until the import starts
        self._status_label = QLabel()
        self._status_label.setObjectName("DialogHint")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        layout.addStretch(1)

        # -- actions
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.clicked.connect(self._on_cancel)

        self._import_button = QPushButton("Import")
        self._import_button.setProperty("variant", "primary")
        self._import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._import_button.setEnabled(False)
        self._import_button.setDefault(True)
        self._import_button.clicked.connect(self._start_import)

        actions = QHBoxLayout()
        actions.setSpacing(m.space_2)
        actions.addStretch(1)
        actions.addWidget(self._cancel_button)
        actions.addWidget(self._import_button)
        layout.addLayout(actions)

    def _build_parser_group(self) -> QGroupBox:
        group = QGroupBox("PARSER")
        layout = QVBoxLayout(group)
        layout.setSpacing(2)

        self._parser_buttons = QButtonGroup(self)

        auto_button = QRadioButton("Auto — detect the format")
        auto_button.setChecked(True)
        self._parser_buttons.addButton(auto_button)
        auto_button.setProperty("parser_key", AUTO)
        layout.addWidget(auto_button)

        for info in self._service.available_parsers():
            button = QRadioButton(info.name)
            button.setProperty("parser_key", info.key)
            button.setToolTip(info.description)
            self._parser_buttons.addButton(button)
            layout.addWidget(button)

        note = QLabel("Choose a parser manually only if detection gets it wrong.")
        note.setObjectName("RadioHint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return group

    # -- interaction -------------------------------------------------------

    def _choose_file(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self, "Choose a PDF", str(Path.home()), "PDF files (*.pdf);;All files (*)"
        )
        if not path_text:
            return
        self._path = Path(path_text)
        self._path_label.setText(self._path.name)
        self._path_label.setToolTip(str(self._path))
        self._path_label.setProperty("state", "selected")
        _repolish(self._path_label)
        self._import_button.setEnabled(True)

    def _selected_parser_key(self) -> str:
        button = self._parser_buttons.checkedButton()
        return str(button.property("parser_key")) if button else AUTO

    def _start_import(self) -> None:
        if self._path is None:
            return

        self._set_busy(True)
        self._status_label.setText(f"Opening {self._path.name}…")

        self._thread = QThread(self)
        self._worker = ImportWorker(self._service, self._path, self._selected_parser_key())
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    def _on_progress(self, message: str, percent: int) -> None:
        self._status_label.setText(message)
        if percent < 0:
            self._progress_bar.setRange(0, 0)  # indeterminate
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(percent)

    def _on_finished(self, result: ImportResult) -> None:
        self.result_data = result
        self._stop_thread()
        self.accept()

    def _on_failed(self, message: str) -> None:
        self._stop_thread()
        self._set_busy(False)
        self._status_label.setVisible(True)
        self._status_label.setText(message)
        self._progress_bar.setVisible(False)
        _show_error(self, "Import failed", message)

    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._status_label.setText("Cancelling…")
            self._cancel_button.setEnabled(False)
            return
        self.reject()

    def _set_busy(self, busy: bool) -> None:
        self._import_button.setEnabled(not busy and self._path is not None)
        self._status_label.setVisible(busy)
        self._progress_bar.setVisible(busy)
        self._cancel_button.setEnabled(True)
        self._cancel_button.setText("Cancel")
        for button in self._parser_buttons.buttons():
            button.setEnabled(not busy)

    def _stop_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
            self._thread = None
        self._worker = None

    def closeEvent(self, event) -> None:
        """Never let the window close out from under a running import."""
        if self._worker is not None:
            self._worker.cancel()
            self._stop_thread()
        super().closeEvent(event)


def _repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _show_error(parent: QWidget, title: str, message: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
