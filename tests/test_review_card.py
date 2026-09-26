"""The review card on the Today page, driven from the keyboard and the mouse.

The engine's rules are tested in test_review_flow; these check that the card
shows what the flow says and passes on exactly what was done: keys 1–4 or
A–D choose, a right answer shows the word and its definition and asks
Again / Hard / Good / Easy (keys 1–4), a wrong one shows the right word and
its definition and waits for Enter, and a new word is shown whole.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import Task
from lexitrack.models.source import Source
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import (
    CardRepository,
    ContextRepository,
    ListRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_flow import StepKind
from lexitrack.ui.components.review_card import _INNER_WIDTH, AnswerButton, ReviewCard, _fit
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


def _study(database: Database, qtbot, *, introduce: bool = True) -> StudyPage:
    clock = FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    ids = list(WordRepository(database).add_entries(
        [entry(word, definition=meaning, part_of_speech="noun") for word, meaning in WORDS.items()],
        source.id,
    ).word_ids)
    ContextRepository(database).add_many(ids[1], ["We keep old toys in the attic."])
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, ids)
    StateRepository(database).set_status_many(ids, ReviewStatus.UNKNOWN)
    engine = LearningService(database, clock)
    engine.create_plan("Test plan", list_ids=[a_list.id])
    if introduce:
        engine.introduce()
        clock.advance_to_day_start(1)
        clock.advance(hours=4)
    study = StudyPage(engine)
    qtbot.addWidget(study)
    study.show()
    study.start_session()
    return study


@pytest.fixture
def page(qapp, database: Database, qtbot):
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackCardTest")
    QSettings().clear()
    ThemeManager().apply(ThemeName.LIGHT)
    yield _study(database, qtbot)
    QSettings().clear()


def _logs(page: StudyPage, word_id: int):
    return CardRepository(page._engine.database).logs_for_word(word_id)


def test_a_question_shows_the_definition_and_four_lettered_words(qtbot, page) -> None:
    card = page.card
    step = card.step
    assert step.kind is StepKind.QUESTION and step.question.task is Task.DEFINITION_TO_WORD
    assert card.prompt_label.text() == step.word.definition
    assert card.prompt_detail.text() == "Which word is it?"
    assert card.task_label.text() == "DEFINITION → WORD"
    titles = [button.title.text() for button in card.word_buttons]
    assert titles == [option.text for option in step.question.options]
    assert [button.key.text() for button in card.word_buttons] == ["A", "B", "C", "D"]
    assert card.word_grid.isVisibleTo(card) and not card.definition_list.isVisibleTo(card)


def test_a_right_answer_shows_the_word_and_asks_how_it_went(qtbot, page) -> None:
    card = page.card
    step = card.step
    qtbot.keyClick(page, Qt.Key.Key_1 + step.question.answer)
    assert card.rating and not card.waiting
    assert card.result_title.text() == "✓  Correct"
    assert step.word.word in card.result_lines.text()
    assert step.word.definition in card.result_lines.text()
    assert card.rating_row.isVisibleTo(card) and not card.continue_holder.isVisibleTo(card)
    assert card.word_buttons[step.question.answer].property("result") == "right"
    assert _logs(page, step.word.id) == [], "nothing is recorded before the rating"

    # 1–4 now rate: 3 is Good. The next question follows at once.
    qtbot.keyClick(page, Qt.Key.Key_3)
    (log,) = _logs(page, step.word.id)
    assert (log.rating, log.correct, log.task) == (Rating.GOOD, True, "definition_to_word")
    assert card.step is not step and not card.rating
    assert "Good" in card.undo_button.text()


def test_a_wrong_answer_shows_the_right_word_and_its_definition(qtbot, page) -> None:
    card = page.card
    step = card.step
    wrong = (step.question.answer + 1) % 4
    qtbot.keyClick(page, [Qt.Key.Key_A, Qt.Key.Key_B, Qt.Key.Key_C, Qt.Key.Key_D][wrong])
    assert card.waiting and not card.rating
    assert card.result_title.text() == "✗  Incorrect"
    lines = card.result_lines.text()
    assert "Correct answer:" in lines and step.word.word in lines
    assert "Definition:" in lines and step.word.definition in lines
    assert "comes back in a few cards" in card.result_note.text()
    assert card.word_buttons[wrong].property("result") == "wrong"
    assert card.word_buttons[step.question.answer].property("result") == "right"
    (log,) = _logs(page, step.word.id)
    assert (log.rating, log.correct) == (Rating.AGAIN, False)

    # The option keys do nothing now; Enter moves on.
    qtbot.keyClick(page, Qt.Key.Key_1)
    assert card.step is step
    qtbot.keyClick(page, Qt.Key.Key_Return)
    assert card.step is not step


def test_the_mouse_works_as_the_keys_do(qtbot, page) -> None:
    card = page.card
    step = card.step
    qtbot.mouseClick(card.word_buttons[step.question.answer], Qt.MouseButton.LeftButton)
    assert card.rating
    qtbot.mouseClick(card.rating_buttons[Rating.EASY], Qt.MouseButton.LeftButton)
    (log,) = _logs(page, step.word.id)
    assert log.rating is Rating.EASY


def test_a_context_question_shows_the_sentence_and_four_definitions(qtbot, page) -> None:
    """A word with a context, asked Definition → Word last time, is asked
    Context → Definition next."""
    flow = page.flow
    attic = next(s for s in [flow.current] + flow._steps if s.word.word == "attic")
    while page.card.step.word.word != "attic":
        step = page.card.step
        qtbot.keyClick(page, Qt.Key.Key_1 + step.question.answer)
        qtbot.keyClick(page, Qt.Key.Key_3)
    assert attic.task is Task.DEFINITION_TO_WORD
    qtbot.keyClick(page, Qt.Key.Key_1 + page.card.step.question.answer)
    qtbot.keyClick(page, Qt.Key.Key_3)
    page.end_session()

    page._engine.clock.advance_to_day_start(40)
    page.start_session()
    while page.card.step.word.word != "attic":
        step = page.card.step
        qtbot.keyClick(page, Qt.Key.Key_1 + step.question.answer)
        qtbot.keyClick(page, Qt.Key.Key_3)
    card = page.card
    question = card.step.question
    assert question.task is Task.CONTEXT_TO_DEFINITION
    assert "<b>attic</b>" in card.prompt_label.text()
    assert card.prompt_detail.text() == "Which definition fits the word in bold?"
    assert card.definition_list.isVisibleTo(card) and not card.word_grid.isVisibleTo(card)
    assert [b.title.text() for b in card.definition_buttons] == [o.text for o in question.options]
    wrong = (question.answer + 1) % 4
    qtbot.keyClick(page, Qt.Key.Key_1 + wrong)
    lines = card.result_lines.text()
    assert "Word:" in lines and "Correct definition:" in lines
    assert "a room just below the roof of a house" in lines


def test_a_new_word_is_shown_whole_then_asked(qapp, database: Database, qtbot) -> None:
    ThemeManager().apply(ThemeName.LIGHT)
    page = _study(database, qtbot, introduce=False)
    card = page.card
    step = card.step
    assert step.kind is StepKind.TEACH
    assert card.teach_face.isVisibleTo(card) and not card.question_face.isVisibleTo(card)
    assert card.word_label.text() == "arid"
    assert card.meta_label.text() == "4 letters · noun"
    assert "very dry, with little rain" in card.teach_label.text()
    assert card.waiting
    while page.card.step.kind is StepKind.TEACH:
        if page.card.step.word.word == "attic":
            assert "<b>attic</b>" in page.card.teach_label.text()
        qtbot.keyClick(page, Qt.Key.Key_Return)
    step = page.card.step
    assert step.kind is StepKind.QUESTION and not step.rated
    qtbot.keyClick(page, Qt.Key.Key_1 + step.question.answer)
    # Practice: no rating asked for, and nothing rated.
    assert page.card.waiting and not page.card.rating
    assert "Practice" in page.card.result_note.text()
    assert _logs(page, step.word.id) == []


def test_escape_ends_the_session_and_undo_takes_back_the_answer(qtbot, page) -> None:
    card = page.card
    step = card.step
    qtbot.keyClick(page, Qt.Key.Key_1 + step.question.answer)
    qtbot.keyClick(page, Qt.Key.Key_4)
    assert _logs(page, step.word.id)
    qtbot.keyClick(page, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert card.step.word.id == step.word.id
    assert all(log.undone_at for log in _logs(page, step.word.id))
    qtbot.keyClick(page, Qt.Key.Key_Escape)
    assert not page.in_session


# -- layout ------------------------------------------------------------------------------


def test_a_prompt_is_as_tall_as_its_text_even_after_a_longer_one(qapp) -> None:
    """Qt clamps a label's measured height to the one it was given before:
    a one-line prompt after a two-line one kept two lines' room."""
    card = ReviewCard()
    label = card.prompt_label
    label.setText("a long definition that goes on " * 12)
    _fit(label, _INNER_WIDTH)
    tall = label.height()
    label.setText("short")
    _fit(label, _INNER_WIDTH)
    assert label.height() < tall
    assert label.height() == label.heightForWidth(_INNER_WIDTH)


def test_a_one_line_definition_option_centres_its_key(qapp) -> None:
    button = AnswerButton("", "A", None, wrap=True)
    button.set_title("to pay attention", _INNER_WIDTH)
    assert button.layout().itemAt(0).alignment() & Qt.AlignmentFlag.AlignVCenter
    button.set_title("a very long definition that needs more than one line " * 4, _INNER_WIDTH)
    assert button.layout().itemAt(0).alignment() & Qt.AlignmentFlag.AlignTop
