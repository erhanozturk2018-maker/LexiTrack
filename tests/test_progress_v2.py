"""Memory and skill on the Progress page (services/progress.py, ui/progress_page.py).

Memory and skill are shown apart, as the engine keeps them; the evidence
beside them is counted from the record; Known is only ever the learner's yes.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import (
    Effort,
    LearningAttempt,
    MemoryResult,
    Phase,
    Role,
    Task,
)
from lexitrack.models.settings import Setting
from lexitrack.models.skill import SkillStage
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.services.learning_service import LearningService
from lexitrack.services.progress import LONG_INTERVAL_DAYS, ProgressService
from lexitrack.ui import progress_page as page_module
from lexitrack.ui.progress_page import ANSWERS, OVERVIEW, READY, SCHEDULER, WORDS, ProgressPage

from .flow_helpers import record_known_evidence
from .test_progress_history import clock, engine, words  # noqa: F401 - fixtures


def _review(learning: LearningService, word_id: int, task: Task, success: bool = True,
            effort: Effort = Effort.NORMAL) -> None:
    now = learning.clock
    rating = Rating.GOOD if success else Rating.AGAIN
    learning.review(
        word_id,
        rating,
        memory_result=MemoryResult.RECALLED if success else MemoryResult.FORGOTTEN,
        attempts=[LearningAttempt(
            word_id=word_id, at=now.now_utc(), on_day=now.today(), phase=Phase.REVIEW,
            role=Role.PRIMARY, task=task, success=success, effort=effort,
        )],
    )


@pytest.fixture
def studied(engine: LearningService, clock: FrozenClock):  # noqa: F811
    """Five words introduced and answered once the next day, the V2 way."""
    ids = [word.id for word in engine.introduce().introduced]
    clock.advance_to_day_start(1)
    _review(engine, ids[0], Task.MEANING_TO_WORD, effort=Effort.INSTANT)
    _review(engine, ids[1], Task.WORD_TO_MEANING)
    _review(engine, ids[2], Task.CONTEXT_CLOZE, success=False)
    engine.answer(ids[3], Rating.GOOD)  # an answer the old way
    return ids


def test_each_answer_says_what_it_asked_and_showed(
    database: Database, engine: LearningService, studied  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    rows = {row.entry.word_id: row for row in progress.answers()}
    assert rows[studied[0]].asked is Task.MEANING_TO_WORD
    assert rows[studied[0]].memory_result is MemoryResult.RECALLED
    assert rows[studied[2]].memory_result is MemoryResult.FORGOTTEN
    # The old route asked word -> meaning and recorded that, but no memory result.
    assert rows[studied[3]].asked is Task.WORD_TO_MEANING
    assert rows[studied[3]].memory_result is None

    journey = progress.journey(studied[0])
    answer = next(step for step in journey.steps if step.kind == "answer")
    assert answer.asked is Task.MEANING_TO_WORD
    assert journey.skill is not None and journey.skill.stage is SkillStage.RECALLED


def test_skill_is_counted_beside_memory(
    database: Database, engine: LearningService, studied, clock  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    overview = progress.skill_overview()
    stages = {stage.label: stage.count for stage in overview.stages}
    # The old route's answer counts as recognition; the failed and the
    # unanswered words have only been encountered.
    assert stages == {"Encountered": 2, "Recognised": 2, "Recalled": 1, "Productive": 0}
    assert sum(stages.values()) == len(progress.words())
    assert overview.automatic == 0, "instant on one day is not yet automatic"

    clock.advance_to_day_start(3)
    _review(engine, studied[0], Task.MEANING_TO_WORD, effort=Effort.INSTANT)
    assert progress.skill_overview().automatic == 1


def test_a_word_remembered_after_a_long_gap_is_evidence(
    database: Database, engine: LearningService, studied, clock  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    assert progress.skill_overview().long_interval == 0
    clock.advance_to_day_start(LONG_INTERVAL_DAYS + 1)
    _review(engine, studied[1], Task.WORD_TO_MEANING)
    _review(engine, studied[4], Task.WORD_TO_MEANING, success=False)
    assert progress.skill_overview().long_interval == 1


def test_the_last_30_days_leave_older_answers_out(
    database: Database, engine: LearningService, studied, clock  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    recent = progress.recent()
    assert (recent.answers, recent.agains, recent.introduced) == (4, 1, 5)
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


def test_known_is_offered_and_marked_only_on_a_yes(
    page, engine: LearningService, database: Database, studied  # noqa: F811
) -> None:
    assert page.suggestions.isHidden(), "nothing is long-term yet"
    engine.save_settings({Setting.MASTERY_STABILITY_DAYS: 1})
    record_known_evidence(engine, studied, gap_days=5)
    page.refresh()
    ready = [word.id for word in engine.known_suggestions()]
    assert not page.suggestions.isHidden()
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
    engine.save_settings({Setting.MASTERY_STABILITY_DAYS: 1})
    record_known_evidence(engine, studied, gap_days=5)
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
    assert page.answers_table.rowCount() == 1
    assert "1 of 4" in page.answers_title.text()
    page._set_answer_filter("all")
    word = page.answers_table.item(0, 1).text()
    page.answer_search.setText(word)
    assert page.answers_table.rowCount() == 1


def test_the_rates_come_with_their_counts(
    database: Database, engine: LearningService, studied  # noqa: F811
) -> None:
    """studied: meaning→word right, word→meaning right, a sentence missed
    (nothing else tried: Forgotten), and one answer the old way."""
    metrics = ProgressService(database, engine).metrics()
    # First questions at level 2+: meaning→word right, the sentence missed.
    assert (metrics.first_attempt.hits, metrics.first_attempt.total) == (1, 2)
    assert metrics.productive.total == 0 and metrics.productive.share is None
    assert (metrics.relearn.hits, metrics.relearn.total) == (1, 4)
    assert metrics.recurring_failures == 0
    routes = {line.route: (line.answers, line.agains) for line in metrics.routes}
    assert routes == {"v1": (1, 0), "v2": (3, 1)}


def test_a_word_lists_its_milestones_and_how_often_it_was_forgotten(
    database: Database, engine: LearningService, studied, clock  # noqa: F811
) -> None:
    progress = ProgressService(database, engine)
    recalled = progress.journey(studied[0])
    labels = [m.label for m in recalled.milestones]
    assert labels == ["First recalled"] and recalled.forgotten == 0
    missed = progress.journey(studied[2])
    assert missed.milestones == () and missed.forgotten == 1
    # A context recalled later, and a long gap survived.
    clock.advance_to_day_start(25)
    _review(engine, studied[2], Task.CONTEXT_CLOZE)
    later = progress.journey(studied[2])
    assert [m.label for m in later.milestones] == [
        "First recalled", "First recalled from a sentence", "First recalled after 21+ days",
    ]
