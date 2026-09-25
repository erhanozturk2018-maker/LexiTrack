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
3. **A button only does what it says on the day it was sent.** Marking new
   words carries the date; a step carries the session and the step's number.
   A tap on anything older is acknowledged and ignored.

A session is the desktop's :class:`ReviewFlow` — the same questions, the same
rules, recorded as answers from Telegram. Its state is saved after every
step, so a restart of the app loses nothing: the next tap, reply or /review
finds the session, restores it, and shows the step it is on, rather than
acting on a card whose question may no longer be the one asked. Answer times
are not measured here: a phone's delivery and typing delays would read as
effort, so typed answers count as normal effort unless they slip.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from html import escape
from typing import Protocol

from ..core.clock import DayClock
from ..database.connection import Database
from ..models.attempt import SelfReport
from ..models.srs import Channel
from ..repositories import RuntimeRepository
from ..services.learning_service import AnswerOutcome, LearningService
from ..services.progress import ProgressService
from ..services.review_flow import Feedback, ReviewFlow, StepKind
from ..services.review_wording import feedback_text
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
        #: Open sessions' flows, by session id. Rebuilt from the saved state
        #: when missing (after a restart), never trusted across one.
        self._flows: dict[str, ReviewFlow] = {}

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
        elif parsed.action == "step":
            await self._step(chat_id, message_id, parsed)
        elif parsed.action == "known":
            await self._known(chat_id, message_id, parsed)
        elif parsed.action == "end":
            await self._end(chat_id, message_id, parsed.session_id or "", stopped_early=True)
        elif parsed.action == "undo":
            await self._undo(chat_id, message_id, parsed.session_id or "")

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
        buttons = ((((f"▶ Start session ({due})", messages.START_DATA),),) if due else ())
        await self._outbox.edit(chat_id, message_id, Message(f"{text_html}\n\n{note}", buttons))
        if result is not None and result.count:
            self._on_activity()

    # -- the session -----------------------------------------------------------

    def _flow(self, session_id: str) -> tuple[ReviewFlow | None, bool]:
        """The session's flow, and whether it had to be restored. Under the lock."""
        flow = self._flows.get(session_id)
        if flow is not None and flow.active:
            return flow, False
        flow = ReviewFlow.restore(self._engine, session_id)
        if flow is None:
            return None, False
        self._flows[session_id] = flow
        return flow, True

    def _card(
        self,
        flow: ReviewFlow,
        *,
        feedback: str | None = None,
        note: str | None = None,
        known_word: tuple[int, str] | None = None,
    ) -> messages.Message:
        step = flow.current
        assert step is not None and flow.session_id is not None
        position = min(flow.position + 1, flow.total)
        # A step half answered — a right answer, a sentence written — comes
        # back as it stood, even after a restart.
        pending = flow.pending_feedback()
        if pending is not None:
            line, _tone = feedback_text(step, pending)
            return messages.assess_card(
                step, flow.session_id, flow.step_number, line, position, flow.total
            )
        if flow.written is not None:
            return messages.write_check(step, flow.written, flow.session_id, flow.step_number)
        return messages.step_card(
            step,
            flow.session_id,
            flow.step_number,
            min(flow.position + 1, flow.total),
            flow.total,
            feedback=feedback,
            note=note,
            can_undo=flow.can_undo(),
            known_word=known_word,
        )

    async def _show(
        self, chat_id: str, flow: ReviewFlow, card: messages.Message, replaces: str | None
    ) -> None:
        """Send the step's card, remember it, and remove the card it replaces."""
        sent = await self._outbox.send(chat_id, card)
        with self._db.lock:
            step = flow.current
            if flow.session_id is not None:
                self._engine.show_in_session(
                    flow.session_id, step.word.id if step else None, message_id=sent
                )
        if replaces is not None and replaces != sent:
            await self._outbox.delete(chat_id, replaces)

    async def _begin_session(self, chat_id: str) -> None:
        """Resume the open session if there is one; else start the day's."""
        note = None
        empty = "Nothing is due right now, and today's new words are in."
        with self._db.lock:
            self._engine.refresh_settings()
            existing = self._engine.open_session(Channel.TELEGRAM)
            flow = None
            replaces = None
            if existing is not None:
                flow, _restored = self._flow(existing.id)
                if flow is not None and flow.current is not None:
                    note = "Picking up where you left off."
                    replaces = existing.message_id
                else:
                    # Nothing left in it, or saved by an older version.
                    if flow is not None:
                        flow.finish()
                    else:
                        self._engine.finish_session(existing.id)
                    self._flows.pop(existing.id, None)
                    flow = None
            if flow is None:
                flow = ReviewFlow(self._engine, Channel.TELEGRAM, chat_id)
                if not flow.start():
                    flow = None
                else:
                    self._flows[flow.session_id] = flow
            card = self._card(flow, note=note) if flow is not None else None
        if card is None:
            await self._outbox.send(chat_id, Message(empty))
            return
        await self._show(chat_id, flow, card, replaces)

    async def _step(self, chat_id: str, message_id: str, parsed: messages.Callback) -> None:
        assert parsed.session_id is not None and parsed.value is not None
        with self._db.lock:
            self._engine.refresh_settings()
            flow, restored = self._flow(parsed.session_id)
            if flow is None or flow.current is None:
                return
            if restored:
                # The app restarted since that card was sent: show where the
                # session is instead of acting on it.
                card = self._card(flow, note="LexiTrack restarted; here is where you were.")
            elif parsed.step != flow.step_number:
                # An older card, or the same card tapped twice.
                return
            else:
                card = None
                step = flow.current
                result = self._act(flow, parsed.value)
                if result is None:
                    return
        if card is not None:
            await self._show(chat_id, flow, card, message_id)
            return
        await self._after(chat_id, message_id, flow, step, result)

    def _act(self, flow: ReviewFlow, value: str) -> Feedback | bool | None:
        """Do what a button says to the step on screen. None when it does not fit."""
        step = flow.current
        kind = step.kind
        report = SelfReport(value) if value in SelfReport._value2member_map_ else None
        if kind is StepKind.TEACH and value == "go":
            return flow.proceed() or None
        if kind is StepKind.TYPE and value == "dk" and not flow.awaiting:
            return flow.submit("")
        if kind is StepKind.TYPE and flow.awaiting and report is not None and report.success:
            return flow.assess(report)
        if kind is StepKind.CHOOSE and value.isdigit():
            return flow.choose(int(value))
        if kind is StepKind.RECALL and report is not None:
            return flow.assess(report)
        if kind is StepKind.WRITE and report is not None and flow.written is not None:
            return flow.assess(report)
        return None

    async def text(self, chat_id: str, text: str) -> None:
        """A message that is not a command: the answer to a typed or written step."""
        chat_id = str(chat_id)
        if not self._admit(chat_id) or self.owner() is None:
            return
        answer = " ".join(text.split())
        with self._db.lock:
            self._engine.refresh_settings()
            session = self._engine.open_session(Channel.TELEGRAM)
            flow, restored = self._flow(session.id) if session is not None else (None, False)
            step = flow.current if flow is not None else None
            reply = None
            card = None
            result = None
            if step is None:
                reply = messages.no_session()
            elif restored:
                card = self._card(flow, note="LexiTrack restarted; here is where you were.")
            elif step.kind is StepKind.TYPE and flow.awaiting:
                reply = messages.use_the_buttons()
            elif step.kind is StepKind.TYPE:
                # No time is passed: see the module docstring.
                result = flow.submit(answer)
            elif step.kind is StepKind.WRITE and answer and flow.written is None:
                flow.note_written(answer)
                card = messages.write_check(step, answer, flow.session_id, flow.step_number)
            else:
                reply = messages.use_the_buttons()
            replaces = session.message_id if session is not None else None
        if reply is not None:
            await self._outbox.send(chat_id, Message(reply))
        elif card is not None:
            await self._show(chat_id, flow, card, replaces)
        elif result is not None:
            await self._after(chat_id, replaces, flow, step, result)

    async def _after(
        self,
        chat_id: str,
        message_id: str | None,
        flow: ReviewFlow,
        step,
        result: Feedback | bool,
    ) -> None:
        """Move on from a step: the next card, opening with what this one did."""
        feedback = result if isinstance(result, Feedback) else None
        line = None
        known = None
        if feedback is not None and feedback.awaiting:
            # A right answer: how it came is asked before anything is recorded.
            line, _tone = feedback_text(step, feedback)
            with self._db.lock:
                card = messages.assess_card(
                    step, flow.session_id or "", flow.step_number, line,
                    min(flow.position + 1, flow.total), flow.total,
                )
            await self._show(chat_id, flow, card, message_id)
            return
        if feedback is not None:
            line, _tone = feedback_text(step, feedback)
            outcome = feedback.outcome
            if outcome is not None and not outcome.duplicate:
                self._on_activity()
                if outcome.suggest_known:
                    line += f" “{outcome.word.word}” is in long-term memory."
                    known = (outcome.word.id, outcome.word.word)
        with self._db.lock:
            finished = flow.current is None
            card = None if finished else self._card(flow, feedback=line, known_word=known)
        if finished:
            await self._end(
                chat_id, message_id or "", flow.session_id or "", stopped_early=False,
                last_line=line,
            )
            return
        await self._show(chat_id, flow, card, message_id)

    async def _known(self, chat_id: str, message_id: str, parsed: messages.Callback) -> None:
        """The learner said yes to Known for a word the answer offered."""
        assert parsed.session_id is not None and parsed.word_id is not None
        with self._db.lock:
            marked = self._engine.confirm_known([parsed.word_id])
            flow, restored = self._flow(parsed.session_id)
            card = None
            if flow is not None and flow.current is not None and not restored:
                word = self._engine.study_item(parsed.word_id)
                name = word.word.word if word is not None else "It"
                card = self._card(
                    flow, note=f"“{name}” is marked Known." if marked else None
                )
        if marked:
            self._on_activity()
        if card is not None:
            # Same step, same number: the card is edited, not replaced.
            await self._outbox.edit(chat_id, message_id, card)

    async def _undo(self, chat_id: str, message_id: str, session_id: str) -> None:
        """Take back the last answer and ask that word again.

        The card comes back as a new message, like every card. A second tap
        finds nothing to take back and does nothing: Undo reaches one answer,
        never a chain of them.
        """
        with self._db.lock:
            self._engine.refresh_settings()
            flow, restored = self._flow(session_id)
            if flow is None or restored:
                return
            word = flow.undo()
            if word is None or flow.current is None:
                return
            card = self._card(flow, note=f"Took back your answer to {word.word}.")
        self._on_activity()
        await self._show(chat_id, flow, card, message_id)

    async def _end(
        self,
        chat_id: str,
        message_id: str,
        session_id: str,
        stopped_early: bool,
        last: AnswerOutcome | None = None,
        last_line: str | None = None,
    ) -> None:
        with self._db.lock:
            session = self._engine.session(session_id)
            if session is None:
                return
            flow = self._flows.pop(session_id, None)
            learned = flow.learned if flow is not None else 0
            if flow is not None and flow.active:
                flow.finish()
            elif session.is_open:
                self._engine.finish_session(session_id)
            ratings = self._session_ratings(session_id)
            # Counted from the record, so answers taken back are left out.
            answered = sum(ratings.values())
            plan = self._engine.daily_plan()
        text = messages.session_summary(answered, ratings, plan, stopped_early, learned)
        if last_line:
            text = f"<i>{escape(last_line)}</i>\n\n{text}"
        elif last is not None and not last.duplicate:
            text = f"<i>{escape(messages.answer_line(last))}</i>\n\n{text}"
        if message_id:
            await self._outbox.edit(chat_id, message_id, Message(text))
        else:
            await self._outbox.send(chat_id, Message(text))

    def _session_ratings(self, session_id: str) -> dict[int, int]:
        rows = self._db.connection.execute(
            "SELECT rating, COUNT(*) AS n FROM review_logs "
            "WHERE session_id = ? AND undone_at IS NULL GROUP BY rating",
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
            if notification is Notification.MORNING:
                message = messages.morning_brief(plan)
            elif notification is Notification.EVENING:
                message = messages.evening_reminder(plan)
            else:
                with self._db.lock:
                    week = ProgressService(self._db, self._engine).week()
                message = messages.weekly_summary(week)
            if message is not None:
                await self._outbox.send(chat_id, message)
            # Marked even when the reminder stayed quiet: "nothing to remind"
            # is an answer for the day, not a reason to ask again in 30 s.
            with self._db.lock:
                mark_sent(notification, self._engine.clock, settings, self._runtime)
            sent.append(notification)
        return sent
