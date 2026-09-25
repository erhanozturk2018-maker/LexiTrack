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
from lexitrack.models.srs import Channel, Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import (
    ListRepository,
    RuntimeRepository,
    SettingsRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.repositories.word_repository import StoredWord
from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_flow import Step, StepKind
from lexitrack.telegram import config as telegram_config
from lexitrack.telegram.config import load_config, read_env_file
from lexitrack.telegram.core import BotCore
from lexitrack.telegram.messages import (
    START_DATA,
    Message,
    end_data,
    intro_data,
    known_data,
    parse_callback,
    step_card,
    step_data,
    undo_data,
    write_check,
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
        parsed = parse_callback(step_data("abc", 12, "remembered"))
        assert (parsed.action, parsed.session_id, parsed.step, parsed.value) == (
            "step", "abc", 12, "remembered"
        )
        known = parse_callback(known_data("abc", 7))
        assert (known.action, known.word_id) == ("known", 7)
        assert parse_callback(end_data("abc")).action == "end"
        assert parse_callback("start").action == "start"

    @pytest.mark.parametrize(
        "data",
        ["", "intro:yesterday", "st:x:y:go", "st:s:1:", "known:s:x", "nonsense", "end",
         # A card of the route before version 5: ignored, not misread.
         "ans:s:1:3"],
    )
    def test_malformed_data_is_ignored_not_an_error(self, data: str) -> None:
        assert parse_callback(data) is None

    def test_every_callback_fits_telegrams_64_bytes(self) -> None:
        for data in (step_data("f" * 32, 999_999, "go"), known_data("f" * 32, 999_999_999)):
            assert len(data.encode()) <= 64


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
        assert "25 new words marked as studied" in edited.text

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


def _session_of(message: Message) -> str:
    data = next(d for d in FakeOutbox.callbacks(message) if d.startswith(("st:", "end:")))
    return parse_callback(data).session_id


def respond(bot: BotCore, outbox: FakeOutbox, session: str, right: bool = True) -> Step:
    """Answer the step on screen as the learner would, from the phone."""
    flow = bot._flows[session]
    step, number, card = flow.current, flow.step_number, outbox.last_id()

    def press(value: str) -> None:
        run(bot.callback(OWNER, outbox.last_id(), step_data(session, number, value), ""))

    if step.kind is StepKind.TYPE:
        run(bot.text(OWNER, step.prompt.accepted[0] if right else "nothing like it"))
        if right and step.phase.value == "review":
            # A right answer: the card asks how it came.
            assert "How did it come?" in outbox.sent[-1][1].text
            press("remembered")
    elif step.kind is StepKind.CHOOSE:
        ids = [option.id for option in step.options]
        right_index = ids.index(step.word.id)
        press(str(right_index if right else (right_index + 1) % len(ids)))
    elif step.kind is StepKind.RECALL:
        press("remembered" if right else "forgot")
    elif step.kind is StepKind.TEACH:
        press("go")
    else:  # WRITE: the sentence as a reply, then the grade
        run(bot.text(OWNER, "A sentence of my own."))
        press("remembered" if right else "forgot")
    assert card  # the card answered was the one on screen
    return step


class TestReviews:
    @pytest.fixture
    def due(self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock) -> BotCore:
        """25 words marked as studied yesterday, due now."""
        run(bot.callback(OWNER, "55", intro_data(clock.today()), ""))
        clock.advance_to_day_start(1)
        clock.advance(hours=18)
        SettingsRepository(bot.engine.database).set(Setting.NEW_WORDS_PER_DAY, 0)
        outbox.sent.clear()
        outbox.edits.clear()
        return bot

    def start(self, bot: BotCore, outbox: FakeOutbox) -> str:
        run(bot.command(OWNER, "/review"))
        return _session_of(outbox.sent[-1][1])

    def test_a_session_asks_the_first_word_the_way_the_desktop_would(
        self, due: BotCore, outbox: FakeOutbox
    ) -> None:
        session = self.start(due, outbox)
        flow = due._flows[session]
        assert flow.channel is Channel.TELEGRAM
        card = outbox.sent[-1][1]
        assert "1 / 25" in card.text
        assert flow.current.label.upper() in card.text
        assert end_data(session) in FakeOutbox.callbacks(card)

    def test_a_typed_reply_answers_and_the_next_card_replaces_it(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database
    ) -> None:
        session = self.start(due, outbox)
        first_card = outbox.last_id()
        step = respond(due, outbox, session)
        assert due.engine.rating_counts()[int(Rating.GOOD)] + due.engine.rating_counts()[
            int(Rating.EASY)
        ] == 1
        channel = seeded.connection.execute(
            "SELECT channel FROM review_logs WHERE session_id = ?", (session,)
        ).fetchone()[0]
        assert channel == Channel.TELEGRAM.value
        card = outbox.sent[-1][1]
        assert "2 / 25" in card.text
        assert "✓" in card.text and step.word.word in card.text, "it opens with the result"
        assert (OWNER, first_card) in outbox.deleted
        assert due.engine.session(session).message_id == outbox.last_id()

    def test_the_answer_is_recorded_with_its_question(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database
    ) -> None:
        session = self.start(due, outbox)
        respond(due, outbox, session)
        row = seeded.connection.execute(
            "SELECT a.route_version, a.role FROM learning_attempts a "
            "JOIN review_logs r ON r.id = a.review_log_id WHERE r.session_id = ?",
            (session,),
        ).fetchone()
        assert tuple(row) == ("v2", "primary")

    def test_a_double_tap_counts_once(self, due: BotCore, outbox: FakeOutbox) -> None:
        """Telegram re-delivers a tap it was unsure about."""
        session = self.start(due, outbox)
        flow = due._flows[session]
        number = flow.step_number
        data = step_data(session, number, "dk")
        run(due.callback(OWNER, outbox.last_id(), data, ""))
        sent = len(outbox.sent)
        run(due.callback(OWNER, outbox.last_id(), data, ""))
        assert len(outbox.sent) == sent, "the second tap found an older step number"

    def test_a_tap_on_an_old_card_is_ignored(self, due: BotCore, outbox: FakeOutbox) -> None:
        session = self.start(due, outbox)
        old = step_data(session, due._flows[session].step_number, "dk")
        respond(due, outbox, session)
        before = sum(due.engine.rating_counts().values())
        run(due.callback(OWNER, "1", old, ""))
        assert sum(due.engine.rating_counts().values()) == before

    def test_a_reply_with_no_session_or_on_a_button_card_says_what_to_do(
        self, due: BotCore, outbox: FakeOutbox
    ) -> None:
        run(due.text(OWNER, "hello"))
        assert "/review" in outbox.sent[-1][1].text
        session = self.start(due, outbox)
        flow = due._flows[session]
        # Wrong on purpose until a button step (choose among four) is on screen.
        respond(due, outbox, session, right=False)
        # A missed "meaning to word" is probed by choosing among four.
        assert flow.current.kind is StepKind.CHOOSE
        run(due.text(OWNER, "a word"))
        assert "buttons" in outbox.sent[-1][1].text

    def test_undo_puts_the_word_back(self, due: BotCore, outbox: FakeOutbox) -> None:
        session = self.start(due, outbox)
        assert not any(
            d.startswith("undo:") for d in FakeOutbox.callbacks(outbox.sent[-1][1])
        ), "nothing to take back on the first card"
        step = respond(due, outbox, session)
        second = outbox.last_id()
        assert undo_data(session) in FakeOutbox.callbacks(outbox.sent[-1][1])
        run(due.callback(OWNER, second, undo_data(session), ""))
        card = outbox.sent[-1][1]
        assert "Took back your answer" in card.text
        assert "1 / 25" in card.text
        assert due._flows[session].current.word.id == step.word.id
        assert (OWNER, second) in outbox.deleted
        assert sum(due.engine.rating_counts().values()) == 0
        sent = len(outbox.sent)
        run(due.callback(OWNER, outbox.last_id(), undo_data(session), ""))
        assert len(outbox.sent) == sent, "a second Undo finds nothing to take back"

    def test_the_last_answer_ends_with_a_summary(self, due: BotCore, outbox: FakeOutbox) -> None:
        session = self.start(due, outbox)
        for _ in range(200):
            if session not in due._flows:
                break
            respond(due, outbox, session)
        text = outbox.edits[-1][2].text
        assert "Session finished" in text
        assert "25 reviewed" in text
        assert due.engine.open_session(Channel.TELEGRAM) is None

    def test_stop_here_keeps_what_was_answered(self, due: BotCore, outbox: FakeOutbox) -> None:
        session = self.start(due, outbox)
        respond(due, outbox, session)
        run(due.callback(OWNER, outbox.last_id(), end_data(session), ""))
        text = outbox.edits[-1][2].text
        assert "Stopped" in text
        assert "1 reviewed" in text
        assert "24 still due" in text

    def test_review_resumes_the_open_session(self, due: BotCore, outbox: FakeOutbox) -> None:
        session = self.start(due, outbox)
        respond(due, outbox, session)
        again = self.start(due, outbox)
        assert again == session
        assert "Picking up where you left off" in outbox.sent[-1][1].text
        assert "2 / 25" in outbox.sent[-1][1].text

    def test_nothing_due_says_so_instead_of_an_empty_session(
        self, bot: BotCore, outbox: FakeOutbox, seeded: Database
    ) -> None:
        SettingsRepository(seeded).set(Setting.NEW_WORDS_PER_DAY, 0)
        run(bot.command(OWNER, "/review"))
        assert "Nothing is due" in outbox.sent[-1][1].text

    def test_the_desktop_study_page_counts_phone_answers(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        session = self.start(due, outbox)
        respond(due, outbox, session)
        desktop = LearningService(seeded, clock).daily_plan()
        assert desktop.due_count == 24
        assert desktop.reviews_done_today == 1


class TestInterruptions:
    """The app closes mid-session; the phone carries on where it was."""

    @pytest.fixture
    def due(self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock) -> BotCore:
        run(bot.callback(OWNER, "55", intro_data(clock.today()), ""))
        clock.advance_to_day_start(1)
        clock.advance(hours=18)
        SettingsRepository(bot.engine.database).set(Setting.NEW_WORDS_PER_DAY, 0)
        return bot

    def test_a_restart_loses_nothing_and_acts_on_no_old_card(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        run(due.command(OWNER, "/review"))
        session = _session_of(outbox.sent[-1][1])
        respond(due, outbox, session)
        respond(due, outbox, session)
        old = step_data(session, due._flows[session].step_number, "dk")
        answered = sum(due.engine.rating_counts().values())

        # The app is closed and opened again: a new bot, the same database.
        restarted = BotCore(seeded, outbox, clock=clock)
        run(restarted.callback(OWNER, outbox.last_id(), old, ""))
        card = outbox.sent[-1][1]
        assert "restarted" in card.text, "the step is shown again, not acted on"
        assert sum(restarted.engine.rating_counts().values()) == answered
        assert _session_of(card) == session
        assert f"{answered + 1} / 25" in card.text
        # And the session carries on from there.
        respond(restarted, outbox, session)
        assert sum(restarted.engine.rating_counts().values()) == answered + 1

    def test_a_reply_after_a_restart_is_not_taken_as_an_answer(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        """The question on screen may have changed: the reply is not guessed at."""
        run(due.command(OWNER, "/review"))
        restarted = BotCore(seeded, outbox, clock=clock)
        run(restarted.text(OWNER, "anything"))
        assert "restarted" in outbox.sent[-1][1].text
        assert sum(restarted.engine.rating_counts().values()) == 0

    def test_review_after_a_restart_resumes(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        run(due.command(OWNER, "/review"))
        session = _session_of(outbox.sent[-1][1])
        respond(due, outbox, session)
        restarted = BotCore(seeded, outbox, clock=clock)
        run(restarted.command(OWNER, "/review"))
        assert _session_of(outbox.sent[-1][1]) == session
        assert "Picking up where you left off" in outbox.sent[-1][1].text

    def test_a_restart_between_probes_shows_the_same_probe(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        run(due.command(OWNER, "/review"))
        session = _session_of(outbox.sent[-1][1])
        respond(due, outbox, session, right=False)  # the first question missed
        probe = due._flows[session].current
        assert probe.kind is StepKind.CHOOSE
        restarted = BotCore(seeded, outbox, clock=clock)
        run(restarted.command(OWNER, "/review"))
        card = outbox.sent[-1][1]
        options = [label for row in card.buttons for label, _ in row][:4]
        assert options == [o.word for o in probe.options], "the same four, not a new question"

    def test_a_right_answer_waiting_for_its_report_comes_back_after_a_restart(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        run(due.command(OWNER, "/review"))
        session = _session_of(outbox.sent[-1][1])
        step = due._flows[session].current
        run(due.text(OWNER, step.prompt.accepted[0]))
        assert "How did it come?" in outbox.sent[-1][1].text
        restarted = BotCore(seeded, outbox, clock=clock)
        run(restarted.command(OWNER, "/review"))
        assert "How did it come?" in outbox.sent[-1][1].text
        assert sum(restarted.engine.rating_counts().values()) == 0, "not yet recorded"

    def test_the_next_day_starts_afresh(
        self, due: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        run(due.command(OWNER, "/review"))
        session = _session_of(outbox.sent[-1][1])
        clock.advance_to_day_start(1)
        clock.advance(hours=8)
        restarted = BotCore(seeded, outbox, clock=clock)
        restarted.engine.close_stale_sessions()
        run(restarted.command(OWNER, "/review"))
        assert _session_of(outbox.sent[-1][1]) != session


class TestLearningOnThePhone:
    def test_new_words_are_taught_and_practised_in_the_session(
        self, bot: BotCore, outbox: FakeOutbox, seeded: Database
    ) -> None:
        SettingsRepository(seeded).set(Setting.NEW_WORDS_PER_DAY, 4)
        run(bot.callback(OWNER, "1", START_DATA, ""))
        session = _session_of(outbox.sent[-1][1])
        card = outbox.sent[-1][1]
        assert "NEW WORD" in card.text
        assert any(label.startswith("Continue") for row in card.buttons for label, _ in row)
        for _ in range(100):
            if session not in bot._flows:
                break
            respond(bot, outbox, session)
        assert len(bot.engine.daily_plan().introduced_today) == 4
        assert "4 new words learned" in outbox.edits[-1][2].text
        # Practice is recorded, never rated.
        assert sum(bot.engine.rating_counts().values()) == 0

    def test_a_word_in_long_term_memory_is_offered_as_known(
        self, bot: BotCore, outbox: FakeOutbox, seeded: Database, clock: FrozenClock
    ) -> None:
        run(bot.callback(OWNER, "55", intro_data(clock.today()), ""))
        clock.advance_to_day_start(1)
        clock.advance(hours=18)
        SettingsRepository(seeded).set_many(
            {Setting.NEW_WORDS_PER_DAY: 0, Setting.MASTERY_STABILITY_DAYS: 1}
        )
        run(bot.command(OWNER, "/review"))
        session = _session_of(outbox.sent[-1][1])
        step = respond(bot, outbox, session)
        offer = known_data(session, step.word.id)
        assert offer in FakeOutbox.callbacks(outbox.sent[-1][1])
        assert "long-term memory" in outbox.sent[-1][1].text
        run(bot.callback(OWNER, outbox.last_id(), offer, ""))
        status = seeded.connection.execute(
            "SELECT status FROM user_word_state WHERE word_id = ?", (step.word.id,)
        ).fetchone()[0]
        assert status == ReviewStatus.KNOWN.value
        assert "marked Known" in outbox.edits[-1][2].text


class TestCards:
    """How each kind of step reads on the phone."""

    word = StoredWord(id=1, word="reluctant", normalized_word="reluctant",
                      part_of_speech="adjective", cefr_level="B2",
                      definition="unwilling to do something")

    def test_a_shown_word_is_reported_on_before_anything_is_revealed(self) -> None:
        card = step_card(Step(self.word, StepKind.RECALL), "s", 1, 1, 1)
        assert "<tg-spoiler>" not in card.text and self.word.definition not in card.text
        labels = [label for row in card.buttons for label, _ in row]
        assert labels[:4] == ["Forgot", "Effortful", "Remembered", "Instant"]

    def test_a_right_reply_is_followed_by_how_it_came(self) -> None:
        from lexitrack.telegram.messages import assess_card

        card = assess_card(Step(self.word, StepKind.TYPE), "s", 4, "✓ “reluctant”", 1, 9)
        labels = [label for row in card.buttons for label, _ in row]
        # No Forgot after a right answer.
        assert labels[:3] == ["Effortful", "Remembered", "Instant"]
        assert step_data("s", 4, "instant") in FakeOutbox.callbacks(card)

    def test_a_written_sentence_is_graded_beside_the_examples(self) -> None:
        step = Step(self.word, StepKind.WRITE)
        check = write_check(step, "I was <reluctant> to go.", "s", 3)
        assert "&lt;reluctant&gt;" in check.text, "the learner's text is escaped"
        labels = [label for row in check.buttons for label, _ in row]
        assert labels[:4] == ["Forgot", "Effortful", "Remembered", "Instant"]
        assert step_data("s", 3, "remembered") in FakeOutbox.callbacks(check)

    def test_a_report_reaches_the_flow_as_it_was_given(self, bot: BotCore) -> None:
        from lexitrack.models.attempt import SelfReport

        reported = []

        class Flow:
            session_id = "s"
            awaiting = False
            written = "a sentence"
            current = Step(TestCards.word, StepKind.WRITE)

            def assess(self, report):
                reported.append(report)
                return True

        for value in ("forgot", "effortful", "remembered", "instant"):
            bot._act(Flow(), value)
        assert reported == list(SelfReport)
        # Without a sentence written first, a report is not taken.
        unwritten = Flow()
        unwritten.written = None
        assert bot._act(unwritten, "instant") is None


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


# -- the weekly summary ----------------------------------------------------------


class TestWeeklySummary:
    SUNDAY_EVENING = datetime(2026, 9, 20, 18, 30, tzinfo=UTC)  # 21:30 local, Sunday

    def a_week_of_study(self, bot: BotCore, clock: FrozenClock) -> None:
        engine = bot.engine
        engine.introduce()
        for _ in range(3):
            clock.advance_to_day_start(1)
            clock.advance(hours=7)
            engine.introduce()
            for index, item in enumerate(engine.review_queue()):
                engine.answer(item.word.id, Rating.AGAIN if index % 3 == 0 else Rating.GOOD)

    def test_it_is_owed_on_sunday_evening_once(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        self.a_week_of_study(bot, clock)
        clock.set(self.SUNDAY_EVENING)
        sent = run(bot.tick())
        assert Notification.WEEKLY in sent
        weekly = [m.text for _, m in outbox.sent if "Your week" in m.text]
        assert len(weekly) == 1
        assert "answers (" in weekly[0] and "% Again)" in weekly[0]
        assert "new words introduced" in weekly[0]
        run(bot.tick())
        assert len([m for _, m in outbox.sent if "Your week" in m.text]) == 1

    def test_not_on_other_days(self, bot: BotCore, clock: FrozenClock) -> None:
        clock.set(datetime(2026, 9, 19, 18, 30, tzinfo=UTC))  # Saturday 21:30
        assert Notification.WEEKLY not in run(bot.tick())

    def test_not_before_the_evening_hour(self, bot: BotCore, clock: FrozenClock) -> None:
        clock.set(datetime(2026, 9, 20, 9, 0, tzinfo=UTC))  # Sunday 12:00
        assert Notification.WEEKLY not in run(bot.tick())

    def test_it_can_be_switched_off(
        self, bot: BotCore, clock: FrozenClock, seeded: Database
    ) -> None:
        SettingsRepository(seeded).set(Setting.WEEKLY_SUMMARY, False)
        clock.set(self.SUNDAY_EVENING)
        assert Notification.WEEKLY not in run(bot.tick())

    def test_a_week_with_nothing_in_it_stays_quiet(
        self, bot: BotCore, outbox: FakeOutbox, clock: FrozenClock
    ) -> None:
        clock.set(self.SUNDAY_EVENING)
        assert Notification.WEEKLY in run(bot.tick()), "counted as done for the week"
        assert not [m for _, m in outbox.sent if "Your week" in m.text]


def test_a_new_words_page_offers_more_when_there_is_more() -> None:
    word = StoredWord(id=1, word="arid", normalized_word="arid", definition="very dry")
    step = Step(word, StepKind.TEACH)
    with_more = step_card(step, "s", 3, 1, 4, more=True)
    assert step_data("s", 3, "more") in FakeOutbox.callbacks(with_more)
    assert step_data("s", 3, "more") not in FakeOutbox.callbacks(step_card(step, "s", 3, 1, 4))


def test_a_written_sentence_is_checked_against_the_pattern_and_collocations() -> None:
    from lexitrack.models.content import WordContent, WordTeaching

    word = StoredWord(id=1, word="reluctant", normalized_word="reluctant", definition="unwilling")
    teaching = WordTeaching(WordContent(word_id=1, pattern="reluctant to do sth",
                                        collocations=("a reluctant hero", "reluctantly agree")))
    check = write_check(Step(word, StepKind.WRITE, teaching=teaching), "I was reluctant.", "s", 2)
    assert "☐ Pattern: reluctant to do sth" in check.text
    assert "☐ Goes with: a reluctant hero · reluctantly agree" in check.text
