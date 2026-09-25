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
``intro:<date>``        mark the day's new words as studied — only on that date
``start``               begin (or resume) the day's session
``st:<s>:<n>:<v>``      step ``n`` of session ``s``: ``v`` is an option (0–3),
                        a report (``forgot``, ``effortful``, ``remembered``,
                        ``instant``), ``go`` (continue) or ``dk`` (Forgot,
                        before answering)
``known:<s>:<w>``       mark word ``w`` Known, as offered after its answer
``end:<s>``             stop the session and keep what was answered
``undo:<s>``            take back the last answer in session ``s``
======================  ==========================================

The date on ``intro`` is what stops an old morning message from confirming
the *next* day's words, which the user has never seen; the step number on
``st`` is what makes a tap on an older card do nothing.

A session is the desktop's (services/review_flow.py), step by step: typed
answers are sent as replies, a sentence is written as a reply and then
graded, the rest are buttons. The wording of feedback and of a teaching page
is shared with the desktop (services/review_wording.py).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

from ..models.attempt import SUCCESS_REPORTS, SelfReport
from ..models.srs import Rating
from ..services.learning_service import AnswerOutcome, DailyPlan
from ..services.review_flow import Step, StepKind
from ..services.review_wording import teaching_page, when_text

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
    """Start the session (reviews first, then the new words), or skip the
    new words' practice, as Mark as studied does on the desktop."""
    buttons: list[ButtonRow] = []
    work = plan.due_count + len(plan.new_words)
    if work:
        buttons.append(((f"▶ Start session ({work})", START_DATA),))
    if plan.new_words:
        buttons.append(
            ((f"Mark the {len(plan.new_words)} new words as studied",
              intro_data(plan.local_date)),)
        )
    return tuple(buttons)


def introduced(count: int, first_due_on: str | None) -> str:
    """What replaces the button once the new words are marked as studied."""
    if not count:
        return "Nothing new to mark — today's words are already in."
    when = _pretty(first_due_on) if first_due_on else "tomorrow"
    return f"✅ {count} new words marked as studied. First review: {escape(when)}."


