"""Importing one or more files into one or more lists.

The workflow the dialog walks through::

    Choose files  ->  read them (worker thread)  ->  preview each file
                  ->  choose target lists  ->  import (worker thread)  ->  result

Nothing is written until the user presses Import, and each file is written in
its own transaction, so a problem with one file never leaves another
half-imported. Parsing a long PDF takes time, so both the reading and the
writing run off the UI thread and can be cancelled while reading.

Each file gets a small panel: what was detected, how many words are new, any
warnings, and where the words should go — a new list (named from the file),
any existing lists, or both. Where the file does not say what language it is
in, the panel asks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import LexiTrackError
from ..models.language import UNDETERMINED, language_name
from ..parsers.registry import AUTO
from ..repositories.word_repository import ImportResult
from ..services.import_service import ImportPreview, ImportTarget, NewList
from ..services.vocabulary_service import VocabularyService
from .dialogs import dialog_header, language_combo
from .theme.palette import METRICS

log = logging.getLogger(__name__)

FILE_FILTER = "Word lists (*.pdf *.json);;PDF files (*.pdf);;JSON files (*.json);;All files (*)"


# -- workers ---------------------------------------------------------------


@dataclass(slots=True)
class FileOutcome:
    path: Path
    preview: ImportPreview | None = None
    error: str | None = None


@dataclass(slots=True)
class CommitOutcome:
    path: Path
    result: ImportResult | None = None
    error: str | None = None


@dataclass(slots=True)
class CommitJob:
    preview: ImportPreview
    target: ImportTarget
    language: str | None = None


class ReadWorker(QObject):
    """Prepares every chosen file, one after another."""

    progress = Signal(str, int)
    finished = Signal(list)

    def __init__(self, service: VocabularyService, paths: list[Path], parser_key: str) -> None:
        super().__init__()
        self._service = service
        self._paths = paths
        self._parser_key = parser_key
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        outcomes: list[FileOutcome] = []
        count = len(self._paths)
        for index, path in enumerate(self._paths):
            if self._cancelled:
                break
            prefix = f"File {index + 1} of {count} · " if count > 1 else ""

            def report(message: str, percent: int, prefix=prefix, index=index) -> None:
                overall = -1 if percent < 0 else int((index + percent / 100) / count * 100)
                self.progress.emit(prefix + message, overall)

            try:
                preview = self._service.prepare_import(
                    path, self._parser_key, report, lambda: self._cancelled
                )
                if preview is not None:
                    outcomes.append(FileOutcome(path, preview=preview))
            except LexiTrackError as exc:
                log.warning("Could not read %s: %s", path, exc)
                outcomes.append(FileOutcome(path, error=exc.user_message))
            except Exception:
                log.exception("Unexpected error reading %s", path)
                outcomes.append(
                    FileOutcome(
                        path,
                        error=f"{path.name} could not be read because of an unexpected error. "
                        "See the log file for details.",
                    )
                )
        self.finished.emit(outcomes)


class CommitWorker(QObject):
    """Writes each prepared file into its targets."""

    progress = Signal(str, int)
    finished = Signal(list)

    def __init__(self, service: VocabularyService, jobs: list[CommitJob]) -> None:
        super().__init__()
        self._service = service
        self._jobs = jobs

    def run(self) -> None:
        outcomes: list[CommitOutcome] = []
        for index, job in enumerate(self._jobs):
            path = job.preview.path
            self.progress.emit(f"Saving {path.name}…", int(index / len(self._jobs) * 100))
            try:
                result = self._service.commit_import(job.preview, job.target, job.language)
                outcomes.append(CommitOutcome(path, result=result))
            except LexiTrackError as exc:
                outcomes.append(CommitOutcome(path, error=exc.user_message))
            except Exception:
                log.exception("Unexpected error importing %s", path)
                outcomes.append(
                    CommitOutcome(path, error=f"{path.name} could not be imported. See the log.")
                )
        self.finished.emit(outcomes)


# -- one file's preview ----------------------------------------------------


class FilePanel(QFrame):
    """Preview and target choice for one file."""

    changed = Signal()

    def __init__(
        self,
        service: VocabularyService,
        outcome: FileOutcome,
        preselected_list_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._service = service
        self.outcome = outcome
        m = METRICS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_4, m.space_3, m.space_4, m.space_4)
        layout.setSpacing(m.space_2)

        top = QHBoxLayout()
        self.include = QCheckBox()
        self.include.setToolTip("Include this file")
        self.include.setChecked(outcome.preview is not None)
        self.include.setEnabled(outcome.preview is not None)
        self.include.toggled.connect(self._on_include)
        top.addWidget(self.include)
        name = QLabel(outcome.path.name)
        name.setObjectName("ListCardName")
        top.addWidget(name)
        top.addStretch(1)
        layout.addLayout(top)

        if outcome.preview is None:
            error = QLabel(outcome.error or "This file could not be read.")
            error.setObjectName("ErrorText")
            error.setWordWrap(True)
            layout.addWidget(error)
            self.body = None
            return

        preview = outcome.preview
        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        for warning in preview.warnings:
            label = QLabel(warning)
            label.setObjectName("WarningText")
            label.setWordWrap(True)
            layout.addWidget(label)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, m.space_2, 0, 0)
        body.setSpacing(m.space_2)

        # New list
        new_row = QHBoxLayout()
        self.new_list = QCheckBox("New list")
        new_row.addWidget(self.new_list)
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("List name")
        new_row.addWidget(self.new_name, 1)
        body.addLayout(new_row)

        # Language, when the file does not state one
        language_row = QHBoxLayout()
        language_row.addWidget(_faint("Language"))
        if preview.stated_language:
            language_row.addWidget(
                QLabel(f"{language_name(preview.stated_language)} (stated by the file)")
            )
            self.language = None
        else:
            self.language = language_combo(UNDETERMINED)
            self.language.setItemText(0, "Unspecified — or taken from the chosen list")
            language_row.addWidget(self.language, 1)
        language_row.addStretch(1)
        body.addLayout(language_row)

        # Existing lists
        existing = service.lists()
        if existing:
            body.addWidget(_faint("Also add to existing lists"))
            self.targets = QListWidget()
            for lst in existing:
                tag = "" if lst.language == UNDETERMINED else f"   ·   {lst.language_name}"
                item = QListWidgetItem(f"{lst.name}{tag}")
                item.setData(Qt.ItemDataRole.UserRole, lst.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.targets.addItem(item)
            self.targets.setFixedHeight(min(len(existing), 4) * 32 + 12)
            body.addWidget(self.targets)
        else:
            self.targets = None
        layout.addWidget(self.body)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._apply_default(preselected_list_id)
        self.new_list.toggled.connect(self._refresh)
        self.new_name.textChanged.connect(self._refresh)
        if self.language is not None:
            self.language.currentIndexChanged.connect(self._refresh)
        if self.targets is not None:
            self.targets.itemChanged.connect(lambda _item: self._refresh())
        self._refresh()

    # -- state -------------------------------------------------------------

    def _apply_default(self, preselected_list_id: int | None) -> None:
        preview = self.outcome.preview
        assert preview is not None
        default = self._service.default_import_target(preview)
        wanted = set(default.list_ids)
        if preselected_list_id is not None:
            wanted = {preselected_list_id}
            default = ImportTarget(list_ids=(preselected_list_id,))
        self.new_list.setChecked(default.new_list is not None)
        self.new_name.setText(
            default.new_list.name if default.new_list else preview.suggested.name
        )
        if self.targets is not None:
            for row in range(self.targets.count()):
                item = self.targets.item(row)
                if item.data(Qt.ItemDataRole.UserRole) in wanted:
                    item.setCheckState(Qt.CheckState.Checked)

    def target(self) -> ImportTarget:
        list_ids: list[int] = []
        if self.targets is not None:
            for row in range(self.targets.count()):
                item = self.targets.item(row)
                if item.checkState() == Qt.CheckState.Checked:
                    list_ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        new_list = None
        if self.new_list.isChecked():
            new_list = NewList(
                name=self.new_name.text(),
                language=self.chosen_language(),
                description=self.outcome.preview.suggested.description
                if self.outcome.preview
                else None,
            )
        return ImportTarget(list_ids=tuple(list_ids), new_list=new_list)

    def chosen_language(self) -> str | None:
        if self.language is None:
            return None
        value = self.language.currentData()
        return None if value == UNDETERMINED else value

    @property
    def included(self) -> bool:
        return self.include.isChecked() and self.outcome.preview is not None

    def problem(self) -> str | None:
        """Why this file cannot be imported as configured, or ``None``."""
        if not self.included:
            return None
        target = self.target()
        if target.is_empty:
            return "Choose at least one list."
        if target.new_list is not None and not target.new_list.name.strip():
            return "Give the new list a name."
        if target.new_list is not None:
            existing = self._service.lists()
            wanted = target.new_list.name.strip().casefold()
            if any(lst.name.casefold() == wanted for lst in existing):
                return f"A list called “{target.new_list.name.strip()}” already exists."
        try:
            self._service.resolve_import_language(
                self.outcome.preview, target, self.chosen_language()
            )
        except LexiTrackError as exc:
            return exc.user_message
        return None

    def job(self) -> CommitJob:
        assert self.outcome.preview is not None
        return CommitJob(self.outcome.preview, self.target(), self.chosen_language())

    def _on_include(self, included: bool) -> None:
        if self.body is not None:
            self.body.setEnabled(included)
        self._refresh()

    def _refresh(self) -> None:
        preview = self.outcome.preview
        if preview is None:
            return
        target = self.target()
        try:
            language = self._service.resolve_import_language(
                preview, target, self.chosen_language()
            )
        except LexiTrackError:
            language = preview.stated_language or UNDETERMINED
        check = self._service.check_import(preview, language)
        duplicates = preview.parsed_count - preview.unique_count
        parts = [
            preview.format_label,
            f"{check.total:,} words",
            f"{check.new:,} new",
        ]
        if check.existing:
            parts.append(f"{check.existing:,} already in your vocabulary")
        if duplicates:
            parts.append(f"{duplicates:,} repeats merged")
        self.detail.setText("  ·  ".join(parts))

        problem = self.problem()
        if problem:
            self.status.setObjectName("ErrorText")
            self.status.setText(problem)
        elif self.included:
            self.status.setObjectName("Faint")
            self.status.setText(f"Words will be {language_name(language)}.")
        else:
            self.status.setObjectName("Faint")
            self.status.setText("Skipped.")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.changed.emit()


def _faint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    return label


# -- the dialog ------------------------------------------------------------


class ImportDialog(QDialog):
    """Choose files, preview them, choose lists, import."""

    _CHOOSE, _WORKING, _PREVIEW, _DONE = range(4)

    def __init__(
        self,
        service: VocabularyService,
        parent: QWidget | None = None,
        target_list_id: int | None = None,
        initial_paths: list[Path] | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._target_list_id = target_list_id
        self._paths: list[Path] = []
        self._panels: list[FilePanel] = []
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self.results: list[CommitOutcome] = []

        target = service.get_list(target_list_id) if target_list_id else None
        self.setWindowTitle(f"Import into {target.name}" if target else "Import")
        self.setMinimumSize(620, 520)
        self._build(target.name if target else None)
        if initial_paths:
            self._set_paths(initial_paths)

    # -- construction ------------------------------------------------------

    def _build(self, target_name: str | None) -> None:
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        layout.setSpacing(m.space_4)
        hint = (
            "PDF word lists (including the Oxford 3000 and 5000), any text-based PDF, "
            "and LexiTrack JSON files. Words you have already reviewed keep their status."
        )
        layout.addLayout(
            dialog_header(f"Import into {target_name}" if target_name else "Import", hint)
        )

        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)

        # 1. choose
        choose = QWidget()
        choose_layout = QVBoxLayout(choose)
        choose_layout.setContentsMargins(0, 0, 0, 0)
        choose_layout.setSpacing(m.space_3)
        row = QHBoxLayout()
        self.files_label = QLabel("No files selected")
        self.files_label.setObjectName("FilePathLabel")
        self.files_label.setProperty("state", "empty")
        self.files_label.setWordWrap(True)
        row.addWidget(self.files_label, 1)
        browse = QPushButton("Choose Files…")
        browse.clicked.connect(self._choose_files)
        row.addWidget(browse, 0, Qt.AlignmentFlag.AlignTop)
        choose_layout.addLayout(row)

        parser_row = QHBoxLayout()
        parser_row.addWidget(_faint("Format"))
        self.parser = QComboBox()
        self.parser.addItem("Detect automatically", AUTO)
        for info in self._service.available_parsers():
            self.parser.addItem(info.name, info.key)
            self.parser.setItemData(
                self.parser.count() - 1, info.description, Qt.ItemDataRole.ToolTipRole
            )
        parser_row.addWidget(self.parser)
        parser_row.addStretch(1)
        choose_layout.addLayout(parser_row)
        note = QLabel("Choose a format manually only if detection gets it wrong.")
        note.setObjectName("Faint")
        choose_layout.addWidget(note)
        choose_layout.addStretch(1)
        self.pages.addWidget(choose)

        # 2. working
        working = QWidget()
        working_layout = QVBoxLayout(working)
        working_layout.addStretch(1)
        self.progress_label = QLabel("Reading…")
        self.progress_label.setObjectName("DialogHint")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        working_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        working_layout.addWidget(self.progress_bar)
        working_layout.addStretch(1)
        self.pages.addWidget(working)

        # 3. preview
        self.preview_area = QScrollArea()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setFrameShape(QFrame.Shape.NoFrame)
        self.pages.addWidget(self.preview_area)

        # 4. done
        done = QWidget()
        done_layout = QVBoxLayout(done)
        done_layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        done_layout.addWidget(self.summary)
        done_layout.addStretch(1)
        self.pages.addWidget(done)

        # buttons
        buttons = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._back)
        buttons.addWidget(self.back_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel_button)
        self.primary_button = QPushButton("Continue")
        self.primary_button.setProperty("variant", "primary")
        self.primary_button.setDefault(True)
        self.primary_button.clicked.connect(self._primary)
        buttons.addWidget(self.primary_button)
        layout.addLayout(buttons)
        self._show_page(self._CHOOSE)

    # -- navigation --------------------------------------------------------

    def _show_page(self, page: int) -> None:
        self.pages.setCurrentIndex(page)
        self.back_button.setVisible(page == self._PREVIEW)
        self.cancel_button.setVisible(page != self._DONE)
        self.cancel_button.setEnabled(True)
        self.primary_button.setVisible(page != self._WORKING)
        if page == self._CHOOSE:
            self.primary_button.setText("Continue")
            self.primary_button.setEnabled(bool(self._paths))
        elif page == self._PREVIEW:
            self.primary_button.setText("Import")
            self._update_import_button()
        elif page == self._DONE:
            self.primary_button.setText("Done")
            self.primary_button.setEnabled(True)

    def _primary(self) -> None:
        page = self.pages.currentIndex()
        if page == self._CHOOSE:
            self._read()
        elif page == self._PREVIEW:
            self._commit()
        elif page == self._DONE:
            self.accept()

    def _back(self) -> None:
        self._show_page(self._CHOOSE)

    def _cancel(self) -> None:
        if isinstance(self._worker, ReadWorker):
            self._worker.cancel()
            self.progress_label.setText("Cancelling…")
            self.cancel_button.setEnabled(False)
            return
        if isinstance(self._worker, CommitWorker):
            return  # a write in progress finishes; it is one transaction per file
        self.reject()

    # -- step 1 ------------------------------------------------------------

    def _choose_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(
            self, "Choose files to import", str(Path.home()), FILE_FILTER
        )
        if chosen:
            self._set_paths([Path(p) for p in chosen])

    def _set_paths(self, paths: list[Path]) -> None:
        self._paths = paths
        names = ", ".join(p.name for p in paths[:4])
        if len(paths) > 4:
            names += f" and {len(paths) - 4} more"
        self.files_label.setText(names)
        self.files_label.setToolTip("\n".join(str(p) for p in paths))
        self.files_label.setProperty("state", "selected")
        self.files_label.style().unpolish(self.files_label)
        self.files_label.style().polish(self.files_label)
        self.primary_button.setEnabled(True)

    def _read(self) -> None:
        if not self._paths:
            return
        self._show_page(self._WORKING)
        self.progress_label.setText("Reading…")
        self.progress_bar.setRange(0, 0)
        worker = ReadWorker(self._service, list(self._paths), self.parser.currentData())
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_read)
        self._start(worker)

    def _on_read(self, outcomes: list[FileOutcome]) -> None:
        self._stop()
        if not outcomes:  # cancelled before anything was read
            self._show_page(self._CHOOSE)
            return
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(METRICS.space_3)
        self._panels = []
        for outcome in outcomes:
            panel = FilePanel(self._service, outcome, self._target_list_id)
            panel.changed.connect(self._update_import_button)
            self._panels.append(panel)
            column.addWidget(panel)
        column.addStretch(1)
        self.preview_area.setWidget(content)
        self._show_page(self._PREVIEW)

    # -- step 2 ------------------------------------------------------------

    def _update_import_button(self) -> None:
        if self.pages.currentIndex() != self._PREVIEW:
            return
        included = [panel for panel in self._panels if panel.included]
        ok = bool(included) and all(panel.problem() is None for panel in included)
        self.primary_button.setEnabled(ok)
        count = len(included)
        self.primary_button.setText("Import" if count <= 1 else f"Import {count} Files")

    def _commit(self) -> None:
        jobs = [panel.job() for panel in self._panels if panel.included and not panel.problem()]
        if not jobs:
            return
        self._show_page(self._WORKING)
        self.progress_bar.setRange(0, 100)
        self.progress_label.setText("Saving…")
        worker = CommitWorker(self._service, jobs)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_committed)
        self._start(worker)

    def _on_committed(self, outcomes: list[CommitOutcome]) -> None:
        self._stop()
        self.results = outcomes
        failed_reads = [
            panel.outcome for panel in self._panels if panel.outcome.preview is None
        ]
        self.summary.setText(summarise(outcomes, failed_reads))
        self._show_page(self._DONE)

    # -- threads -----------------------------------------------------------

    def _on_progress(self, message: str, percent: int) -> None:
        self.progress_label.setText(message)
        if percent < 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)

    def _start(self, worker: QObject) -> None:
        self._thread = QThread(self)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        self._thread.start()

    def _stop(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(10_000)
            self._thread = None
        self._worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        if isinstance(self._worker, ReadWorker):
            self._worker.cancel()
        self._stop()
        super().closeEvent(event)

    @property
    def imported_anything(self) -> bool:
        return any(outcome.result is not None for outcome in self.results)


def summarise(outcomes: list[CommitOutcome], failed_reads: list[FileOutcome]) -> str:
    """Plain-language result, one paragraph per file."""
    paragraphs: list[str] = []
    for outcome in outcomes:
        if outcome.result is None:
            paragraphs.append(f"<b>{outcome.path.name}</b> was not imported. {outcome.error}")
            continue
        result = outcome.result
        lists = ", ".join(f"“{name}”" for name in result.list_names)
        lines = [f"<b>{outcome.path.name}</b> → {lists}"]
        new = result.new_words
        lines.append(f"{new:,} new {'word' if new == 1 else 'words'} added to your vocabulary.")
        if result.existing_words:
            lines.append(
                f"{result.existing_words:,} were already in your vocabulary and kept their status."
            )
        if not result.added_to_lists:
            lines.append("Every word was already in the chosen lists.")
        paragraphs.append("<br>".join(lines))
    for failed in failed_reads:
        paragraphs.append(f"<b>{failed.path.name}</b> was skipped. {failed.error}")
    return "<br><br>".join(paragraphs)
