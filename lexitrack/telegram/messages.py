"""What the bot says, as plain data.

Every message is built here as text plus a list of button rows, with no
Telegram types involved. That keeps the wording testable without a network,
and keeps the bot module down to "send this, edit that".

Formatting is Telegram HTML rather than Markdown: Markdown needs every ``_``
and ``*`` in a definition escaped, and one missed character makes Telegram
reject the whole message. HTML needs three characters escaped and
:func:`html.escape` does exactly that.

Callback data is kept short (Telegram allows 64 bytes) and self-describing:

======================  ==========================================
``start``               begin (or resume) the reviews due
``learn``               begin (or resume) today's new words
``later``               not now: the new words wait (after the reviews)
``intro:<date>``        retired (Mark as studied); an old button only says so
``st:<s>:<n>:<v>``      step ``n`` of session ``s``: ``v`` is an option (0–3),
                        a rating after a right answer (``again``, ``hard``,
                        ``good``, ``easy``) or ``go`` (continue)
``known:<s>:<w>``       mark word ``w`` Known, as offered after its answer
``end:<s>``             stop the session and keep what was answered
``undo:<s>``            take back the last answer in session ``s``
======================  ==========================================

The step number on ``st`` is what makes a tap on an older card do nothing.

The day has two sessions, as on the desktop: the reviews (/review) and the
new words (/learn), each with its own button, reviews first. When the
reviews are done and new words are waiting, the bot asks: now, or later.

A session is the desktop's (services/review_flow.py), step by step, all
buttons: a new word is shown, then continued; a question has four options —
words as buttons, definitions listed A to D with a button each; a right
answer to the day's question is followed by Again / Hard / Good / Easy. The
wording after an answer is shared with the desktop
(services/review_wording.py).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

from ..models.attempt import Task
from ..models.context import find_word
from ..models.srs import Rating
from ..services.learning_service import AnswerOutcome, DailyPlan
from ..services.review_flow import Step, StepKind
from ..services.review_wording import FeedbackText, interval_text, when_text

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


def step_data(session_id: str, number: int, value: str) -> str:
    return f"st:{session_id}:{number}:{value}"


def known_data(session_id: str, word_id: int) -> str:
    return f"known:{session_id}:{word_id}"


def end_data(session_id: str) -> str:
    return f"end:{session_id}"


def undo_data(session_id: str) -> str:
    return f"undo:{session_id}"


START_DATA = "start"
LEARN_DATA = "learn"
LATER_DATA = "later"


@dataclass(frozen=True, slots=True)
class Callback:
    """A parsed button press."""

    action: str
    local_date: str | None = None
    session_id: str | None = None
    word_id: int | None = None
    #: For ``step``: the step number and what was pressed.
    step: int | None = None
    value: str | None = None


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
        if parts == ["learn"]:
            return Callback("learn")
        if parts == ["later"]:
            return Callback("later")
        if parts[0] == "st" and len(parts) == 4 and parts[3]:
            return Callback("step", session_id=parts[1], step=int(parts[2]), value=parts[3])
        if parts[0] == "known" and len(parts) == 3:
            return Callback("known", session_id=parts[1], word_id=int(parts[2]))
        if parts[0] == "end" and len(parts) == 2:
            return Callback("end", session_id=parts[1])
        if parts[0] == "undo" and len(parts) == 2:
            return Callback("undo", session_id=parts[1])
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

    if plan.new_words:
        levels = Counter(word.cefr_level or "–" for word in plan.new_words)
        spread = ", ".join(f"{level} ×{count}" for level, count in sorted(levels.items()))
        lines += ["", f"<b>{len(plan.new_words)} new words</b>  <i>{escape(spread)}</i>"]
        for word in plan.new_words:
            lines.append(_word_line(word.word, word.definition))
    elif plan.introduced_today:
        lines += ["", f"{len(plan.introduced_today)} new words already learned today."]
    if plan.intake_paused and plan.intake_note:
        lines += ["", f"<i>{escape(plan.intake_note)}</i>"]

    lines.append("")
    if plan.due_count:
        lines.append(f"<b>{plan.due_count} reviews</b> due today.")
    else:
        lines.append("No reviews due today.")
    return Message(_fit("\n".join(lines)), _day_buttons(plan))


def _day_buttons(plan: DailyPlan) -> tuple[ButtonRow, ...]:
    """The day's two sessions, each its own button, reviews first."""
    buttons: list[ButtonRow] = []
    if plan.due_count:
        buttons.append(((f"▶ Reviews ({plan.due_count})", START_DATA),))
    if plan.new_words:
        buttons.append(((f"▶ New words ({len(plan.new_words)})", LEARN_DATA),))
    return tuple(buttons)


