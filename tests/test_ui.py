"""UI behaviour, driven through real widgets.

These tests instantiate actual Qt widgets rather than mocking them, so they
catch the things that only appear once a widget is built: a signal wired to the
wrong slot, a screen that does not switch, a shortcut that does nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lexitrack.models.user_word_state import Progress  # noqa: E402
from lexitrack.repositories.word_repository import StoredWord  # noqa: E402
from lexitrack.services.vocabulary_service import VocabularyService  # noqa: E402
from lexitrack.ui.main_window import MainWindow  # noqa: E402
from lexitrack.ui.progress_widget import ProgressBarWidget, StatsBar  # noqa: E402
from lexitrack.ui.review_widget import ReviewWidget  # noqa: E402
from lexitrack.ui.theme import ThemeManager, ThemeName, build_stylesheet  # noqa: E402
from lexitrack.ui.theme.palette import DARK, LIGHT  # noqa: E402

SIMPLE_PDF = "alpha beta gamma delta epsilon"

_WELCOME_PAGE, _REVIEW_PAGE, _COMPLETED_PAGE = 0, 1, 2


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture
def theme(qapp, monkeypatch, tmp_path):
    # Keep QSettings out of the developer's real registry/config.
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackTest")
    return ThemeManager()


@pytest.fixture
def window(qapp, theme, service: VocabularyService, make_pdf):
    service.import_document(make_pdf([SIMPLE_PDF]))
    win = MainWindow(service, theme)
    yield win
    win.close()


def word(**kwargs) -> StoredWord:
    defaults = dict(
        id=1, word="abandon", normalized_word="abandon",
        part_of_speech="verb", cefr_level="B2", sources=("Oxford 3000",),
    )
    return StoredWord(**{**defaults, **kwargs})


# -- review widget ----------------------------------------------------------


def test_the_word_and_its_metadata_are_displayed(qapp) -> None:
    widget = ReviewWidget()
    widget.show_word(word(), position=1, total=3000)

    assert widget._word_label.text() == "abandon"
    assert widget._meta_label.text() == "verb  ·  B2"
    assert widget._source_label.text() == "Oxford 3000"
    assert widget._position_label.text() == "1 / 3,000"


def test_absent_metadata_hides_its_row_rather_than_leaving_a_gap(qapp) -> None:
    widget = ReviewWidget()
    widget.show_word(
        word(part_of_speech=None, cefr_level=None, definition=None, sources=()),
        position=1,
        total=10,
    )

    assert not widget._meta_label.isVisible()
    assert not widget._definition_label.isVisible()
    assert not widget._source_label.isVisible()


def test_clicking_i_know_emits_a_known_answer(qapp) -> None:
    widget = ReviewWidget()
    widget.show_word(word(id=7), position=1, total=10)
    received: list[tuple[int, bool]] = []
    widget.answered.connect(lambda wid, known: received.append((wid, known)))

    widget._known_button.click()

    assert received == [(7, True)]


def test_clicking_i_dont_know_emits_an_unknown_answer(qapp) -> None:
    widget = ReviewWidget()
    widget.show_word(word(id=7), position=1, total=10)
    received: list[tuple[int, bool]] = []
    widget.answered.connect(lambda wid, known: received.append((wid, known)))

    widget._unknown_button.click()

    assert received == [(7, False)]


@pytest.mark.parametrize(
    "key, expected",
    [
        (Qt.Key.Key_K, True),
        (Qt.Key.Key_Left, True),
        (Qt.Key.Key_U, False),
        (Qt.Key.Key_Right, False),
    ],
)
def test_keyboard_shortcuts_answer(qapp, qtbot, key, expected) -> None:
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    widget.show_word(word(id=3), position=1, total=10)
    received: list[tuple[int, bool]] = []
    widget.answered.connect(lambda wid, known: received.append((wid, known)))

    qtbot.keyClick(widget, key)

    assert received == [(3, expected)]


def test_enter_repeats_the_previous_answer(qapp, qtbot) -> None:
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    received: list[tuple[int, bool]] = []
    widget.answered.connect(lambda wid, known: received.append((wid, known)))

    widget.show_word(word(id=1), position=1, total=10)
    qtbot.keyClick(widget, Qt.Key.Key_U)
    widget.show_word(word(id=2), position=2, total=10)
    qtbot.keyClick(widget, Qt.Key.Key_Return)

    assert received == [(1, False), (2, False)]


def test_enter_does_nothing_before_the_first_answer(qapp, qtbot) -> None:
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    widget.show_word(word(), position=1, total=10)
    received: list[tuple[int, bool]] = []
    widget.answered.connect(lambda wid, known: received.append((wid, known)))

    qtbot.keyClick(widget, Qt.Key.Key_Return)

    assert received == []


def test_answering_twice_in_a_row_is_ignored(qapp) -> None:
    """A double click must not consume two words."""
    widget = ReviewWidget()
    widget.show_word(word(id=5), position=1, total=10)
    received: list[tuple[int, bool]] = []
    widget.answered.connect(lambda wid, known: received.append((wid, known)))

    widget._known_button.click()
    widget._known_button.click()

    assert received == [(5, True)]


def test_undo_is_disabled_until_something_has_been_answered(qapp) -> None:
    widget = ReviewWidget()
    assert not widget._undo_button.isEnabled()
    widget.set_undo_enabled(True)
    assert widget._undo_button.isEnabled()


def test_undo_button_emits_its_signal(qapp) -> None:
    widget = ReviewWidget()
    widget.set_undo_enabled(True)
    received: list[bool] = []
    widget.undo_requested.connect(lambda: received.append(True))

    widget._undo_button.click()

    assert received == [True]


# -- progress ---------------------------------------------------------------


def test_progress_bar_shows_percentage_and_counts(qapp) -> None:
    bar = ProgressBarWidget()
    bar.update_progress(Progress(total=3000, known=120, unknown=12))

    assert bar._bar.value() == 4  # 132 / 3000
    assert bar._caption.text() == "132 / 3,000"


def test_stats_bar_shows_every_counter(qapp) -> None:
    stats = StatsBar()
    stats.update_progress(Progress(total=3000, known=120, unknown=12))

    assert stats._known._value.text() == "120"
    assert stats._unknown._value.text() == "12"
    assert stats._remaining._value.text() == "2,868"
    assert stats._total._value.text() == "3,000"


# -- themes -----------------------------------------------------------------


def test_both_themes_produce_a_complete_stylesheet() -> None:
    for palette in (LIGHT, DARK):
        sheet = build_stylesheet(palette)
        assert palette.background in sheet
        assert palette.known in sheet
        assert palette.unknown in sheet
        assert "{" not in sheet.replace("{{", "").split("QWidget")[0]


def test_light_and_dark_are_genuinely_different() -> None:
    assert build_stylesheet(LIGHT) != build_stylesheet(DARK)
    assert LIGHT.background != DARK.background
    assert LIGHT.text != DARK.text


def test_toggling_switches_between_the_two_themes(theme: ThemeManager) -> None:
    theme.apply(ThemeName.LIGHT)
    assert theme.toggle() is ThemeName.DARK
    assert theme.toggle() is ThemeName.LIGHT


def test_applying_a_theme_sets_the_application_stylesheet(qapp, theme) -> None:
    theme.apply(ThemeName.DARK)
    assert DARK.background in qapp.styleSheet()
    theme.apply(ThemeName.LIGHT)
    assert LIGHT.background in qapp.styleSheet()


def test_the_chosen_theme_is_remembered(qapp, theme) -> None:
    theme.apply(ThemeName.DARK)
    assert ThemeManager().current is ThemeName.DARK
    theme.apply(ThemeName.LIGHT)
    assert ThemeManager().current is ThemeName.LIGHT


# -- main window ------------------------------------------------------------


def test_an_empty_database_shows_the_welcome_screen(qapp, theme, service) -> None:
    win = MainWindow(service, theme)
    try:
        assert win._pages.currentIndex() == _WELCOME_PAGE
    finally:
        win.close()


def test_an_imported_document_shows_the_review_screen(window: MainWindow) -> None:
    assert window._pages.currentIndex() == _REVIEW_PAGE
    assert window._review._word_label.text()


def test_answering_advances_the_window_to_the_next_word(window: MainWindow) -> None:
    first = window._review._word_label.text()
    window._review._known_button.click()
    assert window._review._word_label.text() != first


def test_finishing_every_word_shows_the_completed_screen(window: MainWindow) -> None:
    for _ in range(5):
        window._review._unknown_button.click()

    assert window._pages.currentIndex() == _COMPLETED_PAGE
    assert window._service.get_progress().unknown == 5


def test_export_is_disabled_until_a_word_is_marked_unknown(window: MainWindow) -> None:
    assert not window._export_button.isEnabled()
    window._review._unknown_button.click()
    assert window._export_button.isEnabled()


def test_marking_only_known_words_leaves_export_disabled(window: MainWindow) -> None:
    for _ in range(5):
        window._review._known_button.click()
    assert not window._export_button.isEnabled()


def test_undo_through_the_window_restores_the_word(window: MainWindow) -> None:
    first = window._review._word_label.text()
    window._review._known_button.click()
    assert window._undo_action.isEnabled()

    window.undo_last_answer()

    assert window._review._word_label.text() == first
    assert window._service.get_progress().known == 0
    assert not window._undo_action.isEnabled()


def test_the_window_reflects_progress_in_its_counters(window: MainWindow) -> None:
    window._review._known_button.click()
    window._review._unknown_button.click()

    assert window._stats._known._value.text() == "1"
    assert window._stats._unknown._value.text() == "1"
    assert window._stats._remaining._value.text() == "3"


def test_the_theme_button_names_the_theme_it_switches_to(window: MainWindow) -> None:
    window._theme.apply(ThemeName.LIGHT)
    window._update_theme_button_text()
    assert window._theme_button.text() == "Dark"

    window.toggle_theme()

    assert window._theme.current is ThemeName.DARK
    assert window._theme_button.text() == "Light"
