"""What the bot says, as plain data.

Every message is built here as text plus a list of button rows, with no
Telegram types involved. That keeps the wording testable without a network,
and keeps the bot module down to "send this, edit that".

Formatting is Telegram HTML rather than Markdown: Markdown needs every ``_``
and ``*`` in a definition escaped, and one missed character makes Telegram
reject the whole message. HTML needs three characters escaped and
:func:`html.escape` does exactly that.

Callback data is kept short (Telegram allows 64 bytes) and self-describing:

=====================  ==========================================
``intro:<date>``        confirm the day's new words — only on that date
``start``               begin a review session
``ans:<s>:<w>:<r>``     rating ``r`` for word ``w`` in session ``s``
``end:<s>``             stop the session and keep what was answered
=====================  ==========================================

The date on ``intro`` is what stops an old morning message from confirming
the *next* day's words, which the user has never seen.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

from ..models.srs import Rating
from ..services.learning_service import AnswerOutcome, DailyPlan, StudyItem

#: One row of buttons: ``(label, callback data)`` pairs.
ButtonRow = tuple[tuple[str, str], ...]

#: Telegram's hard limit is 4096 characters; stay well clear of it.
_MAX_LENGTH = 3800
_DEFINITION_LENGTH = 70

_RATING_MARK = {
    Rating.AGAIN: "↺",  # ↺
    Rating.HARD: "◐",  # ◐
    Rating.GOOD: "✓",  # ✓
    Rating.EASY: "★",  # ★
}


@dataclass(frozen=True, slots=True)
class Message:
    """Text in Telegram HTML, and the buttons under it."""

    text: str
    buttons: tuple[ButtonRow, ...] = field(default_factory=tuple)


# -- callback data -------------------------------------------------------------


def intro_data(local_date: str) -> str:
    return f"intro:{local_date}"


def answer_data(session_id: str, word_id: int, rating: Rating) -> str:
    return f"ans:{session_id}:{word_id}:{int(rating)}"


def end_data(session_id: str) -> str:
    return f"end:{session_id}"


START_DATA = "start"


@dataclass(frozen=True, slots=True)
class Callback:
    """A parsed button press."""

    action: str
    local_date: str | None = None
    session_id: str | None = None
    word_id: int | None = None
    rating: Rating | None = None


def parse_callback(data: str | None) -> Callback | None:
    """Read callback data back. Anything malformed is ``None``, never an error."""
    if not data:
        return None
    parts = data.split(":")
    try:
        if parts[0] == "intro" and len(parts) == 2:
            date.fromisoformat(parts[1])
            return Callback("intro", local_date=parts[1])
        if parts == ["start"]:
            return Callback("start")
        if parts[0] == "ans" and len(parts) == 4:
            return Callback(
                "answer",
                session_id=parts[1],
                word_id=int(parts[2]),
                rating=Rating(int(parts[3])),
            )
        if parts[0] == "end" and len(parts) == 2:
            return Callback("end", session_id=parts[1])
    except ValueError:
        return None
    return None


# -- messages ----------------------------------------------------------------


def morning_brief(plan: DailyPlan) -> Message:
    """The 06:00 message: today's words, today's reviews, two buttons.

    The words come with a short definition each, because the point of the
    message is to study from it — on the bus, before the desk.
    """
    lines = [f"<b>Good morning</b> · {escape(_pretty(plan.local_date))}"]
    if not plan.has_plan:
        lines.append("")
        lines.append("There is no study plan yet. Open LexiTrack and choose one.")
        return Message("\n".join(lines))

    buttons: list[ButtonRow] = []
    if plan.new_words:
        levels = Counter(word.cefr_level or "–" for word in plan.new_words)
        spread = ", ".join(f"{level} ×{count}" for level, count in sorted(levels.items()))
        lines += ["", f"<b>{len(plan.new_words)} new words</b>  <i>{escape(spread)}</i>"]
        for word in plan.new_words:
            lines.append(_word_line(word.word, word.definition))
        buttons.append(
            ((f"✅ I studied these {len(plan.new_words)}", intro_data(plan.local_date)),)
        )
    elif plan.introduced_today:
        lines += ["", f"{len(plan.introduced_today)} new words already confirmed today."]
    if plan.intake_paused and plan.intake_note:
        lines += ["", f"<i>{escape(plan.intake_note)}</i>"]

    lines.append("")
    if plan.due_count:
        lines.append(f"<b>{plan.due_count} reviews</b> due today.")
        buttons.append(((f"▶ Start reviews ({plan.due_count})", START_DATA),))
    else:
        lines.append("No reviews due today.")
    return Message(_fit("\n".join(lines)), tuple(buttons))


def introduced(count: int, first_due_on: str | None) -> str:
    """What replaces the confirm button once the words are confirmed."""
    if not count:
        return "Nothing new to confirm — today's words are already in."
    when = _pretty(first_due_on) if first_due_on else "tomorrow"
    return f"✅ {count} new words added. First review: {escape(when)}."


def review_card(
    item: StudyItem,
    session_id: str,
    position: int,
    total: int,
    previous: AnswerOutcome | None = None,
) -> Message:
    """One card, sent as a message of its own.

    The meaning is a spoiler when the setting asks for it hidden: tapping it
    reveals it in place, so hiding costs no extra round-trip to the bot. The
    card must be a new message: an edited one keeps its spoiler revealed.
    """
    word = item.word
    lines: list[str] = []
    if previous is not None and not previous.duplicate:
        lines.append(f"<i>{escape(answer_line(previous))}</i>")
        lines.append("")
    lines.append(f"<b>{escape(word.word)}</b>")
    meta = " · ".join(part for part in (word.part_of_speech, word.cefr_level) if part)
    if meta:
        lines.append(f"<i>{escape(meta)}</i>")
    meaning = word.definition or "No definition stored."
    if word.note:
        meaning = f"{meaning} ({word.note})"
    lines.append("")
    lines.append(
        f"<tg-spoiler>{escape(meaning)}</tg-spoiler>" if item.hide_meaning else escape(meaning)
    )
    if item.is_struggling:
        lines += ["", "⚠ <i>You have been finding this one hard.</i>"]
    lines += ["", f"{position} / {total}"]

    answers = tuple(
        (rating.label, answer_data(session_id, word.id, rating)) for rating in Rating
    )
    return Message(
        "\n".join(lines),
        (answers[:2], answers[2:], (("Stop here", end_data(session_id)),)),
    )


def answer_line(outcome: AnswerOutcome) -> str:
    """One line saying what the last answer did."""
    mark = _RATING_MARK[outcome.rating]
    when = "tomorrow" if outcome.interval_days <= 1 else f"in {outcome.interval_days} days"
    text = f"{mark} {outcome.word.word}: {outcome.rating.label} — back {when}"
    if outcome.marked_known:
        text += " · now known"
    elif outcome.became_struggling:
        text += " · flagged as hard"
    return text


def session_summary(
    answered: int,
    ratings: dict[int, int],
    plan: DailyPlan,
    stopped_early: bool,
) -> str:
    """The end of a session: what was done, and what is left."""
    lines = ["<b>Session finished</b>" if not stopped_early else "<b>Stopped</b>"]
    if answered:
        again = ratings.get(int(Rating.AGAIN), 0)
        rate = round(100 * again / answered) if answered else 0
        lines.append(f"{answered} reviewed · {again} again ({rate}%)")
    else:
        lines.append("Nothing was answered.")
    if plan.due_count:
        lines.append(f"{plan.due_count} still due today.")
    else:
        lines.append("Nothing left for today. \U0001f389")
    tomorrow = dict(plan.forecast).get(_next_day(plan.local_date))
    if tomorrow:
        lines.append(f"Tomorrow: {tomorrow} reviews.")
    return "\n".join(lines)


def evening_reminder(plan: DailyPlan) -> Message | None:
    """21:00, only if something is left. ``None`` means stay quiet."""
    if not plan.has_plan or not plan.has_work:
        return None
    parts = []
    buttons: list[ButtonRow] = []
    if plan.new_words:
        parts.append(f"{len(plan.new_words)} new words to confirm")
        buttons.append(
            ((f"✅ I studied these {len(plan.new_words)}", intro_data(plan.local_date)),)
        )
    if plan.due_count:
        parts.append(f"{plan.due_count} reviews due")
        buttons.append(((f"▶ Start reviews ({plan.due_count})", START_DATA),))
    return Message(
        "<b>Still to do today</b>\n" + " · ".join(parts) + ".", tuple(buttons)
    )


def not_allowed() -> str:
    return "This bot belongs to someone else's LexiTrack."


def welcome(bound: bool) -> str:
    if bound:
        return (
            "<b>LexiTrack is connected.</b>\n"
            "You will get today's words each morning. /today shows them now, "
            "/review starts a session."
        )
    return "LexiTrack is running. /today shows today's words, /review starts a session."


def stale(action: str) -> str:
    return {
        "intro": "That message is from an earlier day. /today shows today's words.",
        "answer": "That card is no longer on screen.",
    }.get(action, "That button has expired.")


# -- helpers -----------------------------------------------------------------


def _word_line(word: str, definition: str | None) -> str:
    text = f"• <b>{escape(word)}</b>"
    if definition:
        short = definition if len(definition) <= _DEFINITION_LENGTH else (
            definition[: _DEFINITION_LENGTH - 1].rstrip() + "…"
        )
        text += f" — {escape(short)}"
    return text


def _fit(text: str) -> str:
    """Trim a message to Telegram's limit on a line boundary."""
    if len(text) <= _MAX_LENGTH:
        return text
    cut = text[:_MAX_LENGTH].rsplit("\n", 1)[0]
    return cut + "\n…"


def _pretty(local_date: str | None) -> str:
    if not local_date:
        return ""
    try:
        day = date.fromisoformat(local_date)
    except ValueError:
        return local_date
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"


def _next_day(local_date: str) -> str:
    return (date.fromisoformat(local_date) + timedelta(days=1)).isoformat()
