"""The day boundary, which every other rule in the engine is measured against.

These tests are about one question with an awkward answer: when a card says
"introduced on 2026-09-17", what does that mean at 01:00 on the 18th? Getting
it wrong is not a visible crash — it silently shows the user yesterday's words
again, or hides today's — so the boundary is pinned down here rather than left
to be discovered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lexitrack.core.clock import (
    DEFAULT_TIMEZONE,
    DayClock,
    FrozenClock,
    from_storage,
    resolve_timezone,
    to_storage,
)


def test_istanbul_is_three_hours_ahead_without_a_zone_database() -> None:
    """Windows has no tzdata; the engine must still know what day it is."""
    clock = DayClock(DEFAULT_TIMEZONE)
    moment = datetime(2026, 9, 17, 21, 30, tzinfo=UTC)
    assert clock.to_local(moment).hour == 0
    assert clock.to_local(moment).date().isoformat() == "2026-09-18"


def test_an_unknown_zone_falls_back_instead_of_raising() -> None:
    """A bad value in the settings table must not stop the app from starting."""
    resolved = resolve_timezone("Mars/Olympus_Mons")
    assert resolved.utcoffset(datetime(2026, 9, 17)) in {
        timedelta(hours=3),
        None,
    }


def test_the_learning_day_turns_over_at_local_midnight() -> None:
    clock = DayClock(DEFAULT_TIMEZONE, day_start_hour=0)
    # 20:59 UTC is 23:59 in Istanbul: still the 17th.
    assert clock.local_date(datetime(2026, 9, 17, 20, 59, tzinfo=UTC)) == "2026-09-17"
    # One minute later it is the 18th.
    assert clock.local_date(datetime(2026, 9, 17, 21, 0, tzinfo=UTC)) == "2026-09-18"


def test_a_later_day_start_keeps_the_small_hours_on_the_previous_day() -> None:
    """With a 04:00 start, a 02:00 review belongs to the day before."""
    clock = DayClock(DEFAULT_TIMEZONE, day_start_hour=4)
    two_am_local = datetime(2026, 9, 18, 23, 0, tzinfo=UTC)  # 02:00 on the 19th
    assert clock.to_local(two_am_local).hour == 2
    assert clock.local_date(two_am_local) == "2026-09-18"


def test_next_day_start_is_the_first_moment_of_tomorrow() -> None:
    """This is when a word introduced today becomes due."""
    clock = DayClock(DEFAULT_TIMEZONE)
    now = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)
    due = clock.next_day_start(now)
    assert clock.local_date(due) == "2026-09-18"
    assert clock.to_local(due).hour == 0
    assert due > now


def test_at_hour_gives_the_local_briefing_time() -> None:
    """06:00 in Istanbul is 03:00 UTC, whatever the host machine thinks."""
    clock = DayClock(DEFAULT_TIMEZONE)
    briefing = clock.at_hour(6, "2026-09-18")
    assert briefing == datetime(2026, 9, 18, 3, 0, tzinfo=UTC)
    assert clock.to_local(briefing).hour == 6


def test_days_between_counts_days_not_hours() -> None:
    """23:00 to 01:00 is one day apart, not zero."""
    clock = DayClock(DEFAULT_TIMEZONE)
    late = datetime(2026, 9, 17, 20, 0, tzinfo=UTC)  # 23:00 local, the 17th
    early = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)  # 01:00 local, the 18th
    assert clock.days_between(late, early) == 1
    assert clock.days_between(early, late) == 0  # never negative


def test_storage_format_is_comparable_as_text() -> None:
    """The repositories compare ``due_at`` in SQL, so encoding must be uniform."""
    aware = datetime(2026, 9, 18, 0, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 18, 0, 0)
    assert to_storage(aware) == to_storage(naive) == "2026-09-18T00:00:00"
    # An Istanbul-local instant is stored as its UTC equivalent, not as itself.
    local = DayClock(DEFAULT_TIMEZONE).day_start("2026-09-18")
    assert to_storage(local) == "2026-09-17T21:00:00"
    assert to_storage(None) is None


def test_storage_round_trips_to_an_aware_instant() -> None:
    moment = datetime(2026, 9, 18, 7, 30, tzinfo=UTC)
    restored = from_storage(to_storage(moment))
    assert restored == moment
    assert restored is not None and restored.tzinfo is not None
    assert from_storage(None) is None
    assert from_storage("not a timestamp") is None


class TestFrozenClock:
    """The clock the tests and the simulator use."""

    def test_it_does_not_move_on_its_own(self) -> None:
        clock = FrozenClock(datetime(2026, 9, 17, 9, 0, tzinfo=UTC))
        assert clock.now_utc() == clock.now_utc()
        assert clock.today() == "2026-09-17"

    def test_advancing_to_a_day_start_lands_on_the_boundary(self) -> None:
        clock = FrozenClock(datetime(2026, 9, 17, 9, 0, tzinfo=UTC))
        clock.advance_to_day_start(1)
        assert clock.today() == "2026-09-18"
        assert clock.to_local(clock.now_utc()).hour == 0

    def test_a_year_of_days_can_be_walked_in_a_loop(self) -> None:
        """What makes the 400-day workload simulation possible at all."""
        clock = FrozenClock(datetime(2026, 1, 1, 6, 0, tzinfo=UTC))
        days = {clock.today()}
        for _ in range(365):
            clock.advance_to_day_start(1)
            days.add(clock.today())
        assert len(days) == 366
        assert clock.today() == "2027-01-01"