def _reports(session_id: str, number: int, reports) -> ButtonRow:
    return tuple((report.label, step_data(session_id, number, report.value)) for report in reports)


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

    ``feedback`` says what the previous step did; ``known_word`` offers to
    mark a word that just reached long-term memory as Known. A RECALL card's
    meaning is a spoiler when the setting asks for it hidden: tapping it
    reveals it in place. Every card is a new message, because an edited one
    keeps a spoiler revealed.
    """
    lines: list[str] = []
    for line in (note, feedback):
        if line:
            lines += [f"<i>{escape(line)}</i>", ""]
    header = escape(step.label.upper()) if step.label else ""
    if step.is_struggling:
        header += "  ·  ⚠ hard for you"
    if header:
        lines.append(f"<b>{header}</b>")

    rows: list[ButtonRow] = []
    act = lambda value: step_data(session_id, number, value)  # noqa: E731
    prompt = step.prompt
    if step.kind is StepKind.TEACH:
        lines += ["", _headword(step)]
        page = teaching_page(step)
        for title, text in page.sections:
            lines += ["", f"<b>{escape(title)}</b>", escape(text)]
        if page.examples:
            lines += ["", "<b>In use</b>"]
            for sentence, translated in page.examples:
                lines.append(f"“{escape(sentence)}”")
                if translated:
                    lines.append(f"<i>{escape(translated)}</i>")
        if page.empty:
            lines += ["", "<i>No more is stored about this word yet.</i>"]
        if step.reason:
            lines += ["", f"<i>{escape(step.reason)}</i>"]
        rows.append((("Continue →", act("go")),))
    elif step.kind is StepKind.TYPE and prompt is not None:
        lines += ["", escape(prompt.text)]
        if prompt.detail:
            lines.append(f"<i>{escape(prompt.detail)}</i>")
        lines += ["", "<i>Reply with the word.</i>"]
        rows.append((("Forgot", act("dk")),))
    elif step.kind is StepKind.CHOOSE and prompt is not None:
        lines += ["", escape(prompt.text)]
        if prompt.detail:
            lines.append(f"<i>{escape(prompt.detail)}</i>")
        options = [(option.word, act(str(index))) for index, option in enumerate(step.options)]
        rows += [tuple(options[i : i + 2]) for i in range(0, len(options), 2)]
    elif step.kind is StepKind.WRITE:
        lines += ["", _headword(step)]
        if prompt is not None:
            lines += ["", escape(prompt.text)]
        lines += ["", "<i>Reply with a sentence of your own.</i>"]
    else:  # RECALL: a word with no meaning to ask from, and the four reports
        lines += ["", _headword(step), "",
                  "<i>No meaning is stored for this word yet. Do you know it?</i>"]
        reports = _reports(session_id, number, tuple(SelfReport))
        rows += [reports[:2], reports[2:]]
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


def assess_card(
    step: Step, session_id: str, number: int, feedback: str, position: int, total: int
) -> Message:
    """After a right typed answer: how it came, before anything is recorded."""
    lines = [
        f"<b>{escape(step.label.upper())}</b>",
        "",
        f"<i>{escape(feedback)}</i>",
        "",
        "How did it come?",
        "",
        f"{position} / {total}",
    ]
    return Message(
        "\n".join(lines),
        (_reports(session_id, number, SUCCESS_REPORTS), (("Stop here", end_data(session_id)),)),
    )


def write_check(step: Step, sentence: str, session_id: str, number: int) -> Message:
    """A written sentence beside the stored examples, and the four reports."""
    lines = [
        f"<b>{escape(step.label.upper())}</b>",
        "",
        _headword(step),
        "",
        "<b>Yours</b>",
        f"“{escape(sentence)}”",
    ]
    teaching = step.teaching
    examples = [context.plain for context in teaching.contexts[:2]] if teaching else []
    if examples:
        lines += ["", "<b>Examples</b>"] + [f"“{escape(text)}”" for text in examples]
    lines += ["", "<i>Did you use it the way the examples do? How did it come?</i>"]
    reports = _reports(session_id, number, tuple(SelfReport))
    return Message(
        _fit("\n".join(lines)),
        (reports[:2], reports[2:], (("Stop here", end_data(session_id)),)),
    )


def _headword(step: Step) -> str:
    word = step.word
    meta = " · ".join(part for part in (word.part_of_speech, word.cefr_level) if part)
    return f"<b>{escape(word.word)}</b>" + (f"  <i>{escape(meta)}</i>" if meta else "")


def answer_line(outcome: AnswerOutcome) -> str:
    """One line saying what the last answer did."""
    mark = _RATING_MARK[outcome.rating]
    when = when_text(outcome.interval_days)
    text = f"{mark} {outcome.word.word}: {outcome.rating.label} — back {when}"
    if outcome.suggest_known:
        text += " · in long-term memory"
    elif outcome.became_struggling:
        text += " · flagged as hard"
    return text


def session_summary(
    answered: int,
    ratings: dict[int, int],
    plan: DailyPlan,
    stopped_early: bool,
    learned: int = 0,
) -> str:
    """The end of a session: what was done, and what is left."""
    lines = ["<b>Session finished</b>" if not stopped_early else "<b>Stopped</b>"]
    if answered:
        again = ratings.get(int(Rating.AGAIN), 0)
        rate = round(100 * again / answered) if answered else 0
        lines.append(f"{answered} reviewed · {again} again ({rate}%)")
    if learned:
        lines.append(f"{learned} new {'word' if learned == 1 else 'words'} learned")
    if not answered and not learned:
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
            "/review starts a session. Typed questions are answered by replying "
            "with the word."
        )
    return "LexiTrack is running. /today shows today's words, /review starts a session."


def use_the_buttons() -> str:
    return "Answer the card with its buttons. /review shows it again."


def no_session() -> str:
    return "No session is open. /review starts one."


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
