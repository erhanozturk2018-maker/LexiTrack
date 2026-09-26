"""Changing words' status from a table, with Undo.

K / U / R, the selection bar and the details panel's buttons change a
status at once — they are the quick way through a list — and the result
comes back in a toast with Undo, so a stray key costs nothing. A change to
many words at once is asked about first: Ctrl+A then K is one keystroke
away from marking a whole list Known.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtWidgets import QMessageBox, QWidget

from ..core.errors import LexiTrackError
from ..models.user_word_state import ReviewStatus
from ..services.vocabulary_service import StatusChange, VocabularyService
from .components.status import STATUS_NAMES
from .components.toast import Toast
from .dialogs import confirm

#: From this many words on, a status change is asked about before it is made.
LARGE_CHANGE = 50


def change_status(
    parent: QWidget,
    service: VocabularyService,
    toast: Toast,
    word_ids: Sequence[int],
    status: ReviewStatus,
    redraw: Callable[[], None],
) -> StatusChange | None:
    """Change the status of ``word_ids``, then offer Undo in ``toast``.

    ``redraw`` brings the page up to date, after the change and after Undo.
    Returns the change, or None when it was not made.
    """
    status = ReviewStatus(status)
    ids = list(dict.fromkeys(int(word_id) for word_id in word_ids))
    if not ids:
        return None
    name = STATUS_NAMES[status]
    if len(ids) >= LARGE_CHANGE and not confirm(
        parent,
        "Change status",
        f"Mark {len(ids):,} words {name}?\n\nYou can take it back with Undo right after.",
        f"Mark {len(ids):,} words {name}",
        destructive=False,
    ):
        return None
    try:
        change = service.change_status(ids, status)
    except LexiTrackError as exc:
        QMessageBox.warning(parent, "Could not change status", exc.user_message)
        return None
    redraw()
    if change.changed:
        toast.show_message(
            _describe(service, change), lambda: _undo(parent, service, toast, change, redraw)
        )
    else:
        # Nothing changed is still an answer: the key was not ignored.
        toast.show_message(
            f"Already {name}." if len(ids) == 1 else f"All {len(ids):,} are already {name}."
        )
    return change


def _describe(service: VocabularyService, change: StatusChange) -> str:
    name = STATUS_NAMES[change.status]
    if change.changed == 1:
        (word_id,) = change.before
        word = service.get_word(word_id)
        if word is not None:
            return f"“{word.word}” marked {name}."
    return f"{change.changed:,} words marked {name}."


def _undo(
    parent: QWidget,
    service: VocabularyService,
    toast: Toast,
    change: StatusChange,
    redraw: Callable[[], None],
) -> None:
    try:
        restored = service.undo_status_change(change)
    except LexiTrackError as exc:
        QMessageBox.warning(parent, "Could not undo", exc.user_message)
        return
    redraw()
    noun = "word" if restored == 1 else "words"
    toast.show_message(f"Took back the change: {restored:,} {noun} as before.")
