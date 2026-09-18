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

WORDS = " ".join(
    f"{a}{b}word" for a in "abcd" for b in "abcdefghijklmnopqrstuvwxyz"
)  # 104 words


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

    def test_the_window_opens_on_study_when_there_is_work(
        self, qapp, loaded, engine
    ) -> None:
        with_plan(engine, loaded)
        win = MainWindow(loaded, ThemeManager(), engine)
        assert win.current_page == STUDY
        win.close()

    def test_without_a_plan_the_window_does_not_open_on_an_empty_study_page(
        self, window
    ) -> None:
        assert window.current_page != STUDY


class TestDayView:
    def test_no_plan_shows_one_explanation_and_one_button(self, window) -> None:
        window.show_page(STUDY)
        assert window.study._stack.currentWidget() is window.study._pages[EMPTY]

    def test_the_day_lists_the_words_it_asks_you_to_confirm(
        self, window, engine, loaded
    ) -> None:
        with_plan(engine, loaded)
        window.show_page(STUDY)
        study = window.study
        assert study._stack.currentWidget() is study._pages[DAY]
        assert study.intake_count.text() == "25"
        assert study.intake_button.isEnabled()
        assert "25" in study.intake_button.text()
        first = engine.daily_plan().new_words[0].word
        assert first in study.intake_words.text()

    def test_confirming_turns_the_button_off_and_says_when_they_come_back(
        self, window, engine, loaded
    ) -> None:
        with_plan(engine, loaded)
        window.show_page(STUDY)
        messages: list[str] = []
        window.study.notify.connect(messages.append)
        window.study.intake_button.click()
        assert not window.study.intake_button.isEnabled()
        assert "introduced today" in window.study.intake_words.text()
        assert messages and "2026-09-18" in messages[0]

    def test_a_finished_day_is_stated_once_not_three_times(
        self, window, engine, loaded
    ) -> None:
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
        assert window.study._forecast_panel.isHidden()
        engine.introduce()
        window.study.refresh()
        assert not window.study._forecast_panel.isHidden()


class TestSession:
    @pytest.fixture
    def due(self, window, engine, loaded, clock):
        with_plan(engine, loaded)
        engine.introduce()
        clock.advance_to_day_start(1)
        window.show_page(STUDY)
        return window.study

    def test_start_opens_the_first_card_with_the_meaning_hidden(self, due) -> None:
        due.review_button.click()
        assert due._stack.currentWidget() is due._pages[SESSION]
        assert due.session_progress.text() == "1 of 25"
        assert due.definition_label.isHidden()
        assert not due.reveal_button.isHidden()

    def test_space_reveals_and_then_answers_good(self, due, engine) -> None:
        due.start_session()
        due.keyPressEvent(_key(Qt.Key.Key_Space))
        assert not due.definition_label.isHidden()
        due.keyPressEvent(_key(Qt.Key.Key_Space))
        assert due.session_progress.text() == "2 of 25"
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
        assert due.due_count.text() == "24"

    def test_number_keys_do_nothing_outside_a_session(self, due, engine) -> None:
        due.keyPressEvent(_key(Qt.Key.Key_1))
        assert sum(engine.rating_counts().values()) == 0

    def test_answering_the_last_card_returns_to_the_day(self, due) -> None:
        due.start_session()
        for _ in range(25):
            due.keyPressEvent(_key(Qt.Key.Key_3))
        assert not due.in_session
        assert due.due_count.text() == "0"
        assert not due.review_button.isEnabled()

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