def day_buttons(plan: DailyPlan) -> tuple[ButtonRow, ...]:
    """What is left today, as buttons, reviews first."""
    return _day_buttons(plan)


def retired_intro() -> str:
    """An old morning message's Mark as studied, tapped now."""
    return (
        "That button is gone: new words are now learned one by one, each shown "
        "and practised. /learn starts them."
    )


def nothing_to_review(plan: DailyPlan) -> Message:
    """/review with no word due."""
    text = "No reviews are due right now."
    if plan.new_words:
        text += f" {len(plan.new_words)} new words are waiting."
    return Message(text, _day_buttons(plan))


def nothing_to_learn(plan: DailyPlan) -> Message:
    """/learn with no new word left today."""
    if plan.intake_paused and plan.intake_note:
        text = f"No new words today. <i>{escape(plan.intake_note)}</i>"
    elif plan.introduced_today:
        text = f"Today's {len(plan.introduced_today)} new words are learned."
    else:
        text = "No new words are waiting today."
    if plan.due_count:
        text += f" {plan.due_count} reviews are due."
    return Message(text, _day_buttons(plan))


def later_note() -> str:
    """Tapped Later after the reviews: the new words wait."""
    return "The new words wait. /learn starts them whenever you like."


#: The ratings after a right answer, as buttons: the callback value is the
#: rating's name.
RATING_VALUES = {rating.name.lower(): rating for rating in Rating}
#: The letters the options of Context → Definition are listed by.
LETTERS = "ABCD"


def step_card(
    step: Step,
    session_id: str,
    number: int,
    position: int,
    total: int,
    *,
    feedback: str | None = None,
    note: str | None = None,
    can_undo: bool = False,
    known_word: tuple[int, str] | None = None,
) -> Message:
    """One step of the session, sent as a message of its own.

    ``feedback`` says what the previous step did (one line or several);
    ``known_word`` offers to mark a word just answered right after a long
    gap as Known.
    """
    lines: list[str] = []
    if note:
        lines += [f"<i>{escape(note)}</i>", ""]
    if feedback:
        lines += [f"<i>{escape(line)}</i>" for line in feedback.splitlines()] + [""]
    header = escape(step.label.upper()) if step.label else ""
    if step.is_struggling:
        header += "  ·  ⚠ hard for you"
    if header:
        lines.append(f"<b>{header}</b>")

    rows: list[ButtonRow] = []
    act = lambda value: step_data(session_id, number, value)  # noqa: E731
    question = step.question
    if step.kind is StepKind.TEACH:
        lines += ["", _headword(step)]
        if step.word.definition:
            lines += ["", "<b>Definition</b>", escape(step.word.definition)]
        if step.contexts:
            lines += ["", "<b>Contexts</b>"]
            lines += [f"• {_marked(context.text, step.word.word)}" for context in step.contexts]
        rows.append((("Continue →", act("go")),))
    elif question is not None and question.task is Task.CONTEXT_TO_DEFINITION:
        lines += ["", f"“{_marked(question.prompt, step.word.word)}”", "",
                  "<i>Which definition fits the word in bold?</i>", ""]
        lines += [
            f"<b>{LETTERS[index]}</b>  {escape(option.text)}"
            for index, option in enumerate(question.options)
        ]
        rows.append(tuple(
            (LETTERS[index], act(str(index))) for index in range(len(question.options))
        ))
    elif question is not None:
        lines += ["", escape(question.prompt), "", "<i>Which word is it?</i>"]
        options = [(option.text, act(str(index))) for index, option in enumerate(question.options)]
        rows += [tuple(options[i : i + 2]) for i in range(0, len(options), 2)]
    lines += ["", f"{position} / {total}"]
    if known_word is not None:
        rows.append(((f"Mark “{known_word[1]}” Known", known_data(session_id, known_word[0])),))
    rows.append(
        (
            *((("↶ Undo", undo_data(session_id)),) if can_undo else ()),
            ("Stop here", end_data(session_id)),
        )
    )
    return Message(_fit("\n".join(lines)), tuple(rows))


