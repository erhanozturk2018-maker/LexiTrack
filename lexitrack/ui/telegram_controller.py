"""The Qt side of the Telegram thread: start it, stop it, hear from it.

The bot runs on its own thread with its own event loop; this object is the
only thing the window talks to about it. It turns the bot's callbacks into Qt
signals — emitted from the bot thread, delivered on the UI thread by Qt's
queued connections — so no widget is ever touched from the wrong thread.

Whether the bot runs is decided in exactly one place, :meth:`apply`, from two
facts: the ``telegram_enabled`` setting and whether ``.env`` holds a token.
The setting lives in the database, so turning the bot off survives a restart:
"I stopped it; will it come back on its own?" — no.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from ..core.clock import DayClock
from ..database.connection import Database
from ..repositories import RuntimeRepository, SettingsRepository
from ..telegram.config import TelegramConfig, load_config
from ..telegram.runtime import BotState, TelegramRuntime

log = logging.getLogger(__name__)


class TelegramController(QObject):
    """Owns the Telegram thread for the lifetime of the window."""

    #: The bot changed something (a review, new words) the pages should show.
    activity = Signal()
    #: ``(state, detail)`` — for the Settings page and the tray.
    state_changed = Signal(str, str)

    def __init__(
        self,
        database: Database,
        clock: DayClock | None = None,
        config: TelegramConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = database
        self._clock = clock
        self._config = config
        self._runtime: TelegramRuntime | None = None
        self._state = BotState.OFF
        self._detail = ""

    # -- state ---------------------------------------------------------------

    @property
    def config(self) -> TelegramConfig:
        if self._config is None:
            self._config = load_config()
        return self._config

    @property
    def state(self) -> BotState:
        return self._state

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def enabled(self) -> bool:
        return SettingsRepository(self._db).load().telegram_enabled

    def owner_chat(self) -> str | None:
        return self.config.allowed_chat_id or RuntimeRepository(self._db).chat_id()

    # -- control -------------------------------------------------------------

    def reload_config(self) -> TelegramConfig:
        """Read ``.env`` again, e.g. after the user pasted a token."""
        self._config = load_config()
        return self._config

    def apply(self) -> BotState:
        """Start or stop the bot so it matches the setting and the token."""
        wanted = self.enabled
        if self._runtime is not None and not self._runtime.running:
            # The thread ended on its own (a rejected token, no network);
            # forget it so this call can try again.
            self._runtime = None
        if not self.config.has_token:
            self._stop_runtime()
            self._publish(BotState.NO_TOKEN if wanted else BotState.OFF, "")
            return self._state
        if wanted and self._runtime is None:
            self._runtime = TelegramRuntime(
                self._db,
                self.config,
                clock=self._clock,
                on_activity=self.activity.emit,
                on_state=lambda state, detail: self._publish(state, detail),
            )
            self._runtime.start()
        elif not wanted:
            self._stop_runtime()
            self._publish(BotState.OFF, "")
        return self._state

    def set_enabled(self, enabled: bool) -> BotState:
        SettingsRepository(self._db).set("telegram_enabled", enabled)
        return self.apply()

    def restart(self) -> BotState:
        """Stop, re-read ``.env`` and start again. For a newly pasted token."""
        self._stop_runtime()
        self.reload_config()
        return self.apply()

    def shutdown(self) -> None:
        """Stop the thread before the application exits."""
        self._stop_runtime()

    # -- internals -----------------------------------------------------------

    def _stop_runtime(self) -> None:
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            runtime.stop()

    def _publish(self, state: BotState, detail: str) -> None:
        # Called from the bot thread as well as this one: plain assignments
        # only, and the signal is delivered to the UI thread by Qt.
        self._state = state
        self._detail = detail
        self.state_changed.emit(state.value, detail)
