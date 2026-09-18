"""Choosing what you are working through: the study plan window.

A plan is two things — a name and a set of lists — and this window is
deliberately not more than that. It resists two temptations:

* **It does not offer per-list word counts as targets.** The daily count is a
  setting, not a property of a plan, because the user changes it for reasons
  that have nothing to do with which lists they picked.
* **It does not warn about overlap.** Two lists sharing a word is normal and
  harmless: the engine deduplicates the union, so the word is introduced once.
  A warning would invite the user to fix something that is not broken.

What it does show is the consequence of the current selection: how many words
the plan contains, how many are still waiting to be introduced, and how long
the plan will last at today's pace. That last number is the one that changes
minds about a selection, so it is on the same screen as the checkboxes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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
from ..models.srs import StudyPlan
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
                "A plan is the set of lists you are working through. Changing it "
                "never affects words you have already learned.",
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

        layout.addWidget(_section("LISTS IN THIS PLAN"))
        scroll = QScrollArea()
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

    def _build_list_checks(self, selected: set[int]) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checks = {}
        lists = self._service.lists()
        if not lists:
            empty = QLabel("You have no lists yet. Import a PDF or create a list first.")
            empty.setObjectName("Faint")
            empty.setWordWrap(True)
            self._list_layout.addWidget(empty)
            return
        for vocabulary_list in lists:
            row = _ListRow(vocabulary_list, vocabulary_list.id in selected)
            row.check.toggled.connect(self._update_summary)
            self._checks[vocabulary_list.id] = row.check
            self._list_layout.addWidget(row)
        self._list_layout.addStretch(1)

    def _on_plan_selected(self) -> None:
        if self._loading:
            return
        plan_id = self.plan_combo.currentData()
        plan = next((item for item in self._plans if item.id == plan_id), None)
        if plan is None:
            self.name_field.setText(_suggest_name(self._plans))
            self._build_list_checks(set())
            self.delete_button.setEnabled(False)
            self.save_button.setText("Create and use")
        else:
            self.name_field.setText(plan.name)
            self._build_list_checks(set(plan.list_ids))
            self.delete_button.setEnabled(len(self._plans) > 0)
            self.save_button.setText("Save and use")
        self._update_summary()

    # -- summary -----------------------------------------------------------

    def _selected_ids(self) -> list[int]:
        return [list_id for list_id, check in self._checks.items() if check.isChecked()]

    def _update_summary(self) -> None:
        """Say what this selection means, in words and in days.

        Computed from the lists themselves rather than from the saved plan, so
        it answers for the selection on screen — including one that has not
        been saved yet.
        """
        selected = self._selected_ids()
        if not selected:
            self.summary.setText("Select at least one list.")
            return
        words: set[int] = set()
        unknown = 0
        for list_id in selected:
            stored = self._service.list_words(list_id)
            for word in stored:
                if word.id in words:
                    continue
                words.add(word.id)
                if word.status.value != "known":
                    unknown += 1
        per_day = max(self._engine.settings.new_words_per_day, 1)
        days = -(-unknown // per_day)  # ceiling division
        overlap = sum(len(self._service.list_words(list_id)) for list_id in selected) - len(words)
        parts = [
            f"{len(words):,} words in {len(selected)} "
            f"{'list' if len(selected) == 1 else 'lists'}",
            f"{unknown:,} still to learn",
            f"about {days:,} days at {per_day} a day",
        ]
        if overlap:
            parts.append(f"{overlap:,} counted once although in more than one list")
        self.summary.setText(" · ".join(parts))

    # -- actions -----------------------------------------------------------

    def _save(self) -> None:
        plan_id = self.plan_combo.currentData()
        selected = self._selected_ids()
        if not selected:
            show_error(self.error, "A study plan needs at least one list.")
            return
        try:
            if plan_id == _NEW:
                self._engine.create_plan(self.name_field.text(), list_ids=selected)
            else:
                self._engine.update_plan(
                    plan_id, name=self.name_field.text(), list_ids=selected
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
    """One list to include: a checkbox, its name and counts, its progress.

    The same progress bar as the list cards on Home, so a list looks the same
    wherever it appears. Clicking anywhere on the row toggles it.
    """

    def __init__(self, vocabulary_list, selected: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlanListRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        m = METRICS
        layout = QHBoxLayout(self)
        layout.setContentsMargins(m.space_3, m.space_2 + 2, m.space_4, m.space_2 + 2)
        layout.setSpacing(m.space_3)
        self.check = QCheckBox()
        self.check.setAccessibleName(vocabulary_list.name)
        layout.addWidget(self.check)
        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(vocabulary_list.name)
        name.setObjectName("PlanListName")
        progress = vocabulary_list.progress
        meta = QLabel(
            f"{progress.total:,} words \u00b7 {progress.known:,} known \u00b7 "
            f"{progress.unknown:,} unknown"
        )
        meta.setObjectName("PlanListMeta")
        text.addWidget(name)
        text.addWidget(meta)
        layout.addLayout(text, 1)
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
        if event.button() == Qt.MouseButton.LeftButton and not self.check.underMouse():
            self.check.toggle()
        super().mouseReleaseEvent(event)


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
