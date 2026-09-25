"""A study session as a state machine, with no widgets in it.

The Today page used to hold the session itself: the queue, which card was
showing, whether its meaning was revealed, how many were answered, what Undo
would take back. That made the page the only place a session could run, and
a session impossible to save and pick up again.

``StudyFlow`` holds all of it. The page asks it what to show and tells it what
the user did; the Telegram bot can drive the same object, and its state is a
small versioned JSON document, stored on the session row after every step
(``review_sessions.flow_state``), so a session can be restored.

This is route V1: the flow the desktop has always had — every due card once,
word shown, meaning revealed, four answers — moved here unchanged. Version 2
(probes, relearning, tasks above level 1) is built on this object, not beside
it.

Nothing in the flow decides anything about memory: every answer goes through
:meth:`LearningService.answer`, and Undo through
:meth:`LearningService.undo_last_answer`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..models.attempt import ROUTE_V1
from ..models.srs import Rating
from ..repositories.word_repository import StoredWord
from .learning_service import AnswerOutcome, LearningService, StudyItem

log = logging.getLogger(__name__)

#: Version of the JSON written to ``review_sessions.flow_state``.
FLOW_VERSION = 1


@dataclass(frozen=True, slots=True)
class FlowAnswer:
    """What one answer did to the flow."""

    outcome: AnswerOutcome | None
    #: True when that was the last card: the caller finishes the session.
    finished: bool


@dataclass(frozen=True, slots=True)
class FlowSummary:
    """A finished session, for the closing message."""

    session_id: str
    answered: int
    #: True when the last answer can still be taken back.
    can_undo: bool


class StudyFlow:
    """One review session: its queue, its position and what Undo would reverse."""

    def __init__(self, engine: LearningService) -> None:
        self._engine = engine
        self._queue: list[StudyItem] = []
        self._index = 0
        self._session_id: str | None = None
        #: The session that just ended, so its last answer can still be undone.
        self._last_session_id: str | None = None
        self._revealed = False
        self._answered = 0
        #: The word and rating of the last answer, for the Undo label.
        self._last_answer: tuple[str, Rating] | None = None

    # -- reading -----------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._session_id is not None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def current(self) -> StudyItem | None:
        if self.active and 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    @property
    def position(self) -> int:
        """The card on screen, counted from 0."""
        return self._index

    @property
    def total(self) -> int:
        return len(self._queue)

    @property
    def revealed(self) -> bool:
        return self._revealed

    @property
    def answered(self) -> int:
        return self._answered

    @property
    def last_answer(self) -> tuple[str, Rating] | None:
        return self._last_answer

    def can_undo(self) -> bool:
        """True when the card's footer should offer to take back the last answer."""
        return (
            self._last_answer is not None
            and self._session_id is not None
            and self._engine.can_undo(self._session_id)
        )

    def intervals(self) -> dict[Rating, int]:
        """When the card on screen would come back, for each answer."""
        item = self.current
        return self._engine.preview_intervals(item.word.id) if item is not None else {}

    # -- acting ------------------------------------------------------------

    def start(self) -> bool:
        """Open a session on today's due cards. False when there are none."""
        queue = self._engine.review_queue()
        if not queue:
            return False
        self._queue = queue
        self._session_id = self._engine.start_session().id
        self._last_session_id = self._session_id
        self._last_answer = None
        self._index = 0
        self._answered = 0
        self._show_current()
        self._save()
        return True

    def reveal(self) -> bool:
        """Show the meaning of the card on screen. False when it already shows."""
        if self.current is None or self._revealed:
            return False
        self._revealed = True
        self._save()
        return True

    def answer(self, rating: Rating) -> FlowAnswer:
        item = self.current
        if item is None:
            return FlowAnswer(None, finished=not self.active)
        outcome = self._engine.answer(item.word.id, rating, session_id=self._session_id)
        if outcome is not None and not outcome.duplicate:
            self._answered += 1
            self._last_answer = (item.word.word, rating)
        self._index += 1
        finished = self._index >= len(self._queue)
        if not finished:
            self._show_current()
        self._save()
        return FlowAnswer(outcome, finished)

    def undo(self) -> StoredWord | None:
        """Take back the last answer; in a session, its card is shown again.

        Also works just after the session ended, from the closing message.
        """
        session_id = self._session_id or self._last_session_id
        if session_id is None:
            return None
        word = self._engine.undo_last_answer(session_id)
        if word is None:
            return None
        self._last_answer = None
        if self.active:
            self._index = max(self._index - 1, 0)
            self._answered = max(self._answered - 1, 0)
            item = self._engine.study_item(word.id)
            if item is not None and self._index < len(self._queue):
                self._queue[self._index] = item
            self._show_current()
            self._save()
        return word

    def finish(self) -> FlowSummary | None:
        """Close the session. None when none was open."""
        if self._session_id is None:
            return None
        session_id = self._session_id
        self._engine.finish_session(session_id)
        self._engine.save_flow_state(session_id, None)
        summary = FlowSummary(session_id, self._answered, self._engine.can_undo(session_id))
        self._session_id = None
        self._queue = []
        return summary

    def _show_current(self) -> None:
        item = self.current
        self._revealed = item is not None and not item.hide_meaning

    # -- saving and restoring ------------------------------------------------

    def state(self) -> dict:
        """Where the flow stands, as plain data."""
        return {
            "version": FLOW_VERSION,
            "route": ROUTE_V1,
            "kind": "review",
            "queue": [item.word.id for item in self._queue],
            "index": self._index,
            "revealed": self._revealed,
            "answered": self._answered,
            "last_answer": (
                {"word": self._last_answer[0], "rating": int(self._last_answer[1])}
                if self._last_answer
                else None
            ),
        }

    def _save(self) -> None:
        if self._session_id is not None:
            self._engine.save_flow_state(self._session_id, json.dumps(self.state()))

    @classmethod
    def restore(cls, engine: LearningService, session_id: str) -> StudyFlow | None:
        """The flow of an open session, as it was last saved.

        None when the session is closed, has no saved state, or the state was
        written by a newer version. Cards answered elsewhere since are still
        in the queue; answering one again is recognised as a duplicate by the
        engine, as a repeated Telegram tap is.
        """
        session = engine.session(session_id)
        if session is None or not session.is_open or not session.flow_state:
            return None
        try:
            data = json.loads(session.flow_state)
        except ValueError:
            log.warning("Session %s has unreadable flow state", session_id)
            return None
        if not isinstance(data, dict) or int(data.get("version", 0)) != FLOW_VERSION:
            return None
        flow = cls(engine)
        queue = [engine.study_item(int(word_id)) for word_id in data.get("queue", [])]
        flow._queue = [item for item in queue if item is not None]
        flow._index = min(int(data.get("index", 0)), len(flow._queue))
        flow._session_id = session_id
        flow._last_session_id = session_id
        flow._answered = int(data.get("answered", 0))
        flow._revealed = bool(data.get("revealed", False))
        last = data.get("last_answer")
        if isinstance(last, dict) and last.get("word"):
            flow._last_answer = (str(last["word"]), Rating(int(last["rating"])))
        return flow
