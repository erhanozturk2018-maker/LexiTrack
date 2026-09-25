"""Export and backup: every way out of LexiTrack, and the way back in.

One window instead of scattered menu items. What is in it answers four
questions:

* **Which words?** Any set — a list or all of them, by status, by level, by
  where they are in learning — as a PDF, CSV or JSON file, previewed before
  it is saved (the Export dialog).
* **Word content** — batches out to be enriched, filled files back in.
* **Learning data** — every answer and every attempt, as CSV, to look at in a
  spreadsheet.
* **Backup** — a copy now, the daily copies, and the portable ``.lexitrack``
  file with everything; and restoring from either, after saving a copy of
  what is there.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..models.user_word_state import ReviewStatus
from ..models.word_entry import CEFR_ORDER
from ..services import portable
from ..services.learning_service import LearningService
from ..services.maintenance import Maintenance
from ..services.vocabulary_service import VocabularyService
from ..services.word_filter import LearningState, WordFilter, filter_words
from .components.settings_rows import CONTROL_WIDTH, SettingsGroup, page, scrolled
from .dialogs import confirm, error_label, show_error
from .export_dialog import ExportDialog, ExportScope
from .theme.palette import METRICS

_WIDE = CONTROL_WIDTH + 80


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("SettingHint")
    label.setWordWrap(True)
    return label


class ExportCenter(QDialog):
    def __init__(
        self,
        service: VocabularyService,
        engine: LearningService,
        open_content: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export and Backup")
        self._service = service
        self._engine = engine
        self._open_content = open_content
        self._maintenance = Maintenance(service.database, engine.clock)
        #: Set when data was restored, for the window behind to reload.
        self.changed = False
        self.resize(760, 780)
        self._build()
        self._refresh_words()
        self._refresh_backups()

    # -- building --------------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = page(
            "Export and backup",
            "Everything that is yours can leave LexiTrack, in files anything can read — "
            "and come back.",
        )

        words = SettingsGroup("WORDS")
        self.from_list = QComboBox()
        self.from_list.addItem("All lists", None)
        for lst in self._service.lists():
            self.from_list.addItem(lst.name, lst.id)
        self.status = QComboBox()
        self.status.addItem("Any", None)
        for status, name in (
            (ReviewStatus.UNKNOWN, "Unknown"),
            (ReviewStatus.KNOWN, "Known"),
            (ReviewStatus.NOT_REVIEWED, "Not reviewed"),
        ):
            self.status.addItem(name, status)
        self.level = QComboBox()
        self.level.addItem("Any", None)
        for level in CEFR_ORDER:
            self.level.addItem(level, level)
        self.state = QComboBox()
        for state in LearningState:
            self.state.addItem(state.label, state)
        for combo, title, hint in (
            (self.from_list, "From", "One list, or every list."),
            (self.status, "Status", "Known, Unknown or not yet sorted."),
            (self.level, "Level", "CEFR level, where the word has one."),
            (self.state, "Learning", "Where the word is in your study plan."),
        ):
            combo.setFixedWidth(_WIDE)
            combo.currentIndexChanged.connect(self._refresh_words)
            words.add(title, hint, combo)
        self.words_count = _hint()
        self.export_words_button = QPushButton("Export…")
        self.export_words_button.setProperty("variant", "primary")
        self.export_words_button.clicked.connect(self._export_words)
        words.add("Words", self.words_count, self.export_words_button)
        layout.addWidget(words)

        if self._open_content is not None:
            content = SettingsGroup("WORD CONTENT")
            button = QPushButton("Word content…")
            button.clicked.connect(self._content)
            content.add(
                "Meanings and examples",
                "Send words out to be enriched, and import the filled file.",
                button,
            )
            layout.addWidget(content)

        data = SettingsGroup("LEARNING DATA")
        answers = QPushButton("Export…")
        answers.clicked.connect(self._export_answers)
        data.add(
            "Every answer",
            "Each review with its rating, what it showed about the memory, and the "
            "schedule it produced, as CSV. Answers taken back are included and marked.",
            answers,
        )
        attempts = QPushButton("Export…")
        attempts.clicked.connect(self._export_attempts)
        data.add(
            "Every attempt",
            "Each question asked — task, level, success, effort, time — as CSV: the "
            "record your skills are read from.",
            attempts,
        )
        layout.addWidget(data)

        backup = SettingsGroup("BACKUP")
        self.backup_hint = _hint()
        backup_now = QPushButton("Back up now")
        backup_now.clicked.connect(self._backup_now)
        backup.add("Daily copy", self.backup_hint, backup_now)
        export_all = QPushButton("Export…")
        export_all.clicked.connect(self._export_portable)
        backup.add(
            "Everything, in one file",
            "A .lexitrack file: words, lists, statuses, plans, cards, every review and "
            "attempt, content in every language, settings. Readable JSON inside.",
            export_all,
        )
        restore_file = QPushButton("Restore…")
        restore_file.clicked.connect(self._restore_portable)
        backup.add(
            "Restore a .lexitrack file",
            "Replaces everything with the file's contents. A copy of what is here now "
            "is saved first.",
            restore_file,
        )
        row = QWidget()
        row.setObjectName("PanelBody")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(m.space_2)
        self.backup_choice = QComboBox()
        self.backup_choice.setFixedWidth(_WIDE)
        line.addWidget(self.backup_choice)
        self.restore_backup_button = QPushButton("Restore…")
        self.restore_backup_button.clicked.connect(self._restore_backup)
        line.addWidget(self.restore_backup_button)
        backup.add(
            "Restore a daily copy",
            "Back to how things were on that day. A copy of what is here now is saved "
            "first.",
            row,
        )
        folder = QPushButton("Open")
        folder.clicked.connect(self._open_folder)
        backup.add("Backups folder", str(self._maintenance.directory), folder)
        layout.addWidget(backup)

        self.message = _hint()
        layout.addWidget(self.message)
        self.error = error_label()
        layout.addWidget(self.error)
        layout.addStretch(1)
        outer.addWidget(scrolled(body), 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(m.space_5, m.space_3, m.space_5, m.space_4)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        outer.addLayout(footer)

    # -- words -----------------------------------------------------------------

    def _filter(self) -> WordFilter:
        # Qt hands enum values back as plain strings: turned back into enums
        # here, or "any" would compare unequal to LearningState.ANY.
        status = self.status.currentData()
        return WordFilter(
            list_id=self.from_list.currentData(),
            status=ReviewStatus(status) if status else None,
            cefr=self.level.currentData(),
            state=LearningState(self.state.currentData() or LearningState.ANY),
        )

    def _refresh_words(self) -> None:
        self._words = filter_words(self._service, self._engine, self._filter())
        count = len(self._words)
        self.words_count.setText(
            f"{count:,} {'word' if count == 1 else 'words'}, as PDF, CSV or JSON — you see "
            "the file before it is saved."
        )
        self.export_words_button.setEnabled(bool(count))

    def _export_words(self) -> None:
        ids = [word.id for word in self._words]
        parts = [self.from_list.currentText()]
        for combo in (self.status, self.level, self.state):
            if combo.currentData() not in (None, LearningState.ANY):
                parts.append(combo.currentText())
        label = f"{' · '.join(parts)} ({len(ids):,})"
        scope = ExportScope(label, lambda: self._service.export_content_for_selection(ids))
        ExportDialog(self._service, [scope], parent=self).exec()

    def _content(self) -> None:
        if self._open_content is not None:
            self._open_content()

    # -- learning data -------------------------------------------------------------

    def _save_csv(self, title: str, name: str) -> Path | None:
        default = paths.exports_dir() / f"{name}-{self._engine.clock.today()}.csv"
        path, _ = QFileDialog.getSaveFileName(self, title, str(default), "CSV files (*.csv)")
        return Path(path) if path else None

    def _export_answers(self) -> None:
        path = self._save_csv("Export Every Answer", "answers")
        if path:
            rows = self._maintenance.export_review_log(path)
            self._say(f"{rows:,} answers written to {path.name}.")

    def _export_attempts(self) -> None:
        path = self._save_csv("Export Every Attempt", "attempts")
        if path:
            rows = self._maintenance.export_attempts(path)
            self._say(f"{rows:,} attempts written to {path.name}.")

    # -- backup --------------------------------------------------------------------

    def _refresh_backups(self) -> None:
        backups = self._maintenance.backups()
        self.backup_hint.setText(
            f"Made every day, the newest ten kept. Latest: {backups[0].stem[11:]}."
            if backups
            else "Made every day, the newest ten kept. None yet."
        )
        self.backup_choice.clear()
        for backup in backups:
            self.backup_choice.addItem(backup.stem.replace("vocabulary-", ""), str(backup))
        self.backup_choice.setEnabled(bool(backups))
        self.restore_backup_button.setEnabled(bool(backups))

    def _backup_now(self) -> None:
        target = self._maintenance.backup()
        if target is None:
            show_error(self.error, "The backup failed. The log file has the details.")
        else:
            self._say(f"Backed up to {target.name}.")
        self._refresh_backups()

    def _export_portable(self) -> None:
        default = paths.exports_dir() / f"lexitrack-{self._engine.clock.today()}.lexitrack"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Everything", str(default), "LexiTrack backup (*.lexitrack)"
        )
        if not path:
            return
        try:
            summary = portable.export(self._service.database, path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        self._say(
            f"Everything written to {summary.path.name}: {summary.words:,} words and "
            f"{summary.reviews:,} reviews."
        )

    def _restore_portable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore Everything", str(paths.exports_dir()), "LexiTrack backup (*.lexitrack)"
        )
        if not path:
            return
        try:
            summary = portable.read(path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        if not confirm(
            self,
            "Replace everything?",
            f"{Path(path).name} holds {summary.words:,} words and {summary.reviews:,} "
            f"reviews, saved {summary.created_at[:10]}. Everything here now — words, "
            "lists, progress, content, settings — is replaced by it.\n\nA copy of what is "
            "here now is saved in the backups folder first.",
            "Replace everything",
        ):
            return
        try:
            safety = self._maintenance.safety_copy()
            portable.restore(self._service.database, path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        self._restored(f"Restored from {Path(path).name}. What was here is in {safety.name}.")

    def _restore_backup(self) -> None:
        path = self.backup_choice.currentData()
        if not path:
            return
        if not confirm(
            self,
            "Go back to that day?",
            f"Everything goes back to the copy of {self.backup_choice.currentText()}; "
            "anything done since is replaced.\n\nA copy of what is here now is saved in "
            "the backups folder first.",
            "Restore",
        ):
            return
        try:
            safety = self._maintenance.restore_backup(path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        self._restored(
            f"Restored the copy of {self.backup_choice.currentText()}. What was here is in "
            f"{safety.name}."
        )

    def _restored(self, text: str) -> None:
        self.changed = True
        self._engine.refresh_settings()
        self._say(text)
        self._refresh_words()
        self._refresh_backups()

    def _open_folder(self) -> None:
        folder = self._maintenance.directory
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _say(self, text: str) -> None:
        show_error(self.error, None)
        self.message.setText(text)
