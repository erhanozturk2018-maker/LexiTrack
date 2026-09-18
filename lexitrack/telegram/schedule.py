"""When the bot speaks unprompted: the morning brief and the evening reminder.

This is a decision, not a timer. The bot ticks every half minute and asks
:func:`due_notifications` what, if anything, is owed *now*. The answer depends
only on the clock, the settings and what ``runtime_state`` says was already
sent today, which is what makes downtime harmless:

* The computer was off at 06:00 and comes on at 09:00 — the morning brief is
  owed and is sent at 09:00. Once.
* It was off for five days — one brief, for today. Nothing is replayed,
  because nothing was owed on the days that passed; the words are still
  candidates and the reviews are simply overdue.
* It comes on at 22:00 without having sent the morning brief — the brief goes
  out, and the evening reminder is skipped, because it would say the same
  thing a minute later.

The reminder is only owed if work is left; that check belongs to the caller,
which has the day's plan. Claiming a day is done with
:meth:`RuntimeRepository.mark_done`, so a manual "send now" and the timer
cannot both fire.
"""

from __future__ import annotations

from enum import StrEnum

from ..core.clock import DayClock
from ..models.settings import LearningSettings
from ..repositories import RuntimeRepository


class Notification(StrEnum):
    MORNING = "morning"
    EVENING = "evening"


def due_notifications(
    clock: DayClock, settings: LearningSettings, runtime: RuntimeRepository
) -> list[Notification]:
    """What should be sent now, in order. Empty most of the time."""
    today = clock.today()
    hour = clock.now_local().hour
    morning_sent = runtime.was_done(RuntimeRepository.LAST_NOTIFIED_ON, today)
    evening_sent = runtime.was_done(RuntimeRepository.LAST_REMINDER_ON, today)

    due: list[Notification] = []
    morning_owed = not morning_sent and hour >= settings.notify_hour
    if morning_owed:
        due.append(Notification.MORNING)
    if (
        not evening_sent
        and not morning_owed
        and morning_sent
        and hour >= settings.evening_reminder_hour
    ):
        due.append(Notification.EVENING)
    return due


def mark_sent(
    notification: Notification,
    clock: DayClock,
    settings: LearningSettings,
    runtime: RuntimeRepository,
) -> None:
    """Record a notification as sent for today.

    A morning brief sent after the reminder hour also counts as the evening
    reminder: the user has just been told everything the reminder would say.
    """
    today = clock.today()
    if notification is Notification.MORNING:
        runtime.mark_done(RuntimeRepository.LAST_NOTIFIED_ON, today)
        if clock.now_local().hour >= settings.evening_reminder_hour:
            runtime.mark_done(RuntimeRepository.LAST_REMINDER_ON, today)
    else:
        runtime.mark_done(RuntimeRepository.LAST_REMINDER_ON, today)