def rate_card(
    step: Step,
    session_id: str,
    number: int,
    text: FeedbackText,
    intervals: dict[Rating, int],
    position: int,
    total: int,
) -> Message:
    """After a right answer to the day's question: the word and its
    definition again, and how it went — Again, Hard, Good or Easy."""
    lines = [f"<b>{escape(step.label.upper())}</b>", "", f"✓ <b>{escape(text.title)}</b>"]
    lines += [f"{escape(label)}: <b>{escape(value)}</b>" if label == "Word"
              else f"{escape(label)}: {escape(value)}" for label, value in text.lines]
    lines += ["", "How did it go?", "", f"{position} / {total}"]
    buttons = tuple(
        (_rating_label(rating, intervals), step_data(session_id, number, name))
        for name, rating in RATING_VALUES.items()
    )
    return Message(
        "\n".join(lines),
        (buttons[:2], buttons[2:], (("Stop here", end_data(session_id)),)),
    )


def feedback_lines(text: FeedbackText) -> str:
    """What an answer did, as plain lines for the top of the next card."""
    mark = "✓" if text.tone == "good" else "✗"
    lines = [f"{mark} {text.title}"] + [f"{label}: {value}" for label, value in text.lines]
    if text.note:
        lines.append(text.note)
    return "\n".join(lines)


def _rating_label(rating: Rating, intervals: dict[Rating, int]) -> str:
    days = intervals.get(rating)
    return rating.label if days is None else f"{rating.label} · {interval_text(days)}"


def _headword(step: Step) -> str:
    word = step.word
    meta = " · ".join(
        part for part in (f"{word.length} letters", word.part_of_speech, word.cefr_level) if part
    )
    return f"<b>{escape(word.word)}</b>" + (f"  <i>{escape(meta)}</i>" if meta else "")


def _marked(text: str, word: str) -> str:
    """A context with the word in bold, where it can be found."""
    span = find_word(text, word)
    if span is None:
        return escape(text)
    start, end = span
    return escape(text[:start]) + f"<b>{escape(text[start:end])}</b>" + escape(text[end:])


def answer_line(outcome: AnswerOutcome) -> str:
    """One line saying what the last answer did."""
    mark = _RATING_MARK[outcome.rating]
    when = when_text(outcome.interval_days)
    text = f"{mark} {outcome.word.word}: {outcome.rating.label} — back {when}"
    if outcome.suggest_known:
        text += " · right after a long gap"
    elif outcome.became_struggling:
        text += " · flagged as hard"
    return text


