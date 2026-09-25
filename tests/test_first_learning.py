"""First learning (services/first_learning.py, and the flow that runs it).

A new word is taught at a depth its content decides, asked at once, maybe
asked again later — and none of it is rated: the first rating is the first
review, on another day.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import Depth, Phase, Role
from lexitrack.models.content import (
    DepthHint,
    EncodingType,
    WordContent,
    WordContext,
    WordLocalization,
    WordTeaching,
)
from lexitrack.models.settings import Setting
from lexitrack.models.source import Source
from lexitrack.models.srs import Rating
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import (
    AttemptRepository,
    CardRepository,
    ContentRepository,
    ListRepository,
    SourceRepository,
    StateRepository,
    WordRepository,
)
from lexitrack.services.first_learning import choose_depth, deeper, estimate
from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_flow import ReviewFlow, StepKind

from .conftest import entry

WORDS = {
    "arid": "very dry, with little rain",
    "attic": "a room just below the roof of a house",
    "avenue": "a wide street in a town",
    "barn": "a large farm building for animals or crops",
    "meadow": "a field of grass and wild flowers",
    "reluctant": "not willing to do something",
}


# -- the rules -------------------------------------------------------------------------


def test_depth_comes_from_the_content() -> None:
    bare = WordTeaching(None, ())
    assert choose_depth(bare).depth is Depth.SHORT
    meaning_only = WordTeaching(
        None, (), "de", WordLocalization(word_id=1, learner_language="de", core_meaning="x")
    )
    assert choose_depth(meaning_only).depth is Depth.SHORT, "a meaning alone is still short"
    usage = WordTeaching(WordContent(word_id=1, pattern="p"), ())
    assert choose_depth(usage).depth is Depth.LIGHT
    deep = WordTeaching(WordContent(word_id=1, depth_hint=DepthHint.DEEP), ())
    assert choose_depth(deep).depth is Depth.DEEP
    abstract = WordTeaching(
        WordContent(word_id=1, pattern="p"), (), "de",
        WordLocalization(word_id=1, learner_language="de", encoding_type=EncodingType.CONTRAST),
    )
    assert choose_depth(abstract).depth is Depth.DEEP


def test_a_miss_goes_deeper_only_when_there_is_more_to_show() -> None:
    assert deeper(Depth.SHORT, WordTeaching(None, ())) is Depth.SHORT
    usage = WordTeaching(WordContent(word_id=1, pattern="p"), ())
    assert deeper(Depth.SHORT, usage) is Depth.LIGHT
    assert deeper(Depth.LIGHT, usage) is Depth.DEEP
    assert deeper(Depth.DEEP, usage) is Depth.DEEP


def test_the_estimate_counts_reviews_and_new_words_by_depth() -> None:
    day = estimate(15, [Depth.SHORT] * 20 + [Depth.LIGHT] * 4 + [Depth.DEEP])
    assert (day.reviews, day.new, day.words) == (15, 25, 40)
    assert day.minutes == 27  # 300 + 900 + 300 + 120 = 1620 seconds
    assert estimate(0, []).minutes == 0


# -- the session -------------------------------------------------------------------------


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))


@pytest.fixture
def engine(database: Database, clock: FrozenClock) -> LearningService:
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    entries = [entry(word, definition=meaning, part_of_speech="noun", language="en")
               for word, meaning in WORDS.items()]
    entries.append(entry("gizmo", language="en"))  # no definition at all
    ids = list(WordRepository(database).add_entries(entries, source.id).word_ids)
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, ids)
    StateRepository(database).set_status_many(ids, ReviewStatus.UNKNOWN)
    service = LearningService(database, clock)
    service.create_plan("Test plan", list_ids=[a_list.id])
    return service


def _ids(database: Database) -> dict[str, int]:
    rows = database.connection.execute("SELECT id, normalized_word FROM words").fetchall()
    return {row["normalized_word"]: int(row["id"]) for row in rows}


def _kinds(flow: ReviewFlow) -> list[tuple[str, str]]:
    return [(step.kind.value, step.word.word) for step in flow._steps]


def _answer_right(flow: ReviewFlow) -> None:
    step = flow.current
    if step.kind is StepKind.TEACH:
        flow.proceed()
    elif step.kind is StepKind.RECALL:
        flow.rate(Rating.GOOD)
    else:
        flow.submit(step.prompt.accepted[0], response_ms=5000)


def test_new_words_are_taught_in_groups_then_asked(engine: LearningService) -> None:
    flow = ReviewFlow(engine)
    assert flow.start()
    assert flow.total == len(WORDS) + 1
    first_eight = _kinds(flow)[:8]
    assert [kind for kind, _ in first_eight] == ["teach"] * 4 + ["type"] * 4
    assert [word for _, word in first_eight[:4]] == [word for _, word in first_eight[4:]]
    step = flow.current
    assert step.phase is Phase.INTRODUCTION and step.depth is Depth.SHORT
    assert step.label == "New word"


def test_practice_is_recorded_not_rated_and_a_done_word_gets_its_card(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    first = flow.current.word
    for _ in range(4):
        flow.proceed()
    assert CardRepository(database).get(first.id) is None, "not learned until asked"
    feedback = flow.submit(first.word, response_ms=4000)
    assert feedback.correct and feedback.outcome is None
    assert CardRepository(database).get(first.id) is not None
    assert first.id not in {item.word.id for item in engine.review_queue()}, "not due today"
    assert flow.learned == 1 and flow.position == 1
    (attempt,) = AttemptRepository(database).for_word(first.id)
    assert attempt.phase is Phase.INTRODUCTION and attempt.role is Role.RETRIEVAL
    assert attempt.depth is Depth.SHORT and attempt.review_log_id is None
    assert CardRepository(database).logs_for_word(first.id) == []


def test_a_word_with_no_meaning_is_shown_and_learned_without_a_question(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    gizmo = _ids(database)["gizmo"]
    assert [kind for kind, word in _kinds(flow) if word == "gizmo"] == ["teach"]
    while flow.current is not None and flow.current.word.id != gizmo:
        _answer_right(flow)
    flow.proceed()
    assert CardRepository(database).get(gizmo) is not None


def test_a_miss_is_taught_again_deeper_and_asked_again_twice_at_most(
    engine: LearningService, database: Database
) -> None:
    word_id = _ids(database)["arid"]
    ContentRepository(database).save_content(WordContent(word_id=word_id, pattern="arid land"))
    flow = ReviewFlow(engine)
    flow.start()
    teaches = []
    while flow.current is not None:
        step = flow.current
        if step.word.id == word_id and step.kind is StepKind.TEACH:
            teaches.append(step.depth)
            flow.proceed()
        elif step.word.id == word_id:
            flow.submit("no idea", response_ms=4000)
        else:
            _answer_right(flow)
    assert teaches == [Depth.LIGHT, Depth.DEEP, Depth.DEEP], "taught, then twice again"
    assert CardRepository(database).get(word_id) is not None, "learned all the same"
    assert CardRepository(database).logs_for_word(word_id) == []


def test_a_light_word_is_asked_again_from_a_context_after_the_next_group(
    engine: LearningService, database: Database
) -> None:
    word_id = _ids(database)["arid"]
    content = ContentRepository(database)
    content.save_content(WordContent(word_id=word_id, pattern="arid land"))
    content.add_contexts([WordContext(word_id=word_id, text="The land was {{arid}} for years.")])
    flow = ReviewFlow(engine)
    flow.start()
    steps = _kinds(flow)
    arid = [i for i, (_, word) in enumerate(steps) if word == "arid"]
    assert len(arid) == 3, "taught, asked, asked again"
    second_group_teaching = [i for i, (kind, _) in enumerate(steps) if kind == "teach"][4]
    assert arid[2] > second_group_teaching, "the second question waits for the next group"
    assert flow._steps[arid[2]].prompt.context_id is not None


def test_reviews_come_before_new_words(
    engine: LearningService, clock: FrozenClock
) -> None:
    engine.save_settings({Setting.NEW_WORDS_PER_DAY: 3})
    engine.introduce()
    clock.advance_to_day_start(1)
    clock.advance(hours=4)
    flow = ReviewFlow(engine)
    flow.start()
    phases = [step.phase for step in flow._steps]
    first_new = phases.index(Phase.INTRODUCTION)
    assert first_new == 3 and set(phases[:3]) == {Phase.REVIEW}


def test_leaving_early_keeps_what_was_learned_and_offers_the_rest_again(
    engine: LearningService,
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    first = flow.current.word
    for _ in range(4):
        flow.proceed()
    flow.submit(first.word, response_ms=4000)
    summary = flow.finish()
    assert summary.learned == 1
    offered = {word.id for word in engine.daily_plan().new_words}
    assert first.id not in offered and len(offered) == len(WORDS)
