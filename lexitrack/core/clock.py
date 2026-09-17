"""The clock the learning engine measures days with.

Everything in the engine turns on one question: *which day is it?* The answer
has to be the same for the morning Telegram message, the review queue and the
statistics, and it has to be controllable in tests — a scheduler that can only
be observed by waiting until tomorrow is not testable at all.

Three decisions worth knowing:

* **Timestamps are stored in UTC, days are computed locally.** A card's
  ``due_at`` is a UTC instant; its ``introduced_on`` is a local date string.
  Storing only the instant would make "introduced today" depend on where the
  reader is; storing only the date would lose the ordering within a day.
* **The day boundary is configurable but defaults to midnight.** The user
  wanted the day to end at 00:00 and the morning message at 06:00 — two
  different things, and conflating them would have made a 6 a.m. review count
  as yesterday's.
* **The zone is resolved without requiring tzdata.** Windows ships no zone
  database, and Türkiye has been on a fixed UTC+3 since 2016 with no DST, so a
  fixed offset is exact rather than an approximation. ``zoneinfo`` is still
  preferred when it is available, which is what makes any other zone work.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo

#: Zones this module can serve without a system time zone database, because
#: they have no daylight saving. The offset is in hours east of UTC.
_FIXED_OFFSETS: dict[str, int] = {
    "Europe/Istanbul": 3,
    "Etc/UTC": 0,
    "UTC": 0,
}

DEFAULT_TIMEZONE = "Europe/Istanbul"


def resolve_timezone(name: str) -> tzinfo:
    """Return a usable ``tzinfo`` for a zone name.

    Falls back to a fixed offset rather than raising: a missing zone database
    must not stop the application from starting, and for the zone the user
    actually lives in the fallback is not a compromise.
    """
    key = (name or "").strip() or DEFAULT_TIMEZONE
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(key)
    except Exception:
        hours = _FIXED_OFFSETS.get(key)
        if hours is None:
            hours = _FIXED_OFFSETS[DEFAULT_TIMEZONE]
        return timezone(timedelta(hours=hours), key)


class DayClock:
    """Tells the engine the time, and which learning day it belongs to.

    Inject one instance everywhere. Tests build a :class:`FrozenClock` with
    the same interface and can then run a year of study in a second.
    """

    def __init__(self, timezone_name: str = DEFAULT_TIMEZONE, day_start_hour: int = 0) -> None:
        self._timezone_name = (timezone_name or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
        self._tz = resolve_timezone(self._timezone_name)
        self._day_start_hour = min(max(int(day_start_hour), 0), 23)

    # -- configuration -----------------------------------------------------

    @property
    def timezone_name(self) -> str:
        return self._timezone_name

    @property
    def day_start_hour(self) -> int:
        return self._day_start_hour

    @property
    def tzinfo(self) -> tzinfo:
        return self._tz

    def with_settings(self, timezone_name: str, day_start_hour: int) -> DayClock:
        """A clock like this one but configured differently."""
        return type(self)(timezone_name, day_start_hour)

    # -- now ---------------------------------------------------------------

    def now_utc(self) -> datetime:
        """The current instant, in UTC, with no microseconds.

        Microseconds are dropped because timestamps round-trip through
        ``isoformat(timespec="seconds")`` in the database; keeping them here
        would make a value read back compare unequal to the one written.
        """
        return datetime.now(UTC).replace(microsecond=0)

    def now_local(self) -> datetime:
        return self.to_local(self.now_utc())

    def today(self) -> str:
        """The current learning day, as ``YYYY-MM-DD``."""
        return self.local_date(self.now_utc())

    # -- conversion --------------------------------------------------------

    def to_local(self, moment: datetime) -> datetime:
        return _aware(moment).astimezone(self._tz)

    def to_utc(self, moment: datetime) -> datetime:
        return _aware(moment).astimezone(UTC).replace(microsecond=0)

    def local_date(self, moment: datetime) -> str:
        """Which learning day an instant belongs to.

        With a day start of 04:00, 02:30 on the 18th is still the 17th. This
        is the only place that shift is applied, so no caller has to remember
        it.
        """
        local = self.to_local(moment)
        if self._day_start_hour and local.hour < self._day_start_hour:
            local -= timedelta(days=1)
        return local.date().isoformat()

    # -- boundaries --------------------------------------------------------

    def day_start(self, day: str | date | None = None) -> datetime:
        """The UTC instant a learning day begins."""
        return self.to_utc(
            datetime.combine(
                _as_date(day) if day else self.local_today_date(),
                time(hour=self._day_start_hour),
                tzinfo=self._tz,
            )
        )

    def next_day_start(self, moment: datetime | None = None) -> datetime:
        """The UTC instant the *next* learning day begins.

        This is when a word introduced today becomes due: not "in 24 hours",
        which would make the first review time depend on when the user got
        round to confirming.
        """
        reference = moment or self.now_utc()
        day = _as_date(self.local_date(reference))
        return self.day_start(day + timedelta(days=1))

    def at_hour(self, hour: int, day: str | date | None = None) -> datetime:
        """A given local hour of a learning day, as a UTC instant.

        Used for the 06:00 briefing and the 21:00 reminder. The hour is a
        wall-clock hour on the *calendar* date the learning day starts on, so
        a day that starts at 04:00 still notifies at 06:00 that morning.
        """
        target = _as_date(day) if day else _as_date(self.local_date(self.now_utc()))
        return self.to_utc(
            datetime.combine(target, time(hour=min(max(int(hour), 0), 23)), tzinfo=self._tz)
        )

    def local_today_date(self) -> date:
        return _as_date(self.today())

    # -- arithmetic --------------------------------------------------------

    def days_between(self, earlier: datetime, later: datetime) -> int:
        """Whole learning days from one instant to another, never negative."""
        first = _as_date(self.local_date(earlier))
        second = _as_date(self.local_date(later))
        return max((second - first).days, 0)

    def shift_days(self, days: int, moment: datetime | None = None) -> str:
        """The learning day ``days`` away from an instant."""
        reference = moment or self.now_utc()
        return (_as_date(self.local_date(reference)) + timedelta(days=int(days))).isoformat()


class FrozenClock(DayClock):
    """A clock that stays where it is put, for tests and the simulator.

    Deliberately a subclass rather than a protocol: anything that works with
    the real clock works with this one, including code that reaches for
    :meth:`day_start` or :meth:`at_hour`.
    """

    def __init__(
        self,
        moment: datetime,
        timezone_name: str = DEFAULT_TIMEZONE,
        day_start_hour: int = 0,
    ) -> None:
        super().__init__(timezone_name, day_start_hour)
        self._moment = _aware(moment).astimezone(UTC).replace(microsecond=0)

    def now_utc(self) -> datetime:
        return self._moment

    def set(self, moment: datetime) -> None:
        self._moment = _aware(moment).astimezone(UTC).replace(microsecond=0)

    def advance(self, *, days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
        self._moment += timedelta(days=days, hours=hours, minutes=minutes)
        return self._moment

    def advance_to_day_start(self, days: int = 1) -> datetime:
        """Jump to the start of a later learning day."""
        day = _as_date(self.local_date(self._moment)) + timedelta(days=max(int(days), 1))
        self._moment = self.day_start(day)
        return self._moment

    def with_settings(self, timezone_name: str, day_start_hour: int) -> FrozenClock:
        return FrozenClock(self._moment, timezone_name, day_start_hour)


# -- storage format --------------------------------------------------------
#
# One format for every timestamp in the database: UTC, to the second, with no
# offset suffix. The repositories compare ``due_at`` as text in SQL, so a
# mixture of "…T00:00:00" and "…T00:00:00+00:00" would sort wrongly and a card
# could be judged not due when it is. Encoding is therefore not each
# repository's business but this module's.


def to_storage(moment: datetime | None) -> str | None:
    """Encode an instant the way the database stores it."""
    if moment is None:
        return None
    return (
        _aware(moment)
        .astimezone(UTC)
        .replace(tzinfo=None, microsecond=0)
        .isoformat(timespec="seconds")
    )


def from_storage(value: str | None) -> datetime | None:
    """Decode a stored timestamp back into an aware UTC instant."""
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value))).astimezone(UTC)
    except ValueError:
        return None


# -- helpers ---------------------------------------------------------------


def _aware(moment: datetime) -> datetime:
    """Treat a naive datetime as UTC.

    Naive values only reach here from the database, where everything was
    written in UTC, so this is a decoding rule rather than a guess.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
