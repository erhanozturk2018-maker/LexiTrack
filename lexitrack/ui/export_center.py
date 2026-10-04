"""Export: every way out of LexiTrack, on one page, as on the phone.

Backups have a page of their own (backups_dialog.py). This one answers:

* **Everything** — the portable ``.lexitrack`` file: words, lists,
  statuses, plans, cards, every review and question, contexts, settings.
  Restored from Backups, here or on another computer.
* **Which words?** Any set — one list, several or all of them, by status, by
  level, by where they are in learning, by whether they have contexts — as a
  PDF, CSV or JSON file, previewed before it is saved, with the columns
  chosen there (the Export dialog). Their contexts too, as a file to edit
  and bring back.
* **Your answers** — every answer and every question asked, as CSV, to look
  at in a spreadsheet: all of it, or the last so many days.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..models.user_word_state import ReviewStatus
from ..models.vocabulary_list import VocabularyList
from ..models.word_entry import CEFR_ORDER
from ..services import portable
from ..services.learning_service import LearningService
from ..services.maintenance import Maintenance
from ..services.vocabulary_service import VocabularyService
from ..services.word_filter import LearningState, WordFilter, filter_words
from .components.settings_rows import CONTROL_WIDTH, SettingsGroup, page, scrolled
from .dialogs import error_label, show_error
from .export_dialog import ExportDialog, ExportScope
from .theme.palette import METRICS

_WIDE = CONTROL_WIDTH + 80


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("SettingHint")
    label.setWordWrap(True)
    return label


class _StayOpenMenu(QMenu):
    """A menu whose ticks toggle without closing it, so several can be picked."""

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        action = self.activeAction()
        if action is not None and action.isEnabled() and action.isCheckable():
            action.trigger()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        action = self.activeAction()
        if event.key() == Qt.Key.Key_Space and action is not None and action.isCheckable():
            action.trigger()
            return
        super().keyPressEvent(event)


class ListPicker(QComboBox):
    """Every list, one, or several: a combo box whose drop-down has ticks."""

    changed = Signal()

    def __init__(self, lists: Sequence[VocabularyList], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.addItem("All lists")
        self._menu = _StayOpenMenu(self)
        self._all = self._menu.addAction("All lists")
        self._all.setCheckable(True)
        self._all.setChecked(True)
        self._all.toggled.connect(self._on_all)
        self._menu.addSeparator()
        self._actions = []
        for lst in lists:
            action = self._menu.addAction(lst.name)
            action.setCheckable(True)
            action.setData(lst.id)
            action.toggled.connect(self._on_list)
            self._actions.append(action)

    def selected(self) -> tuple[int, ...]:
        """The ticked lists' ids; empty for every list."""
        return tuple(a.data() for a in self._actions if a.isChecked())

    def select(self, list_ids: Sequence[int]) -> None:
        for action in self._actions:
            action.blockSignals(True)
            action.setChecked(action.data() in list_ids)
            action.blockSignals(False)
        self._on_list()

    def showPopup(self) -> None:  # noqa: N802
        self._menu.setMinimumWidth(self.width())
        self._menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def hidePopup(self) -> None:  # noqa: N802
        self._menu.hide()
        super().hidePopup()

    def _on_all(self, checked: bool) -> None:
        if checked:
            self.select(())
        elif not self.selected():
            # "All" is what nothing ticked means: it cannot be unticked alone.
            self._set_all(True)

    def _on_list(self, _checked: bool = False) -> None:
        chosen = [a for a in self._actions if a.isChecked()]
        self._set_all(not chosen)
        if not chosen:
            text = "All lists"
        elif len(chosen) == 1:
            text = chosen[0].text()
        else:
            text = f"{len(chosen)} lists"
        self.setItemText(0, text)
        self.changed.emit()

    def _set_all(self, checked: bool) -> None:
        self._all.blockSignals(True)
        self._all.setChecked(checked)
        self._all.blockSignals(False)


#: The learning data's periods: a label, and how many days back (None: all).
_PERIODS: tuple[tuple[str, int | None], ...] = (
    ("All time", None),
    ("Last 7 days", 7),
    ("Last 30 days", 30),
    ("Last 90 days", 90),
    ("Last year", 365),
)


