"""The learning engine's persistence layer.

The invariants tested here are the ones the rest of the engine takes for
granted and would not notice breaking: a word in two of a plan's lists appears
once, a card introduced today is not due today, a repeated Telegram tap is
recognised, and a setting written by the desktop UI is what the bot thread
reads back.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.settings import DEFAULT_SETTINGS, Setting
from lexitrack.models.source import Source
from lexitrack.models.srs import CardState, Channel, Rating, ReviewLogEntry
from lexitrack.repositories import (
    CardRepository,
    ListRepository,
    PlanRepository,
    RuntimeRepository,
    SessionRepository,
    SettingsRepository,
    SourceRepository,
    WordRepository,
)

from .conftest import entry


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))


@pytest.fixture
def words(database: Database) -> dict[str, int]:
    """Eight words across CEFR levels, imported as a real source would."""
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    entries = [
        entry("apple", cefr_level="A1", definition="a apple"),
        entry("bridge", cefr_level="A2", definition="a bridge"),
        entry("candle", cefr_level="B1", definition="a candle"),
        entry("dagger", cefr_level="B2", definition="a dagger"),
        entry("effort", cefr_level="C1", definition="a effort"),
        entry("fabric", cefr_level="A1", definition="a fabric"),
        entry("gadget", definition="a gadget"),
        entry("hazard", cefr_level="A2", definition="a hazard"),
    ]
    WordRepository(database).add_entries(entries, source.id)
    rows = database.connection.execute("SELECT id, normalized_word FROM words").fetchall()
    return {row["normalized_word"]: int(row["id"]) for row in rows}


class TestPlanRepository:
    def test_a_plan_is_the_deduplicated_union_of_its_lists(
        self, database: Database, words: dict[str, int]
    ) -> None:
        lists = ListRepository(database)
        first = lists.create("Core")
        second = lists.create("Extra")
        lists.add_words(first.id, [words["apple"], words["bridge"], words["candle"]])
        lists.add_words(second.id, [words["candle"], words["dagger"]])

        plans = PlanRepository(database)
        plan = plans.create("September", list_ids=[first.id, second.id])
        assert plan.is_active is True
        assert plans.counts(plan.id)["total"] == 4
        assert len(plans.word_ids(plan.id)) == 4

    def test_candidates_come_in_cefr_order(
        self, database: Database, words: dict[str, int]
    ) -> None:
        """A1 before C1, and a word with no level last."""
        lists = ListRepository(database)
        a_list = lists.create("Mixed")
        lists.add_words(
            a_list.id,
            [words["effort"], words["gadget"], words["apple"], words["bridge"]],
        )
        plans = PlanRepository(database)
        plan = plans.create("Ordered", list_ids=[a_list.id])
        candidates = plans.candidate_word_ids(plan.id, include_not_reviewed=True)
        assert candidates == [
            words["apple"],
            words["bridge"],
            words["effort"],
            words["gadget"],
        ]

    def test_a_freshly_imported_list_is_not_an_empty_plan(
        self, database: Database, words: dict[str, int]
    ) -> None:
        """Imported words are "not reviewed", not "unknown"."""
        lists = ListRepository(database)
        a_list = lists.create("Fresh")
        lists.add_words(a_list.id, list(words.values()))
        plans = PlanRepository(database)
        plan = plans.create("Fresh plan", list_ids=[a_list.id])
        assert plans.candidate_count(plan.id, include_not_reviewed=False) == 0
        assert plans.candidate_count(plan.id, include_not_reviewed=True) == len(words)

    def test_a_word_without_a_definition_is_not_offered(
        self, database: Database, words: dict[str, int]
    ) -> None:
        """Both questions are built on the definition: without one, a word waits."""
        lists = ListRepository(database)
        a_list = lists.create("Bare")
        lists.add_words(a_list.id, list(words.values()))
        database.connection.execute(
            "UPDATE word_sources SET definition = NULL WHERE word_id = ?", (words["apple"],)
        )
        plans = PlanRepository(database)
        plan = plans.create("Bare plan", list_ids=[a_list.id])
        offered = plans.candidate_word_ids(plan.id, include_not_reviewed=True)
        assert words["apple"] not in offered
        outlook = plans.outlook(plan.id, include_not_reviewed=True)
        assert outlook.to_introduce == len(words) - 1
        assert outlook.without_definition == 1

    def test_introduced_words_leave_the_candidate_pool(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        """There is no pointer to keep in step — the card *is* the pointer."""
        lists = ListRepository(database)
        a_list = lists.create("Pool")
        lists.add_words(a_list.id, list(words.values()))
        plans = PlanRepository(database)
        plan = plans.create("Pool plan", list_ids=[a_list.id])
        cards = CardRepository(database)

        first_three = plans.candidate_word_ids(plan.id, limit=3, include_not_reviewed=True)
        cards.introduce(
            first_three,
            plan_id=plan.id,
            now=clock.now_utc(),
            local_date=clock.today(),
            due_at=clock.next_day_start(),
        )
        remaining = plans.candidate_word_ids(plan.id, include_not_reviewed=True)
        assert set(first_three).isdisjoint(remaining)
        assert len(remaining) == len(words) - 3

    def test_exactly_one_plan_is_active_and_the_setting_agrees(
        self, database: Database
    ) -> None:
        """The bot thread reads the setting, not the table, so they must match."""
        plans = PlanRepository(database)
        settings = SettingsRepository(database)
        first = plans.create("One")
        second = plans.create("Two")
        assert plans.active().id == second.id
        assert settings.get(Setting.ACTIVE_PLAN_ID) == str(second.id)

        plans.set_active(first.id)
        assert plans.active().id == first.id
        assert settings.load().active_plan_id == first.id
        active = [plan for plan in plans.list_all() if plan.is_active]
        assert len(active) == 1

    def test_deleting_a_plan_leaves_words_and_cards_alone(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        """A plan is a scope, not an owner."""
        lists = ListRepository(database)
        a_list = lists.create("Kept")
        lists.add_words(a_list.id, list(words.values()))
        plans = PlanRepository(database)
        plan = plans.create("Temporary", list_ids=[a_list.id])
        cards = CardRepository(database)
        cards.introduce(
            [words["apple"]],
            plan_id=plan.id,
            now=clock.now_utc(),
            local_date=clock.today(),
            due_at=clock.next_day_start(),
        )
        plans.delete(plan.id)
        assert cards.get(words["apple"]) is not None
        assert database.connection.execute("SELECT COUNT(*) AS n FROM words").fetchone()["n"] == 8


class TestCardRepository:
    def introduce_all(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> CardRepository:
        cards = CardRepository(database)
        cards.introduce(
            list(words.values()),
            plan_id=None,
            now=clock.now_utc(),
            local_date=clock.today(),
            due_at=clock.next_day_start(),
        )
        return cards

    def test_a_card_introduced_today_is_not_due_today(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        """The invariant the whole daily rhythm rests on."""
        cards = self.introduce_all(database, words, clock)
        ids = list(words.values())
        clock.advance(hours=15)  # late the same evening
        assert cards.due_cards(ids, now=clock.now_utc(), before_local_date=clock.today()) == []

        clock.advance_to_day_start(1)
        due = cards.due_cards(ids, now=clock.now_utc(), before_local_date=clock.today())
        assert len(due) == len(ids)

    def test_a_second_confirmation_introduces_nothing(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        """A re-sent Telegram message must not reset yesterday's cards."""
        cards = self.introduce_all(database, words, clock)
        before = cards.get(words["apple"])
        clock.advance(hours=3)
        assert cards.introduce(
            list(words.values()),
            plan_id=None,
            now=clock.now_utc(),
            local_date=clock.today(),
            due_at=clock.next_day_start(),
        ) == []
        assert cards.get(words["apple"]) == before

    def test_struggling_cards_are_offered_first(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        cards = self.introduce_all(database, words, clock)
        clock.advance_to_day_start(1)
        weak = cards.get(words["effort"])
        cards.save(replace(weak, needs_relearning=True, lapse_count=3))
        due = cards.due_cards(
            list(words.values()), now=clock.now_utc(), before_local_date=clock.today()
        )
        assert due[0].word_id == words["effort"]
        assert [card.word_id for card in cards.struggling(list(words.values()))] == [
            words["effort"]
        ]

    def test_due_timestamps_are_returned_for_the_service_to_bucket(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        """Local days are the clock's job, so the repository hands back instants."""
        cards = self.introduce_all(database, words, clock)
        card = cards.get(words["apple"])
        cards.save(replace(card, due_at=clock.day_start("2026-09-21")))
        moments = cards.due_timestamps(list(words.values()))
        assert len(moments) == len(words)
        assert moments == sorted(moments)
        assert [clock.local_date(moment) for moment in moments].count("2026-09-18") == 7
        assert clock.local_date(moments[-1]) == "2026-09-21"

    def test_a_horizon_leaves_out_distant_cards(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        cards = self.introduce_all(database, words, clock)
        cards.save(replace(cards.get(words["apple"]), due_at=clock.day_start("2026-12-01")))
        moments = cards.due_timestamps(
            list(words.values()), until=clock.day_start("2026-09-25")
        )
        assert len(moments) == len(words) - 1

    def test_a_manual_known_archives_the_card_without_losing_it(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        cards = self.introduce_all(database, words, clock)
        assert cards.set_state([words["apple"]], CardState.ARCHIVED) == 1
        clock.advance_to_day_start(1)
        due = cards.due_cards(
            list(words.values()), now=clock.now_utc(), before_local_date=clock.today()
        )
        assert words["apple"] not in {card.word_id for card in due}
        assert cards.get(words["apple"]).state is CardState.ARCHIVED

    def test_a_log_entry_records_what_happened(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        cards = self.introduce_all(database, words, clock)
        clock.advance_to_day_start(1)
        cards.log(
            ReviewLogEntry(
                word_id=words["apple"],
                reviewed_at=clock.now_utc(),
                reviewed_on=clock.today(),
                rating=Rating.HARD,
                channel=Channel.TELEGRAM,
                state_before=CardState.INTRODUCED,
                state_after=CardState.LEARNING,
            )
        )
        logs = cards.logs_on(clock.today())
        assert len(logs) == 1
        assert logs[0].rating is Rating.HARD
        assert logs[0].channel is Channel.TELEGRAM
        assert cards.rating_counts()[int(Rating.HARD)] == 1
        assert cards.reviews_per_day()[clock.today()] == 1

    def test_deleting_a_word_takes_its_card_with_it(
        self, database: Database, words: dict[str, int], clock: FrozenClock
    ) -> None:
        """A card without its word would be a row nothing can ever show."""
        cards = self.introduce_all(database, words, clock)
        database.connection.execute("DELETE FROM words WHERE id = ?", (words["apple"],))
        database.connection.commit()
        assert cards.get(words["apple"]) is None


class TestSessionRepository:
    def test_a_session_tracks_progress_and_closes(
        self, database: Database, clock: FrozenClock
    ) -> None:
        sessions = SessionRepository(database)
        session = sessions.start(
            channel=Channel.TELEGRAM,
            now=clock.now_utc(),
            local_date=clock.today(),
            planned_count=12,
            chat_id="4242",
        )
        assert session.is_open is True
        assert session.remaining == 12

        session = sessions.update(session.id, message_id="99", done_increment=3)
        assert session.done_count == 3
        assert session.message_id == "99"
        assert session.remaining == 9

        session = sessions.finish(session.id, clock.now_utc())
        assert session.is_open is False
        assert session.current_word_id is None

    def test_an_open_telegram_session_is_found_again_after_a_restart(
        self, database: Database, clock: FrozenClock
    ) -> None:
        """So the app edits the waiting message instead of sending a second one."""
        sessions = SessionRepository(database)
        started = sessions.start(
            channel=Channel.TELEGRAM, now=clock.now_utc(), local_date=clock.today()
        )
        sessions.start(channel=Channel.DESKTOP, now=clock.now_utc(), local_date=clock.today())
        found = sessions.open_session(Channel.TELEGRAM)
        assert found is not None and found.id == started.id

    def test_yesterdays_session_is_closed_rather_than_resumed(
        self, database: Database, clock: FrozenClock
    ) -> None:
        sessions = SessionRepository(database)
        sessions.start(
            channel=Channel.TELEGRAM, now=clock.now_utc(), local_date=clock.today()
        )
        clock.advance_to_day_start(1)
        assert sessions.finish_stale(clock.today(), clock.now_utc()) == 1
        assert sessions.open_session(Channel.TELEGRAM) is None

    def test_a_repeated_telegram_tap_is_only_handled_once(
        self, database: Database
    ) -> None:
        """Telegram re-delivers a callback whenever it is unsure it arrived."""
        sessions = SessionRepository(database)
        assert sessions.claim_update("cb:1234:good") is True
        assert sessions.claim_update("cb:1234:good") is False
        assert sessions.was_handled("cb:1234:good") is True
        assert sessions.was_handled("cb:1234:again") is False
        assert sessions.claim_update("  ") is False

    def test_old_idempotency_keys_are_pruned(self, database: Database) -> None:
        sessions = SessionRepository(database)
        sessions.claim_update("old")
        database.connection.execute(
            "UPDATE telegram_updates SET handled_at = datetime('now', '-30 days')"
        )
        database.connection.commit()
        sessions.claim_update("fresh")
        assert sessions.prune_updates(keep_days=14) == 1
        assert sessions.was_handled("fresh") is True
        assert sessions.was_handled("old") is False


class TestSettingsRepository:
    def test_defaults_are_readable_before_anything_is_written(
        self, database: Database
    ) -> None:
        settings = SettingsRepository(database)
        loaded = settings.load()
        assert loaded.new_words_per_day == 25
        assert loaded.review_capacity_per_day == 250
        assert loaded.notify_hour == 6
        assert loaded.developer_mode is False

    def test_a_change_is_visible_to_the_next_reader(self, database: Database) -> None:
        """The desktop UI writes; the Telegram thread reads the same row."""
        settings = SettingsRepository(database)
        settings.set(Setting.NEW_WORDS_PER_DAY, 10)
        settings.set(Setting.TELEGRAM_ENABLED, True)
        reader = SettingsRepository(database)
        assert reader.load().new_words_per_day == 10
        assert reader.load().telegram_enabled is True

    def test_several_settings_are_written_together(self, database: Database) -> None:
        settings = SettingsRepository(database)
        settings.set_many(
            {
                Setting.NEW_WORDS_PER_DAY: 15,
                Setting.REVIEW_CAPACITY_PER_DAY: 120,
                Setting.DESIRED_RETENTION: 0.85,
            }
        )
        loaded = settings.load()
        assert (loaded.new_words_per_day, loaded.review_capacity_per_day) == (15, 120)
        assert loaded.desired_retention == 0.85

    def test_a_nonsense_value_falls_back_to_its_default(self, database: Database) -> None:
        """A hand-edited database must not stop the app from starting."""
        settings = SettingsRepository(database)
        settings.set(Setting.NEW_WORDS_PER_DAY, "twenty-five")
        assert settings.load().new_words_per_day == 25

    def test_reset_keeps_the_active_plan(self, database: Database) -> None:
        """The plan is a fact about the user's data, not a preference."""
        plans = PlanRepository(database)
        plan = plans.create("Kept plan")
        settings = SettingsRepository(database)
        settings.set(Setting.NEW_WORDS_PER_DAY, 40)
        settings.reset()
        assert settings.load().new_words_per_day == int(DEFAULT_SETTINGS["new_words_per_day"])
        assert settings.load().active_plan_id == plan.id

    def test_an_unknown_key_is_reported_not_deleted(self, database: Database) -> None:
        """Downgrading must not destroy a newer version's settings."""
        database.connection.execute(
            "INSERT INTO app_settings (key, value) VALUES ('future_option', 'on')"
        )
        database.connection.commit()
        settings = SettingsRepository(database)
        assert settings.unknown_keys() == ["future_option"]
        settings.reset()
        assert settings.get("future_option") == "on"


class TestRuntimeRepository:
    def test_a_daily_action_is_claimed_once(self, database: Database, clock: FrozenClock) -> None:
        """A missed morning message caught up at 09:00 must not fire again."""
        runtime = RuntimeRepository(database)
        assert runtime.mark_done(runtime.LAST_NOTIFIED_ON, clock.today()) is True
        assert runtime.mark_done(runtime.LAST_NOTIFIED_ON, clock.today()) is False
        assert runtime.was_done(runtime.LAST_NOTIFIED_ON, clock.today()) is True

        clock.advance_to_day_start(1)
        assert runtime.was_done(runtime.LAST_NOTIFIED_ON, clock.today()) is False
        assert runtime.mark_done(runtime.LAST_NOTIFIED_ON, clock.today()) is True

    def test_a_first_run_has_missed_nothing(self, database: Database, clock: FrozenClock) -> None:
        runtime = RuntimeRepository(database)
        assert runtime.last_seen() is None
        assert runtime.missed_days(clock.now_utc()) == 0

    def test_downtime_is_measured_in_days(self, database: Database, clock: FrozenClock) -> None:
        runtime = RuntimeRepository(database)
        runtime.touch(clock.now_utc())
        assert runtime.last_seen() == clock.now_utc()
        clock.advance(days=5)
        assert runtime.missed_days(clock.now_utc()) == 5

    def test_the_telegram_offset_and_chat_survive_a_restart(self, database: Database) -> None:
        runtime = RuntimeRepository(database)
        assert runtime.telegram_offset() is None
        runtime.set_telegram_offset(918273)
        runtime.set_chat_id(4242)
        reader = RuntimeRepository(database)
        assert reader.telegram_offset() == 918273
        assert reader.chat_id() == "4242"
