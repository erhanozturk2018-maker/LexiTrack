"""Backups: a copy now, the daily copies, and restoring one — on its own page.

As on the phone (Settings ▸ Backups), backups are not mixed in with
exporting. The window answers three questions:

* **Is there a copy?** One is made every day the app runs; the newest ten
  are kept. *Back up now* makes one at once.
* **Can I go back to a day?** The daily copies are listed, newest first;
  *Restore…* goes back to the one picked.
* **Can I bring everything back from a file?** *Restore from a file…* reads
  a ``.lexitrack`` file (Export ▸ Everything, in one file).

Every restore saves a copy of what is there first.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..services import portable
from ..services.learning_service import LearningService
from ..services.maintenance import Maintenance
from ..services.vocabulary_service import VocabularyService
from .components.settings_rows import SettingsGroup, page, scrolled
from .dialogs import confirm, error_label, show_error
from .theme.palette import METRICS


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("SettingHint")
    label.setWordWrap(True)
    return label


def _day_of(backup: Path) -> str:
    """"vocabulary-2026-10-04" → "2026-10-04" (the rest of the name, if any, kept)."""
    return backup.stem.replace("vocabulary-", "")


class BackupsDialog(QDialog):
    def __init__(
        self,
        service: VocabularyService,
        engine: LearningService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Backups")
        self._service = service
        self._engine = engine
        self._maintenance = Maintenance(service.database, engine.clock)
        #: Set when data was restored, for the window behind to reload.
        self.changed = False
        self.resize(660, 720)
        self._build()
        self._refresh()

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = page(
            "Backups",
            "LexiTrack makes a copy every day it runs and keeps the newest ten. "
            "Every restore saves a copy of what is here first.",
        )

        now = SettingsGroup("A COPY NOW")
        self.latest = _hint()
        backup_now = QPushButton("Back up now")
        backup_now.setProperty("variant", "primary")
        backup_now.clicked.connect(self._backup_now)
        now.add("Daily copy", self.latest, backup_now)
        layout.addWidget(now)

        days = SettingsGroup("ON THIS COMPUTER")
        self.copies = QListWidget()
        self.copies.setObjectName("BackupList")
        self.copies.setMinimumHeight(150)
        self.copies.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.copies.itemDoubleClicked.connect(lambda _item: self._restore_backup())
        days.add_widget(self.copies)
        row = QWidget()
        row.setObjectName("PanelBody")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(m.space_2)
        self.restore_button = QPushButton("Restore…")
        self.restore_button.setToolTip("Go back to the copy picked above")
        self.restore_button.clicked.connect(self._restore_backup)
        line.addWidget(self.restore_button)
        folder = QPushButton("Open folder")
        folder.setToolTip(str(self._maintenance.directory))
        folder.clicked.connect(self._open_folder)
        line.addWidget(folder)
        line.addStretch(1)
        days.add_widget(row)
        layout.addWidget(days)

        from_file = SettingsGroup("FROM A FILE")
        restore_file = QPushButton("Restore from a file…")
        restore_file.clicked.connect(self._restore_portable)
        from_file.add(
            "Everything, from a .lexitrack file",
            "Made by Export ▸ Everything, in one file, here or on another computer. "
            "It replaces everything here.",
            restore_file,
        )
        layout.addWidget(from_file)

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

    # -- state ---------------------------------------------------------------

    def _refresh(self) -> None:
        backups = self._maintenance.backups()
        self.latest.setText(
            f"Made every day, the newest ten kept. Latest: {_day_of(backups[0])}."
            if backups
            else "Made every day, the newest ten kept. None yet."
        )
        self.copies.clear()
        for backup in backups:
            size = backup.stat().st_size / 1024 if backup.exists() else 0
            item = QListWidgetItem(f"{_day_of(backup)}    ·    {size:,.0f} KB")
            item.setData(256, str(backup))
            self.copies.addItem(item)
        if backups:
            self.copies.setCurrentRow(0)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.restore_button.setEnabled(self.copies.currentItem() is not None)

    def _picked(self) -> tuple[str, str] | None:
        item = self.copies.currentItem()
        if item is None:
            return None
        return str(item.data(256)), _day_of(Path(str(item.data(256))))

    # -- actions -------------------------------------------------------------

    def _backup_now(self) -> None:
        target = self._maintenance.backup()
        if target is None:
            show_error(self.error, "The backup failed. The log file has the details.")
        else:
            self._say(f"Backed up to {target.name}.")
        self._refresh()

    def _restore_backup(self) -> None:
        picked = self._picked()
        if picked is None:
            return
        path, day = picked
        if not confirm(
            self,
            "Go back to that day?",
            f"Everything goes back to the copy of {day}; anything done since is "
            "replaced.\n\nA copy of what is here now is saved in the backups folder first.",
            "Restore",
            irreversible=False,
        ):
            return
        try:
            safety = self._maintenance.restore_backup(path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        self._restored(f"Restored the copy of {day}. What was here is in {safety.name}.")

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
            irreversible=False,
        ):
            return
        try:
            safety = self._maintenance.safety_copy()
            portable.restore(self._service.database, path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        self._restored(f"Restored from {Path(path).name}. What was here is in {safety.name}.")

    def _restored(self, text: str) -> None:
        self.changed = True
        self._engine.refresh_settings()
        self._say(text)
        self._refresh()

    def _open_folder(self) -> None:
        folder = self._maintenance.directory
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _say(self, text: str) -> None:
        show_error(self.error, None)
        self.message.setText(text)
