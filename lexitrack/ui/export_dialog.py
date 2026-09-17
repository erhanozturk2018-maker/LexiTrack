"""Exporting words: see the file before you save it.

The dialog opens ready to go. The settings on the left start from sensible
defaults (or from the last export, when "Remember these settings" is on) and
the right shows the file those settings produce — the real first page of the
PDF, or the first lines of the CSV or JSON. Someone who wants the defaults
presses Enter; someone who wants something else changes a setting and watches
the preview follow. Only then does Save As ask where the file goes.

The caller decides which scopes make sense where the user is — the words of
the list on screen, its unknown words, a table selection, every unknown word —
and which one is selected first. The dialog never offers a scope that does not
apply.

Ordering is applied here, to the words handed to the export service, so every
format honours it: a JSON file exported "by CEFR level" imports back in that
order.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import pymupdf
from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..models.word_entry import CEFR_ORDER
from ..repositories.word_repository import StoredWord
from ..services.export_service import ExportContent, ExportFormat
from ..services.vocabulary_service import VocabularyService
from .components.toast import notify
from .dialogs import error_label, show_error
from .theme.palette import METRICS

FORMAT_HINTS = {
    ExportFormat.PDF: "A printable study sheet.",
    ExportFormat.CSV: "For Excel, Google Sheets or Anki.",
    ExportFormat.JSON: "LexiTrack's own format. Edit it and import it again.",
}

#: Words rendered for the preview. Enough to fill a page; few enough to be instant.
PREVIEW_WORDS = 40
#: Lines of a CSV or JSON preview.
PREVIEW_LINES = 40

_SETTINGS_FORMAT = "export/format"
_SETTINGS_ORDER = "export/order"
_SETTINGS_REMEMBER = "export/remember"


class ExportOrder(StrEnum):
    ALPHABETICAL = "alphabetical"
    CEFR = "cefr"
    LIST = "list"

    @property
    def label(self) -> str:
        return {
            "alphabetical": "A → Z",
            "cefr": "CEFR level, then A → Z",
            "list": "As in the list",
        }[self.value]


def order_words(words: Sequence[StoredWord], order: ExportOrder) -> list[StoredWord]:
    """Return ``words`` in ``order``. Words without a level come after C2."""
    if order is ExportOrder.ALPHABETICAL:
        return sorted(words, key=lambda w: (w.normalized_word, w.word))
    if order is ExportOrder.CEFR:
        def level(word: StoredWord) -> int:
            return (
                CEFR_ORDER.index(word.cefr_level)
                if word.cefr_level in CEFR_ORDER
                else len(CEFR_ORDER)
            )

        return sorted(words, key=lambda w: (level(w), w.normalized_word, w.word))
    return list(words)


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
        self._contents: dict[int, ExportContent] = {}
        self._settings = QSettings()
        self._preview_dir = Path(tempfile.mkdtemp(prefix="lexitrack-preview-"))
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._render_preview)
        self.written: Path | None = None
        #: Once a format is chosen (now or remembered), switching scope keeps it.
        self._format_chosen = False
        self.setWindowTitle("Export")
        self.setMinimumSize(820, 600)
        self._build()
        self._restore_settings()
        self._schedule_preview()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Settings column
        settings = QFrame()
        settings.setObjectName("ExportSettings")
        settings.setFixedWidth(360)
        column = QVBoxLayout(settings)
        column.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        column.setSpacing(m.space_2)

        title = QLabel("Export Words")
        title.setObjectName("DialogTitle")
        column.addWidget(title)
        column.addSpacing(m.space_2)

        column.addWidget(_section("WORDS"))
        self._scope_group = QButtonGroup(self)
        for index, scope in enumerate(self._scopes):
            radio = QRadioButton(scope.label)
            self._scope_group.addButton(radio, index)
            column.addWidget(radio)
        self._scope_group.button(0).setChecked(True)
        self._scope_group.idToggled.connect(self._on_scope)

        column.addSpacing(m.space_3)
        column.addWidget(_section("FORMAT"))
        switch = QFrame()
        switch.setObjectName("ModeSwitch")
        switch.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        switch_layout = QHBoxLayout(switch)
        switch_layout.setContentsMargins(3, 3, 3, 3)
        switch_layout.setSpacing(2)
        self._format_group = QButtonGroup(self)
        self._format_group.setExclusive(True)
        self._format_buttons: dict[ExportFormat, QPushButton] = {}
        for index, file_format in enumerate(ExportFormat):
            button = QPushButton(file_format.value.upper())
            button.setObjectName("ModeButton")
            button.setCheckable(True)
            button.setToolTip(f"{file_format.label}. {FORMAT_HINTS[file_format]}")
            button.setAccessibleName(file_format.label)
            self._format_group.addButton(button, index)
            self._format_buttons[file_format] = button
            switch_layout.addWidget(button, 1)
        self._format_group.idToggled.connect(self._on_setting)
        self._format_group.idClicked.connect(self._on_format_chosen)
        column.addWidget(switch)
        self.format_hint = QLabel()
        self.format_hint.setObjectName("Faint")
        self.format_hint.setWordWrap(True)
        column.addWidget(self.format_hint)

        column.addSpacing(m.space_3)
        column.addWidget(_section("ORDER"))
        self._order_group = QButtonGroup(self)
        self._order_buttons: dict[ExportOrder, QRadioButton] = {}
        for index, order in enumerate(ExportOrder):
            radio = QRadioButton(order.label)
            self._order_group.addButton(radio, index)
            self._order_buttons[order] = radio
            column.addWidget(radio)
        self._order_group.idToggled.connect(self._on_setting)

        column.addStretch(1)
        self.remember = QCheckBox("Remember these settings")
        self.remember.setToolTip("Start the next export with this format and order")
        column.addWidget(self.remember)

        outer.addWidget(settings)

        # Preview column
        preview = QFrame()
        preview.setObjectName("ExportPreview")
        preview.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right = QVBoxLayout(preview)
        right.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        right.setSpacing(m.space_3)

        self.summary = QLabel()
        self.summary.setObjectName("Muted")
        right.addWidget(self.summary)

        self.preview_stack = QStackedWidget()
        self.page_view = QLabel()
        self.page_view.setObjectName("PreviewPage")
        self.page_view.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        page_scroll = QScrollArea()
        page_scroll.setObjectName("PreviewScroll")
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_scroll.setWidget(self.page_view)
        self.preview_stack.addWidget(page_scroll)
        self.text_view = QPlainTextEdit()
        self.text_view.setObjectName("PreviewText")
        self.text_view.setReadOnly(True)
        self.text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview_stack.addWidget(self.text_view)
        right.addWidget(self.preview_stack, 1)

        self.error = error_label()
        right.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.save_button = QPushButton("Save As…")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setDefault(True)
        self.save_button.setAutoDefault(True)
        self.save_button.setToolTip("Choose where to save the file (Enter)")
        self.save_button.clicked.connect(self._export)
        buttons.addWidget(self.save_button)
        right.addLayout(buttons)

        outer.addWidget(preview, 1)
        self.save_button.setFocus()

    def _restore_settings(self) -> None:
        remember = _as_bool(self._settings.value(_SETTINGS_REMEMBER, True))
        self.remember.setChecked(remember)
        file_format = self._scopes[0].default_format
        order = ExportOrder.ALPHABETICAL
        stored = self._settings.value(_SETTINGS_FORMAT)
        if remember and stored:
            file_format = _enum(ExportFormat, stored, file_format)
            self._format_chosen = True
        if remember:
            order = _enum(ExportOrder, self._settings.value(_SETTINGS_ORDER), order)
        self._format_buttons[file_format].setChecked(True)
        self._order_buttons[order].setChecked(True)
        self._update_hint()

    # -- state -------------------------------------------------------------

    def selected_format(self) -> ExportFormat:
        return list(ExportFormat)[self._format_group.checkedId()]

    def selected_order(self) -> ExportOrder:
        return list(ExportOrder)[self._order_group.checkedId()]

    def _on_scope(self, index: int, checked: bool) -> None:
        if not checked:
            return
        if not self._format_chosen:
            self._format_buttons[self._scopes[index].default_format].setChecked(True)
        self._schedule_preview()

    def _on_format_chosen(self, _index: int) -> None:
        self._format_chosen = True

    def _on_setting(self, _index: int, checked: bool) -> None:
        if checked:
            self._update_hint()
            self._schedule_preview()

    def _update_hint(self) -> None:
        if self._format_group.checkedId() >= 0:
            self.format_hint.setText(FORMAT_HINTS[self.selected_format()])

    def _content(self) -> ExportContent:
        """The selected scope's words, in the selected order."""
        index = self._scope_group.checkedId()
        if index not in self._contents:
            self._contents[index] = self._scopes[index].content()
        content = self._contents[index]
        return replace(content, words=order_words(content.words, self.selected_order()))

    # -- preview -----------------------------------------------------------

    def _schedule_preview(self) -> None:
        self._preview_timer.start()

    def _render_preview(self) -> None:
        show_error(self.error, None)
        try:
            content = self._content()
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return
        count = len(content.words)
        file_format = self.selected_format()
        self.summary.setText(
            f"{count:,} {'word' if count == 1 else 'words'}  ·  "
            f"{self.selected_order().label}  ·  {file_format.value.upper()}"
        )
        self.save_button.setEnabled(count > 0)
        if not count:
            self.text_view.setPlainText("There are no words to export in that selection.")
            self.preview_stack.setCurrentWidget(self.text_view)
            return

        sample = replace(content, words=content.words[:PREVIEW_WORDS])
        target = self._preview_dir / f"preview.{file_format.value}"
        try:
            self._service.export(sample, target, file_format)
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return

        if file_format is ExportFormat.PDF:
            self.page_view.setPixmap(self._render_first_page(target))
            self.preview_stack.setCurrentIndex(0)
        else:
            lines = target.read_text(encoding="utf-8-sig").splitlines()
            shown = lines[:PREVIEW_LINES]
            if len(lines) > PREVIEW_LINES or count > PREVIEW_WORDS:
                shown.append("…")
            self.text_view.setPlainText("\n".join(shown))
            self.preview_stack.setCurrentWidget(self.text_view)

    def _render_first_page(self, pdf: Path) -> QPixmap:
        width = max(self.preview_stack.width() - 40, 320)
        ratio = self.devicePixelRatioF()
        with pymupdf.open(pdf) as document:
            page = document[0]
            zoom = width * ratio / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            image = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
            ).copy()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._format_group.checkedId() >= 0 and self.selected_format() is ExportFormat.PDF:
            self._schedule_preview()

    # -- saving ------------------------------------------------------------

    def _export(self) -> None:
        file_format = self.selected_format()
        try:
            content = self._content()
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

        self._save_settings()
        count = len(content.words)
        written = self.written
        host = self.parentWidget()
        if host is not None:
            notify(
                host,
                f"Exported {count:,} {'word' if count == 1 else 'words'} to {written.name}",
                action=(
                    "Open Folder",
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(written.parent))),
                ),
            )
        self.accept()

    def _save_settings(self) -> None:
        remember = self.remember.isChecked()
        self._settings.setValue(_SETTINGS_REMEMBER, remember)
        if remember:
            self._settings.setValue(_SETTINGS_FORMAT, self.selected_format().value)
            self._settings.setValue(_SETTINGS_ORDER, self.selected_order().value)

    def done(self, result: int) -> None:
        self._preview_timer.stop()
        shutil.rmtree(self._preview_dir, ignore_errors=True)
        super().done(result)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def _as_bool(value: object) -> bool:
    # QSettings returns "true"/"false" strings from the registry.
    return value in (True, "true", "1", 1)


def _enum(kind, value: object, default):
    try:
        return kind(value)
    except ValueError:
        return default
