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
from html import escape

from PySide6.QtCore import QCoreApplication, QLocale, QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import autostart, paths
from ..core.errors import LexiTrackError
from ..core.logging_config import set_file_logging
from ..models.language import LANGUAGE_NAMES, UNDETERMINED
from ..models.settings import Setting
from ..services.learning_service import LearningService
from ..services.maintenance import KEEP_BACKUPS, Maintenance
from ..services.optimizer import FitResult, Personaliser
from ..services.review_queue import HARD_CEILING, effective_capacity
from ..services.simulation import DEFAULT_PROFILE, PROFILES, simulate_current_settings
from ..services.vocabulary_service import VocabularyService
from ..telegram.config import env_file_candidates
from ..telegram.runtime import BotState
from .components.settings_rows import CONTROL_WIDTH as _CONTROL_WIDTH
from .components.settings_rows import SettingsGroup as _Group
from .components.settings_rows import note as _note
from .components.settings_rows import page as _page
from .components.settings_rows import scrolled as _scrolled
from .components.settings_rows import spin as _spin
from .components.settings_rows import switch as _switch
from .dialogs import confirm, error_label, show_error
from .telegram_controller import TelegramController
from .theme import ThemeManager, ThemeName, current_palette
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

        workload = _Group("DAILY WORKLOAD")
        self.new_words = _spin(0, 200, " words")
        workload.add("New words a day", "0 pauses new words; reviews carry on.", self.new_words)
        self.include_not_reviewed = _switch()
        workload.add(
            "Offer words you have never answered",
            "A freshly imported list is “not reviewed”, not “unknown”. "
            "Without this, such a list has nothing to offer.",
            self.include_not_reviewed,
        )
        self.capacity = _spin(10, HARD_CEILING, " reviews")
        workload.add(
            "Review limit a day",
            f"At most {HARD_CEILING}. Over it, the most fragile words and those you are "
            "most likely to have forgotten come first, and new words shrink so "
            "tomorrow stays under it too.",
            self.capacity,
        )
        layout.addWidget(workload)

        known = _Group("WORDS YOU KNOW")
        self.mastery_days = _spin(1, 365, " days")
        known.add(
            "Offer as known after",
            "Once LexiTrack expects you to remember a word this long, it offers to mark "
            "it Known. It never marks a word Known by itself.",
            self.mastery_days,
        )
        self.review_known = _switch()
        known.add(
            "Keep reviewing words learned here",
            "Only words that became Known through Study; words you knew before "
            "are never scheduled. On, they come round every few months, which is "
            "how LexiTrack learns whether its predictions were right.",
            self.review_known,
        )
        layout.addWidget(known)

        language = _Group("LANGUAGE")
        self.learner_language = QComboBox()
        self.learner_language.addItem("None", "")
        for code, name in sorted(LANGUAGE_NAMES.items(), key=lambda item: item[1]):
            if code != UNDETERMINED:
                self.learner_language.addItem(name, code)
        self.learner_language.setFixedWidth(_CONTROL_WIDTH)
        language.add(
            "Explain words in",
            "Your own language, for meanings, nuances and translations, where content "
            "in it has been added. None: words are explained by their definitions. The "
            "words and their examples stay in the language you are learning.",
            self.learner_language,
        )
        layout.addWidget(language)

        day = _Group("THE DAY")
        self.day_start = _HourSpin()
        day.add(
            "Day starts at",
            "At 00:00 a review at 01:00 counts for the new day.",
            self.day_start,
        )
        self.notify_hour = _HourSpin()
        day.add("Morning message", "When today's words are sent to Telegram.", self.notify_hour)
        self.reminder_hour = _HourSpin()
        day.add(
            "Evening reminder",
            "Sent only if something is still waiting.",
            self.reminder_hour,
        )
        layout.addWidget(day)
        self.timezone_note = _note("")
        layout.addWidget(self.timezone_note)
        layout.addStretch(1)
        return page

    def _build_telegram(self) -> QWidget:
        page, layout = _page(
            "Telegram",
            "Today's words each morning and your reviews on your phone, for as "
            "long as LexiTrack is running.",
        )
        bot = _Group("BOT")
        self.telegram_enabled = _switch()
        bot.add(
            "Send my daily words and reviews",
            "Stays as you leave it across restarts.",
            self.telegram_enabled,
        )
        self.weekly_summary = _switch()
        bot.add(
            "Weekly summary",
            "Sunday at the evening reminder hour: the week's answers, the words "
            "learned and the words giving you trouble.",
            self.weekly_summary,
        )
        self.telegram_status = QLabel()
        self.telegram_status.setTextFormat(Qt.TextFormat.RichText)
        bot.add("Status", None, self.telegram_status)
        self.telegram_chat = QLabel()
        self.telegram_chat.setWordWrap(True)
        self.telegram_chat.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.telegram_chat.setMaximumWidth(300)
        bot.add("Your chat", None, self.telegram_chat)
        layout.addWidget(bot)

        token = _Group("TOKEN")
        self.telegram_token = QLabel()
        self.telegram_token.setObjectName("SettingHint")
        self.telegram_token.setWordWrap(True)
        self.telegram_token.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons = QWidget()
        buttons.setObjectName("PanelBody")
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(METRICS.space_2)
        open_env = QPushButton("Open .env")
        open_env.setToolTip("Open the file the token is read from")
        open_env.clicked.connect(self._open_env)
        reload_env = QPushButton("Reload .env")
        reload_env.setToolTip("Read the token again, after you have pasted it in")
        reload_env.clicked.connect(self._reload_env)
        row.addWidget(open_env)
        row.addWidget(reload_env)
        token.add("Bot token", self.telegram_token, buttons)
        layout.addWidget(token)

        steps = _Group("SETTING IT UP")
        guide = QLabel(
            "1.  In Telegram, open @BotFather, send /newbot and answer its two "
            "questions. It replies with a token.\n"
            "2.  Press Open .env, paste the token after LEXITRACK_TELEGRAM_TOKEN= and save.\n"
            "3.  Press Reload .env, switch the bot on above and press Save.\n"
            "4.  Open your bot in Telegram and send /start. That chat becomes "
            "the only one it answers."
        )
        guide.setObjectName("Muted")
        guide.setWordWrap(True)
        steps.add_widget(guide)
        layout.addWidget(steps)
        layout.addWidget(
            _note(
                "The token is never stored in the vocabulary database, so it is not in "
                "your backups. If it leaks, revoke it in @BotFather and paste the new one."
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
        palette = current_palette()
        # A dot in the state's colour carries the meaning at a glance; the
        # words stay at body weight like every other value on the page.
        colour = {
            BotState.RUNNING: palette.known,
            BotState.CONNECTING: palette.unknown,
            BotState.NO_TOKEN: palette.unknown,
            BotState.ERROR: palette.danger,
        }.get(state, palette.text_faint)
        status = escape(state.label)
        if telegram.detail:
            status = f"{status} · {escape(telegram.detail)}"
        self.telegram_status.setText(f"<span style='color:{colour}'>●</span>&nbsp; {status}")

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
        page, layout = _page("Appearance", "The one setting here that is not shared with the bot.")
        group = _Group(None)
        self.theme_combo = QComboBox()
        for name in (ThemeName.LIGHT, ThemeName.DARK):
            self.theme_combo.addItem(name.label, name.value)
        self.theme_combo.setFixedWidth(_CONTROL_WIDTH)
        group.add(
            "Theme",
            "Also Ctrl+T anywhere. Remembered on this computer, not in the database.",
            self.theme_combo,
        )
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_data(self) -> QWidget:
        page, layout = _page("Data", "Where your words live, and how to start over.")

        storage = _Group("ON THIS COMPUTER")
        self.autostart = _switch()
        self.autostart.setChecked(autostart.is_enabled())
        if autostart.supported():
            storage.add(
                "Start with Windows",
                "Starts in the tray, without a window, so the morning message is sent.",
                self.autostart,
            )
        open_button = QPushButton("Open")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(_file_url(paths.data_dir()))
        )
        storage.add("Data folder", str(paths.data_dir()), open_button)
        layout.addWidget(storage)

        backups = _Group("BACKUPS")
        self.backup_label = QLabel()
        self.backup_label.setObjectName("SettingHint")
        self.backup_label.setWordWrap(True)
        backup_now = QPushButton("Back up now")
        backup_now.clicked.connect(self._backup_now)
        backups.add("Daily copy", self.backup_label, backup_now)
        history = QPushButton("Export history")
        history.clicked.connect(self._export_history)
        backups.add(
            "Review history",
            "Every answer you have given, as a CSV file for a spreadsheet.",
            history,
        )
        layout.addWidget(backups)
        self._show_backups()

        over = _Group("STARTING OVER")
        reset = QPushButton("Reset progress")
        reset.setProperty("variant", "danger")
        reset.clicked.connect(self._reset_progress)
        over.add(
            "Reset all progress",
            "Every word back to not reviewed; the schedule, every answer and "
            "the status history are cleared. Words, lists, definitions and "
            "plans are kept.",
            reset,
        )
        layout.addWidget(over)
        layout.addStretch(1)
        return page

    def _build_advanced(self) -> QWidget:
        page, layout = _page(
            "Advanced",
            "For looking under the hood. Nothing here is needed for everyday use.",
        )
        diagnostics = _Group("DIAGNOSTICS")
        self.developer_mode = _switch()
        self.developer_mode.toggled.connect(self._apply_developer_mode)
        diagnostics.add(
            "Developer mode",
            "Unlocks the scheduler controls, the simulator and debug logging.",
            self.developer_mode,
        )
        self.debug_logging = _switch()
        diagnostics.add(
            "Debug logging",
            "One file a day in the logs folder, kept for a week. Nothing is written "
            "to disk while this is off.",
            self.debug_logging,
        )
        self.logs_button = QPushButton("Open")
        self.logs_button.setToolTip(str(paths.logs_dir()))
        self.logs_button.clicked.connect(self._open_logs)
        diagnostics.add("Logs folder", str(paths.logs_dir()), self.logs_button)
        layout.addWidget(diagnostics)

        # Not behind Developer mode: it only ever acts on a button press, asks
        # before using anything, and says where it stands at every stage.
        personal = _Group("FITTED TO YOU")
        self.personal_status = QLabel()
        self.personal_status.setObjectName("SettingHint")
        self.personal_status.setWordWrap(True)
        self.personal_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.personal_button = QPushButton("Fit to my answers")
        self.personal_button.clicked.connect(self._personal_action)
        personal.add("Scheduler parameters", self.personal_status, self.personal_button)
        self.personal_result = QLabel()
        self.personal_result.setObjectName("SettingHint")
        self.personal_result.setWordWrap(True)
        self.personal_use = QPushButton("Use these")
        self.personal_use.setProperty("variant", "primary")
        self.personal_use.clicked.connect(self._use_fit)
        personal.add("Result", self.personal_result, self.personal_use)
        self._personal_result_row = self.personal_use.parentWidget()
        # The hairline above the result row goes and comes back with it.
        self._personal_divider = personal._rows.itemAt(personal._rows.count() - 2).widget()
        layout.addWidget(personal)
        self._fit: FitResult | None = None
        self._fit_thread: QThread | None = None

        # Everything below is disabled, not hidden, outside Developer mode.
        self._advanced_box = QWidget()
        self._advanced_box.setObjectName("PanelBody")
        box = QVBoxLayout(self._advanced_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(METRICS.space_4)

        scheduler = _Group("SCHEDULER")
        self.retention = QDoubleSpinBox()
        self.retention.setRange(0.70, 0.99)
        self.retention.setSingleStep(0.01)
        self.retention.setDecimals(2)
        self.retention.setFixedWidth(_CONTROL_WIDTH)
        # The interface is English, so the number is too: "0.90", not the
        # "0,90" a system locale with a decimal comma would otherwise produce.
        self.retention.setLocale(QLocale(QLocale.Language.English))
        scheduler.add(
            "Target retention",
            "Higher means shorter intervals and many more reviews. 0.90 is the default.",
            self.retention,
        )
        self.leech_consecutive = _spin(1, 20, " in a row")
        scheduler.add("Flag as hard after", "Failures in a row.", self.leech_consecutive)
        self.leech_total = _spin(1, 100, " in total")
        scheduler.add("Or after", "Failures over the word's lifetime.", self.leech_total)
        self.leech_weak = _spin(0, 90, " days")
        scheduler.add(
            "Weak memory under",
            "Answered right but forgotten within this many days.",
            self.leech_weak,
        )
        box.addWidget(scheduler)

        order = _Group("SESSION ORDER")
        self.warm_up = _spin(0, 20, " words")
        order.add(
            "Start with",
            "The easiest words due, so a session does not open on a miss.",
            self.warm_up,
        )
        self.fragile_every = _spin(2, 20, "")
        self.fragile_every.setPrefix("one in every ")
        order.add(
            "Hard words at most",
            "Words that recently failed are mixed in, never more often than this.",
            self.fragile_every,
        )
        box.addWidget(order)

        simulation = _Group("SIMULATE A YEAR")
        self.profile_combo = QComboBox()
        for key in sorted(PROFILES):
            self.profile_combo.addItem(PROFILES[key].name, key)
        self.profile_combo.setCurrentIndex(self.profile_combo.findData(DEFAULT_PROFILE))
        self.profile_combo.setFixedWidth(_CONTROL_WIDTH)
        simulation.add(
            "Answers like",
            "The real scheduler, run forward over your plan. It writes nothing.",
            self.profile_combo,
        )
        self.simulate_button = QPushButton("Run 365 days")
        self.simulate_button.clicked.connect(self._simulate)
        self.simulation_result = QLabel("")
        self.simulation_result.setObjectName("SettingHint")
        self.simulation_result.setWordWrap(True)
        self.simulation_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        simulation.add("Result", self.simulation_result, self.simulate_button)
        box.addWidget(simulation)
        layout.addWidget(self._advanced_box)
        layout.addStretch(1)
        return page

    def _build_about(self) -> QWidget:
        page, layout = _page("About", "LexiTrack — Vocabulary Learning & Review")
        counts = self._engine.state_counts()
        plan = self._engine.active_plan()
        group = _Group("YOUR STUDY")
        group.add(
            "Study plan",
            plan.list_label if plan else None,
            QLabel(plan.name if plan else "None"),
        )
        group.add("Words scheduled", None, QLabel(f"{sum(counts.values()):,}"))
        for state, count in counts.items():
            if count:
                group.add(state.replace("_", " ").title(), None, QLabel(f"{count:,}"))
        group.add("Words in total", None, QLabel(f"{self._service.get_progress().total:,}"))
        layout.addWidget(group)
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
        # Shown as the limit that applies: a stored "no limit" or a number over
        # the ceiling is read as the ceiling, and saved as such only if the
        # user saves.
        self.capacity.setValue(effective_capacity(s.review_capacity_per_day))
        self.warm_up.setValue(s.review_warm_up)
        self.fragile_every.setValue(s.fragile_every)
        self.mastery_days.setValue(int(s.mastery_stability_days))
        self.review_known.setChecked(s.review_known_words)
        index = self.learner_language.findData(s.learner_language or "")
        self.learner_language.setCurrentIndex(max(index, 0))
        self.timezone_note.setText(f"Times are in {s.timezone}.")
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
        self.weekly_summary.setChecked(s.weekly_summary)
        self._show_telegram_state()
        index = self.theme_combo.findData(self._theme.current.value)
        self.theme_combo.setCurrentIndex(max(index, 0))
        self._apply_developer_mode(s.developer_mode)
        self._show_personal()

    # -- fitted parameters ---------------------------------------------------

    def _personaliser(self) -> Personaliser:
        return Personaliser(self._service.database, self._engine)

    def _show_personal(self) -> None:
        """Say where fitting stands, and offer only what can be done now."""
        personaliser = self._personaliser()
        note = personaliser.fit_note()
        self._personal_result_row.setVisible(self._fit is not None)
        self._personal_divider.setVisible(self._fit is not None)
        if note is not None:
            when = note.get("on", "earlier")
            count = note.get("reviews")
            before, after = note.get("loss_before"), note.get("loss_after")
            better = (
                f" They predicted your answers {round((before - after) / before * 100)}% "
                f"better than the defaults."
                if before and after
                else ""
            )
            self.personal_status.setText(
                f"Fitted to your answers on {when}"
                + (f", from {count:,} of them." if count else ".")
                + better
            )
            self.personal_button.setText("Use the defaults")
            self.personal_button.setEnabled(True)
            return
        ready = personaliser.readiness()
        self.personal_button.setText("Fit to my answers")
        if not ready.enough:
            self.personal_status.setText(
                f"Using the published FSRS defaults. Fitting needs {ready.required:,} "
                f"answers given on a later day than the word's previous answer; you "
                f"have {ready.usable:,}. Until then it could only return the defaults."
            )
            self.personal_button.setEnabled(False)
        elif not ready.installed:
            self.personal_status.setText(
                f"You have {ready.usable:,} answers, enough to fit. Fitting needs the "
                f"optional optimizer, a large download: pip install \"lexitrack[optimizer]\" "
                f"in LexiTrack's environment, then restart."
            )
            self.personal_button.setEnabled(False)
        else:
            self.personal_status.setText(
                f"Using the published FSRS defaults. You have {ready.usable:,} answers: "
                f"enough to fit parameters to your memory. Nothing changes until you "
                f"choose to use the result."
            )
            self.personal_button.setEnabled(self._fit_thread is None)

    def _personal_action(self) -> None:
        if self._personaliser().fit_note() is not None:
            if confirm(
                self,
                "Use the Defaults",
                "Go back to the published FSRS parameters? Your fitted ones are "
                "discarded; you can fit again at any time.",
                "Use the defaults",
            ):
                self._personaliser().revert()
                self._fit = None
                self.changed.emit()
            self._show_personal()
            return
        self._start_fit()

    def _start_fit(self) -> None:
        """Fit on a worker thread; the answers are read here, first."""
        personaliser = self._personaliser()
        logs = personaliser.snapshot()
        self.personal_button.setEnabled(False)
        self.personal_button.setText("Fitting\u2026")
        self.personal_status.setText(
            "Fitting to your answers. This can take a minute; the window stays usable."
        )
        worker = _FitWorker(personaliser, logs)
        # Owned by the application, not this window: closing Settings mid-fit
        # must not destroy a running thread. A result for a closed window is
        # simply dropped, and _RUNNING keeps the worker alive until it ends.
        thread = QThread(QCoreApplication.instance())
        _RUNNING.add(worker)
        thread.finished.connect(lambda: _RUNNING.discard(worker))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._fit_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._fit_thread_done)
        self._fit_worker = worker
        self._fit_thread = thread
        thread.start()

    def _fit_thread_done(self) -> None:
        self._fit_thread = None

    def _fit_finished(self, result: object) -> None:
        if isinstance(result, Exception):
            self._fit = None
            self._show_personal()
            show_error(self.error, f"Fitting failed: {result}")
            return
        self._fit = result
        self._show_personal()
        share = round(result.improvement * 100)
        if result.improvement > 0:
            self.personal_result.setText(
                f"Fitted to {result.reviews:,} answers, these parameters predict your "
                f"answers {share}% better than the ones in use (log loss "
                f"{result.loss_before:.3f} \u2192 {result.loss_after:.3f}). Use them?"
            )
            self.personal_use.setEnabled(True)
        else:
            self.personal_result.setText(
                "The fitted parameters did not predict your answers better than the ones "
                "in use, so there is nothing to gain yet. Try again after more reviews."
            )
            self.personal_use.setEnabled(False)

    def _use_fit(self) -> None:
        if self._fit is None:
            return
        self._personaliser().apply(self._fit)
        self._fit = None
        self.changed.emit()
        self._show_personal()

    def _open_logs(self) -> None:
        folder = paths.logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(_file_url(folder))

    def _apply_developer_mode(self, enabled: bool) -> None:
        """Advanced controls are disabled rather than hidden.

        Hiding them would make the switch look broken — the user turns on
        Developer mode and the page does not change height — and it would hide
        the current values from someone reading the window to find out what
        the engine is doing.
        """
        self._advanced_box.setEnabled(enabled)
        self.debug_logging.setEnabled(enabled)
        self.logs_button.setEnabled(enabled)

    def _save(self) -> None:
        values: dict[str, object] = {
            Setting.NEW_WORDS_PER_DAY: self.new_words.value(),
            Setting.NEW_WORDS_INCLUDE_NOT_REVIEWED: self.include_not_reviewed.isChecked(),
            Setting.REVIEW_CAPACITY_PER_DAY: self.capacity.value(),
            Setting.MASTERY_STABILITY_DAYS: self.mastery_days.value(),
            Setting.REVIEW_KNOWN_WORDS: self.review_known.isChecked(),
            Setting.LEARNER_LANGUAGE: self.learner_language.currentData() or "",
            Setting.DAY_START_HOUR: self.day_start.value(),
            Setting.NOTIFY_HOUR: self.notify_hour.value(),
            Setting.EVENING_REMINDER_HOUR: self.reminder_hour.value(),
            Setting.DESIRED_RETENTION: round(self.retention.value(), 2),
            Setting.LEECH_CONSECUTIVE: self.leech_consecutive.value(),
            Setting.LEECH_TOTAL_LAPSES: self.leech_total.value(),
            Setting.LEECH_WEAK_STABILITY_DAYS: self.leech_weak.value(),
            Setting.REVIEW_WARM_UP: self.warm_up.value(),
            Setting.FRAGILE_EVERY: self.fragile_every.value(),
            Setting.DEVELOPER_MODE: self.developer_mode.isChecked(),
            # Debug logging belongs to Developer mode: leaving Developer mode
            # turns it off, so no file keeps growing behind a hidden switch.
            Setting.DEBUG_LOGGING: (
                self.developer_mode.isChecked() and self.debug_logging.isChecked()
            ),
            Setting.TELEGRAM_ENABLED: self.telegram_enabled.isChecked(),
            Setting.WEEKLY_SUMMARY: self.weekly_summary.isChecked(),
        }
        try:
            self._engine.save_settings(values)
        except LexiTrackError as exc:
            show_error(self.error, exc.user_message)
            return
        if autostart.supported() and self.autostart.isChecked() != autostart.is_enabled():
            autostart.set_enabled(self.autostart.isChecked())
        saved = self._engine.settings
        set_file_logging(saved.developer_mode and saved.debug_logging)
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

    def _maintenance(self) -> Maintenance:
        return Maintenance(self._service.database, self._engine.clock)

    def _show_backups(self) -> None:
        maintenance = self._maintenance()
        existing = maintenance.backups()
        # The folder is in the tooltip rather than the text: a full path in a
        # one-line hint breaks mid-word, and the data folder row shows it.
        if existing:
            newest = existing[0].stem.replace("vocabulary-", "")
            text = (
                f"Made every day, the newest {KEEP_BACKUPS} kept. "
                f"Latest: {newest} · {len(existing)} kept."
            )
        else:
            text = f"Made every day, the newest {KEEP_BACKUPS} kept. None yet."
        self.backup_label.setToolTip(str(maintenance.directory))
        self.backup_label.setText(text)

    def _backup_now(self) -> None:
        target = self._maintenance().backup()
        if target is None:
            show_error(self.error, "The backup failed. The log file has the details.")
        self._show_backups()

    def _export_history(self) -> None:
        default = paths.exports_dir() / f"review-history-{self._engine.clock.today()}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Review History", str(default), "CSV files (*.csv)"
        )
        if not path:
            return
        rows = self._maintenance().export_review_log(path)
        show_error(self.error, None)
        self.backup_label.setText(f"Exported {rows:,} reviews to {path}.")

    def _reset_progress(self) -> None:
        if not confirm(
            self,
            "Reset All Progress",
            "Mark every word as not reviewed and clear the study schedule, every "
            "answer you have given and the history of how each word was learned?"
            "\n\nYour answers are also what LexiTrack would learn your memory "
            "from. To keep a copy, cancel and use Export history first.\n\n"
            "Your words, lists, definitions and plans are kept, and yesterday's "
            "backup stays in the data folder. This cannot be undone.",
            "Reset progress",
        ):
            return
        self._service.reset_progress()
        self.changed.emit()
        self.simulation_result.setText("")


#: Fits still running, kept alive whatever happens to the window that started them.
_RUNNING: set = set()


class _FitWorker(QObject):
    """Runs a fit off the window's thread, on answers already read."""

    finished = Signal(object)

    def __init__(self, personaliser: Personaliser, logs: list) -> None:
        super().__init__()
        self._personaliser = personaliser
        self._logs = logs

    def run(self) -> None:
        try:
            self.finished.emit(self._personaliser.fit(self._logs))
        except Exception as exc:  # reported in the window, never lost
            self.finished.emit(exc)


# -- small builders --------------------------------------------------------


class _HourSpin(QSpinBox):
    """An hour of the day, shown as ``06:00`` rather than ``6:00``."""

    def __init__(self) -> None:
        super().__init__()
        self.setRange(0, 23)
        self.setFixedWidth(_CONTROL_WIDTH)

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt naming
        return f"{value:02d}:00"

    def valueFromText(self, text: str) -> int:  # noqa: N802 - Qt naming
        digits = text.split(":", 1)[0].strip()
        return int(digits) if digits.isdigit() else 0




def _file_url(path) -> QUrl:
    return QUrl.fromLocalFile(str(path))