class ExportCenter(QDialog):
    def __init__(
        self,
        service: VocabularyService,
        engine: LearningService,
        open_content: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export")
        self._service = service
        self._engine = engine
        self._open_content = open_content
        self._maintenance = Maintenance(service.database, engine.clock)
        #: Nothing here changes the data (restoring is on Backups); kept for
        #: callers that ask.
        self.changed = False
        self.resize(760, 780)
        self._build()
        self._refresh_words()

    # -- building --------------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body, layout = page(
            "Export",
            "Everything that is yours can leave LexiTrack, in files anything can read — "
            "and come back. Backups are on their own page.",
        )

        everything = SettingsGroup("EVERYTHING, IN ONE FILE")
        export_all = QPushButton("Export everything…")
        export_all.setProperty("variant", "primary")
        export_all.clicked.connect(self._export_portable)
        everything.add(
            "Everything",
            "A .lexitrack file: words, lists, statuses, plans, cards, every answer and "
            "question, contexts, settings. Keep it somewhere safe; restore it in Backups, "
            "here or on another computer.",
            export_all,
        )
        layout.addWidget(everything)

        words = SettingsGroup("WORDS")
        self.from_list = ListPicker(self._service.lists())
        self.from_list.setFixedWidth(_WIDE)
        self.from_list.changed.connect(self._refresh_words)
        words.add("From", "Every list, one, or several: tick them.", self.from_list)
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
        self.content = QComboBox()
        self.content.addItem("Any", None)
        self.content.addItem("With contexts", "yes")
        self.content.addItem("Without contexts", "no")
        for combo, title, hint in (
            (self.status, "Status", "Known, Unknown or not yet sorted."),
            (self.level, "Level", "CEFR level, where the word has one."),
            (self.state, "Learning", "Where the word is in your study plan."),
            (self.content, "Contexts", "Sentences showing the word in use."),
        ):
            combo.setFixedWidth(_WIDE)
            combo.currentIndexChanged.connect(self._refresh_words)
            words.add(title, hint, combo)
        self.words_count = _hint()
        self.export_words_button = QPushButton("Export…")
        self.export_words_button.clicked.connect(self._export_words)
        words.add("Words", self.words_count, self.export_words_button)

        if self._open_content is not None:
            button = QPushButton("Word contexts…")
            button.clicked.connect(self._content)
            words.add(
                "Their contexts",
                "Words with their contexts as JSON, to edit and bring back in.",
                button,
            )

        layout.addWidget(words)

        data = SettingsGroup("YOUR ANSWERS")
        self.period = QComboBox()
        for label, days in _PERIODS:
            self.period.addItem(label, days)
        self.period.setFixedWidth(_WIDE)
        data.add("Period", "The days the two files below cover.", self.period)
        answers = QPushButton("Export…")
        answers.clicked.connect(self._export_answers)
        data.add(
            "Every answer",
            "Each day's answer: the task, whether it was right, its rating, and the "
            "schedule it produced, as CSV. Answers taken back are included and marked.",
            answers,
        )
        attempts = QPushButton("Export…")
        attempts.clicked.connect(self._export_attempts)
        data.add(
            "Every question",
            "Each question asked — task, right or wrong, effort, time — practice "
            "included, as CSV.",
            attempts,
        )
        layout.addWidget(data)

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
        contexts = self.content.currentData()
        return WordFilter(
            lists=self.from_list.selected(),
            status=ReviewStatus(status) if status else None,
            cefr=self.level.currentData(),
            state=LearningState(self.state.currentData() or LearningState.ANY),
            contexts=None if contexts is None else contexts == "yes",
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
        for combo in (self.status, self.level, self.state, self.content):
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

    def _since(self) -> date | None:
        """The first learning day of the chosen period, or None for all of it."""
        days = self.period.currentData()
        if not days:
            return None
        return date.fromisoformat(self._engine.clock.today()) - timedelta(days=days - 1)

    def _export_answers(self) -> None:
        path = self._save_csv("Export Every Answer", "answers")
        if path:
            rows = self._maintenance.export_review_log(path, since=self._since())
            self._say(f"{rows:,} answers written to {path.name}.")

    def _export_attempts(self) -> None:
        path = self._save_csv("Export Every Question", "questions")
        if path:
            rows = self._maintenance.export_attempts(path, since=self._since())
            self._say(f"{rows:,} questions written to {path.name}.")

    # -- everything ----------------------------------------------------------------

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

    def _say(self, text: str) -> None:
        show_error(self.error, None)
        self.message.setText(text)
