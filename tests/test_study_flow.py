"""The study session as a state machine (services/study_flow.py).

The flow was moved out of the Today page without changing what it does; the
page's own tests cover that. These cover what the move added: the flow runs
with no widgets, its state is saved on the session after every step, and a
saved flow can be restored.
"""

from __future__ import annotations

import json
import pathlib

from lexitrack.core.clock import FrozenClock
from lexitrack.models.srs import Rating
from lexitrack.services.learning_service import LearningService
from lexitrack.services.study_flow import FLOW_VERSION, StudyFlow

from .test_learning_service import clock, engine  # noqa: F401 - fixtures


def _due_tomorrow(service: LearningService, day_clock: FrozenClock) -> None:
    service.introduce()
    day_clock.advance_to_day_start(1)
    day_clock.advance(hours=4)


def _saved(service: LearningService, session_id: str) -> dict:
    return json.loads(service.session(session_id).flow_state)


def test_nothing_due_opens_no_session(engine: LearningService) -> None:  # noqa: F811
    flow = StudyFlow(engine)
    assert flow.start() is False
    assert not flow.active and flow.current is None


def test_a_session_runs_to_the_end_without_widgets(
    engine: LearningService, clock: FrozenClock  # noqa: F811
) -> None:
    _due_tomorrow(engine, clock)
    flow = StudyFlow(engine)
    assert flow.start()
    assert flow.total == 25 and flow.position == 0
    assert not flow.revealed  # the meaning starts hidden
    assert flow.reveal() and flow.revealed
    assert not flow.reveal()  # already showing

    finished = False
    while not finished:
        result = flow.answer(Rating.GOOD)
        assert result.outcome is not None
        finished = result.finished
    assert flow.answered == 25
    summary = flow.finish()
    assert summary is not None and summary.answered == 25 and summary.can_undo
    assert not flow.active
    assert engine.session(summary.session_id).flow_state is None


def test_every_step_is_saved_on_the_session(
    engine: LearningService, clock: FrozenClock  # noqa: F811
) -> None:
    _due_tomorrow(engine, clock)
    flow = StudyFlow(engine)
    flow.start()
    session_id = flow.session_id
    state = _saved(engine, session_id)
    assert state["version"] == FLOW_VERSION and state["route"] == "v1"
    assert len(state["queue"]) == 25 and state["index"] == 0

    flow.reveal()
    assert _saved(engine, session_id)["revealed"] is True
    flow.answer(Rating.HARD)
    state = _saved(engine, session_id)
    assert state["index"] == 1 and state["answered"] == 1
    assert state["revealed"] is False
    assert state["last_answer"]["rating"] == int(Rating.HARD)


def test_undo_puts_the_card_back_and_the_count_down(
    engine: LearningService, clock: FrozenClock  # noqa: F811
) -> None:
    _due_tomorrow(engine, clock)
    flow = StudyFlow(engine)
    flow.start()
    first = flow.current.word
    flow.answer(Rating.AGAIN)
    assert flow.can_undo() and flow.last_answer == (first.word, Rating.AGAIN)

    assert flow.undo().id == first.id
    assert flow.position == 0 and flow.answered == 0
    assert flow.current.word.id == first.id
    assert not flow.can_undo()


def test_the_last_answer_can_be_undone_after_the_session_ends(
    engine: LearningService, clock: FrozenClock  # noqa: F811
) -> None:
    _due_tomorrow(engine, clock)
    flow = StudyFlow(engine)
    flow.start()
    flow.answer(Rating.GOOD)
    flow.finish()
    word = flow.undo()
    assert word is not None
    assert engine.review_queue()[0].word.id == word.id


def test_a_saved_flow_is_restored_where_it_stood(
    engine: LearningService, clock: FrozenClock  # noqa: F811
) -> None:
    _due_tomorrow(engine, clock)
    flow = StudyFlow(engine)
    flow.start()
    flow.answer(Rating.GOOD)
    flow.answer(Rating.EASY)
    flow.reveal()

    again = StudyFlow.restore(engine, flow.session_id)
    assert again is not None
    assert again.position == 2 and again.answered == 2 and again.revealed
    assert again.current.word.id == flow.current.word.id
    assert again.last_answer == flow.last_answer

    flow.finish()
    assert StudyFlow.restore(engine, flow.session_id) is None


def test_a_state_from_a_newer_version_is_not_guessed_at(
    engine: LearningService, clock: FrozenClock  # noqa: F811
) -> None:
    _due_tomorrow(engine, clock)
    flow = StudyFlow(engine)
    flow.start()
    state = flow.state() | {"version": FLOW_VERSION + 1}
    engine.save_flow_state(flow.session_id, json.dumps(state))
    assert StudyFlow.restore(engine, flow.session_id) is None


def test_the_today_page_holds_no_session_state() -> None:
    """The flow is the only place the session lives; the page shows it."""
    source = pathlib.Path(__file__).parents[1] / "lexitrack" / "ui" / "study_page.py"
    text = source.read_text(encoding="utf-8")
    for name in ("self._queue", "self._index", "self._revealed", "self._answered",
                 "self._session_id", "self._engine.answer(", "self._engine.undo_last_answer("):
        assert name not in text, name
