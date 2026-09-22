"""The Telegram client, without Telegram.

:class:`BotCore` talks through an outbox; here the outbox is a list. What is
tested is the behaviour a phone user would notice: the right card, the right
day, one answer per tap, and silence towards anyone who is not the owner.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.settings import Setting
from lexitrack.models.source import Source
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import (
    ListRepository,
    RuntimeRepository,
    SettingsRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.learning_service import LearningService
from lexitrack.telegram import config as telegram_config
from lexitrack.telegram.config import load_config, read_env_file
from lexitrack.telegram.core import BotCore
from lexitrack.telegram.messages import (
    Message,
    answer_data,
    end_data,
    intro_data,
    parse_callback,
    undo_data,
)
from lexitrack.telegram.schedule import Notification, due_notifications, mark_sent

from .conftest import entry

OWNER = "4242"
STRANGER = "9999"


class FakeOutbox:
    """Records what the bot would have sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []
        self.edits: list[tuple[str, str, Message]] = []
        self.deleted: list[tuple[str, str]] = []
        self._next_id = 100

    async def send(self, chat_id: str, message: Message) -> str:
        self.sent.append((chat_id, message))
        self._next_id += 1
        return str(self._next_id)

    async def edit(self, chat_id: str, message_id: str, message: Message) -> None:
        self.edits.append((chat_id, message_id, message))

    async def delete(self, chat_id: str, message_id: str) -> None:
        self.deleted.append((chat_id, message_id))

    def last_id(self) -> str:
        return str(self._next_id)

    def last_text(self) -> str:
        texts = [m.text for _, m in self.sent] + [m.text for _, _, m in self.edits]
        return texts[-1] if texts else ""

    @staticmethod
    def callbacks(message: Message) -> list[str]:
        return [data for row in message.buttons for _label, data in row]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clock() -> FrozenClock:
    """07:00 on 17 September 2026 in Istanbul."""
    return FrozenClock(datetime(2026, 9, 17, 4, 0, tzinfo=UTC))


@pytest.fixture
def seeded(database: Database, clock: FrozenClock) -> Database:
    source = SourceRepository(database).upsert(
        Source(key="t", name="Test", parser_type="generic")
    )
    names = [f"{a}{b}word" for a in "abc" for b in "abcdefghijklmnopqrstuvwxyz"]
    result = WordRepository(database).add_entries(
        [entry(name, cefr_level="A1", definition=f"meaning of {name}") for name in names],
        source.id,
    )
    ids = list(result.word_ids)
    a_list = ListRepository(database).create("Words")
    ListRepository(database).add_words(a_list.id, ids)
    StateRepository(database).set_status_many(ids, ReviewStatus.UNKNOWN)
    LearningService(database, clock).create_plan("Plan", list_ids=[a_list.id])
    return database


@pytest.fixture
def outbox() -> FakeOutbox:
    return FakeOutbox()


@pytest.fixture
def bot(seeded: Database, outbox: FakeOutbox, clock: FrozenClock) -> BotCore:
    """A bot connected on an earlier day, so today's brief is still owed."""
    core = BotCore(seeded, outbox, clock=clock)
    run(core.command(OWNER, "/start"))
    RuntimeRepository(seeded).clear(RuntimeRepository.LAST_NOTIFIED_ON)
    outbox.sent.clear()
    return core


# -- configuration -------------------------------------------------------------


