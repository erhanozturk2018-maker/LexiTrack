"""Settings: five pages, and a firm line between two kinds of preference.

Most of what is here changes how the engine behaves and therefore lives in the
database, where the Telegram thread can see it too. A setting that only exists
in the Windows registry is invisible to anything that is not the Qt
application, which is why the theme is the *only* thing on these pages that
stays in ``QSettings``.

The other line is between ordinary and advanced. Developer Mode is a single
switch that reveals the knobs whose effect the user cannot judge from the
label — FSRS retention, the leech thresholds, the simulator — and it is
separate from Debug Logging on purpose: wanting to see how the scheduler
decided is not the same as wanting a large log file.

Nothing is saved until Save is pressed, and Save writes every page in one
transaction. Half-applied settings are the one way this window could leave the
engine in a state the user did not choose.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QLocale, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..core.errors import LexiTrackError
from ..models.settings import Setting
from ..services.learning_service import LearningService
from ..services.simulation import DEFAULT_PROFILE, PROFILES, simulate_current_settings
from ..services.vocabulary_service import VocabularyService
from ..telegram.config import env_file_candidates
from ..telegram.runtime import BotState
from .dialogs import confirm, error_label, show_error
from .telegram_controller import TelegramController
from .theme import ThemeManager, ThemeName
from .theme.palette import METRICS

LEARNING, TELEGRAM, APPEARANCE, DATA, ADVANCED, ABOUT = (
    "Learning",
    "Telegram",
    "Appearance",
    "Data",
    "Advanced",
    "About",
)


class SettingsDialog(QDialog):
    """Everything the user can configure, in one window."""

    #: A setting changed that the pages behind this window show.
    changed = Signal()

    def __init__(
        self,
        engine: LearningService,
        service: VocabularyService,
        theme: ThemeManager,
        telegram: TelegramController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._service = service
        self._theme = theme
        self._telegram = telegram
        self._settings = engine.refresh_settings()

        self.setWindowTitle("Settings")
        self.setMinimumSize(720, 640)
        self._build()
        self._load()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setFixedWidth(168)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.pages = QStackedWidget()
        self._page_widgets: dict[str, QWidget] = {
            LEARNING: self._build_learning(),
            TELEGRAM: self._build_telegram(),
            APPEARANCE: self._build_appearance(),
            DATA: self._build_data(),
            ADVANCED: self._build_advanced(),
            ABOUT: self._build_about(),
        }
        for name, widget in self._page_widgets.items():
            QListWidgetItem(name, self.nav)
            self.pages.addWidget(_scrolled(widget))
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(m.space_5, m.space_3, m.space_5, m.space_4)
        self.error = error_label()
        footer.addWidget(self.error, 1)
        buttons = QDialogButtonBox()
        save = buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setProperty("variant", "primary")
        save.setDefault(True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        outer.addLayout(footer)

    # -- pages -------------------------------------------------------------

    def _build_learning(self) -> QWidget:
        page, layout = _page("Learning", "How much you take on, and when the day turns over.")
        form = _form()

        self.new_words = _spin(0, 200, " words")
        self.new_words.setToolTip("How many new words you are offered each day. 0 pauses intake.")
        form.addRow("New words a day", self.new_words)

        self.include_not_reviewed = QCheckBox("Also offer words you have never answered")
        self.include_not_reviewed.setToolTip(
            "A freshly imported list is “not reviewed” rather than “unknown”. "
            "Without this, such a list has nothing to offer."
        )
        form.addRow("", self.include_not_reviewed)

        self.capacity = _spin(0, 1000, " reviews")
        self.capacity.setSpecialValueText("No limit")
        self.capacity.setToolTip(
            "The most reviews you want in one day. When a day is already over this, "
            "new words pause until it clears."
        )
        form.addRow("Review limit a day", self.capacity)

        self.mastery_days = _spin(1, 365, " days")
        self.mastery_days.setToolTip(
            "Once LexiTrack expects you to remember a word for this long, it marks it known."
        )
        form.addRow("Count as known after", self.mastery_days)

        self.review_known = QCheckBox("Keep reviewing words marked known")
        self.review_known.setToolTip(
            "Known words still come round, just rarely. Turn this off and a known "
            "word leaves the schedule until you reset it."
        )
        form.addRow("", self.review_known)

        self.hide_meaning = QCheckBox("Hide the meaning until I ask for it")
        self.hide_meaning.setToolTip(
            "Applies to study reviews only. Flashcards and the word table always show it."
        )
        form.addRow("", self.hide_meaning)

        # Same form, so the fields line up: two forms each size their own
        # label column and the boxes end up at different offsets.
        form.addRow(_heading("THE DAY"))
        day_form = form
        self.day_start = _HourSpin()
        self.day_start.setToolTip(
            "When a new learning day begins. At 0 the day turns over at midnight, "
            "so a review at 01:00 counts as the new day."
        )
        day_form.addRow("Day starts at", self.day_start)

        self.notify_hour = _HourSpin()
        self.notify_hour.setToolTip("When the morning message with the day's words is sent.")
        day_form.addRow("Morning message", self.notify_hour)

        self.reminder_hour = _HourSpin()
        self.reminder_hour.setToolTip("When you are reminded about anything still unanswered.")
        day_form.addRow("Evening reminder", self.reminder_hour)
        layout.addLayout(form)
        layout.addWidget(
            _note(
                "Times are in Europe/Istanbul. The morning message and the day "
                "boundary are separate: a 06:00 message with a midnight boundary "
                "means a 06:00 answer counts for today, not yesterday."
            )
        )
        layout.addStretch(1)
        return page

    def _build_telegram(self) -> QWidget:
        page, layout = _page(
            "Telegram",
            "Today's words each morning and your reviews on your phone, for as "
            "long as LexiTrack is running.",
        )
        self.telegram_enabled = QCheckBox("Send my daily words and reviews to Telegram")
        self.telegram_enabled.setToolTip(
            "Stays as you leave it across restarts. Turning it off stops the bot "
            "until you turn it on again."
        )
        layout.addWidget(self.telegram_enabled)

        form = _form()
        self.telegram_status = QLabel()
        self.telegram_status.setObjectName("ContextName")
        form.addRow("Status", self.telegram_status)
        self.telegram_token = QLabel()
        self.telegram_token.setWordWrap(True)
        self.telegram_token.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Token", self.telegram_token)
        self.telegram_chat = QLabel()
        self.telegram_chat.setWordWrap(True)
        form.addRow("Your chat", self.telegram_chat)
        layout.addLayout(form)

        row = QHBoxLayout()
        row.setSpacing(METRICS.space_2)
        open_env = QPushButton("Open .env")
        open_env.setToolTip("Open the file the token is read from")
        open_env.clicked.connect(self._open_env)
        row.addWidget(open_env)
        reload_env = QPushButton("Reload .env")
        reload_env.setToolTip("Read the token again, after you have pasted it in")
        reload_env.clicked.connect(self._reload_env)
        row.addWidget(reload_env)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(_heading("SETTING IT UP"))
        layout.addWidget(
            _note(
                "1.  In Telegram, open @BotFather, send /newbot and follow the two "
                "questions. It replies with a token.\n"
                "2.  Open .env, paste the token after LEXITRACK_TELEGRAM_TOKEN= and save.\n"
                "3.  Press Reload .env, tick the box above and press Save.\n"
                "4.  Open your new bot in Telegram and send /start. That chat becomes "
                "the only one the bot answers."
            )
        )
        layout.addWidget(
            _note(
                "The token is never stored in the vocabulary database, so it is not "
                "in your backups. If it leaks, revoke it in @BotFather and paste the "
                "new one."
            )
        )
        layout.addStretch(1)
        if self._telegram is not None:
            self._telegram.state_changed.connect(lambda *_: self._show_telegram_state())
        return page

    def _show_telegram_state(self) -> None:
        telegram = self._telegram
        if telegram is None:
            self.telegram_status.setText("Unavailable")
            return
        state = telegram.state
        status = state.label
        if telegram.detail:
            status = f"{status} · {telegram.detail}"
        self.telegram_status.setText(status)
        self.telegram_status.setProperty("tone", "error" if state is BotState.ERROR else "")

        config = telegram.config
        if config.has_token:
            where = str(config.source) if config.source else "the environment"
            self.telegram_token.setText(f"{config.masked_token}  (from {where})")
        else:
            target = next(
                (path for path in env_file_candidates() if path.exists()),
                env_file_candidates()[-1],
            )
            self.telegram_token.setText(f"Not set. Paste it into {target}")
        owner = telegram.owner_chat()
        self.telegram_chat.setText(
            owner if owner else "Not connected yet — send /start to your bot"
        )

    def _open_env(self) -> None:
        candidates = env_file_candidates()
        target = next((path for path in candidates if path.exists()), candidates[-1])
        if not target.exists():
            example = target.with_name(".env.example")
            text = (
                example.read_text(encoding="utf-8")
                if example.exists()
                else "LEXITRACK_TELEGRAM_TOKEN=\nLEXITRACK_TELEGRAM_CHAT_ID=\n"
            )
            target.write_text(text, encoding="utf-8")
        QDesktopServices.openUrl(_file_url(target))

    def _reload_env(self) -> None:
        if self._telegram is not None:
            self._telegram.restart()
        self._show_telegram_state()

    def _build_appearance(self) -> QWidget:
        page, layout = _page("Appearance", "The only setting here that is not shared with the bot.")
        form = _form()
        self.theme_combo = QComboBox()
        for name in (ThemeName.LIGHT, ThemeName.DARK):
            self.theme_combo.addItem(name.label, name.value)
        self.theme_combo.setToolTip("Also Ctrl+T from anywhere in the app")
        form.addRow("Theme", self.theme_combo)
        layout.addLayout(form)
        layout.addWidget(
            _note(
                "The theme is remembered per computer rather than in your vocabulary "
                "database, so copying the database to another machine does not carry "
                "it over."
            )
        )
        layout.addStretch(1)
        return page

    def _build_data(self) -> QWidget:
        page, layout = _page("Data", "Where your words live, and how to start over.")
        form = _form()
        folder = QLabel(str(paths.data_dir()))
        folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        folder.setWordWrap(True)
        form.addRow("Data folder", folder)
        database = QLabel(str(paths.database_path()))
        database.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        database.setWordWrap(True)
        form.addRow("Database", database)
        layout.addLayout(form)

        row = QHBoxLayout()
        row.setSpacing(METRICS.space_2)
        open_button = QPushButton("Open Data Folder")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(_file_url(paths.data_dir()))
        )
        row.addWidget(open_button)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(_heading("STARTING OVER"))
        layout.addWidget(
            _note(
                "Resetting progress marks every word as not reviewed and removes the "
                "schedule, so the engine starts again from your first day. Your words, "
                "lists and definitions are kept."
            )
        )
        reset_row = QHBoxLayout()
        reset = QPushButton("Reset All Progress…")
        reset.setProperty("variant", "ghost")
        reset.clicked.connect(self._reset_progress)
        reset_row.addWidget(reset)
        reset_row.addStretch(1)
        layout.addLayout(reset_row)
        layout.addStretch(1)
        return page

    def _build_advanced(self) -> QWidget:
        page, layout = _page(
            "Advanced",
            "Two separate switches: one shows the controls below, the other writes "
            "a detailed log.",
        )
        switches = _form()
        self.developer_mode = QCheckBox("Developer mode")
        self.developer_mode.setToolTip("Reveals the scheduler controls and the simulator")
        self.developer_mode.toggled.connect(self._apply_developer_mode)
        switches.addRow("", self.developer_mode)
        self.debug_logging = QCheckBox("Debug logging")
        self.debug_logging.setToolTip(
            "Writes every scheduling decision to the log file. Useful for a bug "
            "report; the file grows quickly."
        )
        switches.addRow("", self.debug_logging)
        layout.addLayout(switches)

        self._advanced_box = QFrame()
        self._advanced_box.setObjectName("Panel")
        self._advanced_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box = QVBoxLayout(self._advanced_box)
        box.setContentsMargins(
            METRICS.space_4, METRICS.space_4, METRICS.space_4, METRICS.space_4
        )
        box.setSpacing(METRICS.space_3)
        box.addWidget(_heading("SCHEDULER"))
        scheduler_form = _form()
        self.retention = QDoubleSpinBox()
        self.retention.setRange(0.70, 0.99)
        self.retention.setSingleStep(0.01)
        self.retention.setDecimals(2)
        self.retention.setMaximumWidth(160)
        # The interface is English, so the number is too: "0.90", not the
        # "0,90" a Turkish system locale would otherwise produce.
        self.retention.setLocale(QLocale(QLocale.Language.English))
        self.retention.setToolTip(
            "How much you want to remember at each review. Higher means shorter "
            "intervals and many more reviews; 0.90 is the tuned default."
        )
        scheduler_form.addRow("Target retention", self.retention)

        self.leech_consecutive = _spin(1, 20, "")
        self.leech_consecutive.setToolTip("Failures in a row before a word is flagged")
        scheduler_form.addRow("Flag after failures in a row", self.leech_consecutive)
        self.leech_total = _spin(1, 100, "")
        self.leech_total.setToolTip("Failures in total before a word is flagged")
        scheduler_form.addRow("Flag after failures in total", self.leech_total)
        self.leech_weak = _spin(0, 90, " days")
        self.leech_weak.setToolTip(
            "A word answered right but forgotten within this many days is flagged too"
        )
        scheduler_form.addRow("Weak memory under", self.leech_weak)
        box.addLayout(scheduler_form)

        box.addWidget(_heading("SIMULATE A YEAR"))
        box.addWidget(
            _note(
                "Runs the real scheduler forward over an imaginary word pool and "
                "reports the load. It writes nothing: no cards, no history, no "
                "status changes."
            )
        )
        sim_row = QHBoxLayout()
        sim_row.setSpacing(METRICS.space_2)
        self.profile_combo = QComboBox()
        for key in sorted(PROFILES):
            self.profile_combo.addItem(PROFILES[key].name, key)
        self.profile_combo.setCurrentIndex(self.profile_combo.findData(DEFAULT_PROFILE))
        sim_row.addWidget(QLabel("Answers like"))
        sim_row.addWidget(self.profile_combo)
        self.simulate_button = QPushButton("Run 365-day simulation")
        self.simulate_button.clicked.connect(self._simulate)
        sim_row.addWidget(self.simulate_button)
        sim_row.addStretch(1)
        box.addLayout(sim_row)
        self.simulation_result = QLabel("")
        self.simulation_result.setObjectName("Muted")
        self.simulation_result.setWordWrap(True)
        self.simulation_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        box.addWidget(self.simulation_result)
        layout.addWidget(self._advanced_box)
        layout.addStretch(1)
        return page

    def _build_about(self) -> QWidget:
        page, layout = _page("About", "LexiTrack — Vocabulary Learning & Review")
        counts = self._engine.state_counts()
        plan = self._engine.active_plan()
        form = _form()
        form.addRow("Study plan", QLabel(plan.name if plan else "None"))
        form.addRow("Lists in plan", QLabel(plan.list_label if plan else "—"))
        form.addRow("Words scheduled", QLabel(f"{sum(counts.values()):,}"))
        for state, count in counts.items():
            if count:
                form.addRow(f"  {state.replace('_', ' ').title()}", QLabel(f"{count:,}"))
        form.addRow("Words in total", QLabel(f"{self._service.get_progress().total:,}"))
        layout.addLayout(form)
        layout.addWidget(
            _note(
                "Everything is stored on this computer. LexiTrack sends nothing "
                "anywhere unless you connect a Telegram bot yourself."
            )
        )
        layout.addStretch(1)
        return page

    # -- loading and saving ------------------------------------------------

    def _load(self) -> None:
        s = self._settings
        self.new_words.setValue(s.new_words_per_day)
        self.include_not_reviewed.setChecked(s.new_words_include_not_reviewed)
        self.capacity.setValue(s.review_capacity_per_day)
        self.mastery_days.setValue(int(s.mastery_stability_days))
        self.review_known.setChecked(s.review_known_words)
        self.hide_meaning.setChecked(s.hide_meaning_in_study)
        self.day_start.setValue(s.day_start_hour)
        self.notify_hour.setValue(s.notify_hour)
        self.reminder_hour.setValue(s.evening_reminder_hour)
        self.retention.setValue(s.desired_retention)
        self.leech_consecutive.setValue(s.leech_consecutive)
        self.leech_total.setValue(s.leech_total_lapses)
        self.leech_weak.setValue(int(s.leech_weak_stability_days))
        self.developer_mode.setChecked(s.developer_mode)
        self.debug_logging.setChecked(s.debug_logging)
        self.telegram_enabled.setChecked(s.telegram_enabled)
        self._show_telegram_state()
        index = self.theme_combo.findData(self._theme.current.value)
        self.theme_combo.setCurrentIndex(max(index, 0))
        self._apply_developer_mode(s.developer_mode)

    def _apply_developer_mode(self, enabled: bool) -> None:
        """Advanced controls are disabled rather than hidden.

        Hiding them would make the switch look broken — the user turns on
        Developer mode and the page does not change height — and it would hide
        the current values from someone reading the window to find out what
        the engine is doing.
        """
        self._advanced_box.setEnabled(enabled)

    def _save(self) -> None:
        values: dict[str, object] = {
            Setting.NEW_WORDS_PER_DAY: self.new_words.value(),
            Setting.NEW_WORDS_INCLUDE_NOT_REVIEWED: self.include_not_reviewed.isChecked(),
            Setting.REVIEW_CAPACITY_PER_DAY: self.capacity.value(),
            Setting.MASTERY_STABILITY_DAYS: self.mastery_days.value(),
            Setting.REVIEW_KNOWN_WORDS: self.review_known.isChecked(),
            Setting.HIDE_MEANING_IN_STUDY: self.hide_meaning.isChecked(),
            Setting.DAY_START_HOUR: self.day_start.value(),
            Setting.NOTIFY_HOUR: self.notify_hour.value(),
            Setting.EVENING_REMINDER_HOUR: self.reminder_hour.value(),
            Setting.DESIRED_RETENTION: round(self.retention.value(), 2),
            Setting.LEECH_CONSECUTIVE: self.leech_consecutive.value(),
            Setting.LEECH_TOTAL_LAPSES: self.leech_total.value(),
            Setting.LEECH_WEAK_STABILITY_DAYS: self.leech_weak.value(),
            Setting.DEVELOPER_MODE: self.developer_mode.isChecked(),
            Setting.DEBUG_LOGGING: self.debug_logging.isChecked(),
            Setting.TELEGRAM_ENABLED: self.telegram_enabled.isChecked(),
        }
        try:
            self._engine.save_settings(values)
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return
        chosen = ThemeName(str(self.theme_combo.currentData()))
        if chosen is not self._theme.current:
            self._theme.apply(chosen)
        self.changed.emit()
        self.accept()

    # -- actions -----------------------------------------------------------

    def _simulate(self) -> None:
        """Run the simulation with the values on screen, not the saved ones.

        Otherwise the button would answer a question about the old settings,
        which is worse than not answering at all.
        """
        pending = replace(
            self._settings,
            new_words_per_day=self.new_words.value(),
            review_capacity_per_day=self.capacity.value(),
            desired_retention=round(self.retention.value(), 2),
        )
        plan = self._engine.active_plan()
        pool = (
            self._engine.plan_counts(plan.id)["total"]
            if plan
            else self._service.get_progress().total
        )
        self.simulate_button.setEnabled(False)
        self.simulation_result.setText("Simulating a year…")
        self.simulation_result.repaint()
        try:
            result = simulate_current_settings(
                pending,
                pool_size=max(pool, 1),
                profile=str(self.profile_combo.currentData()),
            )
        finally:
            self.simulate_button.setEnabled(True)
        text = result.summary()
        if result.warnings:
            text += "\n\n" + "\n".join(f"• {note}" for note in result.warnings)
        self.simulation_result.setText(text)

    def _reset_progress(self) -> None:
        if not confirm(
            self,
            "Reset All Progress",
            "Mark every word as not reviewed and clear the review schedule?\n\n"
            "Your words, lists and definitions are kept. This cannot be undone.",
            "Reset Progress",
        ):
            return
        self._service.reset_progress()
        self.changed.emit()
        self.simulation_result.setText("")


# -- small builders --------------------------------------------------------


def _page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    m = METRICS
    page = QWidget()
    page.setObjectName("PanelBody")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_5)
    layout.setSpacing(m.space_4)
    heading = QLabel(title)
    heading.setObjectName("PageTitle")
    layout.addWidget(heading)
    hint = QLabel(subtitle)
    hint.setObjectName("PageSubtitle")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    return page, layout


def _scrolled(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setSpacing(METRICS.space_3)
    # Centred on the field, not on its top edge: the boxes are taller than a
    # line of text, and top-aligned labels look like they belong to the row
    # above.
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


def _spin(minimum: int, maximum: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSuffix(suffix)
    spin.setMaximumWidth(160)
    return spin


class _HourSpin(QSpinBox):
    """An hour of the day, shown as ``06:00`` rather than ``6:00``."""

    def __init__(self) -> None:
        super().__init__()
        self.setRange(0, 23)
        self.setMaximumWidth(160)

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt naming
        return f"{value:02d}:00"

    def valueFromText(self, text: str) -> int:  # noqa: N802 - Qt naming
        digits = text.split(":", 1)[0].strip()
        return int(digits) if digits.isdigit() else 0


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label


def _file_url(path) -> QUrl:
    return QUrl.fromLocalFile(str(path))
