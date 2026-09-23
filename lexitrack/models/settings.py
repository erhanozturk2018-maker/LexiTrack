"""Settings the learning engine reads, with their defaults.

These live in the database (``app_settings``), not in ``QSettings``, because
the Telegram thread and the desktop UI must agree on them and a setting that
only exists in the Windows registry is invisible to anything that is not the
Qt application.

Purely presentational preferences — theme, window geometry, which list was
last open — stay in ``QSettings``; they are not here.

Defaults are chosen from the measured workload of 25 new words a day, not
from habit: see ``docs/LEARNING_ENGINE.md`` §H.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum

#: Every setting the engine understands, with its default as text.
DEFAULT_SETTINGS: dict[str, str] = {
    # -- daily workload
    "new_words_per_day": "25",
    # Words are introduced from the Unknown pool. A freshly imported list is
    # "not reviewed" rather than "unknown", so this lets those words in too.
    "new_words_include_not_reviewed": "false",
    "review_capacity_per_day": "250",
    # -- the day
    "day_start_hour": "0",
    "timezone": "Europe/Istanbul",
    "notify_hour": "6",
    "evening_reminder_hour": "21",
    # -- scheduling
    "desired_retention": "0.9",
    # Parameters fitted to the user's own reviews, as a JSON list of 21
    # numbers; empty means the published FSRS defaults. See optimizer.py.
    "fsrs_parameters": "",
    "mastery_stability_days": "21",
    "review_known_words": "true",
    # -- struggling words (leeches): any one of these is enough
    "leech_consecutive": "4",
    "leech_total_lapses": "8",
    "leech_weak_stability_days": "7",
    # -- study behaviour
    "hide_meaning_in_study": "true",
    # -- clients
    "telegram_enabled": "false",
    # Sunday evening on Telegram: the week's answers, words learned, hard words.
    "weekly_summary": "true",
    # -- diagnostics: two switches, never one
    "developer_mode": "false",
    "debug_logging": "false",
    # -- set when a plan is created
    "active_plan_id": "",
}

_TRUE = {"true", "1", "yes", "on"}


class Setting(StrEnum):
    """Names of the settings, so callers do not pass raw strings around."""

    NEW_WORDS_PER_DAY = "new_words_per_day"
    NEW_WORDS_INCLUDE_NOT_REVIEWED = "new_words_include_not_reviewed"
    REVIEW_CAPACITY_PER_DAY = "review_capacity_per_day"
    DAY_START_HOUR = "day_start_hour"
    TIMEZONE = "timezone"
    NOTIFY_HOUR = "notify_hour"
    EVENING_REMINDER_HOUR = "evening_reminder_hour"
    DESIRED_RETENTION = "desired_retention"
    FSRS_PARAMETERS = "fsrs_parameters"
    MASTERY_STABILITY_DAYS = "mastery_stability_days"
    REVIEW_KNOWN_WORDS = "review_known_words"
    LEECH_CONSECUTIVE = "leech_consecutive"
    LEECH_TOTAL_LAPSES = "leech_total_lapses"
    LEECH_WEAK_STABILITY_DAYS = "leech_weak_stability_days"
    HIDE_MEANING_IN_STUDY = "hide_meaning_in_study"
    TELEGRAM_ENABLED = "telegram_enabled"
    WEEKLY_SUMMARY = "weekly_summary"
    DEVELOPER_MODE = "developer_mode"
    DEBUG_LOGGING = "debug_logging"
    ACTIVE_PLAN_ID = "active_plan_id"


@dataclass(frozen=True, slots=True)
class LearningSettings:
    """A snapshot of the settings, converted to the types callers want.

    Read once per operation rather than per row: the queue builder needs the
    same numbers for every card it considers.
    """

    new_words_per_day: int = 25
    new_words_include_not_reviewed: bool = False
    review_capacity_per_day: int = 250
    day_start_hour: int = 0
    timezone: str = "Europe/Istanbul"
    notify_hour: int = 6
    evening_reminder_hour: int = 21
    desired_retention: float = 0.9
    #: Fitted parameters, or None for the FSRS defaults.
    fsrs_parameters: tuple[float, ...] | None = None
    mastery_stability_days: float = 21.0
    review_known_words: bool = True
    leech_consecutive: int = 4
    leech_total_lapses: int = 8
    leech_weak_stability_days: float = 7.0
    hide_meaning_in_study: bool = True
    telegram_enabled: bool = False
    weekly_summary: bool = True
    developer_mode: bool = False
    debug_logging: bool = False
    active_plan_id: int | None = None

    @classmethod
    def from_values(cls, values: dict[str, str]) -> LearningSettings:
        """Build a snapshot, falling back to the default of any bad value.

        A setting edited by hand in the database must not stop the app from
        starting, so an unparsable value is logged by the caller and replaced
        by its default rather than raising.
        """
        merged = {**DEFAULT_SETTINGS, **values}

        def integer(key: str) -> int:
            try:
                return int(str(merged[key]).strip())
            except (TypeError, ValueError):
                return int(DEFAULT_SETTINGS[key])

        def number(key: str) -> float:
            try:
                return float(str(merged[key]).strip())
            except (TypeError, ValueError):
                return float(DEFAULT_SETTINGS[key])

        def flag(key: str) -> bool:
            return str(merged[key]).strip().casefold() in _TRUE

        plan = str(merged[Setting.ACTIVE_PLAN_ID]).strip()
        return cls(
            new_words_per_day=max(integer(Setting.NEW_WORDS_PER_DAY), 0),
            new_words_include_not_reviewed=flag(Setting.NEW_WORDS_INCLUDE_NOT_REVIEWED),
            review_capacity_per_day=max(integer(Setting.REVIEW_CAPACITY_PER_DAY), 0),
            day_start_hour=min(max(integer(Setting.DAY_START_HOUR), 0), 23),
            timezone=str(merged[Setting.TIMEZONE]).strip() or "Europe/Istanbul",
            notify_hour=min(max(integer(Setting.NOTIFY_HOUR), 0), 23),
            evening_reminder_hour=min(max(integer(Setting.EVENING_REMINDER_HOUR), 0), 23),
            desired_retention=min(max(number(Setting.DESIRED_RETENTION), 0.7), 0.99),
            fsrs_parameters=_parameters(merged[Setting.FSRS_PARAMETERS]),
            mastery_stability_days=max(number(Setting.MASTERY_STABILITY_DAYS), 1.0),
            review_known_words=flag(Setting.REVIEW_KNOWN_WORDS),
            leech_consecutive=max(integer(Setting.LEECH_CONSECUTIVE), 1),
            leech_total_lapses=max(integer(Setting.LEECH_TOTAL_LAPSES), 1),
            leech_weak_stability_days=max(number(Setting.LEECH_WEAK_STABILITY_DAYS), 0.0),
            hide_meaning_in_study=flag(Setting.HIDE_MEANING_IN_STUDY),
            telegram_enabled=flag(Setting.TELEGRAM_ENABLED),
            weekly_summary=flag(Setting.WEEKLY_SUMMARY),
            developer_mode=flag(Setting.DEVELOPER_MODE),
            debug_logging=flag(Setting.DEBUG_LOGGING),
            active_plan_id=int(plan) if plan.isdigit() else None,
        )


def _parameters(value: object) -> tuple[float, ...] | None:
    """Read stored FSRS parameters, or None when absent or unusable.

    Anything but a list of exactly 21 finite numbers falls back to the
    defaults rather than stopping the scheduler.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        numbers = tuple(float(item) for item in json.loads(text))
    except (TypeError, ValueError):
        return None
    if len(numbers) != 21 or not all(math.isfinite(n) for n in numbers):
        return None
    return numbers


def serialize(value: object) -> str:
    """Turn a Python value into the text stored in ``app_settings``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)
