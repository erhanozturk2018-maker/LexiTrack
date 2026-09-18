"""The tray icon: how LexiTrack is controlled when its window is closed.

With the Telegram bot on, closing the window does not quit — the bot has to
keep running to send the morning message. That only works if the running app
stays visible and controllable, so everything that matters is one right-click
away:

    Open LexiTrack
    Today: 25 new · 140 due       (read-only)
    ───────────────
    ✓ Telegram bot                  on/off, remembered across restarts
    ✓ Start with Windows            the per-user Run entry
    ───────────────
    Settings…
    Quit LexiTrack                  ends everything, the bot included

Three switches, independent on purpose: turning the bot off leaves the app
running; turning off Start with Windows does not stop anything now; Quit
stops everything now but changes neither setting.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core import autostart
from ..services.learning_service import LearningService
from ..telegram.runtime import BotState
from .telegram_controller import TelegramController

APP_ICON = Path(__file__).with_name("theme") / "icons" / "app.svg"


def app_icon() -> QIcon:
    return QIcon(str(APP_ICON))


class Tray(QObject):
    """The tray icon and its menu."""

    open_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        engine: LearningService,
        telegram: TelegramController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._telegram = telegram
        self.icon = QSystemTrayIcon(app_icon(), self)
        self.icon.setToolTip("LexiTrack")
        self.icon.activated.connect(self._on_activated)

        self.menu = QMenu()
        self.open_action = self.menu.addAction("Open LexiTrack", self.open_requested.emit)
        self.today_action = self.menu.addAction("")
        self.today_action.setEnabled(False)
        self.menu.addSeparator()
        self.bot_action = QAction("Telegram bot", self.menu)
        self.bot_action.setCheckable(True)
        self.bot_action.toggled.connect(self._toggle_bot)
        self.menu.addAction(self.bot_action)
        self.autostart_action = QAction("Start with Windows", self.menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setVisible(autostart.supported())
        self.autostart_action.toggled.connect(self._toggle_autostart)
        self.menu.addAction(self.autostart_action)
        self.menu.addSeparator()
        self.menu.addAction("Settings…", self.settings_requested.emit)
        self.menu.addAction("Quit LexiTrack", self.quit_requested.emit)
        self.menu.aboutToShow.connect(self.refresh)
        self.icon.setContextMenu(self.menu)

        telegram.state_changed.connect(lambda *_: self.refresh())

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.refresh()
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def message(self, title: str, text: str) -> None:
        self.icon.showMessage(title, text, app_icon(), 6000)

    def refresh(self) -> None:
        """Re-read everything the menu shows. Called before it opens."""
        plan = self._engine.daily_plan()
        if plan.has_plan:
            today = f"Today: {len(plan.new_words)} new · {plan.due_count} due"
        else:
            today = "No study plan yet"
        self.today_action.setText(today)

        state = self._telegram.state
        self.bot_action.blockSignals(True)
        self.bot_action.setChecked(self._telegram.enabled)
        self.bot_action.blockSignals(False)
        label = "Telegram bot"
        if self._telegram.enabled and state is not BotState.RUNNING:
            label = f"Telegram bot ({state.label.lower()})"
        self.bot_action.setText(label)

        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(autostart.is_enabled())
        self.autostart_action.blockSignals(False)

        tip = "LexiTrack"
        if plan.has_plan:
            tip += f"\n{today}"
        if self._telegram.enabled:
            tip += f"\nTelegram: {state.label}"
        self.icon.setToolTip(tip)

    def _toggle_bot(self, enabled: bool) -> None:
        self._telegram.set_enabled(enabled)
        self.refresh()

    def _toggle_autostart(self, enabled: bool) -> None:
        autostart.set_enabled(enabled)
        self.refresh()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()
