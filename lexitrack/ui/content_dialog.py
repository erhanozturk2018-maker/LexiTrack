"""Word content: sending words out to be enriched, and taking the result in.

LexiTrack never calls an LLM. It writes a batch of words that still need
content — with a prompt that explains every field — to a file; any tool fills
it in; this window reads the filled file back, shows what it would change,
and imports it when told to. See ``docs/formats/content-enrichment.md``.

The window has three parts: how much of your vocabulary has content, the
next batch to export, and the file to import — with the batches still out
listed, since their words are left out of the next batch until they return.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..models.content import ContentStatus, Related
from ..models.language import language_name
from ..services.content_service import ContentImportPreview, ContentService
from ..services.learning_service import LearningService
from ..services.vocabulary_service import VocabularyService
from .components.settings_rows import CONTROL_WIDTH, SettingsGroup, note, page, scrolled, spin
from .dialogs import error_label, show_error
from .theme.palette import METRICS

_PLAN, _NEW, _SELECTION = "plan", "new", "selection"


class ContentDialog(QDialog):
    def __init__(
        self,
        service: VocabularyService,
        engine: LearningService,
        word_ids: Sequence[int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Word Content")
        self._service = service
        self._engine = engine
        self._content = ContentService(service.database)
        self._selection = [int(i) for i in word_ids] if word_ids else []
        #: Set when something was imported, for the window behind to refresh.
        self.changed = False
        self.resize(760, 760)
        self._build()
        self._refresh()

    # -- building --------------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body, layout = page(
            "Word content",
            "Meanings in your language, how a word is used, examples and memory cues — "
            "added in batches from any tool you like, such as an LLM. LexiTrack never "
            "calls one itself: it writes the words to a file, and reads the filled file "
            "back.",
        )

        overview = SettingsGroup("YOUR WORDS")
        self.status_label = QLabel("")
        self.status_label.setObjectName("SettingHint")
        self.status_label.setWordWrap(True)
        self.status_scope = QLabel("")
        self.status_scope.setObjectName("SettingTitle")
        overview.add("With content", self.status_label, self.status_scope)
        layout.addWidget(overview)

        export = SettingsGroup("EXPORT A BATCH")
        self.source = QComboBox()
        self.source.setFixedWidth(CONTROL_WIDTH + 80)
        if self._selection:
            self.source.addItem(f"The {len(self._selection)} words chosen", _SELECTION)
        self.source.addItem("Your study plan", _PLAN)
        self.source.addItem("Today's new words", _NEW)
        for lst in self._service.lists():
            self.source.addItem(lst.name, lst.id)
        self.source.currentIndexChanged.connect(self._refresh)
        export.add("Words from", "Only words that still need content are taken.", self.source)

        self.languages = QLineEdit(self._engine.settings.learner_language or "")
        self.languages.setPlaceholderText("de, es, tr…")
        self.languages.setFixedWidth(CONTROL_WIDTH + 80)
        self.languages.textChanged.connect(self._refresh)
        export.add(
            "Explain in",
            "Language codes, several allowed. Empty: only what is true of the word in "
            "its own language — patterns, collocations, examples.",
            self.languages,
        )
        self.size = spin(1, 500, " words")
        self.size.setValue(25)
        self.size.setFixedWidth(CONTROL_WIDTH + 80)
        self.size.valueChanged.connect(self._refresh)
        export.add("Batch size", "Smaller batches are easier to check.", self.size)

        self.export_summary = QLabel("")
        self.export_summary.setObjectName("SettingHint")
        self.export_summary.setWordWrap(True)
        self.export_button = QPushButton("Export batch…")
        self.export_button.setProperty("variant", "primary")
        self.export_button.clicked.connect(self._export)
        export.add("Next", self.export_summary, self.export_button)
        layout.addWidget(export)

        take_in = SettingsGroup("IMPORT A FILLED FILE")
        import_button = QPushButton("Choose file…")
        import_button.clicked.connect(self._import)
        take_in.add(
            "Filled batch",
            "You see what it would change before anything is written. Nothing you "
            "already have is replaced unless you choose to.",
            import_button,
        )
        layout.addWidget(take_in)

        self.open_group = SettingsGroup("WAITING FOR")
        self._open_rows = QVBoxLayout()
        holder = QWidget()
        holder.setObjectName("PanelBody")
        holder.setLayout(self._open_rows)
        self._open_rows.setContentsMargins(0, 0, 0, 0)
        self._open_rows.setSpacing(m.space_2)
        self.open_group.add_widget(holder)
        layout.addWidget(self.open_group)
        layout.addWidget(note(
            "The file format, and what each field means, are in "
            "docs/formats/content-enrichment.md."
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

    def _languages(self) -> list[str]:
        return [part.strip() for part in self.languages.text().replace(";", ",").split(",")
                if part.strip()]

    def _scope_ids(self) -> list[int]:
        choice = self.source.currentData()
        if choice == _SELECTION:
            return list(self._selection)
        if choice == _NEW:
            return [word.id for word in self._engine.daily_plan().new_words]
        if choice == _PLAN:
            return self._engine.plan_word_ids()
        return [word.id for word in self._service.list_words(int(choice))]

    def _refresh(self) -> None:
        ids = self._scope_ids()
        languages = self._languages()
        language = languages[0] if languages else None
        statuses = self._content.statuses(ids, language)
        counts = {status: 0 for status in ContentStatus}
        for status in statuses.values():
            counts[status] += 1
        pair = f"explained in {language_name(language)}" if language else "on their own"
        self.status_label.setText(
            f"{counts[ContentStatus.COMPLETE]:,} complete · "
            f"{counts[ContentStatus.PARTIAL]:,} partial · "
            f"{counts[ContentStatus.NONE]:,} with nothing yet, {pair}."
        )
        self.status_scope.setText(f"{len(ids):,} words")

        candidates = self._content.batch_candidates(ids, self.size.value(), languages)
        self._candidates = candidates
        name = self._content.next_batch_name()
        self._batch_name = name
        if candidates:
            self.export_summary.setText(
                f"{name}: {len(candidates)} {'word' if len(candidates) == 1 else 'words'} "
                "that still need content."
            )
        else:
            self.export_summary.setText(
                "Nothing here needs content"
                + (" that is not already out in a batch." if self._content.open_batches()
                   else ".")
            )
        self.export_button.setEnabled(bool(candidates))
        self._show_open_batches()

    def _show_open_batches(self) -> None:
        while self._open_rows.count():
            item = self._open_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        batches = self._content.open_batches()
        self.open_group.setVisible(bool(batches))
        for batch in batches:
            row = QWidget()
            row.setObjectName("PanelBody")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            label = QLabel(
                f"{batch.name} · {len(batch.word_ids)} words · exported {batch.exported_on}"
            )
            label.setObjectName("SettingTitle")
            label.setToolTip(batch.path or "")
            line.addWidget(label, 1)
            forget = QPushButton("Forget")
            forget.setProperty("variant", "ghost")
            forget.setToolTip("It will not come back: offer its words again")
            forget.clicked.connect(lambda _c=False, n=batch.name: self._forget(n))
            line.addWidget(forget)
            self._open_rows.addWidget(row)

    # -- actions -----------------------------------------------------------------

    def _export(self) -> None:
        if not self._candidates:
            return
        default = paths.exports_dir() / f"content_{self._batch_name}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Content Batch", str(default), "JSON files (*.json)"
        )
        if not path:
            return
        try:
            self._content.export_batch(
                self._candidates, path, self._batch_name, self._languages()
            )
        except (OSError, LexiTrackError) as exc:
            show_error(self.error, f"The batch could not be written: {exc}")
            return
        show_error(self.error, None)
        self._refresh()
        self.export_summary.setText(
            f"Written to {Path(path).name}. Give it to the tool of your choice, "
            "then import the filled file here."
        )

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Filled Batch", str(paths.exports_dir()), "JSON files (*.json)"
        )
        if not path:
            return
        try:
            preview = self._content.preview_import(path)
        except LexiTrackError as exc:
            show_error(self.error, str(exc))
            return
        show_error(self.error, None)
        dialog = ContentImportDialog(self._content, preview, parent=self)
        imported = bool(dialog.exec()) and bool(dialog.result_text)
        self._refresh()
        if imported:
            self.changed = True
            self.export_summary.setText(dialog.result_text)

    def _forget(self, name: str) -> None:
        self._content.forget_batch(name)
        self._refresh()


class ContentImportDialog(QDialog):
    """What a filled file would change, and which conflicts to take."""

    def __init__(
        self,
        content: ContentService,
        preview: ContentImportPreview,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Content")
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
        title = QLabel(f"Import {preview.batch or preview.path.name}")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        languages = ", ".join(language_name(code) for code in preview.learner_languages)
        summary = QLabel(
            f"{len(preview.plans)} words · {preview.fill_count} fields to fill · "
            f"{preview.context_count} new examples · {preview.translation_count} translations"
            + (f" · explained in {languages}" if languages else "")
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        conflicts = [
            (plan, key, old, new)
            for plan in preview.plans
            for key, (old, new) in plan.conflicts.items()
        ]
        heading = QLabel(
            f"ALREADY SET, DIFFERENT IN THE FILE · {len(conflicts)}"
            if conflicts
            else "NOTHING YOU HAVE WOULD BE REPLACED"
        )
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        self.table = QTableWidget(len(conflicts), 4)
        self.table.setHorizontalHeaderLabels(["Replace", "Word · field", "Now", "In the file"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._conflict_keys: list[tuple[int, str]] = []
        for row, (plan, key, old, new) in enumerate(conflicts):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(f"{plan.word} · {_field_name(key)}"))
            self.table.setItem(row, 2, QTableWidgetItem(_shown(old)))
            self.table.setItem(row, 3, QTableWidgetItem(_shown(new)))
            self._conflict_keys.append((plan.word_id, key))
        self.table.setVisible(bool(conflicts))
        layout.addWidget(self.table, 1)

        notes = [f"Rejected — {reason}" for reason in preview.rejected]
        notes += [
            f"{plan.word}: {warning}" for plan in preview.plans for warning in plan.warnings
        ]
        notes += [
            f"{plan.word}: {plan.duplicate_contexts} example(s) already stored, not added again"
            for plan in preview.plans
            if plan.duplicate_contexts
        ]
        notes_heading = QLabel(f"NOTES · {len(notes)}")
        notes_heading.setObjectName("SectionTitle")
        notes_heading.setVisible(bool(notes))
        layout.addWidget(notes_heading)
        self.notes = QPlainTextEdit("\n".join(notes))
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(140)
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
        self.import_button.setEnabled(bool(preview.plans))
        self.import_button.clicked.connect(self._import)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)

    def chosen(self) -> list[tuple[int, str]]:
        return [
            key
            for row, key in enumerate(self._conflict_keys)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def _import(self) -> None:
        result = self._content.apply_import(self._preview, replace_fields=self.chosen())
        self.result_text = (
            f"Imported {result.words} words: {result.fields_filled} fields filled, "
            f"{result.fields_replaced} replaced, {result.contexts_added} examples and "
            f"{result.translations_added} translations added."
        )
        self.accept()


def _field_name(key: str) -> str:
    part, name = key.split(".", 1)
    name = name.replace("_", " ")
    return name if part == "target" else f"{name} ({language_name(part)})"


def _shown(value: object) -> str:
    """A stored or incoming field value, as text for the table."""
    if isinstance(value, tuple):
        return " · ".join(
            f"{item.word} ({item.relation})" if isinstance(item, Related) else str(item)
            for item in value
        )
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
