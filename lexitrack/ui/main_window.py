"""The application window.

Three screens live in a stack — welcome, review and completed — and the window
switches between them based on one question asked of the service: is there a
next word? Keeping that decision in a single method means the UI cannot drift
out of step with the database.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..repositories.word_repository import ImportResult
from ..services.vocabulary_service import VocabularyService
from .empty_state import CompletedState, WelcomeState
from .import_dialog import ImportDialog
from .progress_widget import ProgressBarWidget, StatsBar
from .review_widget import ReviewWidget
from .theme import ThemeManager, ThemeName
from .theme.palette import METRICS

log = logging.getLogger(__name__)

_WELCOME_PAGE = 0
_REVIEW_PAGE = 1
_COMPLETED_PAGE = 2


class MainWindow(QMainWindow):
    """LexiTrack's main window."""

    def __init__(
        self,
        service: VocabularyService,
        theme: ThemeManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._theme = theme
        #: The word answered most recently, so Undo has something to undo.
        self._last_answered_id: int | None = None

        self.setWindowTitle("LexiTrack")
        self.resize(940, 720)
        self.setMinimumSize(620, 560)

        self._build_menu()
        self._build_body()
        self.refresh()

    # -- construction ------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")

        self._import_action = QAction("&Import PDF…", self)
        self._import_action.setShortcut(QKeySequence.StandardKey.Open)
        self._import_action.triggered.connect(self.import_pdf)
        file_menu.addAction(self._import_action)

        file_menu.addSeparator()

        self._export_pdf_action = QAction("Export Unknown Words as &PDF…", self)
        self._export_pdf_action.triggered.connect(lambda: self.export_unknown("pdf"))
        file_menu.addAction(self._export_pdf_action)

        self._export_csv_action = QAction("Export Unknown Words as &CSV…", self)
        self._export_csv_action.triggered.connect(lambda: self.export_unknown("csv"))
        file_menu.addAction(self._export_csv_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menu_bar.addMenu("&View")

        self._theme_action = QAction(self)
        self._theme_action.setShortcut("Ctrl+T")
        self._theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(self._theme_action)
        self._update_theme_action_text()

        review_menu = menu_bar.addMenu("&Review")

        self._undo_action = QAction("&Undo Last Answer", self)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.setEnabled(False)
        self._undo_action.triggered.connect(self.undo_last_answer)
        review_menu.addAction(self._undo_action)

        self._reset_action = QAction("&Reset All Progress…", self)
        self._reset_action.triggered.connect(self.reset_progress)
        review_menu.addAction(self._reset_action)

        help_menu = menu_bar.addMenu("&Help")

        open_data_action = QAction("Open &Data Folder", self)
        open_data_action.triggered.connect(self._open_data_folder)
        help_menu.addAction(open_data_action)

        about_action = QAction("&About LexiTrack", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _build_body(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_app_bar())

        self._pages = QStackedWidget()

        self._welcome = WelcomeState()
        self._welcome.import_requested.connect(self.import_pdf)

        self._review = ReviewWidget()
        self._review.answered.connect(self._on_answered)
        self._review.undo_requested.connect(self.undo_last_answer)

        self._completed = CompletedState()
        self._completed.export_requested.connect(lambda: self.export_unknown("pdf"))
        self._completed.import_requested.connect(self.import_pdf)

        self._pages.addWidget(self._welcome)
        self._pages.addWidget(self._review)
        self._pages.addWidget(self._completed)
        layout.addWidget(self._pages, 1)

        self._stats = StatsBar()
        layout.addWidget(self._stats)

        self.setCentralWidget(central)

    def _build_app_bar(self) -> QWidget:
        m = METRICS

        bar = QFrame()
        bar.setObjectName("AppBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(m.space_5, m.space_3, m.space_5, m.space_3)
        layout.setSpacing(m.space_4)

        title = QLabel("LexiTrack")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        self._progress_bar = ProgressBarWidget()
        layout.addWidget(self._progress_bar, 1)

        import_button = QPushButton("Import")
        import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        import_button.setToolTip("Import a PDF (Ctrl+O)")
        import_button.clicked.connect(self.import_pdf)
        layout.addWidget(import_button)

        self._export_button = QPushButton("Export")
        self._export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_button.setToolTip("Export the words you marked as unknown")
        self._export_button.clicked.connect(lambda: self.export_unknown("pdf"))
        layout.addWidget(self._export_button)

        self._theme_button = QPushButton()
        self._theme_button.setProperty("variant", "ghost")
        self._theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_button.setToolTip("Switch between Light and Dark (Ctrl+T)")
        self._theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self._theme_button)
        self._update_theme_button_text()

        return bar

    # -- state -------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the database and show whichever screen now applies."""
        try:
            progress = self._service.get_progress()
            word = self._service.get_next_word()
        except LexiTrackError as exc:
            self._show_error("Could not read your vocabulary", exc.user_message)
            return

        self._stats.update_progress(progress)
        self._progress_bar.update_progress(progress)

        has_unknown = progress.unknown > 0
        for action in (self._export_pdf_action, self._export_csv_action):
            action.setEnabled(has_unknown)
        self._export_button.setEnabled(has_unknown)
        self._reset_action.setEnabled(progress.total > 0)

        undo_available = self._last_answered_id is not None
        self._undo_action.setEnabled(undo_available)
        self._review.set_undo_enabled(undo_available)

        if progress.total == 0:
            self._pages.setCurrentIndex(_WELCOME_PAGE)
            return

        if word is None:
            self._completed.update_summary(progress.known, progress.unknown)
            self._pages.setCurrentIndex(_COMPLETED_PAGE)
            return

        self._review.show_word(word, progress.reviewed + 1, progress.total)
        self._pages.setCurrentIndex(_REVIEW_PAGE)
        self._review.setFocus()

    # -- review ------------------------------------------------------------

    def _on_answered(self, word_id: int, known: bool) -> None:
        try:
            self._service.mark(word_id, known)
        except LexiTrackError as exc:
            self._show_error("Could not save your answer", exc.user_message)
            return
        self._last_answered_id = word_id
        self.refresh()

    def undo_last_answer(self) -> None:
        """Return the most recently answered word to the queue."""
        if self._last_answered_id is None:
            return
        try:
            self._service.undo(self._last_answered_id)
        except LexiTrackError as exc:
            self._show_error("Could not undo", exc.user_message)
            return
        self._last_answered_id = None
        self.refresh()

    def reset_progress(self) -> None:
        """Clear every answer, after confirming — this cannot be undone."""
        progress = self._service.get_progress()
        if progress.reviewed == 0:
            return

        confirmed = QMessageBox.question(
            self,
            "Reset all progress?",
            f"This marks all {progress.total:,} words as not reviewed, clearing "
            f"{progress.known:,} known and {progress.unknown:,} unknown answers.\n\n"
            "Your imported words are kept. This cannot be undone.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Reset,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmed != QMessageBox.StandardButton.Reset:
            return

        try:
            self._service.reset_progress()
        except LexiTrackError as exc:
            self._show_error("Could not reset progress", exc.user_message)
            return
        self._last_answered_id = None
        self.refresh()

    # -- import ------------------------------------------------------------

    def import_pdf(self) -> None:
        """Open the import dialog and refresh when it succeeds."""
        dialog = ImportDialog(self._service, self)
        if dialog.exec() != ImportDialog.DialogCode.Accepted:
            return

        self._last_answered_id = None
        self.refresh()
        if dialog.result_data is not None:
            self._report_import(dialog.result_data)

    def _report_import(self, result: ImportResult) -> None:
        if result.total_words == 0:
            return

        lines = [f"Imported {result.source_name}."]
        if result.new_words:
            noun = "word" if result.new_words == 1 else "words"
            lines.append(f"{result.new_words:,} new {noun} added to your review queue.")
        if result.existing_words:
            noun = "word was" if result.existing_words == 1 else "words were"
            lines.append(
                f"{result.existing_words:,} {noun} already in your vocabulary "
                "and kept their review state."
            )
        if not result.new_words:
            lines.append("There is nothing new to review from this document.")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Import complete")
        box.setText("\n".join(lines))
        box.exec()

    # -- export ------------------------------------------------------------

    def export_unknown(self, file_format: str) -> None:
        """Export the unknown words as ``pdf`` or ``csv``."""
        count = self._service.unknown_count()
        if count == 0:
            QMessageBox.information(
                self,
                "Nothing to export",
                "You have not marked any words as unknown yet.",
            )
            return

        paths.ensure_data_dirs()
        suggested = paths.exports_dir() / f"unknown_words.{file_format}"
        label = "PDF file (*.pdf)" if file_format == "pdf" else "CSV file (*.csv)"

        path_text, _ = QFileDialog.getSaveFileName(
            self, "Export Unknown Words", str(suggested), label
        )
        if not path_text:
            return

        target = Path(path_text)
        try:
            if file_format == "pdf":
                self._service.export_unknown_pdf(target)
            else:
                self._service.export_unknown_csv(target)
        except LexiTrackError as exc:
            self._show_error("Export failed", exc.user_message)
            return

        noun = "word" if count == 1 else "words"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Export complete")
        box.setText(f"Exported {count:,} {noun} to:\n{target}")
        open_button = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

    # -- appearance --------------------------------------------------------

    def toggle_theme(self) -> None:
        self._theme.toggle()
        self._update_theme_button_text()
        self._update_theme_action_text()

    def _update_theme_button_text(self) -> None:
        # The button names the theme it switches *to*, which is what the user
        # is deciding about.
        going_dark = self._theme.current is ThemeName.LIGHT
        self._theme_button.setText("Dark" if going_dark else "Light")

    def _update_theme_action_text(self) -> None:
        going_dark = self._theme.current is ThemeName.LIGHT
        self._theme_action.setText(
            "Switch to &Dark Mode" if going_dark else "Switch to &Light Mode"
        )

    # -- misc --------------------------------------------------------------

    def _open_data_folder(self) -> None:
        folder = paths.ensure_data_dirs()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About LexiTrack",
            "<h3>LexiTrack</h3>"
            "<p>PDF Vocabulary Learning &amp; Review</p>"
            "<p>Import vocabulary from PDF documents and review it one word at "
            "a time. Your progress is stored locally and nothing leaves your "
            "computer.</p>"
            f"<p style='color:gray'>Data folder: {paths.data_dir()}</p>",
        )

    def _show_error(self, title: str, message: str) -> None:
        log.error("%s: %s", title, message)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._service.close()
        super().closeEvent(event)
