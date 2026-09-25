"""The V2 review card on the Today page, driven from the keyboard.

The engine's rules are tested in test_review_flow; these check that the card
passes on exactly what was done: an Enter that submits does not also move
past its own feedback, the hint and Escape work from inside the text box,
and the keys 1–4 choose.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import SelfReport
from lexitrack.models.source import Source
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import (
    ListRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_flow import StepKind
from lexitrack.ui.study_page import StudyPage
from lexitrack.ui.theme import ThemeManager, ThemeName

from .conftest import entry

WORDS = {
    "arid": "very dry, with little rain",
    "attic": "a room just below the roof of a house",
    "avenue": "a wide street in a town",
    "barn": "a large farm building for animals or crops",
    "meadow": "a field of grass and wild flowers",
}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture
def page(qapp, database: Database, qtbot):
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackCardTest")
    QSettings().clear()
    ThemeManager().apply(ThemeName.LIGHT)
    clock = FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    ids = list(WordRepository(database).add_entries(
        [entry(word, definition=meaning, part_of_speech="noun") for word, meaning in WORDS.items()],
        source.id,
    ).word_ids)
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, ids)
    StateRepository(database).set_status_many(ids, ReviewStatus.UNKNOWN)
    engine = LearningService(database, clock)
    engine.create_plan("Test plan", list_ids=[a_list.id])
    engine.introduce()
    clock.advance_to_day_start(1)
    clock.advance(hours=4)
    study = StudyPage(engine)
    qtbot.addWidget(study)
    study.show()
    study.start_session()
    yield study
    QSettings().clear()


def _type(qtbot, page: StudyPage, text: str) -> None:
    page.card.answer_input.setText(text)
    qtbot.keyClick(page.card.answer_input, Qt.Key.Key_Return)


def test_a_right_answer_asks_how_it_came_then_waits(qtbot, page) -> None:
    card = page.card
    first = card.step
    assert first.kind is StepKind.TYPE
    assert card.task_label.text() == "MEANING → WORD"
    assert first.word.word not in card.prompt_label.text()
    _type(qtbot, page, first.word.word)
    # The Enter that submitted must not also have moved on, nor answered.
    assert card.assessing and card.step is first and not card.waiting
    assert card.feedback_label.text().startswith(f"✓ “{first.word.word}”")
    assert "how did it come" in card.feedback_label.text()
    assert card.answer_input.property("result") == "right"
    assert not card.report_row.isHidden() and card.continue_button.isHidden()
    labels = [b.title.text() for b in card.report_buttons.values()]
    assert labels == ["Effortful", "Remembered", "Instant"]
    qtbot.keyClick(page, Qt.Key.Key_Return)
    assert page.flow.answered == 0, "Enter is not a report"
    qtbot.keyClick(page, Qt.Key.Key_4)
    assert page.flow.answered == 1 and card.waiting
    assert page.flow._runs[first.word.id].resolution.rating is Rating.EASY
    qtbot.keyClick(page, Qt.Key.Key_Return)
    assert not card.waiting and card.step is not first
    assert card.answer_input.text() == "" and card.answer_input.isEnabled()


def test_the_hint_is_telemetry_and_the_report_decides(qtbot, page) -> None:
    card = page.card
    word = card.step.word.word
    card.answer_input.setFocus()
    qtbot.keyClick(card.answer_input, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
    assert card.hinted and card.hint_label.text().startswith(word[0])
    _type(qtbot, page, word)
    card.report_buttons[SelfReport.EFFORTFUL].click()
    assert page.flow._runs[card.step.word.id].resolution.rating is Rating.HARD


def test_a_wrong_word_is_followed_by_a_choice_answered_with_a_number(qtbot, page) -> None:
    card = page.card
    word = card.step.word
    _type(qtbot, page, "zzz")
    assert "One more question" in card.feedback_label.text()
    assert word.word not in card.feedback_label.text(), "the answer stays hidden"
    qtbot.keyClick(page, Qt.Key.Key_Return)
    assert card.step.kind is StepKind.CHOOSE
    right = next(i for i, o in enumerate(card.step.options) if o.id == word.id)
    qtbot.keyClick(page, getattr(Qt.Key, f"Key_{right + 1}"))
    assert card.waiting and "you recognised it" in card.feedback_label.text()
    assert card.choice_buttons[right].property("result") == "right"


def test_forgot_is_an_empty_enter(qtbot, page) -> None:
    card = page.card
    _type(qtbot, page, "")
    assert card.waiting and card.answer_input.placeholderText() == "No answer"


def test_escape_from_the_text_box_ends_the_session(qtbot, page) -> None:
    card = page.card
    card.answer_input.setFocus()
    qtbot.keyClick(card.answer_input, Qt.Key.Key_Escape)
    assert not page.in_session


def test_undo_puts_the_word_back_as_a_question(qtbot, page) -> None:
    card = page.card
    first = card.step.word
    _type(qtbot, page, first.word)
    qtbot.keyClick(page, Qt.Key.Key_3)
    assert not card.undo_button.isHidden()
    card.undo_button.click()
    assert card.step.word.id == first.id and card.step.kind is StepKind.TYPE
    assert not card.waiting
    assert page.flow.answered == 0


def test_a_session_left_open_is_resumed_as_it_stood(qtbot, page) -> None:
    """The app closed with a right answer waiting for its report."""
    card = page.card
    first = card.step
    _type(qtbot, page, first.word.word)
    assert card.assessing
    session = page.flow.session_id
    # A new page, as after starting LexiTrack again.
    other = StudyPage(page._engine)
    qtbot.addWidget(other)
    other.show()
    other.start_session()
    assert other.flow.session_id == session
    assert other.card.step.word.id == first.word.id and other.card.assessing
    assert other.card.answer_input.text() == first.prompt.answer
    qtbot.keyClick(other, Qt.Key.Key_3)
    assert other.flow.answered == 1
