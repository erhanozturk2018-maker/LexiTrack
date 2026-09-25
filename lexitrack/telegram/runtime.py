"""The Telegram thread: long polling, the half-minute tick, a clean stop.

This is the only module that imports ``python-telegram-bot``. It owns an
asyncio loop on a background thread, because the library is asynchronous and
Qt's event loop is not; the two meet only at :meth:`TelegramRuntime.stop` and
at the ``on_activity`` / ``on_state`` callbacks, which the Qt side turns into
queued signals.

Why long polling and not a webhook: a webhook needs a public HTTPS address,
and this is a desktop app on a home connection. Polling needs nothing but an
outgoing connection, and Telegram keeps undelivered updates for a day, so a
laptop that was asleep catches up when it wakes.

The acknowledgement order matters. Telegram expects ``answerCallbackQuery``
within a few seconds of a tap; until it arrives the button spins on the phone,
and if it never arrives the update is delivered again. So every button press
is acknowledged *first*, and only then handed to :class:`BotCore` — which,
combined with the idempotency key on every answer, turns a re-delivered tap
into a no-op instead of a second review.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from enum import StrEnum

from ..core.clock import DayClock
from ..database.connection import Database
from .config import TelegramConfig
from .core import BotCore
from .messages import Message

log = logging.getLogger(__name__)

#: How often the bot asks whether a notification is owed.
TICK_SECONDS = 30


class BotState(StrEnum):
    """What the tray and Settings show about the bot."""

    OFF = "off"
    NO_TOKEN = "no_token"
    CONNECTING = "connecting"
    RUNNING = "running"
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            "off": "Off",
            "no_token": "No token in .env",
            "connecting": "Connecting…",
            "running": "Running",
            "error": "Not connected",
        }[self.value]


class _BotOutbox:
    """The :class:`Outbox` over the real Bot API."""

    def __init__(self, bot) -> None:
        self._bot = bot

    @staticmethod
    def _markup(message: Message):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if not message.buttons:
            return None
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(label, callback_data=data) for label, data in row]
                for row in message.buttons
            ]
        )

    async def send(self, chat_id: str, message: Message) -> str:
        sent = await self._bot.send_message(
            chat_id=chat_id,
            text=message.text,
            parse_mode="HTML",
            reply_markup=self._markup(message),
            disable_web_page_preview=True,
        )
        return str(sent.message_id)

    async def edit(self, chat_id: str, message_id: str, message: Message) -> None:
        from telegram.error import BadRequest

        try:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=message.text,
                parse_mode="HTML",
                reply_markup=self._markup(message),
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            # Editing a message into exactly what it already says is an error
            # in the Bot API; it happens on a repeated tap and means nothing.
            if "not modified" not in str(exc).lower():
                raise

    async def delete(self, chat_id: str, message_id: str) -> None:
        from telegram.error import TelegramError

        try:
            await self._bot.delete_message(chat_id=chat_id, message_id=int(message_id))
        except TelegramError as exc:
            # Already gone, or older than the Bot API lets a bot delete. The
            # card stays; its buttons are stripped so it cannot be mistaken
            # for the live one.
            log.info("Could not delete Telegram message: %s", exc)
            try:
                await self._bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=int(message_id), reply_markup=None
                )
            except TelegramError:
                pass


class TelegramRuntime:
    """Runs the bot on a background thread until told to stop."""

    def __init__(
        self,
        database: Database,
        config: TelegramConfig,
        clock: DayClock | None = None,
        on_activity: Callable[[], None] | None = None,
        on_state: Callable[[BotState, str], None] | None = None,
    ) -> None:
        self._db = database
        self._config = config
        self._clock = clock
        self._on_activity = on_activity or (lambda: None)
        self._on_state = on_state or (lambda _state, _detail: None)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self.state = BotState.OFF if config.has_token else BotState.NO_TOKEN

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start polling. Returns ``False`` when there is no token."""
        if self.running:
            return True
        if not self._config.has_token:
            self._set_state(BotState.NO_TOKEN, "")
            return False
        self._thread = threading.Thread(target=self._run, name="telegram", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the thread to finish and wait for it.

        Polling is stopped cleanly so Telegram records the last update as
        consumed; killing the thread instead would make the next start
        receive the same updates again.
        """
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and loop.is_running():
            loop.call_soon_threadsafe(stop.set)
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._set_state(BotState.OFF, "")

    # -- the thread ----------------------------------------------------------

    def _set_state(self, state: BotState, detail: str) -> None:
        self.state = state
        try:
            self._on_state(state, detail)
        except Exception:  # pragma: no cover - a UI callback must not kill the bot
            log.exception("Bot state callback failed")

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:
            log.exception("The Telegram bot stopped with an error")
            self._set_state(BotState.ERROR, str(exc))
        finally:
            self._loop.close()
            self._loop = None

    async def _main(self) -> None:
        from telegram import Update
        from telegram.error import InvalidToken, TelegramError
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

        self._stop = asyncio.Event()
        self._set_state(BotState.CONNECTING, "")
        application = Application.builder().token(self._config.token or "").build()
        core = BotCore(
            self._db,
            _BotOutbox(application.bot),
            clock=self._clock,
            allowed_chat_id=self._config.allowed_chat_id,
            on_activity=self._on_activity,
        )

        async def on_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_chat is None or update.effective_message is None:
                return
            text = update.effective_message.text or ""
            await core.command(str(update.effective_chat.id), text.split()[0])

        async def on_button(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if query is None:
                return
            # Acknowledge first: see the module docstring.
            try:
                await query.answer()
            except TelegramError:
                log.warning("Could not acknowledge a button press", exc_info=True)
            message = query.message
            if message is None or update.effective_chat is None:
                return
            await core.callback(
                str(update.effective_chat.id),
                str(message.message_id),
                query.data or "",
                getattr(message, "text_html", "") or "",
            )

        async def on_text(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
            # A reply to a typed or written step (see core.BotCore.text).
            if update.effective_chat is None or update.effective_message is None:
                return
            await core.text(str(update.effective_chat.id), update.effective_message.text or "")

        async def on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            log.warning("Telegram handler error: %s", context.error, exc_info=context.error)

        for name in ("start", "today", "brief", "review", "help"):
            application.add_handler(CommandHandler(name, on_command))
        application.add_handler(CallbackQueryHandler(on_button))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
        application.add_error_handler(on_error)

        try:
            await application.initialize()
        except InvalidToken:
            self._set_state(BotState.ERROR, "Telegram rejected the token in .env.")
            return
        except TelegramError as exc:
            self._set_state(BotState.ERROR, f"Could not reach Telegram: {exc}")
            return

        await application.start()
        assert application.updater is not None
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        me = application.bot.username
        self._set_state(BotState.RUNNING, f"@{me}" if me else "")
        log.info("Telegram bot @%s is polling", me)
        try:
            while not self._stop.is_set():
                try:
                    await core.tick()
                except TelegramError:
                    # A dropped connection during a send: the notification is
                    # not marked as sent, so the next tick tries again.
                    log.warning("Telegram tick failed; will retry", exc_info=True)
                except Exception:
                    log.exception("Telegram tick failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
                except TimeoutError:
                    pass
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            log.info("Telegram bot stopped")
