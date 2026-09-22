"""Choosing what you are working through: the study plan window.

A plan is a name and a set of lists, and it teaches only the words in them
that you marked **Unknown** - never the ones you already know. The window used
to talk in whole lists and total word counts, which made it look as if a plan
took every word of every list; it now talks in the words that will actually
be taught, and every figure comes from the engine's own rule for choosing the
day's new words (``LearningService.selection_outlook``), so the window and the
Study page cannot disagree.

Two things it still deliberately does not do:

* **It does not offer per-list word counts as targets.** The daily count is a
  setting, not a property of a plan.
* **It does not warn about overlap.** A word in two lists is taught once.

What it shows is the consequence of the current selection - what is left to
introduce, what is in progress, what has been learned - and how long the new
words will last at today's pace, on the same screen as the checkboxes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import LexiTrackError
from ..models.srs import PlanOutlook, StudyPlan
from ..services.learning_service import LearningService
from ..services.vocabulary_service import VocabularyService
from .components.cards import SegmentedProgress, repolish
from .dialogs import confirm, dialog_header, error_label, show_error
from .theme.palette import METRICS

_NEW = -1


class StudyPlanDialog(QDialog):
    """Create, edit, switch and delete study plans."""

    def __init__(
        self,
        engine: LearningService,
        service: VocabularyService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._service = service
        self._plans: list[StudyPlan] = []
        self._checks: dict[int, QCheckBox] = {}
        self._all_row: _ListRow | None = None
        #: The single-list choices from before All my lists was ticked.
        self._chosen: dict[int, bool] = {}
        self._loading = False
        self.changed = False

        self.setWindowTitle("Study Plan")
        self.setMinimumWidth(560)
        self.setMinimumHeight(620)
        self._build()
        self._load_plans()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
        layout.setSpacing(m.space_4)
        layout.addLayout(
            dialog_header(
                "Study Plan",
                f"LexiTrack teaches the words you marked Unknown in the lists you "
                f"choose, {engine_per_day(self._engine)} a day. Words you know are never "
                f"offered, and changing the plan never affects what you have learned.",
            )
        )

        picker = QHBoxLayout()
        picker.setSpacing(m.space_2)
        self.plan_combo = QComboBox()
        self.plan_combo.setAccessibleName("Study plan")
        self.plan_combo.currentIndexChanged.connect(self._on_plan_selected)
        picker.addWidget(self.plan_combo, 1)
        self.delete_button = QPushButton("Delete plan")
        self.delete_button.setProperty("variant", "ghost")
        self.delete_button.clicked.connect(self._delete)
        picker.addWidget(self.delete_button)
        layout.addLayout(picker)

        form = QFormLayout()
        form.setSpacing(m.space_3)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.name_field = QLineEdit()
        self.name_field.setMaxLength(80)
        self.name_field.setPlaceholderText("e.g. Oxford 3000")
        form.addRow("Name", self.name_field)
        layout.addLayout(form)

        layout.addWidget(_section("LEARN THE UNKNOWN WORDS FROM"))
        # All my lists sits above the scrolling lists, not among them: the
        # window opens scrolled to the plan's own list, and an option that
        # scrolled away with the rest would never be found.
        self._all_holder = QVBoxLayout()
        self._all_holder.setContentsMargins(0, 0, m.space_1 + 8, 0)
        layout.addLayout(self._all_holder)
        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("PanelBody")
        self._list_layout = QVBoxLayout(holder)
        self._list_layout.setContentsMargins(0, 0, m.space_1, 0)
        self._list_layout.setSpacing(m.space_2)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)

        summary_box = QFrame()
        summary_box.setObjectName("SummaryBox")
        summary_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setContentsMargins(m.space_4, m.space_3, m.space_4, m.space_3)
        self.summary = QLabel()
        self.summary.setObjectName("SummaryText")
        self.summary.setWordWrap(True)
        summary_layout.addWidget(self.summary)
        layout.addWidget(summary_box)

        self.error = error_label()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(
            "Save and use", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.save_button.setProperty("variant", "primary")
        self.save_button.setDefault(True)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- loading -----------------------------------------------------------

    def _load_plans(self) -> None:
        self._loading = True
        self.plan_combo.clear()
        self._plans = self._engine.plans()
        active = self._engine.active_plan()
        for plan in self._plans:
            suffix = "  (in use)" if plan.is_active else ""
            self.plan_combo.addItem(f"{plan.name}{suffix}", plan.id)
        self.plan_combo.addItem("New plan…", _NEW)
        index = self.plan_combo.findData(active.id if active else _NEW)
        self.plan_combo.setCurrentIndex(max(index, 0))
        self._loading = False
        self._on_plan_selected()

    def _build_list_checks(self, selected: set[int], all_lists: bool = False) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checks = {}
        if self._all_row is not None:
            self._all_holder.removeWidget(self._all_row)
            self._all_row.deleteLater()
        self._all_row = None
        lists = self._service.lists()
        if not lists:
            empty = QLabel("You have no lists yet. Import a PDF or create a list first.")
            empty.setObjectName("Faint")
            empty.setWordWrap(True)
            self._list_layout.addWidget(empty)
            return
        every = self._engine.selection_outlook([], all_lists=True)
        self._all_row = _ListRow(
            "All my lists",
            _all_lists_meta(every, len(lists)),
            None,
            all_lists,
        )
        self._all_row.check.toggled.connect(self._on_all_toggled)
        self._all_holder.addWidget(self._all_row)
        for vocabulary_list in lists:
            outlook = self._engine.selection_outlook([vocabulary_list.id])
            row = _ListRow(
                vocabulary_list.name,
                _list_meta(outlook, self._engine.settings.new_words_include_not_reviewed),
                vocabulary_list.progress,
                vocabulary_list.id in selected,
            )
            row.check.toggled.connect(self._update_summary)
            self._checks[vocabulary_list.id] = row.check
            self._list_layout.addWidget(row)
        self._list_layout.addStretch(1)
        self._chosen = {list_id: check.isChecked() for list_id, check in self._checks.items()}
        self._on_all_toggled(all_lists)
        first = next((c for c in self._checks.values() if c.isChecked()), None)
        if first is not None and first.parentWidget() is not None:
            # The plan's own list may sit below the fold; show it on opening.
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(first.parentWidget()))

    def _all_selected(self) -> bool:
        return self._all_row is not None and self._all_row.check.isChecked()

    def _on_all_toggled(self, checked: bool) -> None:
        """Every list, including future ones: the single choices step aside.

        While All my lists is ticked each list shows as ticked and inactive,
        because each is included; the choices made before are kept and come
        back when it is unticked.
        """
        if checked:
            self._chosen = {list_id: check.isChecked() for list_id, check in self._checks.items()}
        for list_id, check in self._checks.items():
            check.blockSignals(True)
            check.setChecked(True if checked else self._chosen.get(list_id, False))
            check.blockSignals(False)
            row = check.parentWidget()
            if isinstance(row, _ListRow):
                row._show_selected(check.isChecked())
                row.setEnabled(not checked)
        self._update_summary()

    def _on_plan_selected(self) -> None:
        if self._loading:
            return
        plan_id = self.plan_combo.currentData()
        plan = next((item for item in self._plans if item.id == plan_id), None)
        if plan is None:
            self.name_field.setText(_suggest_name(self._plans))
            self._build_list_checks(set(), all_lists=False)
            self.delete_button.setEnabled(False)
            self.save_button.setText("Create and use")
        else:
            self.name_field.setText(plan.name)
            self._build_list_checks(set(plan.list_ids), all_lists=plan.all_lists)
            self.delete_button.setEnabled(len(self._plans) > 0)
            self.save_button.setText("Save and use")
        self._update_summary()

    # -- summary -----------------------------------------------------------

    def _selected_ids(self) -> list[int]:
        """The single lists chosen, ignoring the ticks All my lists puts on them."""
        if self._all_selected():
            return [list_id for list_id, chosen in self._chosen.items() if chosen]
        return [list_id for list_id, check in self._checks.items() if check.isChecked()]

    def _update_summary(self) -> None:
        """Say what this selection means, in words and in days.

        Computed for the selection on screen, saved or not, by the same rule
        the engine uses to choose the day's new words.
        """
        every = self._all_selected()
        selected = self._selected_ids()
        if not every and not selected:
            self.summary.setText("Choose at least one list, or All my lists.")
            return
        outlook = self._engine.selection_outlook(selected, all_lists=every)
        self.summary.setText(
            _summary_text(outlook, engine_per_day(self._engine))
        )

    # -- actions -----------------------------------------------------------

    def _save(self) -> None:
        plan_id = self.plan_combo.currentData()
        selected = self._selected_ids()
        every = self._all_selected()
        if not selected and not every:
            show_error(self.error, "A study plan needs at least one list.")
            return
        try:
            if plan_id == _NEW:
                self._engine.create_plan(
                    self.name_field.text(), list_ids=selected, all_lists=every
                )
            else:
                self._engine.update_plan(
                    plan_id, name=self.name_field.text(), list_ids=selected, all_lists=every
                )
                self._engine.set_active_plan(plan_id)
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return
        self.changed = True
        self.accept()

    def _delete(self) -> None:
        plan_id = self.plan_combo.currentData()
        plan = next((item for item in self._plans if item.id == plan_id), None)
        if plan is None:
            return
        if not confirm(
            self,
            "Delete Study Plan",
            f"Delete “{plan.name}”?\n\nYour words, your progress and everything you "
            f"have already learned are kept. Only the plan itself is removed.",
            "Delete plan",
        ):
            return
        try:
            self._engine.delete_plan(plan.id)
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return
        self.changed = True
        self._load_plans()


class _ListRow(QFrame):
    """One source of words: a checkbox, its name, what it would teach.

    The same progress bar as the list cards on Home, so a list looks the same
    wherever it appears. Clicking anywhere on the row toggles it.
    """

    def __init__(
        self,
        title: str,
        meta_text: str,
        progress,
        selected: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PlanListRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        m = METRICS
        layout = QHBoxLayout(self)
        layout.setContentsMargins(m.space_3, m.space_2 + 2, m.space_4, m.space_2 + 2)
        layout.setSpacing(m.space_3)
        self.check = QCheckBox()
        self.check.setAccessibleName(title)
        self.check.setAccessibleDescription(meta_text)
        layout.addWidget(self.check)
        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(title)
        name.setObjectName("PlanListName")
        self.meta = QLabel(meta_text)
        self.meta.setObjectName("PlanListMeta")
        self.meta.setWordWrap(True)
        text.addWidget(name)
        text.addWidget(self.meta)
        layout.addLayout(text, 1)
        if progress is not None:
            bar = SegmentedProgress()
            bar.setFixedWidth(120)
            bar.set_progress(progress)
            layout.addWidget(bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.check.toggled.connect(self._show_selected)
        self.check.setChecked(selected)
        self._show_selected(selected)

    def _show_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        repolish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.isEnabled()
            and not self.check.underMouse()
        ):
            self.check.toggle()
        super().mouseReleaseEvent(event)


def engine_per_day(engine: LearningService) -> int:
    return max(engine.settings.new_words_per_day, 0)


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


def _list_meta(outlook: PlanOutlook, include_not_reviewed: bool) -> str:
    """What one list would teach, most useful figure first."""
    if outlook.total == 0:
        return "No words yet"
    parts = [f"{outlook.to_introduce:,} to learn"]
    if outlook.in_progress:
        parts.append(f"{outlook.in_progress:,} in progress")
    parts.append(f"{outlook.known:,} known")
    text = " \u00b7 ".join(parts)
    if outlook.not_reviewed and not include_not_reviewed:
        # The trap a fresh import falls into: nothing is Unknown yet, so the
        # list would teach nothing and nobody would say why.
        text += (
            f"\n{outlook.not_reviewed:,} never answered: sort them on the Review tab first"
        )
    return text


def _all_lists_meta(outlook: PlanOutlook, list_count: int) -> str:
    return (
        f"{outlook.to_introduce:,} to learn across {_plural(list_count, 'list')}, "
        f"and any list you import later"
    )


def _summary_text(outlook: PlanOutlook, per_day: int) -> str:
    if outlook.to_introduce == 0:
        first = "No new words left to offer."
        if outlook.in_progress:
            first += f" {outlook.in_progress:,} words are still being reviewed."
        return (
            f"{first} To learn more, mark words Unknown on the Review tab or add a list."
        )
    parts = [f"{outlook.to_introduce:,} words to learn"]
    if outlook.in_progress:
        parts.append(f"{outlook.in_progress:,} in progress")
    if outlook.learned_here:
        parts.append(f"{outlook.learned_here:,} learned here")
    days = outlook.days_at(per_day) if per_day else 0
    pace = (
        f"About {_plural(days, 'day')} of new words at {per_day} a day."
        if per_day
        else "New words are paused: the daily count is 0 in Settings."
    )
    return " \u00b7 ".join(parts) + "\n" + pace


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def _suggest_name(plans: list[StudyPlan]) -> str:
    """A name that is not taken, so the field is never a blocking empty box."""
    taken = {plan.name.casefold() for plan in plans}
    if "my study plan" not in taken:
        return "My Study Plan"
    index = 2
    while f"study plan {index}".casefold() in taken:
        index += 1
    return f"Study Plan {index}"
