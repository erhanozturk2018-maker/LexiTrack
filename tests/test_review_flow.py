"""A day's study as a session (services/review_flow.py), without widgets.

Each test walks a real engine on a frozen clock through a day and checks what
reaches the database: the two tasks and nothing else, four options each, one
rating per word a day with its correctness and effort kept apart, a wrong
answer rated Again and asked again as practice, and nothing left behind by
Undo or a restart.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import Effort, Phase, Role, Task
from lexitrack.models.source import Source
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import (
    AttemptRepository,
    CardRepository,
    ContextRepository,
    ListRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_flow import ReviewFlow, StepKind
from lexitrack.services.review_wording import feedback_text

from .conftest import entry
from .flow_helpers import right, wrong

WORDS = {
    "sleep in": ("verb", "to sleep later than usual"),
    "stay up": ("verb", "to not go to bed until late"),
    "wake up": ("verb", "to stop sleeping"),
    "lie down": ("verb", "to put your body flat on a bed or the floor"),
    "attic": ("noun", "a room just below the roof of a house"),
    "avenue": ("noun", "a wide street in a town"),
    "barn": ("noun", "a large farm building for animals or crops"),
    "meadow": ("noun", "a field of grass and wild flowers"),
}
CONTEXTS = {
    "sleep in": ["I don't have to work tomorrow, so I can sleep in.",
                 "I usually sleep in on Sundays."],
    "attic": ["We keep old toys in the attic."],
}


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))


def _setup(database: Database, clock: FrozenClock, introduce: bool = True) -> LearningService:
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    entries = [entry(word, part_of_speech=pos, definition=meaning, cefr_level="B1")
               for word, (pos, meaning) in WORDS.items()]
    ids = list(WordRepository(database).add_entries(entries, source.id).word_ids)
    contexts = ContextRepository(database)
    for word_id, word in zip(ids, WORDS, strict=True):
        contexts.add_many(word_id, CONTEXTS.get(word, []))
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, ids)
    StateRepository(database).set_status_many(ids, ReviewStatus.UNKNOWN, at=clock.now_utc())
    service = LearningService(database, clock)
    service.create_plan("Test plan", list_ids=[a_list.id])
    if introduce:
        service.introduce()
        clock.advance_to_day_start(1)
        clock.advance(hours=4)
    return service


@pytest.fixture
def engine(database: Database, clock: FrozenClock) -> LearningService:
    """Eight words introduced yesterday: all due today."""
    return _setup(database, clock)


def _id(database: Database, word: str) -> int:
    return WordRepository(database).find(word).id


def _logs(database: Database, word_id: int):
    return [log for log in CardRepository(database).logs_for_word(word_id) if not log.undone_at]


def _to(flow: ReviewFlow, word: str) -> None:
    """Answer the others right until ``word`` is on screen."""
    while flow.current.word.word != word:
        if flow.current.kind is StepKind.TEACH:
            flow.proceed()
        else:
            right(flow)


def _asked(flow: ReviewFlow) -> list:
    """Every step of the session, answered right, as it was on screen."""
    seen = []
    while flow.current is not None:
        step = flow.current
        seen.append(step)
        if step.kind is StepKind.TEACH:
            flow.proceed()
        else:
            right(flow)
    return seen


# -- the two tasks ------------------------------------------------------------------


def test_definition_to_word_shows_the_definition_and_four_words(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    assert flow.start()
    _to(flow, "barn")
    step = flow.current
    question = step.question
    assert step.kind is StepKind.QUESTION and step.rated
    assert question.task is Task.DEFINITION_TO_WORD
    assert question.prompt == "a large farm building for animals or crops"
    assert len(question.options) == 4
    assert question.right.word_id == step.word.id and question.right.text == "barn"
    assert len({o.word_id for o in question.options}) == 4
    assert len({o.text for o in question.options}) == 4


def test_context_to_definition_shows_a_context_and_four_definitions(
    engine: LearningService, database: Database
) -> None:
    # Asked Definition → Word last time, a word with contexts is asked the
    # other way today.
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "sleep in")
    assert flow.current.question.task is Task.DEFINITION_TO_WORD
    right(flow)
    flow.finish()

    engine.clock.advance_to_day_start(30)
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "sleep in")
    question = flow.current.question
    assert question.task is Task.CONTEXT_TO_DEFINITION
    assert question.prompt in CONTEXTS["sleep in"]
    assert question.right.text == "to sleep later than usual"
    assert all(option.text in {m for _, m in WORDS.values()} for option in question.options)
    assert len({option.text for option in question.options}) == 4
    start, end = question.highlight
    assert question.prompt[start:end] == "sleep in"


def test_context_to_word_is_never_asked(engine: LearningService, database: Database) -> None:
    """Over many days every question is one of the two tasks, and a context is
    only ever shown with definitions to choose from, never words."""
    definitions = {meaning for _pos, meaning in WORDS.values()}
    for _day in range(6):
        flow = ReviewFlow(engine)
        if flow.start():
            for step in _asked(flow):
                if step.kind is not StepKind.QUESTION:
                    continue
                question = step.question
                assert question.task in (Task.DEFINITION_TO_WORD, Task.CONTEXT_TO_DEFINITION)
                if question.task is Task.CONTEXT_TO_DEFINITION:
                    assert all(o.text in definitions for o in question.options)
                else:
                    assert question.prompt in definitions
                    assert all(o.text not in definitions for o in question.options)
            flow.finish()
        engine.clock.advance_to_day_start(40)
    tasks = {row[0] for row in database.connection.execute("SELECT task FROM learning_attempts")}
    assert tasks <= {"definition_to_word", "context_to_definition"}


def test_a_word_without_contexts_is_only_asked_from_its_definition(
    engine: LearningService, database: Database
) -> None:
    barn = _id(database, "barn")
    for _ in range(4):
        flow = ReviewFlow(engine)
        if flow.start():
            _asked(flow)
            flow.finish()
        engine.clock.advance_to_day_start(60)
    attempts = AttemptRepository(database).for_word(barn)
    assert attempts and {a.task for a in attempts} == {Task.DEFINITION_TO_WORD}


# -- right and wrong ------------------------------------------------------------------


def test_a_right_answer_waits_for_its_effort_then_is_recorded_with_it(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    step = flow.current
    feedback = flow.choose(step.question.answer, response_ms=3200)
    assert feedback.correct and feedback.awaiting and feedback.outcome is None
    assert _logs(database, step.word.id) == []
    assert flow.awaiting and flow.intervals()

    rated = flow.rate(Rating.HARD)
    assert rated.correct and rated.outcome.rating is Rating.HARD
    (log,) = _logs(database, step.word.id)
    assert (log.task, log.correct, log.rating, log.route_version) == (
        "definition_to_word", True, Rating.HARD, "v3",
    )
    (attempt,) = AttemptRepository(database).for_word(step.word.id)
    assert (attempt.correct, attempt.effort, attempt.role, attempt.phase) == (
        True, Effort.HARD, Role.PRIMARY, Phase.REVIEW,
    )
    assert attempt.review_log_id == log.id and attempt.response_ms == 3200
    assert flow.position == 1 and flow.answered == 1


@pytest.mark.parametrize("rating", list(Rating))
def test_every_effort_can_follow_a_right_answer(
    engine: LearningService, database: Database, rating: Rating
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    step = flow.current
    flow.choose(step.question.answer)
    feedback = flow.rate(rating)
    (log,) = _logs(database, step.word.id)
    assert log.correct is True and log.rating is rating
    (attempt,) = AttemptRepository(database).for_word(step.word.id)
    assert attempt.effort is Effort.of(rating)
    # Right but rated Again: the learner's word that it did not come; asked
    # again later, like a miss.
    assert feedback.again_later is (rating is Rating.AGAIN)


def test_a_wrong_answer_is_recorded_as_not_correct_and_rated_again(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    step = flow.current
    feedback = wrong(flow)
    assert not feedback.correct and not feedback.awaiting
    assert feedback.outcome.rating is Rating.AGAIN and feedback.again_later
    (log,) = _logs(database, step.word.id)
    assert (log.correct, log.rating, log.task) == (False, Rating.AGAIN, "definition_to_word")
    (attempt,) = AttemptRepository(database).for_word(step.word.id)
    assert attempt.correct is False and attempt.effort is None


def test_after_a_wrong_answer_the_right_word_and_its_definition_are_shown(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    step = flow.current
    feedback = wrong(flow)
    text = feedback_text(step, feedback)
    assert text.title == "Incorrect"
    assert text.lines == (
        ("Correct answer", step.word.word),
        ("Definition", step.word.definition),
    )
    assert feedback.answer == step.question.answer
    assert feedback.picked != feedback.answer


def test_a_wrong_context_answer_names_the_word_and_its_definition(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "sleep in")
    right(flow)
    flow.finish()
    engine.clock.advance_to_day_start(30)
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "sleep in")
    step = flow.current
    assert step.question.task is Task.CONTEXT_TO_DEFINITION
    feedback = wrong(flow)
    text = feedback_text(step, feedback)
    assert text.lines == (
        ("Word", "sleep in"),
        ("Correct definition", "to sleep later than usual"),
    )


def test_a_missed_word_comes_back_as_practice_the_other_way_round(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "sleep in")
    word_id = flow.current.word.id
    wrong(flow)
    later = [s for s in flow._steps if s.word.id == word_id]
    assert len(later) == 1
    (again,) = later
    assert again.phase is Phase.RELEARN and again.role is Role.RETRIEVAL
    assert again.task is Task.CONTEXT_TO_DEFINITION
    # A few cards later, not at once.
    assert flow._steps.index(again) == 3
    _to(flow, "sleep in")
    step = flow.current
    assert not step.rated and step.question.task is Task.CONTEXT_TO_DEFINITION
    feedback = right(flow)
    assert feedback.practice and feedback.outcome is None
    # Still one rating today: the practice changed nothing.
    assert len(_logs(database, word_id)) == 1
    attempts = AttemptRepository(database).for_word(word_id)
    assert [a.role for a in attempts] == [Role.PRIMARY, Role.RETRIEVAL]
    assert attempts[1].review_log_id == attempts[0].review_log_id


def test_a_word_is_asked_again_at_most_twice(engine: LearningService, database: Database) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    word_id = flow.current.word.id
    wrong(flow)
    asked = 1
    while flow.current is not None:
        if flow.current.word.id == word_id:
            wrong(flow)
            asked += 1
        else:
            right(flow)
    assert asked == 3


def test_one_word_one_task_a_day(engine: LearningService, database: Database) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    wrong(flow)
    _asked(flow)
    for word in WORDS:
        word_id = _id(database, word)
        assert len(_logs(database, word_id)) == 1
        primaries = [a for a in AttemptRepository(database).for_word(word_id)
                     if a.role is Role.PRIMARY]
        assert len(primaries) == 1
    # A second session the same day asks nothing more.
    assert not ReviewFlow(engine).start()


# -- new words -------------------------------------------------------------------------


def test_new_words_are_shown_then_asked_both_ways_and_learned(
    database: Database, clock: FrozenClock
) -> None:
    engine = _setup(database, clock, introduce=False)
    flow = ReviewFlow(engine)
    assert flow.start()
    steps = _asked(flow)
    first_group = [s.word.word for s in steps[:4]]
    assert all(s.kind is StepKind.TEACH for s in steps[:4])
    assert first_group == list(WORDS)[:4]
    # Shown whole: the word's contexts on its page.
    assert steps[0].contexts and steps[0].contexts[0].text in CONTEXTS["sleep in"]
    tasks = {(s.word.word, s.task) for s in steps if s.kind is StepKind.QUESTION}
    assert ("sleep in", Task.CONTEXT_TO_DEFINITION) in tasks
    assert ("barn", Task.CONTEXT_TO_DEFINITION) not in tasks
    assert all(not s.rated for s in steps)
    assert flow.learned == len(WORDS)
    # Practice only: nothing is rated before the first review, tomorrow.
    assert database.connection.execute("SELECT COUNT(*) FROM review_logs").fetchone()[0] == 0
    assert len(CardRepository(database).all_cards()) == len(WORDS)


def test_a_word_without_a_definition_is_never_asked(
    database: Database, clock: FrozenClock
) -> None:
    engine = _setup(database, clock, introduce=False)
    bare = WordRepository(database).add_entries(
        [entry("gizmo")],
        SourceRepository(database).upsert(Source(key="t2", name="T2", parser_type="generic")).id,
    ).word_ids[0]
    lists = ListRepository(database)
    lists.add_words(lists.all()[0].id, [bare])
    StateRepository(database).set_status_many([bare], ReviewStatus.UNKNOWN)
    plan = engine.daily_plan()
    assert bare not in {w.id for w in plan.new_words}
    assert plan.without_definition == 1
    flow = ReviewFlow(engine)
    flow.start()
    assert all(step.word.id != bare for step in _asked(flow))


# -- undo and restoring ----------------------------------------------------------------


def test_undo_takes_the_answer_back_and_asks_it_again(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    step = flow.current
    wrong(flow)
    assert flow.can_undo()
    assert flow.undo().id == step.word.id
    assert flow.current.word.id == step.word.id and flow.current.rated
    assert _logs(database, step.word.id) == []
    # The ask-again step that followed the miss is gone with it.
    assert sum(1 for s in flow._steps if s.word.id == step.word.id) == 1
    right(flow, Rating.EASY)
    (log,) = _logs(database, step.word.id)
    assert log.rating is Rating.EASY and log.correct is True


def test_a_session_is_restored_as_it_stood(engine: LearningService, database: Database) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    right(flow)
    wrong(flow)
    step = flow.current
    flow.choose(step.question.answer)
    assert flow.awaiting

    again = ReviewFlow.restore(engine, flow.session_id)
    assert again is not None
    assert again.current.word.id == step.word.id
    assert again.current.question == step.question
    assert again.awaiting and again.pending_feedback().correct
    assert again.position == flow.position and again.total == flow.total
    assert again.step_number > flow.step_number
    again.rate(Rating.GOOD)
    (log,) = _logs(database, step.word.id)
    assert log.correct is True
    # The rest of the session, asked-again steps included, is still there.
    assert [s.word.id for s in again._steps] == [s.word.id for s in flow._steps[1:]]


def test_a_session_saved_by_an_earlier_version_is_not_restored(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    engine.save_flow_state(flow.session_id, '{"version": 2, "route": "v2", "steps": []}')
    assert ReviewFlow.restore(engine, flow.session_id) is None


def test_a_word_deleted_during_a_session_is_left_out_on_restore(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    later = flow._steps[2].word.id
    WordRepository(database).delete([later])
    again = ReviewFlow.restore(engine, flow.session_id)
    assert again is not None
    assert all(step.word.id != later for step in again._steps)
    assert again.total == flow.total - 1