def session_summary(
    kind: str,
    answered: int,
    ratings: dict[int, int],
    plan: DailyPlan,
    stopped_early: bool,
    learned: int = 0,
) -> Message:
    """The end of a session: what was done, what is left, and the next step.

    Reviews finished with new words waiting ask about them: now, or later.
    Anything else offers what is left, reviews first.
    """
    learning = kind == "learn"
    if stopped_early:
        title = "Stopped"
    else:
        title = "New words learned ✅" if learning else "Reviews done ✅"
    lines = [f"<b>{title}</b>"]
    if answered:
        again = ratings.get(int(Rating.AGAIN), 0)
        rate = round(100 * again / answered) if answered else 0
        lines.append(f"{answered} reviewed · {again} again ({rate}%)")
    if learned:
        noun = "word" if learned == 1 else "words"
        lines.append(f"{learned} new {noun} learned — first review tomorrow")
    if not answered and not learned:
        lines.append("Nothing was answered.")

    left = []
    if plan.due_count:
        left.append(f"{plan.due_count} {'review' if plan.due_count == 1 else 'reviews'} due")
    if plan.new_words:
        left.append(f"{len(plan.new_words)} new words waiting")
    tomorrow = dict(plan.forecast).get(_next_day(plan.local_date))

    buttons: tuple[ButtonRow, ...]
    if not learning and not stopped_early and plan.new_words and not plan.due_count:
        lines += ["", f"Next: <b>{len(plan.new_words)} new words</b>."]
        buttons = ((("▶ Start now", LEARN_DATA), ("Later", LATER_DATA)),)
    else:
        if left:
            lines.append("Still today: " + " · ".join(left) + ".")
        else:
            lines.append("Nothing left for today. 🎉")
        buttons = _day_buttons(plan)
    if tomorrow:
        lines.append(f"Tomorrow: {tomorrow} reviews.")
    return Message("\n".join(lines), buttons)


def evening_reminder(plan: DailyPlan) -> Message | None:
    """21:00, only if something is left. ``None`` means stay quiet."""
    if not plan.has_plan or not plan.has_work:
        return None
    parts = []
    if plan.new_words:
        parts.append(f"{len(plan.new_words)} new words to learn")
    if plan.due_count:
        parts.append(f"{plan.due_count} reviews due")
    return Message(
        "<b>Still to do today</b>\n" + " · ".join(parts) + ".", _day_buttons(plan)
    )


def weekly_summary(week) -> Message | None:
    """Sunday evening: the week in a few lines. ``None`` for a week with nothing."""
    if not week.answers and not week.introduced:
        return None
    lines = [
        f"<b>Your week</b> \u00b7 {escape(_pretty(week.first_day))} to "
        f"{escape(_pretty(week.last_day))}",
        "",
    ]
    rate = f" ({round(week.again_rate * 100)}% Again)" if week.again_rate is not None else ""
    lines.append(f"{week.answers} answers{rate}")
    lines.append(f"{week.introduced} new words introduced")
    if week.learned:
        shown = ", ".join(escape(word) for word in week.learned[:8])
        more = f" and {len(week.learned) - 8} more" if len(week.learned) > 8 else ""
        lines.append(f"<b>{len(week.learned)} learned</b>: {shown}{more}")
    else:
        lines.append("No word reached Known this week")
    lines.append(f"{week.in_progress} still in progress")
    if week.hard:
        shown = ", ".join(escape(word) for word in week.hard[:5])
        lines += ["", f"\u26a0 <i>Giving you trouble:</i> {shown}"]
    return Message(_fit("\n".join(lines)))


def not_allowed() -> str:
    return "This bot belongs to someone else's LexiTrack."


def welcome(bound: bool) -> str:
    if bound:
        return (
            "<b>LexiTrack is connected.</b>\n"
            "You will get today's words each morning. /today shows them now, "
            "/review starts the reviews and /learn the new words. Every question is "
            "answered with its buttons."
        )
    return (
        "LexiTrack is running. /today shows today's words, /review starts the "
        "reviews, /learn the new words."
    )


def use_the_buttons() -> str:
    return "Answer the card with its buttons. /review or /learn shows it again."


def no_session() -> str:
    return "No session is open. /review starts the reviews, /learn the new words."


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