class TestConfig:
    def test_the_env_file_format(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "# comment\n"
            "LEXITRACK_TELEGRAM_TOKEN=\"123:abc\"\n"
            "export OTHER=value # trailing\n"
            "BROKEN LINE\n",
            encoding="utf-8",
        )
        values = read_env_file(env)
        assert values["LEXITRACK_TELEGRAM_TOKEN"] == "123:abc"
        assert values["OTHER"] == "value"
        assert "BROKEN LINE" not in values

    def test_a_missing_file_reads_as_empty(self, tmp_path: Path) -> None:
        assert read_env_file(tmp_path / "nope.env") == {}

    def test_the_environment_wins_over_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / ".env"
        env.write_text("LEXITRACK_TELEGRAM_TOKEN=from-file:1\n", encoding="utf-8")
        monkeypatch.setattr(telegram_config, "env_file_candidates", lambda: [env])
        monkeypatch.setenv("LEXITRACK_TELEGRAM_TOKEN", "from-env:2")
        assert load_config().token == "from-env:2"
        monkeypatch.delenv("LEXITRACK_TELEGRAM_TOKEN")
        loaded = load_config()
        assert loaded.token == "from-file:1"
        assert loaded.source == env

    def test_an_empty_template_means_no_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / ".env"
        env.write_text("LEXITRACK_TELEGRAM_TOKEN=\nLEXITRACK_TELEGRAM_CHAT_ID=\n", encoding="utf-8")
        monkeypatch.setattr(telegram_config, "env_file_candidates", lambda: [env])
        monkeypatch.delenv("LEXITRACK_TELEGRAM_TOKEN", raising=False)
        assert load_config().has_token is False

    def test_the_token_is_never_shown_whole(self) -> None:
        masked = telegram_config.TelegramConfig(token="123456:ABCDEFGHIJKLMNOP").masked_token
        assert masked.startswith("123456:")
        assert "ABCDEFGH" not in masked
        assert masked.endswith("MNOP")

    def test_the_token_is_not_a_database_setting(self, database: Database) -> None:
        """The database is what gets backed up and shared."""
        keys = SettingsRepository(database).all_values()
        assert not any("token" in key for key in keys)


# -- callback data -------------------------------------------------------------


class TestCallbackData:
    def test_round_trips(self) -> None:
        assert parse_callback(intro_data("2026-09-17")).local_date == "2026-09-17"
        parsed = parse_callback(answer_data("abc", 12, Rating.HARD))
        assert (parsed.session_id, parsed.word_id, parsed.rating) == ("abc", 12, Rating.HARD)
        assert parse_callback(end_data("abc")).action == "end"
        assert parse_callback("start").action == "start"

    @pytest.mark.parametrize(
        "data", ["", "intro:yesterday", "ans:x:y:3", "ans:s:1:9", "nonsense", "end"]
    )
    def test_malformed_data_is_ignored_not_an_error(self, data: str) -> None:
        assert parse_callback(data) is None

    def test_every_callback_fits_telegrams_64_bytes(self) -> None:
        longest = answer_data("f" * 32, 999_999_999, Rating.EASY)
        assert len(longest.encode()) <= 64


# -- ownership -----------------------------------------------------------------


