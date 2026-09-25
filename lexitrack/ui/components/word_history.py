"""A word's history: how it was learned, answer by answer.

Opened from the details panel or from a word on the Study page. It answers
three questions, in this order, because that is the order people ask them:

1. **Where is it now?** Four facts across the top: when it was introduced,
   how many answers, how long it is expected to be remembered, what is next.
2. **Why is it due when it is?** One sentence from the last answer that still
   counts, and the chance of remembering it today.
3. **How did it get here?** A chart of stability after each answer, with the
   line it has to cross to count as Known, and the full list below: every
   answer, every change of status, and answers taken back, marked as such.

Nothing here is computed for display only: the figures are the scheduler's
own, read through :class:`~lexitrack.services.progress.ProgressService`.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...models.skill import SkillStage
from ...models.srs import CardState, Channel, Rating
from ...models.user_word_state import ReviewStatus, StatusCause
from ...services.progress import Group, JourneyStep, WordJourney
from ..theme import current_palette
from ..theme.palette import METRICS
from .chips import chip
from .status import StatusBadge

_STATE_WORDS = {
    CardState.INTRODUCED: "new",
    CardState.LEARNING: "learning",
    CardState.REVIEW: "review",
    CardState.RELEARNING: "relearning",
    CardState.ARCHIVED: "archived",
}

_RATING_TONE = {
    Rating.AGAIN: "again",
    Rating.HARD: "hard-answer",
    Rating.GOOD: "good",
    Rating.EASY: "easy",
}


def pretty_day(day: str | None) -> str:
    """``2026-09-18`` as ``18 Sep``; the year only when it is not this one."""
    if not day:
        return "—"
    value = date.fromisoformat(day)
    text = f"{value.day} {value.strftime('%b')}"
    return text if value.year == date.today().year else f"{text} {value.year}"


def remembered_for(stability: float | None) -> str:
    if stability is None:
        return "not yet known"
    if stability < 1:
        return "under a day"
    if stability < 1.5:
        return "about a day"
    return f"about {stability:.0f} days" if stability >= 10 else f"about {stability:.1f} days"


def status_sentence(step: JourneyStep) -> str:
    """A change of status, in words."""
    to = step.status_to
    cause = step.cause
    if cause is StatusCause.MASTERY:
        text = "Marked Known after reaching long-term memory"
    elif cause is StatusCause.UNDO:
        text = f"Back to {_status_word(to)}: an answer was taken back"
    elif cause is StatusCause.SORTING:
        text = f"Marked {_status_word(to)} on Sort words"
    elif to is ReviewStatus.NOT_REVIEWED:
        text = "Reset to not reviewed"
    else:
        text = f"Marked {_status_word(to)} by hand"
    if step.reconstructed:
        text += " (dated from the answer, recorded before 0.4)"
    return text


def skill_sentence(journey: WordJourney) -> str:
    """What the record shows can be done with the word, in one line."""
    skill = journey.skill
    if skill is None or skill.stage is SkillStage.NONE:
        return ""
    counts = []
    if skill.recognized:
        counts.append(f"recognised {_times(skill.recognized)}")
    if skill.recalled:
        counts.append(f"recalled {_times(skill.recalled)}")
    if skill.produced_in:
        counts.append(f"used in {skill.produced_in} "
                      f"{'context' if skill.produced_in == 1 else 'contexts'}")
    text = f"Skill: {skill.stage.label}"
    if counts:
        text += " — " + ", ".join(counts) + " on later days"
    if skill.automatic:
        text += f"; instant on {skill.automatic_days} days, so automatic"
    if skill.from_v1 and not skill.recalled and not skill.produced_in:
        text += ". Answers from before this version asked for the meaning only"
    return text + "."


def _times(count: int) -> str:
    return "once" if count == 1 else f"{count} times"


def why_sentence(journey: WordJourney) -> str:
    """Why the word is due when it is, from the last answer that still counts."""
    card = journey.card
    if card is None:
        if journey.word.status is ReviewStatus.KNOWN:
            return "Known before you studied it here, so it is not on your schedule."
        if journey.word.status is ReviewStatus.UNKNOWN:
            return (
                "Unknown, and not introduced yet: a study plan that includes one of its "
                "lists offers it as a new word."
            )
        return "Not sorted yet. Mark it Unknown and your study plan will teach it."
    if card.state is CardState.ARCHIVED:
        return "Marked Known by hand, so it has left the schedule. Reset it to bring it back."
    last = journey.last_answer
    if last is None:
        return (
            f"Introduced on {pretty_day(journey.introduced_on)}. Its first question comes "
            f"on {pretty_day(journey.next_due_on)}; no answer has been given yet."
        )
    head = (
        f"Your last answer, {last.rating.label if last.rating else '?'} on "
        f"{pretty_day(last.day)}, means you should remember it for "
        f"{remembered_for(last.stability)}."
    )
    chance = (
        f" The chance you still remember it now is about {round(journey.recall_now * 100)}%."
        if journey.recall_now is not None
        else ""
    )
    if journey.due_today:
        return f"Due today. {head}{chance}"
    return f"Next on {pretty_day(journey.next_due_on)}. {head}{chance}"


class StabilityChart(QWidget):
    """Stability after each answer, against the line of long-term memory.

    A step line, because stability only changes when an answer is given, with
    a dot per answer in that answer's colour. The dashed line is the mastery
    threshold; from the answer that first crosses it, the word is offered as Known.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[str, float, Rating | None]] = []
        self._threshold = 21.0
        self.setMinimumHeight(150)

    def set_points(
        self, points: list[tuple[str, float, Rating | None]], threshold: float
    ) -> None:
        self._points = points
        self._threshold = threshold
        highest = max((value for _, value, _ in points), default=0.0)
        self.setAccessibleName(
            f"Stability after each of {len(points)} answers; highest "
            f"{highest:.1f} days; Known at {threshold:g} days"
        )
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Room on the right for half a date label under the last point.
        left, right, top, bottom = 34.0, 32.0, 10.0, 22.0
        area = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        small = QFont(self.font())
        small.setPixelSize(11)
        painter.setFont(small)
        faint = QColor(palette.text_faint)

        if not self._points:
            painter.setPen(faint)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "The chart starts with the first answer."
            )
            painter.end()
            return

        ceiling = max(self._threshold * 1.25, max(v for _, v, _ in self._points) * 1.1)

        def y_of(value: float) -> float:
            return area.bottom() - area.height() * min(value, ceiling) / ceiling

        count = len(self._points)

        def x_of(index: int) -> float:
            if count == 1:
                return area.center().x()
            return area.left() + area.width() * index / (count - 1)

        # axis: zero and the threshold, labelled in days
        painter.setPen(QPen(QColor(palette.border), 1))
        painter.drawLine(QPointF(area.left(), area.bottom()), QPointF(area.right(), area.bottom()))
        threshold_y = y_of(self._threshold)
        pen = QPen(QColor(palette.known), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(area.left(), threshold_y), QPointF(area.right(), threshold_y))
        painter.setPen(faint)
        painter.drawText(
            QRectF(0, threshold_y - 8, left - 6, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{self._threshold:g}d",
        )
        painter.drawText(
            QRectF(0, area.bottom() - 8, left - 6, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "0",
        )
        painter.setPen(QColor(palette.known_text))
        painter.drawText(
            QRectF(area.left() + 4, threshold_y - 16, area.width(), 14),
            Qt.AlignmentFlag.AlignLeft,
            "Known",
        )

        # the step line
        path = QPainterPath()
        for index, (_, value, _) in enumerate(self._points):
            point = QPointF(x_of(index), y_of(value))
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(QPointF(point.x(), path.currentPosition().y()))
                path.lineTo(point)
        painter.setPen(QPen(QColor(palette.accent), 2))
        painter.drawPath(path)

        colours = {
            Rating.AGAIN: palette.unknown,
            Rating.HARD: palette.text_muted,
            Rating.GOOD: palette.known,
            Rating.EASY: palette.known,
        }
        for index, (day, value, rating) in enumerate(self._points):
            centre = QPointF(x_of(index), y_of(value))
            painter.setPen(QPen(QColor(palette.surface), 2))
            painter.setBrush(QColor(colours.get(rating, palette.accent)))
            painter.drawEllipse(centre, 4.5, 4.5)
            if index in (0, count - 1) or count <= 8:
                painter.setPen(faint)
                painter.drawText(
                    QRectF(centre.x() - 30, area.bottom() + 4, 60, 16),
                    Qt.AlignmentFlag.AlignHCenter,
                    pretty_day(day),
                )
        painter.end()


class WordHistoryView(QWidget):
    """The whole history of one word. Used by :class:`WordHistoryDialog`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(m.space_3)

        head = QHBoxLayout()
        head.setSpacing(m.space_3)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("PanelWord")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.title)
        self.meta = QLabel()
        self.meta.setObjectName("Muted")
        titles.addWidget(self.meta)
        head.addLayout(titles, 1)
        self.badge = StatusBadge()
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        facts = QGridLayout()
        facts.setHorizontalSpacing(m.space_3)
        facts.setVerticalSpacing(m.space_3)
        self._facts: list[tuple[QLabel, QLabel]] = []
        for index in range(4):
            tile = QFrame()
            tile.setObjectName("HistoryFact")
            tile.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            inner = QVBoxLayout(tile)
            inner.setContentsMargins(m.space_3, m.space_2, m.space_3, m.space_2)
            inner.setSpacing(2)
            caption = QLabel()
            caption.setObjectName("HistoryFactCaption")
            value = QLabel()
            value.setObjectName("HistoryFactValue")
            value.setWordWrap(True)
            inner.addWidget(caption)
            inner.addWidget(value)
            facts.addWidget(tile, 0, index)
            self._facts.append((caption, value))
        layout.addLayout(facts)

        # Skill, beside the memory the facts describe: what the answers showed.
        self.skill = QLabel()
        self.skill.setObjectName("HistorySkill")
        self.skill.setWordWrap(True)
        layout.addWidget(self.skill)

        self.why = QLabel()
        self.why.setObjectName("HistoryWhy")
        self.why.setWordWrap(True)
        self.why.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.why)

        self.chart_title = QLabel("REMEMBERED FOR, AFTER EACH ANSWER")
        self.chart_title.setObjectName("SectionTitle")
        layout.addWidget(self.chart_title)
        self.chart = StabilityChart()
        layout.addWidget(self.chart)

        self.steps_title = QLabel("EVERYTHING THAT HAPPENED")
        self.steps_title.setObjectName("SectionTitle")
        layout.addWidget(self.steps_title)
        self.steps = QVBoxLayout()
        self.steps.setSpacing(0)
        steps_holder = QFrame()
        steps_holder.setObjectName("HistorySteps")
        steps_holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        steps_holder.setLayout(self.steps)
        layout.addWidget(steps_holder)

    def show_journey(self, journey: WordJourney) -> None:
        word = journey.word
        self.title.setText(word.word)
        meta = " · ".join(part for part in (word.part_of_speech, word.cefr_level) if part)
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))
        self.badge.set_status(word.status)

        facts = _facts(journey)
        for (caption, value), (title, text) in zip(self._facts, facts, strict=False):
            caption.setText(title)
            value.setText(text)

        skill = skill_sentence(journey)
        self.skill.setText(skill)
        self.skill.setVisible(bool(skill))
        self.why.setText(why_sentence(journey))
        points = [
            (step.day, step.stability, step.rating)
            for step in journey.steps
            if step.kind == "answer" and not step.undone and step.stability is not None
        ]
        self.chart.set_points(points, journey.mastery_days)
        has_card = journey.card is not None
        self.chart_title.setVisible(has_card)
        self.chart.setVisible(has_card)

        while self.steps.count():
            item = self.steps.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        rows = list(reversed(journey.steps))
        if not rows:
            empty = QLabel("Nothing recorded for this word yet.")
            empty.setObjectName("Faint")
            empty.setContentsMargins(METRICS.space_3, METRICS.space_3, 0, METRICS.space_3)
            self.steps.addWidget(empty)
        for index, step in enumerate(rows):
            self.steps.addWidget(_StepRow(step, first=index == 0))


class _StepRow(QFrame):
    """One line of the history: a day, what happened, and its detail."""

    def __init__(self, step: JourneyStep, first: bool) -> None:
        super().__init__()
        self.setObjectName("HistoryStep")
        self.setProperty("first", "true" if first else "false")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        m = METRICS
        layout = QHBoxLayout(self)
        layout.setContentsMargins(m.space_3, m.space_2, m.space_3, m.space_2)
        layout.setSpacing(m.space_3)
        day = QLabel(pretty_day(step.day))
        day.setObjectName("HistoryDay")
        day.setFixedWidth(64)
        layout.addWidget(day)

        if step.kind == "answer" and step.rating is not None:
            badge = chip(step.rating.label, tone=_RATING_TONE[step.rating])
            badge.setFixedWidth(64)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(badge)
            parts = []
            if step.asked is not None:
                parts.append(step.asked.label)
            if step.memory_result is not None:
                parts.append(step.memory_result.label.lower())
            if step.state_before and step.state_after and step.state_before != step.state_after:
                parts.append(
                    f"{_STATE_WORDS[step.state_before]} → {_STATE_WORDS[step.state_after]}"
                )
            parts.append(f"remembered for {remembered_for(step.stability)}")
            if step.channel is Channel.TELEGRAM:
                parts.append("on Telegram")
            detail = QLabel(" · ".join(parts))
        elif step.kind == "introduced":
            badge = chip("New", tone="done")
            badge.setFixedWidth(64)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(badge)
            detail = QLabel("Studied and confirmed as one of the day's new words")
        else:
            spacer = QLabel("")
            spacer.setFixedWidth(64)
            layout.addWidget(spacer)
            detail = QLabel(status_sentence(step))
        detail.setObjectName("HistoryDetail")
        detail.setWordWrap(True)
        layout.addWidget(detail, 1)
        if step.undone:
            taken = QLabel("taken back")
            taken.setObjectName("HistoryUndone")
            layout.addWidget(taken)
            self.setProperty("undone", "true")
            detail.setProperty("undone", "true")


def _facts(journey: WordJourney) -> list[tuple[str, str]]:
    introduced = journey.introduced_on
    plan = f" · {journey.plan_name}" if journey.plan_name else ""
    first = ("INTRODUCED", f"{pretty_day(introduced)}{plan}" if introduced else "Not yet")
    agains = f" ({journey.agains} Again)" if journey.agains else ""
    second = ("ANSWERS", f"{journey.answers}{agains}")
    card = journey.card
    if journey.known_on:
        how = "by the schedule" if journey.known_cause is StatusCause.MASTERY else "by hand"
        days = ""
        if introduced:
            span = (date.fromisoformat(journey.known_on) - date.fromisoformat(introduced)).days
            days = f", {span} days after introduction"
        third = ("KNOWN", f"{pretty_day(journey.known_on)} {how}{days}")
    else:
        third = (
            "REMEMBERED FOR",
            remembered_for(card.stability) if card is not None else "—",
        )
        if card is not None and card.stability is not None:
            third = (third[0], f"{third[1]} ({journey.mastery_days:g} needed)")
    if card is None or card.state is CardState.ARCHIVED:
        fourth = ("NEXT", "Not scheduled")
    elif journey.due_today:
        fourth = ("NEXT", "Today")
    else:
        fourth = ("NEXT", pretty_day(journey.next_due_on))
    return [first, second, third, fourth]


def _status_word(status: ReviewStatus | None) -> str:
    return {
        ReviewStatus.KNOWN: "Known",
        ReviewStatus.UNKNOWN: "Unknown",
        ReviewStatus.NOT_REVIEWED: "not reviewed",
    }.get(status, "?")


def journey_summary(journey: WordJourney) -> str:
    """Two short lines for the details panel."""
    card = journey.card
    if card is None:
        return why_sentence(journey)
    if journey.group is Group.LEARNED and journey.known_on:
        return (
            f"Learned here: introduced {pretty_day(journey.introduced_on)}, Known "
            f"{pretty_day(journey.known_on)} after {journey.answers} answers."
        )
    if card.state is CardState.ARCHIVED or journey.group is Group.MARKED_KNOWN:
        return (
            f"Introduced {pretty_day(journey.introduced_on)}, then marked Known by hand "
            f"after {journey.answers} answers."
        )
    nxt = "today" if journey.due_today else pretty_day(journey.next_due_on)
    return (
        f"In progress: {journey.answers} answers so far, remembered for "
        f"{remembered_for(card.stability)}. Next: {nxt}."
    )


class WordHistoryDialog(QDialog):
    """A word's history in its own window."""

    def __init__(self, journey: WordJourney, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"History of {journey.word.word}")
        self.setMinimumSize(640, 600)
        m = METRICS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("PanelBody")
        inner = QVBoxLayout(body)
        inner.setContentsMargins(m.space_5, m.space_5, m.space_5, m.space_4)
        self.view = WordHistoryView()
        self.view.show_journey(journey)
        inner.addWidget(self.view)
        inner.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.setContentsMargins(m.space_5, m.space_3, m.space_5, m.space_4)
        footer.addStretch(1)
        footer.addWidget(buttons)
        outer.addLayout(footer)
