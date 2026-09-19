"""The bot's behaviour, separate from the network.

:class:`BotCore` decides what to do with a command, a button press or a clock
tick, and says it through an :class:`Outbox`. It never imports
``python-telegram-bot``: the real outbox wraps the library, and the tests use
one that records messages in a list. Everything that can go wrong with the
*behaviour* — the wrong card rated, yesterday's words confirmed, a stranger
answering — is therefore testable without a token or a network.

Three rules the handlers below all follow:

1. **Only the owner's chat is served.** The first chat to say ``/start`` is
   remembered (unless ``.env`` names one), and every other chat gets a single
   refusal and nothing else.
2. **The engine is called under the database lock.** A button press reads the
   queue, answers a card and reads the queue again; holding the lock across
   the three means the desktop cannot slip an answer in between and make the
   bot show a card that was just reviewed.
3. **A button only does what it says on the day it was sent.** Confirming new
   words carries the date; answering carries the session and the word. A tap
   on anything older is acknowledged and ignored.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from html import escape
from typing import Protocol

from ..core.clock import DayClock
from ..database.connection import Database
from ..models.srs import Channel
from ..repositories import RuntimeRepository
from ..services.learning_service import AnswerOutcome, LearningService
from . import messages
from .messages import Message
from .schedule import Notification, due_notifications, mark_sent

log = logging.getLogger(__name__)


class Outbox(Protocol):
    """How the core talks. Implemented over the Bot API, and by the tests."""

    async def send(self, chat_id: str, message: Message) -> str:
        """Send a message and return its id."""

    async def edit(self, chat_id: str, message_id: str, message: Message) -> None:
        """Replace a message's text and buttons."""

    async def delete(self, chat_id: str, message_id: str) -> None:
        """Remove a message. Best effort: a message that cannot go is left."""