class TestOwnership:
    def test_the_first_start_binds_the_chat(
        self, seeded: Database, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        core = BotCore(seeded, outbox, clock=clock)
        assert core.owner() is None
        run(core.command(OWNER, "/start"))
        assert core.owner() == OWNER
        assert RuntimeRepository(seeded).chat_id() == OWNER
        assert "connected" in outbox.sent[0][1].text
        assert "Good morning" in outbox.sent[1][1].text

    def test_a_stranger_gets_one_refusal_and_nothing_else(
        self, bot: BotCore, outbox: FakeOutbox
    ) -> None:
        run(bot.command(STRANGER, "/start"))
        run(bot.command(STRANGER, "/today"))
        assert [chat for chat, _ in outbox.sent] == [STRANGER, STRANGER]
        assert all("someone else" in m.text for _, m in outbox.sent)
        assert bot.owner() == OWNER

    def test_a_strangers_button_press_does_nothing(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        run(bot.callback(STRANGER, "1", intro_data(clock.today()), ""))
        assert outbox.edits == []
        assert bot.engine.daily_plan().introduced_today == ()

    def test_a_chat_named_in_env_cannot_be_taken_over(
        self, seeded: Database, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        core = BotCore(seeded, outbox, clock=clock, allowed_chat_id=OWNER)
        run(core.command(STRANGER, "/start"))
        assert core.owner() == OWNER
        assert "someone else" in outbox.sent[0][1].text


# -- the morning brief and new words -------------------------------------------


class TestNewWords:
    def test_the_brief_lists_the_words_with_their_meaning(
        self, bot: BotCore, outbox: FakeOutbox
    ) -> None:
        run(bot.command(OWNER, "/today"))
        text = outbox.sent[-1][1].text
        assert "25 new words" in text
        assert "meaning of aaword" in text
        assert intro_data("2026-09-17") in FakeOutbox.callbacks(outbox.sent[-1][1])

    def test_confirming_introduces_the_words_and_edits_the_message(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        run(bot.callback(OWNER, "55", intro_data(clock.today()), "<b>brief</b>"))
        assert len(bot.engine.daily_plan().introduced_today) == 25
        chat, message_id, edited = outbox.edits[-1]
        assert (chat, message_id) == (OWNER, "55")
        assert edited.text.startswith("<b>brief</b>")
        assert "25 new words added" in edited.text

    def test_confirming_twice_adds_nothing_the_second_time(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        run(bot.callback(OWNER, "55", intro_data(clock.today()), ""))
        run(bot.callback(OWNER, "55", intro_data(clock.today()), ""))
        assert len(bot.engine.daily_plan().introduced_today) == 25
        assert "already in" in outbox.edits[-1][2].text

    def test_yesterdays_button_does_not_confirm_todays_words(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        """The words on an old message are not the words offered today."""
        old = intro_data(clock.today())
        clock.advance_to_day_start(1)
        run(bot.callback(OWNER, "55", old, ""))
        assert bot.engine.daily_plan().introduced_today == ()
        assert "earlier day" in outbox.edits[-1][2].text

    def test_the_desktop_sees_what_the_phone_confirmed(
        self, bot: BotCore, seeded: Database, clock: FrozenClock
    ) -> None:
        """One database, two clients: no sync step in between."""
        run(bot.callback(OWNER, "55", intro_data(clock.today()), ""))
        desktop = LearningService(seeded, clock)
        assert len(desktop.daily_plan().introduced_today) == 25


# -- review sessions -----------------------------------------------------------


class TestReviews:
    @pytest.fixture
    def due(self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock) -> BotCore:
        run(bot.callback(OWNER, "55", intro_data(clock.today()), ""))
        clock.advance_to_day_start(1)
        clock.advance(hours=18)
        outbox.sent.clear()
        outbox.edits.clear()
        return bot

    def start(self, bot: BotCore, outbox: FakeOutbox) -> tuple[str, str, int]:
        run(bot.command(OWNER, "/review"))
        message = outbox.sent[-1][1]
        answer = next(d for d in FakeOutbox.callbacks(message) if d.startswith("ans:"))
        parsed = parse_callback(answer)
        return outbox.last_id(), parsed.session_id, parsed.word_id

    @staticmethod
    def next_word(outbox: FakeOutbox) -> int | None:
        """The word on the card now on screen, or None once the session ended."""
        answers = [d for d in FakeOutbox.callbacks(outbox.sent[-1][1]) if d.startswith("ans:")]
        return parse_callback(answers[0]).word_id if answers else None

    def test_a_session_shows_the_first_card_with_four_answers(
        self, due: BotCore, outbox: FakeOutbox
    ) -> None:
        self.start(due, outbox)
        message = outbox.sent[-1][1]
        assert "1 / 25" in message.text
        assert "<tg-spoiler>" in message.text, "the meaning starts hidden"
        labels = [label for row in message.buttons for label, _ in row]
        assert labels[:4] == ["Again", "Hard", "Good", "Easy"]

    def test_an_answer_is_recorded_and_the_next_card_replaces_it(
        self, due: BotCore, outbox: FakeOutbox
    ) -> None:
        message_id, session, word = self.start(due, outbox)
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.GOOD), ""))
        assert due.engine.rating_counts()[int(Rating.GOOD)] == 1
        card = outbox.sent[-1][1]
        assert "2 / 25" in card.text
        assert "Good" in card.text and "back" in card.text
        assert outbox.deleted == [(OWNER, message_id)]
        assert due.engine.session(session).message_id == outbox.last_id()

    def test_every_card_is_a_new_message_so_its_meaning_starts_hidden(
        self, due: BotCore, outbox: FakeOutbox
    ) -> None:
        """Telegram keeps a tapped spoiler open through edits of that message."""
        message_id, session, word = self.start(due, outbox)
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.GOOD), ""))
        assert outbox.edits == [], "a card is never edited into the next one"
        assert "<tg-spoiler>" in outbox.sent[-1][1].text
        assert outbox.last_id() != message_id

    def test_a_double_tap_counts_once(self, due: BotCore, outbox: FakeOutbox) -> None:
        """Telegram re-delivers a tap it was unsure about."""
        message_id, session, word = self.start(due, outbox)
        data = answer_data(session, word, Rating.GOOD)
        run(due.callback(OWNER, message_id, data, ""))
        run(due.callback(OWNER, message_id, data, ""))
        assert sum(due.engine.rating_counts().values()) == 1

    def test_a_tap_on_an_old_card_is_ignored(self, due: BotCore, outbox: FakeOutbox) -> None:
        message_id, session, word = self.start(due, outbox)
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.GOOD), ""))
        # The first card's Again button, pressed after the card moved on.
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.AGAIN), ""))
        counts = due.engine.rating_counts()
        assert counts[int(Rating.AGAIN)] == 0
        assert counts[int(Rating.GOOD)] == 1

    def test_undo_appears_from_the_second_card_and_puts_the_word_back(
        self, due: BotCore, outbox: FakeOutbox
    ) -> None:
        message_id, session, word = self.start(due, outbox)
        assert not any(
            d.startswith("undo:") for d in FakeOutbox.callbacks(outbox.sent[-1][1])
        ), "nothing to take back on the first card"
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.EASY), ""))
        second_id = outbox.last_id()
        assert undo_data(session) in FakeOutbox.callbacks(outbox.sent[-1][1])
        run(due.callback(OWNER, second_id, undo_data(session), ""))
        card = outbox.sent[-1][1]
        assert "Took back your answer" in card.text
        assert "1 / 25" in card.text
        assert "<tg-spoiler>" in card.text, "a new message, so hidden again"
        assert (OWNER, second_id) in outbox.deleted
        assert sum(due.engine.rating_counts().values()) == 0
        # The same word can be answered again, even with the same button.
        run(due.callback(OWNER, outbox.last_id(), answer_data(session, word, Rating.GOOD), ""))
        assert due.engine.rating_counts()[int(Rating.GOOD)] == 1

    def test_a_second_undo_does_nothing(self, due: BotCore, outbox: FakeOutbox) -> None:
        message_id, session, word = self.start(due, outbox)
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.GOOD), ""))
        run(due.callback(OWNER, outbox.last_id(), undo_data(session), ""))
        sent = len(outbox.sent)
        run(due.callback(OWNER, outbox.last_id(), undo_data(session), ""))
        assert len(outbox.sent) == sent

    def test_the_last_answer_ends_with_a_summary(self, due: BotCore, outbox: FakeOutbox) -> None:
        message_id, session, word = self.start(due, outbox)
        for _ in range(25):
            sent_before = len(outbox.sent)
            run(due.callback(OWNER, message_id, answer_data(session, word, Rating.GOOD), ""))
            if len(outbox.sent) == sent_before:
                break
            message_id, word = outbox.last_id(), self.next_word(outbox)
        _, summary_id, summary = outbox.edits[-1]
        assert summary_id == message_id, "the last card becomes the summary"
        text = summary.text
        assert "Session finished" in text
        assert "25 reviewed" in text
        assert due.engine.open_session(due.engine.session(session).channel) is None

    def test_stop_here_keeps_what_was_answered(self, due: BotCore, outbox: FakeOutbox) -> None:
        message_id, session, word = self.start(due, outbox)
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.HARD), ""))
        run(due.callback(OWNER, outbox.last_id(), end_data(session), ""))
        text = outbox.edits[-1][2].text
        assert "Stopped" in text
        assert "1 reviewed" in text
        assert "24 still due" in text

    def test_starting_again_closes_the_previous_session(
        self, due: BotCore, outbox: FakeOutbox
    ) -> None:
        _, first, _ = self.start(due, outbox)
        _, second, _ = self.start(due, outbox)
        assert first != second
        assert due.engine.session(first).is_open is False

    def test_nothing_due_says_so_instead_of_an_empty_session(
        self, bot: BotCore, outbox: FakeOutbox
    ) -> None:
        run(bot.command(OWNER, "/review"))
        assert "Nothing is due" in outbox.sent[-1][1].text

    def test_the_desktop_study_page_counts_phone_answers(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        message_id, session, word = self.start(due, outbox)
        run(due.callback(OWNER, message_id, answer_data(session, word, Rating.GOOD), ""))
        desktop = LearningService(seeded, clock).daily_plan()
        assert desktop.due_count == 24
        assert desktop.reviews_done_today == 1

    def test_a_visible_meaning_when_the_setting_says_so(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database
    ) -> None:
        SettingsRepository(seeded).set(Setting.HIDE_MEANING_IN_STUDY, False)
        self.start(due, outbox)
        assert "<tg-spoiler>" not in outbox.sent[-1][1].text


# -- notifications -------------------------------------------------------------


class TestNotifications:
    def test_nothing_before_the_morning_hour(self, bot: BotCore, clock: FrozenClock) -> None:
        clock.set(datetime(2026, 9, 17, 2, 0, tzinfo=UTC))  # 05:00 local
        assert run(bot.tick()) == []

    def test_the_brief_goes_once_after_the_hour(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        clock.set(datetime(2026, 9, 17, 3, 5, tzinfo=UTC))  # 06:05 local
        assert run(bot.tick()) == [Notification.MORNING]
        assert run(bot.tick()) == []
        assert len(outbox.sent) == 1
        assert "Good morning" in outbox.sent[0][1].text

    def test_a_late_start_sends_the_brief_late_not_never(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        clock.set(datetime(2026, 9, 17, 9, 0, tzinfo=UTC))  # 12:00 local
        assert run(bot.tick()) == [Notification.MORNING]

    def test_five_days_off_means_one_brief_not_five(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        clock.set(datetime(2026, 9, 17, 3, 5, tzinfo=UTC))
        run(bot.tick())
        clock.advance(days=5)
        run(bot.tick())
        run(bot.tick())
        assert len(outbox.sent) == 2

    def test_the_evening_reminder_only_when_work_is_left(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        clock.set(datetime(2026, 9, 17, 3, 5, tzinfo=UTC))
        run(bot.tick())
        clock.set(datetime(2026, 9, 17, 18, 5, tzinfo=UTC))  # 21:05 local
        assert run(bot.tick()) == [Notification.EVENING]
        assert "Still to do" in outbox.sent[-1][1].text

    def test_a_finished_day_gets_no_reminder(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        clock.set(datetime(2026, 9, 17, 3, 5, tzinfo=UTC))
        run(bot.tick())
        run(bot.callback(OWNER, "1", intro_data(clock.today()), ""))
        clock.set(datetime(2026, 9, 17, 18, 5, tzinfo=UTC))
        sent_before = len(outbox.sent)
        assert run(bot.tick()) == [Notification.EVENING]
        assert len(outbox.sent) == sent_before, "claimed for the day, but silent"
        assert run(bot.tick()) == []

    def test_a_brief_after_the_reminder_hour_replaces_the_reminder(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        clock.set(datetime(2026, 9, 17, 19, 0, tzinfo=UTC))  # 22:00 local
        assert run(bot.tick()) == [Notification.MORNING]
        assert run(bot.tick()) == []
        assert len(outbox.sent) == 1

    def test_no_owner_means_no_notifications(
        self, seeded: Database, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        core = BotCore(seeded, outbox, clock=clock)
        clock.set(datetime(2026, 9, 17, 3, 5, tzinfo=UTC))
        assert run(core.tick()) == []
        assert outbox.sent == []

    def test_the_hours_are_settings(
        self, bot: BotCore, seeded: Database, clock: FrozenClock
    ) -> None:
        SettingsRepository(seeded).set(Setting.NOTIFY_HOUR, 9)
        clock.set(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))  # 08:00 local
        assert run(bot.tick()) == []
        clock.set(datetime(2026, 9, 17, 6, 0, tzinfo=UTC))  # 09:00 local
        assert run(bot.tick()) == [Notification.MORNING]


def test_mark_sent_is_what_due_notifications_reads(database: Database, clock: FrozenClock) -> None:
    runtime = RuntimeRepository(database)
    settings = SettingsRepository(database).load()
    clock.set(datetime(2026, 9, 17, 3, 5, tzinfo=UTC))
    assert due_notifications(clock, settings, runtime) == [Notification.MORNING]
    mark_sent(Notification.MORNING, clock, settings, runtime)
    assert due_notifications(clock, settings, runtime) == []


def test_the_token_is_masked_in_every_log_line() -> None:
    """The Bot API puts the token in the URL; the log must never show it."""
    import logging

    from lexitrack.core.logging_config import RedactingFormatter, redact

    secret = "123456789:" + "FAKE-test-secret-" + "x" * 20  # never a real token
    url = f"POST https://api.telegram.org/bot{secret}/getUpdates"
    assert "FAKE-test-secret" not in redact(url)
    assert "123456789:[redacted]" in redact(url)
    assert redact("08:24:02,726 at 12:30:45") == "08:24:02,726 at 12:30:45"

    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP %s", (url,), None)
    assert "FAKE-test-secret" not in RedactingFormatter("%(message)s").format(record)


def test_connecting_after_the_morning_hour_does_not_send_the_list_twice(
    seeded: Database, outbox: FakeOutbox, clock: FrozenClock
) -> None:
    core = BotCore(seeded, outbox, clock=clock)  # 07:00, after the 06:00 brief time
    run(core.command(OWNER, "/start"))
    briefs = [m for _, m in outbox.sent if "Good morning" in m.text]
    assert len(briefs) == 1
    assert run(core.tick()) == []
