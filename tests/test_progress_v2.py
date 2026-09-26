"""How the answers go, on the Progress page (services/progress.py, ui/progress_page.py).

Every answer says what it asked and whether it was right; the rates are
counted from the day's questions, each with its count; a right answer after a
long gap is the case for Known, and Known is only ever the learner's yes.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import Effort, LearningAttempt, Phase, Role, Task
from lexitrack.models.settings import Setting
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.services.learning_service import LearningService
from lexitrack.services.progress import LONG_INTERVAL_DAYS, ProgressService
from lexitrack.ui import progress_page as page_module
from lexitrack.ui.progress_page import ANSWERS, OVERVIEW, READY, SCHEDULER, WORDS, ProgressPage

from .flow_helpers import record_known_evidence
from .test_progress_history import clock, engine, words  # noqa: F401 - fixtures


def _review(learning: LearningService, word_id: int, task: Task, correct: bool = True,
            rating: Rating = Rating.GOOD) -> None:
    now = learning.clock
    rating = rating if correct else Rating.AGAIN
    learning.review(
        word_id,
        rating,
        task=task,
        correct=correct,
        attempts=[LearningAttempt(
            word_id=word_id, at=now.now_utc(), on_day=now.today(), phase=Phase.REVIEW,
            role=Role.PRIMARY, task=task, correct=correct,
            effort=Effort.of(rating) if correct else None,
        )],
    )


@pytest.fixture
def studied(engine: LearningService, clock: FrozenClock):  # noqa: F811
    """Five words introduced and answered once the next day."""
    ids = [word.id for word in engine.introduce().introduced]
    clock.advance_to_day_start(1)
    _review(engine, ids[0], Task.DEFINITION_TO_WORD, rating=Rating.EASY)
    _review(engine, ids[1], Task.CONTEXT_TO_DEFINITION)
    _review(engine, ids[2], Task.DEFINITION_TO_WORD, correct=False)
    # Right, but rated Again: it did not come.
    _review(engine, ids[3], Task.CONTEXT_TO_DEFINITION, rating=Rating.AGAIN)
    return ids


def test_each_answer_says_what_it_asked_and_whether_it_was_right(
    database: Database, engine: LearningService, studied  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    rows = {row.entry.word_id: row for row in progress.answers()}
    assert (rows[studied[0]].asked, rows[studied[0]].correct) == (Task.DEFINITION_TO_WORD, True)
    assert (rows[studied[2]].asked, rows[studied[2]].correct) == (Task.DEFINITION_TO_WORD, False)
    assert rows[studied[3]].correct is True and rows[studied[3]].entry.rating is Rating.AGAIN

    journey = progress.journey(studied[1])
    step = next(step for step in journey.steps if step.kind == "answer")
    assert (step.asked, step.correct) == (Task.CONTEXT_TO_DEFINITION, True)


def test_the_rates_come_with_their_counts(
    database: Database, engine: LearningService, studied  # noqa: F811
) -> None:
    metrics = ProgressService(database, engine).metrics()
    assert (metrics.first_attempt.hits, metrics.first_attempt.total) == (3, 4)
    assert (metrics.definition_to_word.hits, metrics.definition_to_word.total) == (1, 2)
    assert (metrics.context_to_definition.hits, metrics.context_to_definition.total) == (2, 2)
    assert (metrics.relearn.hits, metrics.relearn.total) == (2, 4)
    assert metrics.long_interval.total == 0 and metrics.long_interval.share is None
    assert metrics.recurring_failures == 0
    routes = {line.route: (line.answers, line.agains) for line in metrics.routes}
    assert routes == {"v3": (4, 2)}


def test_practice_is_not_counted_in_the_rates(
    database: Database, engine: LearningService, studied  # noqa: F811
) -> None:
    now = engine.clock
    engine.record_practice(
        LearningAttempt(word_id=studied[2], at=now.now_utc(), on_day=now.today(),
                        phase=Phase.RELEARN, role=Role.RETRIEVAL,
                        task=Task.CONTEXT_TO_DEFINITION, correct=True),
        None,
    )
    metrics = ProgressService(database, engine).metrics()
    assert metrics.first_attempt.total == 4


def test_a_right_answer_after_a_long_gap_is_counted_and_a_wrong_one_is_not(
    database: Database, engine: LearningService, studied, clock  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    clock.advance_to_day_start(LONG_INTERVAL_DAYS + 1)
    _review(engine, studied[1], Task.DEFINITION_TO_WORD)
    _review(engine, studied[4], Task.DEFINITION_TO_WORD, correct=False)
    late = progress.metrics().long_interval
    assert (late.hits, late.total) == (1, 2)
    assert [w.id for w in engine.known_suggestions()] == [studied[1]]


def test_the_last_30_days_leave_older_answers_out(
    database: Database, engine: LearningService, studied, clock  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    recent = progress.recent()
    assert (recent.answers, recent.agains, recent.introduced) == (4, 2, 5)
    clock.advance_to_day_start(40)
    later = progress.recent()
    assert (later.answers, later.introduced) == (0, 0)


def test_memory_shows_words_in_long_term_memory_apart_from_known(
    database: Database, engine: LearningService, studied  # noqa: F811
) -> None:
    engine.save_settings({Setting.MASTERY_STABILITY_DAYS: 1})
    progress = ProgressService(database, engine)
    stages = {s.label: s.count for s in progress.pipeline()}
    lasting = sum(1 for row in progress.words() if (row.stability or 0) >= 1)
    assert lasting and stages["1+ days, not Known"] == lasting
    assert stages["Known"] == 0, "long-term is not Known until the learner says so"
    # Memory alone is no case for Known: nothing is offered yet.
    assert engine.known_suggestions() == []


def test_a_word_lists_its_milestones_and_how_often_it_was_rated_again(
    database: Database, engine: LearningService, studied, clock  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    first = progress.journey(studied[0])
    assert [m.label for m in first.milestones] == ["First right from its definition"]
    assert first.forgotten == 0
    missed = progress.journey(studied[2])
    assert missed.milestones == () and missed.forgotten == 1
    clock.advance_to_day_start(25)
    _review(engine, studied[2], Task.CONTEXT_TO_DEFINITION)
    later = progress.journey(studied[2])
    assert [m.label for m in later.milestones] == [
        "First right from a context", "First right after 21+ days",
    ]


# -- the page ----------------------------------------------------------------


@pytest.fixture
def page(qtbot, database: Database, engine: LearningService, studied):  # noqa: F811
    QApplication.instance() or QApplication([])
    widget = ProgressPage(ProgressService(database, engine), engine)
    qtbot.addWidget(widget)
    widget.resize(1200, 900)
    widget.show()
    widget.refresh()
    return widget


def test_one_tab_at_a_time_and_only_its_height(page) -> None:
    for tab in (WORDS, ANSWERS, SCHEDULER, OVERVIEW):
        page.show_tab(tab)
        visible = [key for key, widget in page._pages.items() if widget.isVisible()]
        assert visible == [tab]
        assert page.tab_buttons[tab].isChecked()


def test_the_rate_tiles_show_each_rate_with_its_count(page) -> None:
    assert page.rate_tiles["first_attempt"].value_label.text() == "75%  (3 of 4)"
    assert page.rate_tiles["context_to_definition"].value_label.text() == "100%  (2 of 2)"
    assert page.rate_tiles["long_interval"].value_label.text() == "— none yet"


def test_known_is_offered_and_marked_only_on_a_yes(
    page, engine: LearningService, database: Database, studied  # noqa: F811
) -> None:
    assert page.suggestions.isHidden(), "nothing answered after a long gap yet"
    record_known_evidence(engine, studied, gap_days=30)
    page.refresh()
    ready = [word.id for word in engine.known_suggestions()]
    assert ready and not page.suggestions.isHidden()
    assert str(len(ready)) in page.suggestions_title.text()
    messages: list[str] = []
    page.notify.connect(messages.append)
    page._confirm([ready[0]])
    status = database.connection.execute(
        "SELECT status FROM user_word_state WHERE word_id = ?", (ready[0],)
    ).fetchone()[0]
    assert status == ReviewStatus.KNOWN.value
    assert messages == ["1 word marked Known."]
    assert len(engine.known_suggestions()) == len(ready) - 1


def test_the_rest_of_the_suggestions_open_in_words(
    page, engine: LearningService, monkeypatch, studied  # noqa: F811
) -> None:
    monkeypatch.setattr(page_module, "_SUGGESTIONS_SHOWN", 1)
    record_known_evidence(engine, studied, gap_days=30)
    page.refresh()
    ready = len(engine.known_suggestions())
    assert ready > 1 and not page.suggestions_more.isHidden()
    page.suggestions_more.click()
    assert page.tab == WORDS
    assert page.filter_buttons[READY].isChecked()
    assert page.words_table.rowCount() == ready


def test_answers_filter_by_again_and_by_word(page) -> None:
    page.show_tab(ANSWERS)
    assert page.answers_table.rowCount() == 4
    page._set_answer_filter("again")
    assert page.answers_table.rowCount() == 2
    assert "2 of 4" in page.answers_title.text()
    page._set_answer_filter("all")
    word = page.answers_table.item(0, 1).text()
    page.answer_search.setText(word)
    assert page.answers_table.rowCount() == 1
    assert page.answers_table.item(0, 3).text() in ("Right", "Wrong")