class BotCore:
    """Commands, button presses and the clock, turned into messages."""

    def __init__(
        self,
        database: Database,
        outbox: Outbox,
        clock: DayClock | None = None,
        allowed_chat_id: str | None = None,
        on_activity: Callable[[], None] | None = None,
    ) -> None:
        self._db = database
        self._outbox = outbox
        # Its own service: settings and clock objects are per client, the
        # database underneath is shared.
        self._engine = LearningService(database, clock)
        self._runtime = RuntimeRepository(database)
        self._configured_chat = allowed_chat_id
        self._on_activity = on_activity or (lambda: None)

    @property
    def engine(self) -> LearningService:
        return self._engine

    # -- who may talk to the bot -------------------------------------------

    def owner(self) -> str | None:
        return self._configured_chat or self._runtime.chat_id()

    def _admit(self, chat_id: str) -> bool:
        owner = self.owner()
        return owner is None or owner == str(chat_id)

    # -- commands ------------------------------------------------------------

    async def command(self, chat_id: str, name: str) -> None:
        chat_id = str(chat_id)
        name = name.lstrip("/").split("@", 1)[0].lower()
        if name == "start":
            await self._start(chat_id)
            return
        if not self._admit(chat_id):
            await self._outbox.send(chat_id, Message(messages.not_allowed()))
            return
        if self.owner() is None:
            await self._start(chat_id)
            return
        if name in ("today", "brief"):
            await self._outbox.send(chat_id, self._brief())
        elif name == "review":
            await self._begin_session(chat_id)
        else:
            await self._outbox.send(chat_id, Message(messages.welcome(bound=False)))

    async def _start(self, chat_id: str) -> None:
        owner = self.owner()
        if owner is not None and owner != chat_id:
            await self._outbox.send(chat_id, Message(messages.not_allowed()))
            return
        bound = owner is None
        if bound:
            self._runtime.set_chat_id(chat_id)
            log.info("Telegram bot bound to chat %s", chat_id)
        await self._outbox.send(chat_id, Message(messages.welcome(bound=bound)))
        if bound:
            await self._outbox.send(chat_id, self._brief())
            # That was today's brief. Without this, connecting after 06:00
            # would be followed half a minute later by the same list again.
            with self._db.lock:
                settings = self._engine.refresh_settings()
                if self._engine.clock.now_local().hour >= settings.notify_hour:
                    mark_sent(Notification.MORNING, self._engine.clock, settings, self._runtime)

    def _brief(self) -> Message:
        with self._db.lock:
            self._engine.refresh_settings()
            return messages.morning_brief(self._engine.daily_plan())

    # -- buttons -------------------------------------------------------------

    async def callback(self, chat_id: str, message_id: str, data: str, text_html: str) -> None:
        """A button was pressed. The caller has already acknowledged it."""
        chat_id, message_id = str(chat_id), str(message_id)
        if not self._admit(chat_id) or self.owner() is None:
            return
        parsed = messages.parse_callback(data)
        if parsed is None:
            return
        if parsed.action == "intro":
            await self._introduce(chat_id, message_id, parsed.local_date or "", text_html)
        elif parsed.action == "start":
            await self._begin_session(chat_id)
        elif parsed.action == "answer":
            await self._answer(chat_id, message_id, parsed)
        elif parsed.action == "end":
            await self._end(chat_id, message_id, parsed.session_id or "", stopped_early=True)

    async def _introduce(
        self, chat_id: str, message_id: str, local_date: str, text_html: str
    ) -> None:
        with self._db.lock:
            self._engine.refresh_settings()
            if local_date != self._engine.clock.today():
                note, result, due = messages.stale("intro"), None, 0
            else:
                result = self._engine.introduce()
                note = messages.introduced(result.count, result.first_due_on)
                due = self._engine.daily_plan().due_count
        buttons = ((((f"▶ Start reviews ({due})", messages.START_DATA),),) if due else ())
        await self._outbox.edit(chat_id, message_id, Message(f"{text_html}\n\n{note}", buttons))
        if result is not None and result.count:
            self._on_activity()

    async def _begin_session(self, chat_id: str) -> None:
        with self._db.lock:
            self._engine.refresh_settings()
            existing = self._engine.open_session(Channel.TELEGRAM)
            if existing is not None:
                self._engine.finish_session(existing.id)
            queue = self._engine.review_queue()
            if not queue:
                plan = self._engine.daily_plan()
            else:
                session = self._engine.start_session(Channel.TELEGRAM, chat_id=chat_id)
                first = queue[0]
                card = messages.review_card(first, session.id, 1, session.planned_count)
        if not queue:
            text = (
                "Nothing is due right now."
                if not plan.new_words
                else "Nothing is due yet — today's new words are reviewed from tomorrow."
            )
            await self._outbox.send(chat_id, Message(text))
            return
        sent = await self._outbox.send(chat_id, card)
        with self._db.lock:
            self._engine.show_in_session(session.id, first.word.id, message_id=sent)

    async def _answer(self, chat_id: str, message_id: str, parsed: messages.Callback) -> None:
        assert parsed.session_id and parsed.word_id and parsed.rating
        with self._db.lock:
            self._engine.refresh_settings()
            session = self._engine.session(parsed.session_id)
            if (
                session is None
                or not session.is_open
                or session.current_word_id != parsed.word_id
            ):
                # A tap on a card that is no longer the one on screen: the
                # same card tapped twice, or an old message. Nothing to do.
                return
            outcome: AnswerOutcome | None = self._engine.answer(
                parsed.word_id,
                parsed.rating,
                session_id=session.id,
                channel=Channel.TELEGRAM,
                update_key=f"ans:{session.id}:{parsed.word_id}",
            )
            queue = self._engine.review_queue()
            session = self._engine.session(session.id)
            assert session is not None
            if queue:
                nxt = queue[0]
                self._engine.show_in_session(session.id, nxt.word.id)
                card = messages.review_card(
                    nxt,
                    session.id,
                    session.done_count + 1,
                    max(session.planned_count, session.done_count + len(queue)),
                    previous=outcome,
                )
        self._on_activity()
        if queue:
            # Each card is a new message rather than an edit of the last one:
            # Telegram keeps a spoiler open once tapped, through every later
            # edit of the same message, so an edited card would show the next
            # meaning uncovered. The answered card is removed to keep the chat
            # to one card; the new one opens with that card's result.
            sent = await self._outbox.send(chat_id, card)
            with self._db.lock:
                self._engine.show_in_session(session.id, nxt.word.id, message_id=sent)
            await self._outbox.delete(chat_id, message_id)
        else:
            await self._end(
                chat_id, message_id, session.id, stopped_early=False, last=outcome
            )

    async def _end(
        self,
        chat_id: str,
        message_id: str,
        session_id: str,
        stopped_early: bool,
        last: AnswerOutcome | None = None,
    ) -> None:
        with self._db.lock:
            session = self._engine.session(session_id)
            if session is None:
                return
            if session.is_open:
                session = self._engine.finish_session(session_id)
            ratings = self._session_ratings(session_id)
            plan = self._engine.daily_plan()
        text = messages.session_summary(session.done_count, ratings, plan, stopped_early)
        if last is not None and not last.duplicate:
            text = f"<i>{escape(messages.answer_line(last))}</i>\n\n{text}"
        await self._outbox.edit(chat_id, message_id, Message(text))

    def _session_ratings(self, session_id: str) -> dict[int, int]:
        rows = self._db.connection.execute(
            "SELECT rating, COUNT(*) AS n FROM review_logs WHERE session_id = ? GROUP BY rating",
            (session_id,),
        ).fetchall()
        return {int(row["rating"]): int(row["n"]) for row in rows}

    # -- the clock -----------------------------------------------------------

    async def tick(self) -> list[Notification]:
        """Send whatever is owed now. Called every half minute by the bot."""
        chat_id = self.owner()
        if chat_id is None:
            return []
        with self._db.lock:
            settings = self._engine.refresh_settings()
            self._runtime.touch(self._engine.clock.now_utc())
            due = due_notifications(self._engine.clock, settings, self._runtime)
            plan = self._engine.daily_plan() if due else None
        sent: list[Notification] = []
        for notification in due:
            assert plan is not None
            message = (
                messages.morning_brief(plan)
                if notification is Notification.MORNING
                else messages.evening_reminder(plan)
            )
            if message is not None:
                await self._outbox.send(chat_id, message)
            # Marked even when the reminder stayed quiet: "nothing to remind"
            # is an answer for the day, not a reason to ask again in 30 s.
            with self._db.lock:
                mark_sent(notification, self._engine.clock, settings, self._runtime)
            sent.append(notification)
        return sent
