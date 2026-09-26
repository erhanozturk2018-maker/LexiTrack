"""Word contexts: how many words have them, and moving them in and out as JSON.

LexiTrack never writes a context itself. Contexts are added on a word's page,
or imported from a JSON file prepared anywhere — by hand, or with an LLM:

    [{"word": "sleep in", "contexts": ["I usually sleep in on Sundays."]}]

The window has three parts: how much of the vocabulary has contexts, an
export (words with their definition and contexts, to be filled in), and an
import, which shows what a file would add before anything is written. See
``docs/formats/contexts.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..services.content_service import ContentService, ContextImportPreview
from ..services.learning_service import LearningService
from ..services.vocabulary_service import VocabularyService
from .components.settings_rows import CONTROL_WIDTH, SettingsGroup, note, page, scrolled
from .dialogs import error_label, show_error
from .theme.palette import METRICS

_PLAN, _SELECTION, _ALL = "plan", "selection", "all"


class ContentDialog(QDialog):
    def __init__(
        self,
        service: VocabularyService,
        engine: LearningService,
        word_ids: Sequence[int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Word Contexts")
        self._service = service
        self._engine = engine
        self._content = ContentService(service.database)
        self._selection = [int(i) for i in word_ids] if word_ids else []
        #: Set when something was imported, for the window behind to refresh.
        self.changed = False
        self.resize(720, 640)
        self._build()
        self._refresh()

    # -- building --------------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body, layout = page(
            "Word contexts",
            "Sentences that show a word in use. A word with contexts is asked two "
            "ways — from its definition, and its definition from a context. Add them "
            "on a word's page, or import a JSON file written by hand or with any tool "
            "you like. LexiTrack never writes them itself.",
        )

        overview = SettingsGroup("YOUR WORDS")
        self.status_label = QLabel("")
        self.status_label.setObjectName("SettingHint")
        self.status_label.setWordWrap(True)
        self.status_scope = QLabel("")
        self.status_scope.setObjectName("SettingTitle")
        overview.add("With contexts", self.status_label, self.status_scope)
        layout.addWidget(overview)

        export = SettingsGroup("EXPORT")
        self.source = QComboBox()
        self.source.setFixedWidth(CONTROL_WIDTH + 80)
        if self._selection:
            self.source.addItem(f"The {len(self._selection)} words chosen", _SELECTION)
        self.source.addItem("All your words", _ALL)
        self.source.addItem("Your study plan", _PLAN)
        for lst in self._service.lists():
            self.source.addItem(lst.name, lst.id)
        self.source.currentIndexChanged.connect(self._refresh)
        export.add("Words from", "Each with its length, level, part of speech, "
                   "definition and contexts.", self.source)
        self.only_missing = QCheckBox("Only words without contexts")
        self.only_missing.toggled.connect(self._refresh)
        export.add("Which", "To fill in: an empty “contexts” list for each.", self.only_missing)
        self.export_summary = QLabel("")
        self.export_summary.setObjectName("SettingHint")
        self.export_summary.setWordWrap(True)
        self.export_button = QPushButton("Export…")
        self.export_button.setProperty("variant", "primary")
        self.export_button.clicked.connect(self._export)
        export.add("File", self.export_summary, self.export_button)
        layout.addWidget(export)

        take_in = SettingsGroup("IMPORT")
        import_button = QPushButton("Choose file…")
        import_button.clicked.connect(self._import)
        take_in.add(
            "Contexts file",
            "Each word is found among your words and its new contexts are added; a "
            "word is never created, and a sentence it already has is not added twice. "
            "You see what it would add before anything is written.",
            import_button,
        )
        layout.addWidget(take_in)
        layout.addWidget(note(
            'The file is a list: [{"word": "sleep in", "contexts": ["…", "…"]}]. A '
            '"definition" may be given too, to replace the word\'s. The format is in '
            "docs/formats/contexts.md."
        ))

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

    # -- state ------------------------------------------------------------------

    def _scope_ids(self) -> list[int]:
        choice = self.source.currentData()
        if choice == _SELECTION:
            return list(self._selection)
        if choice == _PLAN:
            return self._engine.plan_word_ids()
        if choice == _ALL:
            return self._content.all_word_ids()
        return [word.id for word in self._service.list_words(int(choice))]

    def _export_ids(self) -> list[int]:
        ids = self._scope_ids()
        if self.only_missing.isChecked():
            have = self._content.words_with_contexts(ids)
            ids = [word_id for word_id in ids if word_id not in have]
        return ids

    def _refresh(self) -> None:
        ids = self._scope_ids()
        status = self._content.status(ids)
        text = (
            f"{status.with_contexts:,} of {status.words:,} words have contexts, "
            f"{status.contexts:,} in all."
        )
        if status.without_definition:
            text += (
                f" {status.without_definition:,} have no definition: they are not asked "
                "until they have one."
            )
        self.status_label.setText(text)
        self.status_scope.setText(f"{status.words:,} words")
        count = len(self._export_ids())
        self.export_summary.setText(
            f"{count:,} {'word' if count == 1 else 'words'} to a JSON file."
            if count else "No words to export here."
        )
        self.export_button.setEnabled(bool(count))

    # -- actions -----------------------------------------------------------------

    def _export(self) -> None:
        ids = self._export_ids()
        if not ids:
            return
        default = paths.exports_dir() / "lexitrack-contexts.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Words and Contexts", str(default), "JSON files (*.json)"
        )
        if not path:
            return
        try:
            target, count = self._content.export(path, ids)
        except (OSError, LexiTrackError) as exc:
            show_error(self.error, f"The file could not be written: {exc}")
            return
        show_error(self.error, None)
        self.export_summary.setText(
            f"{_count(count, 'word')} written to {target.name}. Fill in their contexts, "
            "then import the file here."
        )

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Contexts", str(paths.exports_dir()), "JSON files (*.json)"
        )
        if not path:
            return
        try:
            preview = self._content.preview_import(path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        show_error(self.error, None)
        dialog = ContextImportDialog(self._content, preview, parent=self)
        imported = bool(dialog.exec()) and bool(dialog.result_text)
        self._refresh()
        if imported:
            self.changed = True
            self.export_summary.setText(dialog.result_text)


class ContextImportDialog(QDialog):
    """What a contexts file would add, before anything is written."""

    def __init__(
        self,
        content: ContentService,
        preview: ContextImportPreview,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Contexts")
        self._content = content
        self._preview = preview
        self.result_text = ""
        self.resize(820, 620)
        self._build()

    def _build(self) -> None:
        m = METRICS
        preview = self._preview
        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_4)
        layout.setSpacing(m.space_3)
        title = QLabel(f"Import {preview.path.name}")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        parts = [
            _count(preview.words, "word"),
            _count(preview.context_count, "new context"),
        ]
        if preview.definition_count:
            parts.append(f"{_count(preview.definition_count, 'definition')} replaced")
        if preview.duplicate_count:
            parts.append(f"{preview.duplicate_count:,} already there, not added again")
        if preview.rejected:
            parts.append(f"{len(preview.rejected):,} not imported")
        summary = QLabel(" · ".join(parts))
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        changing = [entry for entry in preview.entries if entry.changes_anything]
        heading = QLabel(f"WHAT IT ADDS · {_count(len(changing), 'word').upper()}" if changing
                         else "NOTHING NEW TO ADD")
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        self.table = QTableWidget(len(changing), 3)
        self.table.setHorizontalHeaderLabels(["Word", "New contexts", "Definition"])
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, entry in enumerate(changing):
            self.table.setItem(row, 0, QTableWidgetItem(entry.word.word if entry.word else ""))
            self.table.setItem(row, 1, QTableWidgetItem("\n".join(entry.new_contexts)))
            self.table.setItem(
                row, 2, QTableWidgetItem(entry.definition or "unchanged")
            )
        self.table.resizeRowsToContents()
        self.table.setVisible(bool(changing))
        layout.addWidget(self.table, 1)

        notes = [f"#{entry.index} “{entry.text}”: {entry.problem}" for entry in preview.rejected]
        notes += [
            f"{entry.word.word}: {warning}"
            for entry in preview.entries
            if entry.problem is None and entry.word is not None
            for warning in entry.warnings
        ]
        notes_heading = QLabel(f"NOTES · {len(notes):,}")
        notes_heading.setObjectName("SectionTitle")
        notes_heading.setVisible(bool(notes))
        layout.addWidget(notes_heading)
        self.notes = QPlainTextEdit("\n".join(notes))
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(160)
        self.notes.setVisible(bool(notes))
        layout.addWidget(self.notes)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.import_button = QPushButton("Import")
        self.import_button.setProperty("variant", "primary")
        self.import_button.setDefault(True)
        self.import_button.setEnabled(preview.changes_anything)
        self.import_button.clicked.connect(self._import)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)

    def _import(self) -> None:
        result = self._content.apply_import(self._preview)
        text = (
            f"Imported {_count(result.contexts_added, 'context')} "
            f"for {_count(result.words, 'word')}"
        )
        if result.definitions_changed:
            text += f", and {_count(result.definitions_changed, 'definition')}"
        text += "."
        if result.rejected:
            text += f" {_count(result.rejected, 'entry', 'entries')} not imported: see the notes."
        self.result_text = text
        self.accept()


def _count(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"
