"""The Study tab, the Study Plan window and Settings, driven like a user would.

The engine's rules are tested in ``test_learning_service.py``. These tests are
about the screens keeping their promises: the button says what it does, the
keys work only where there is a card for them, and a setting saved in the
window is the setting the engine then uses.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lexitrack.core.clock import FrozenClock  # noqa: E402
from lexitrack.models.srs import Rating  # noqa: E402
from lexitrack.models.user_word_state import ReviewStatus  # noqa: E402
from lexitrack.services.learning_service import LearningService  # noqa: E402
from lexitrack.services.vocabulary_service import VocabularyService  # noqa: E402
from lexitrack.ui.main_window import HOME, STUDY, MainWindow  # noqa: E402
from lexitrack.ui.settings_dialog import SettingsDialog  # noqa: E402
from lexitrack.ui.study_page import DAY, EMPTY, SESSION, _interval  # noqa: E402
from lexitrack.ui.study_plan_dialog import StudyPlanDialog  # noqa: E402
from lexitrack.ui.theme import ThemeManager, ThemeName  # noqa: E402

WORDS = " ".join(f"{a}{b}word" for a in "abcd" for b in "abcdefghijklmnopqrstuvwxyz")  # 104 words


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture(autouse=True)
def isolated_settings(qapp):
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackStudyTest")
    QSettings().clear()
    yield
    QSettings().clear()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))


@pytest.fixture
def loaded(service: VocabularyService, make_pdf) -> VocabularyService:
    """One list of 104 words, all marked unknown."""
    service.import_document(make_pdf([WORDS]))
    list_id = service.lists()[0].id
    service.set_status([w.id for w in service.list_words(list_id)], ReviewStatus.UNKNOWN)
    return service


@pytest.fixture
def engine(loaded: VocabularyService, clock: FrozenClock) -> LearningService:
    return LearningService(loaded.database, clock)


@pytest.fixture
def window(qapp, loaded, engine):
    win = MainWindow(loaded, ThemeManager(), engine)
    yield win
    win.close()


def with_plan(engine: LearningService, service: VocabularyService) -> None:
    engine.create_plan("Test plan", list_ids=[service.lists()[0].id])


class TestNavigation:
    def test_study_is_the_first_tab(self, window) -> None:
        tabs = list(window.tab_buttons)
        assert tabs[0] == STUDY
        assert window.tab_buttons[STUDY].text() == "Study"

    def test_the_window_opens_on_study_when_there_is_work(self, qapp, loaded, engine) -> None:
        with_plan(engine, loaded)
        win = MainWindow(loaded, ThemeManager(), engine)
        assert win.current_page == STUDY
        win.close()

    def test_without_a_plan_the_window_does_not_open_on_an_empty_study_page(self, window) -> None:
        assert window.current_page != STUDY


class TestDayView:
    def test_no_plan_shows_one_explanation_and_one_button(self, window) -> None:
        window.show_page(STUDY)
        assert window.study._stack.currentWidget() is window.study._pages[EMPTY]

    def test_the_day_lists_the_words_it_asks_you_to_confirm(self, window, engine, loaded) -> None:
        with_plan(engine, loaded)
        window.show_page(STUDY)
        study = window.study
        assert study._stack.currentWidget() is study._pages[DAY]
        assert study.step_new.text.text() == "Learn 25 new words"
        assert study.primary_button.isEnabled()
        assert study.primary_button.text() == "I studied these 25"
        chips = study.word_chips()
        assert len(chips) == 25
        assert engine.daily_plan().new_words[0].word in chips
        assert study.words_section.title.text() == "NEW WORDS \u00b7 25"

    def test_confirming_turns_the_button_off_and_says_when_they_come_back(
        self, window, engine, loaded
    ) -> None:
        with_plan(engine, loaded)
        window.show_page(STUDY)
        messages: list[str] = []
        window.study.notify.connect(messages.append)
        window.study.primary_button.click()
        # Nothing is due on day one, so the day is finished and says so.
        assert not window.study.primary_button.isEnabled()
        assert "All done" in window.study.primary_button.text()
        assert window.study.step_new.text.text() == "Learned 25 new words"
        # The words stay on screen, greyed, as what was learned today.
        assert window.study.words_section.title.text() == "LEARNED TODAY \u00b7 25"
        assert messages and "2026-09-18" in messages[0]

    def test_a_finished_day_is_stated_once_not_three_times(self, window, engine, loaded) -> None:
        with_plan(engine, loaded)
        engine.introduce()
        window.show_page(STUDY)
        assert window.study.intake_note.isHidden()

    def test_the_plan_name_is_not_repeated_when_it_matches_its_list(
        self, window, engine, loaded
    ) -> None:
        name = loaded.lists()[0].name
        engine.create_plan(name, list_ids=[loaded.lists()[0].id])
        window.show_page(STUDY)
        assert window.study.plan_label.text().count(name) == 1
        assert "September" in window.study.plan_label.text()

    def test_the_week_panel_waits_until_there_is_a_week_to_show(
        self, window, engine, loaded
    ) -> None:
        with_plan(engine, loaded)
        window.show_page(STUDY)
        assert window.study.week_section.isHidden()
        engine.introduce()
        window.study.refresh()
        assert not window.study.week_section.isHidden()
        tiles = window.study.week.tiles
        assert tiles[0].name.text() == "Today"
        assert tiles[1].count.text() == "25", "tomorrow's reviews"


class TestSession:
    @pytest.fixture
    def due(self, window, engine, loaded, clock):
        with_plan(engine, loaded)
        engine.introduce()
        clock.advance_to_day_start(1)
        window.show_page(STUDY)
        return window.study

    def test_start_opens_the_first_card_with_the_meaning_hidden(self, due) -> None:
        # New words and reviews on the same day: the primary button follows
        # the day's order, and reviewing first is the secondary choice.
        assert due.primary_button.text() == "I studied these 25"
        assert not due.secondary_button.isHidden()
        assert due.secondary_button.text() == "Review 25 first"
        due.secondary_button.click()
        assert due._stack.currentWidget() is due._pages[SESSION]
        assert due.session_progress.text() == "1 / 25"
        assert due.definition_label.isHidden()
        assert not due.reveal_button.isHidden()

    def test_space_reveals_and_then_answers_good(self, due, engine) -> None:
        due.start_session()
        due.keyPressEvent(_key(Qt.Key.Key_Space))
        assert not due.definition_label.isHidden()
        due.keyPressEvent(_key(Qt.Key.Key_Space))
        assert due.session_progress.text() == "2 / 25"
        assert engine.rating_counts()[int(Rating.GOOD)] == 1

    def test_number_keys_are_the_four_answers(self, due, engine) -> None:
        due.start_session()
        for key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4):
            due.keyPressEvent(_key(key))
        counts = engine.rating_counts()
        assert [counts[int(r)] for r in Rating] == [1, 1, 1, 1]

    def test_escape_ends_the_session_and_keeps_the_answers(self, due, engine) -> None:
        due.start_session()
        due.keyPressEvent(_key(Qt.Key.Key_3))
        due.keyPressEvent(_key(Qt.Key.Key_Escape))
        assert not due.in_session
        assert due._stack.currentWidget() is due._pages[DAY]
        assert due.step_review.text.text() == "Review 24 words"
        assert due.step_review.meta.text() == "1 answered"
        assert due.secondary_button.text() == "Review 24 first"

    def test_number_keys_do_nothing_outside_a_session(self, due, engine) -> None:
        due.keyPressEvent(_key(Qt.Key.Key_1))
        assert sum(engine.rating_counts().values()) == 0

    def test_answering_the_last_card_returns_to_the_day(self, due) -> None:
        due.start_session()
        for _ in range(25):
            due.keyPressEvent(_key(Qt.Key.Key_3))
        assert not due.in_session
        assert due.step_review.text.text() == "Reviewed 25 words"
        assert due.secondary_button.isHidden(), "nothing left to review"
        assert due.primary_button.text() == "I studied these 25"

    def test_identical_intervals_are_said_once(self, due) -> None:
        due.start_session()
        due._show_intervals({rating: 1 for rating in Rating})
        assert [b.text() for b in due.answer_buttons.values()] == [
            "Again",
            "Hard",
            "Good",
            "Easy",
        ]
        assert "tomorrow" in due.answer_hint.text()

    def test_different_intervals_are_shown_on_each_button(self, due) -> None:
        due.start_session()
        due._show_intervals({Rating.AGAIN: 1, Rating.HARD: 3, Rating.GOOD: 8, Rating.EASY: 21})
        assert due.answer_buttons[Rating.GOOD].text() == "Good\n8 days"
        assert due.answer_hint.isHidden()


def test_intervals_read_as_words() -> None:
    assert _interval(None) == "–"
    assert _interval(0) == "tomorrow"
    assert _interval(1) == "tomorrow"
    assert _interval(12) == "12 days"
    assert _interval(90) == "3 months"
    assert _interval(365) == "1 year"


class TestStudyPlanDialog:
    def test_a_new_plan_needs_a_list(self, qapp, engine, loaded) -> None:
        dialog = StudyPlanDialog(engine, loaded)
        dialog._save()
        assert dialog.error.isVisible() or dialog.error.text()
        assert engine.active_plan() is None

    def test_creating_a_plan_makes_it_the_one_in_use(self, qapp, engine, loaded) -> None:
        dialog = StudyPlanDialog(engine, loaded)
        next(iter(dialog._checks.values())).setChecked(True)
        assert "104 words" in dialog.summary.text()
        dialog._save()
        assert dialog.changed
        assert engine.active_plan() is not None
        assert engine.active_plan().name == "My Study Plan"

    def test_editing_a_plan_keeps_its_cards(self, qapp, engine, loaded) -> None:
        with_plan(engine, loaded)
        engine.introduce()
        dialog = StudyPlanDialog(engine, loaded)
        dialog.name_field.setText("Renamed")
        dialog._save()
        assert engine.active_plan().name == "Renamed"
        assert engine.daily_plan().introduced_today


class TestSettingsDialog:
    def test_the_window_shows_the_stored_values(self, qapp, engine, loaded) -> None:
        dialog = SettingsDialog(engine, loaded, ThemeManager())
        assert dialog.new_words.value() == 25
        assert dialog.capacity.value() == 250
        assert dialog.notify_hour.text() == "06:00"
        assert dialog.retention.text() == "0.90"

    def test_saving_reaches_the_engine(self, qapp, engine, loaded) -> None:
        dialog = SettingsDialog(engine, loaded, ThemeManager())
        dialog.new_words.setValue(12)
        dialog.capacity.setValue(0)
        dialog._save()
        assert engine.settings.new_words_per_day == 12
        assert engine.settings.review_capacity_per_day == 0

    def test_cancel_saves_nothing(self, qapp, engine, loaded) -> None:
        dialog = SettingsDialog(engine, loaded, ThemeManager())
        dialog.new_words.setValue(99)
        dialog.reject()
        assert engine.refresh_settings().new_words_per_day == 25

    def test_advanced_controls_follow_developer_mode(self, qapp, engine, loaded) -> None:
        dialog = SettingsDialog(engine, loaded, ThemeManager())
        assert not dialog._advanced_box.isEnabled()
        dialog.developer_mode.setChecked(True)
        assert dialog._advanced_box.isEnabled()
        assert not dialog.debug_logging.isChecked(), "two switches, not one"

    def test_the_theme_choice_is_applied_on_save(self, qapp, engine, loaded) -> None:
        theme = ThemeManager()
        theme.apply(ThemeName.LIGHT)
        dialog = SettingsDialog(engine, loaded, theme)
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
        dialog._save()
        assert theme.current is ThemeName.DARK
        theme.apply(ThemeName.LIGHT)

    def test_the_simulator_uses_the_values_on_screen(self, qapp, engine, loaded) -> None:
        dialog = SettingsDialog(engine, loaded, ThemeManager())
        dialog.developer_mode.setChecked(True)
        dialog.new_words.setValue(5)
        dialog._simulate()
        assert dialog.simulation_result.text().startswith("5 new words a day")


def test_ctrl_p_and_ctrl_comma_are_listed_as_commands(window) -> None:
    titles = {command.title: command.shortcut for command in window.commands()}
    assert titles["Study Plan…"] == "Ctrl+P"
    assert titles["Settings…"] == "Ctrl+,"
    assert titles["Go to Study"] == "Alt+S"


def test_shortcuts_window_documents_the_study_keys(window) -> None:
    sections = [name for name, _keys in window.shortcut_sections()]
    assert sections[0] == "Study reviews"
    window.show_page(HOME)


def _key(key: Qt.Key):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


class TestTelegramController:
    def test_no_token_and_switched_off_is_simply_off(self, qapp, loaded) -> None:
        from lexitrack.telegram.config import TelegramConfig
        from lexitrack.telegram.runtime import BotState
        from lexitrack.ui.telegram_controller import TelegramController

        controller = TelegramController(loaded.database, config=TelegramConfig())
        assert controller.apply() is BotState.OFF

    def test_switched_on_without_a_token_says_what_is_missing(self, qapp, loaded) -> None:
        from lexitrack.telegram.config import TelegramConfig
        from lexitrack.telegram.runtime import BotState
        from lexitrack.ui.telegram_controller import TelegramController

        controller = TelegramController(loaded.database, config=TelegramConfig())
        states: list[str] = []
        controller.state_changed.connect(lambda state, _detail: states.append(state))
        assert controller.set_enabled(True) is BotState.NO_TOKEN
        assert states[-1] == BotState.NO_TOKEN.value
        assert controller.enabled is True, "the switch is remembered even without a token"

    def test_the_settings_page_shows_where_to_paste_the_token(self, qapp, engine, loaded) -> None:
        from lexitrack.telegram.config import TelegramConfig
        from lexitrack.ui.telegram_controller import TelegramController

        controller = TelegramController(loaded.database, config=TelegramConfig())
        dialog = SettingsDialog(engine, loaded, ThemeManager(), telegram=controller)
        assert ".env" in dialog.telegram_token.text()
        assert "/start" in dialog.telegram_chat.text()
        dialog.telegram_enabled.setChecked(True)
        dialog._save()
        assert engine.settings.telegram_enabled is True


class TestBackgroundLife:
    """Closing, the tray, a second launch, and Start with Windows."""

    def test_without_the_bot_closing_the_window_quits(self, qapp, loaded, engine) -> None:
        from lexitrack.telegram.config import TelegramConfig
        from lexitrack.ui.telegram_controller import TelegramController

        controller = TelegramController(loaded.database, config=TelegramConfig())
        win = MainWindow(loaded, ThemeManager(), engine, telegram=controller, use_tray=True)
        win.show()
        assert win.close() is True

    def test_with_the_bot_on_closing_hides_to_the_tray(self, qapp, loaded, engine) -> None:
        from lexitrack.telegram.config import TelegramConfig
        from lexitrack.ui.telegram_controller import TelegramController
        from lexitrack.ui.tray import Tray

        if not Tray.available():
            pytest.skip("no system tray on this machine")
        controller = TelegramController(loaded.database, config=TelegramConfig())
        controller.set_enabled(True)
        win = MainWindow(loaded, ThemeManager(), engine, telegram=controller, use_tray=True)
        win.show()
        assert win.close() is False, "the close was refused"
        assert not win.isVisible()
        win.bring_forward()
        assert win.isVisible()
        win._quitting = True
        assert win.close() is True

    def test_the_tray_menu_reads_the_day_and_the_switches(self, qapp, loaded, engine) -> None:
        from lexitrack.telegram.config import TelegramConfig
        from lexitrack.ui.telegram_controller import TelegramController
        from lexitrack.ui.tray import Tray

        with_plan(engine, loaded)
        controller = TelegramController(loaded.database, config=TelegramConfig())
        tray = Tray(engine, controller)
        tray.refresh()
        assert tray.today_action.text() == "Today: 25 new · 0 due"
        assert tray.bot_action.isChecked() is False
        tray.bot_action.setChecked(True)
        assert controller.enabled is True
        assert "no token" in tray.bot_action.text().lower()

    def test_a_second_launch_brings_the_first_forward(self, qapp) -> None:
        """Two real processes, as in life.

        On Windows the socket is a named pipe, and a client in the same thread
        as the server cannot complete the exchange — so the second launch is a
        real second Python process.
        """
        import subprocess
        import sys
        import time

        from lexitrack.ui.single_instance import InstanceServer, notify_running_instance

        name = f"LexiTrack-test-{time.time_ns()}"
        server = InstanceServer(name)
        assert server.listen()
        seen: list[bool] = []
        server.show_requested.connect(lambda: seen.append(True))
        client = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from PySide6.QtCore import QCoreApplication;"
                "from lexitrack.ui.single_instance import notify_running_instance as n;"
                f"app = QCoreApplication([]); raise SystemExit(0 if n({name!r}) else 3)",
            ]
        )
        deadline = time.monotonic() + 15
        while not seen and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert client.wait(15) == 0
        server.close()
        assert seen == [True]
        assert notify_running_instance(name) is False, "nobody is listening any more"


def test_start_with_windows_runs_the_app_minimized_without_a_console() -> None:
    from lexitrack.core import autostart

    command = autostart.launch_command()
    assert command.endswith("-m lexitrack --minimized")
    if autostart.supported():
        assert "pythonw.exe" in command.lower() or "python" in command.lower()


class TestStudyExtras:
    def test_copy_puts_the_words_and_meanings_on_the_clipboard(
        self, qapp, window, engine, loaded
    ) -> None:
        with_plan(engine, loaded)
        window.show_page(STUDY)
        # The text is checked directly: the system clipboard is shared with
        # every other program and can be briefly locked by one of them.
        lines = window.study.copy_text().splitlines()
        assert len(lines) == 25
        assert lines[0] == engine.daily_plan().new_words[0].word
        messages: list[str] = []
        window.study.notify.connect(messages.append)
        window.study.copy_button.click()
        assert messages == ["Copied 25 words with their meanings."]

    def test_hard_words_appear_only_once_there_are_some(
        self, window, engine, loaded, clock
    ) -> None:
        with_plan(engine, loaded)
        engine.save_settings({"leech_consecutive": 2})
        window.show_page(STUDY)
        assert window.study.hard_section.isHidden()
        engine.introduce()
        word = engine.daily_plan().introduced_today[0]
        for _ in range(2):
            clock.advance_to_day_start(1)
            engine.answer(word.id, Rating.AGAIN)
        window.study.refresh()
        assert not window.study.hard_section.isHidden()
        chips = window.study.hard_chips.texts()
        assert chips == [f"{word.word}  \u00d72"]

    def test_the_month_panel_counts_reviews_and_the_again_rate(
        self, window, engine, loaded, clock
    ) -> None:
        with_plan(engine, loaded)
        window.show_page(STUDY)
        assert window.study.stats_section.isHidden()
        engine.introduce()
        clock.advance_to_day_start(1)
        queue = engine.review_queue()
        for index, item in enumerate(queue):
            engine.answer(item.word.id, Rating.AGAIN if index < 5 else Rating.GOOD)
        window.study.refresh()
        assert not window.study.stats_section.isHidden()
        assert window.study.stat_reviews.value_label.text() == "25"
        assert window.study.stat_again.value_label.text() == "20%"
        assert window.study.stat_introduced.value_label.text() == "25"

    def test_export_from_study_offers_todays_words(
        self, window, engine, loaded, monkeypatch
    ) -> None:
        import lexitrack.ui.main_window as main_window

        captured: dict = {}

        class FakeDialog:
            def __init__(self, _service, scopes, parent=None) -> None:
                captured["scopes"] = scopes

            def exec(self) -> int:
                return 0

        monkeypatch.setattr(main_window, "ExportDialog", FakeDialog)
        with_plan(engine, loaded)
        window.show_page(STUDY)
        window.export()
        labels = [scope.label for scope in captured["scopes"]]
        assert labels == ["Today's new words (25)"]
        content = captured["scopes"][0].content()
        assert len(content.words) == 25


def test_quit_in_the_menu_really_quits_even_with_the_bot_on(
    qapp, loaded, engine, monkeypatch
) -> None:
    """Quit means quit. Only the window's X hides to the tray."""
    from PySide6.QtWidgets import QApplication as App

    from lexitrack.telegram.config import TelegramConfig
    from lexitrack.ui.telegram_controller import TelegramController
    from lexitrack.ui.tray import Tray

    if not Tray.available():
        pytest.skip("no system tray on this machine")
    quits: list[bool] = []
    monkeypatch.setattr(App, "quit", staticmethod(lambda: quits.append(True)))
    controller = TelegramController(loaded.database, config=TelegramConfig())
    controller.set_enabled(True)
    win = MainWindow(loaded, ThemeManager(), engine, telegram=controller, use_tray=True)
    win.show()
    win._fill_app_menu()
    quit_action = next(a for a in win.app_menu.actions() if a.text() == "Quit")
    quit_action.trigger()
    assert quits == [True]
    assert not win.isVisible()
