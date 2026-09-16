"""Flashcard review with multi-step backward navigation.

Two things are kept strictly apart here:

**Learning status** — Not Reviewed, Known, Unknown — lives in the database and
changes only when the user explicitly answers or resets a word.

**Navigation history** — which words the user has answered in this session,
and where they are looking now — lives only in this object.

So Backspace *moves*; it never changes a status::

    A → Known, B → Unknown, C → Known
    Backspace   now showing C, still Known
    Backspace   now showing B, still Unknown
    U           B stays Unknown (explicit answer), move forward to C
    K           C stays Known, move forward to the next unreviewed word

Answering a word you navigated back to *is* an explicit change, so it is
saved; resetting it (R) returns it to Not Reviewed on purpose.

History is per session and in memory. Reopening the app starts a fresh session
at the first unreviewed word, which is where the user expects to resume;
persisting a navigation stack across restarts would add a table for no real
gain (see DECISIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.user_word_state import Progress, ReviewStatus
from ..repositories.state_repository import StateRepository
from ..repositories.word_repository import StoredWord, WordRepository


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """What the flashcard screen should show right now."""

    word: StoredWord
    #: ``0`` at the live position (the next unreviewed word). ``n`` when the
    #: user has stepped ``n`` words back through their history.
    steps_back: int
    progress: Progress

    @property
    def is_history(self) -> bool:
        return self.steps_back > 0

    @property
    def position(self) -> int:
        """1-based position of a live word within the list."""
        return min(self.progress.reviewed + 1, self.progress.total)


class ReviewSession:
    """A flashcard session over one list (or, with ``list_id=None``, everything)."""

    def __init__(
        self,
        words: WordRepository,
        state: StateRepository,
        list_id: int | None = None,
    ) -> None:
        self._words = words
        self._state = state
        self.list_id = list_id
        #: Word ids answered this session, oldest first.
        self._history: list[int] = []
        #: Index into history of the word on screen; ``len(history)`` means live.
        self._cursor = 0
        #: The most recent answer, for Enter-repeats-last-answer.
        self.last_answer: bool | None = None

    # -- state -------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        return self._cursor >= len(self._history)

    @property
    def can_go_back(self) -> bool:
        return self._cursor > 0

    @property
    def can_go_forward(self) -> bool:
        return not self.is_live

    @property
    def history_length(self) -> int:
        return len(self._history)

    def current(self) -> ReviewItem | None:
        """The word to show, or ``None`` when the list has nothing left to review."""
        while not self.is_live:
            word = self._words.get(self._history[self._cursor])
            if word is not None:
                return self._item(word)
            # The word was deleted (its list removed) since it was answered.
            del self._history[self._cursor]

        word = self._words.next_unreviewed(self.list_id)
        return self._item(word) if word is not None else None

    # -- navigation (never changes status) --------------------------------

    def back(self) -> ReviewItem | None:
        """Step back one answered word. A no-op at the start of the session."""
        if self.can_go_back:
            self._cursor -= 1
        return self.current()

    def forward(self) -> ReviewItem | None:
        """Step forward through history, keeping every status as it is."""
        if self.can_go_forward:
            self._cursor += 1
        return self.current()

    # -- explicit status changes ------------------------------------------

    def answer(self, known: bool) -> ReviewItem | None:
        """Record Known/Unknown for the word on screen, then move forward."""
        item = self.current()
        if item is None:
            return None
        status = ReviewStatus.KNOWN if known else ReviewStatus.UNKNOWN
        self._state.set_status(item.word.id, status)
        self.last_answer = known

        if self.is_live:
            # A word can come round again live if it was reset; keep one entry
            # per word so Backspace never visits it twice.
            if item.word.id in self._history:
                self._history.remove(item.word.id)
            self._history.append(item.word.id)
            self._cursor = len(self._history)
        else:
            self._cursor += 1
        return self.current()

    def repeat_last_answer(self) -> ReviewItem | None:
        """Enter: repeat the last answer on a live word; keep and move on in history."""
        if not self.is_live:
            return self.forward()
        if self.last_answer is None:
            return self.current()
        return self.answer(self.last_answer)

    def reset_current(self) -> ReviewItem | None:
        """Return the word on screen to Not Reviewed, and stay on it."""
        item = self.current()
        if item is None:
            return None
        self._state.set_status(item.word.id, ReviewStatus.NOT_REVIEWED)
        return self.current()

    # -- helpers -----------------------------------------------------------

    def _item(self, word: StoredWord) -> ReviewItem:
        return ReviewItem(
            word=word,
            steps_back=len(self._history) - self._cursor,
            progress=self._state.progress(self.list_id),
        )
