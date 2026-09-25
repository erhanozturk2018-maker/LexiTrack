"""Review route V2 as a session (services/review_flow.py), without widgets.

Each test walks a real engine on a frozen clock through a day's reviews and
checks what reaches the database: one rating per word, with its memory
result; the probes and practice as attempts linked to it; nothing rated
twice, and nothing left behind by Undo.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lexitrack.core.clock import FrozenClock
from lexitrack.database.connection import Database
from lexitrack.models.attempt import LearningAttempt, MemoryResult, Phase, Role, SelfReport, Task
from lexitrack.models.content import WordContext, WordLocalization
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
from lexitrack.services.learning_service import LearningService
from lexitrack.services.review_flow import ReviewFlow, StepKind

from .conftest import entry
from .flow_helpers import say

WORDS = {
    "reluctant": "not willing to do something",
    "arid": "very dry, with little rain",
    "attic": "a room just below the roof of a house",
    "avenue": "a wide street in a town",
    "barn": "a large farm building for animals or crops",
    "meadow": "a field of grass and wild flowers",
}


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))


@pytest.fixture
def engine(database: Database, clock: FrozenClock) -> LearningService:
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    entries = [entry(word, definition=meaning, part_of_speech="noun")
               for word, meaning in WORDS.items()]
    entries.append(entry("gizmo"))  # no definition: reviewed the V1 way
    ids = list(WordRepository(database).add_entries(entries, source.id).word_ids)
    a_list = ListRepository(database).create("Test list")
    ListRepository(database).add_words(a_list.id, ids)
    StateRepository(database).set_status_many(ids, ReviewStatus.UNKNOWN)
    service = LearningService(database, clock)
    service.create_plan("Test plan", list_ids=[a_list.id])
    service.introduce()
    clock.advance_to_day_start(1)
    clock.advance(hours=4)
    return service


def _ids(database: Database) -> dict[str, int]:
    rows = database.connection.execute("SELECT id, normalized_word FROM words").fetchall()
    return {row["normalized_word"]: int(row["id"]) for row in rows}


def _to(flow: ReviewFlow, word: str) -> None:
    """Answer until ``word`` is on screen (correctly, at a normal pace)."""
    while flow.current.word.word != word:
        step = flow.current
        if step.kind is StepKind.RECALL:
            flow.assess(SelfReport.REMEMBERED)
        elif step.kind is StepKind.TEACH:
            flow.proceed()
        elif step.kind is StepKind.WRITE:
            flow.assess(SelfReport.REMEMBERED)
        else:
            say(flow, step.prompt.accepted[0], response_ms=6000)


def _logs(database: Database, word_id: int):
    return [log for log in CardRepository(database).logs_for_word(word_id) if not log.undone_at]


def test_a_recalled_word_is_rated_once_with_its_memory_result(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    assert flow.start()
    step = flow.current
    assert step.kind is StepKind.TYPE and step.prompt.task is Task.MEANING_TO_WORD
    feedback = say(flow, step.word.word, response_ms=6000)
    assert feedback.correct and feedback.outcome.rating is Rating.GOOD
    assert feedback.resolution.memory is MemoryResult.RECALLED
    (log,) = _logs(database, step.word.id)
    assert log.route_version == "v2" and log.memory_result == "RECALLED"
    (attempt,) = AttemptRepository(database).for_word(step.word.id)
    assert attempt.review_log_id == log.id and attempt.role is Role.PRIMARY
    assert flow.position == 1 and flow.answered == 1


def test_the_learners_report_is_the_result(engine: LearningService, database: Database) -> None:
    """Instant is Easy, Effortful is Hard: whatever the clock or a hint said."""
    flow = ReviewFlow(engine)
    flow.start()
    first = flow.current
    waiting = flow.submit(first.word.word, response_ms=30_000, hinted=True)
    assert waiting.awaiting and waiting.correct and waiting.outcome is None
    assert _logs(database, first.word.id) == [], "nothing recorded before the report"
    assert flow.assess(SelfReport.INSTANT).outcome.rating is Rating.EASY
    second = flow.current
    feedback = say(flow, second.word.word, SelfReport.EFFORTFUL, response_ms=900)
    assert feedback.outcome.rating is Rating.HARD
    assert feedback.resolution.memory is MemoryResult.RECALLED_EFFORT
    third = flow.current
    assert say(flow, third.word.word, SelfReport.REMEMBERED).outcome.rating is Rating.GOOD
    # The time is kept beside the report, as telemetry.
    (attempt,) = AttemptRepository(database).for_word(first.word.id)
    assert attempt.response_ms == 30_000 and attempt.effort.value == "instant"


def test_forgot_is_again_and_a_wrong_answer_is_not_a_report(engine: LearningService) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    word = flow.current.word
    assert not flow.submit("", response_ms=4000).awaiting, "Forgot: straight to the probe"
    assert flow.current.kind is StepKind.CHOOSE
    wrong = next(i for i, o in enumerate(flow.current.options) if o.id != word.id)
    assert flow.choose(wrong).outcome.rating is Rating.AGAIN


def test_a_failed_recall_is_probed_without_showing_the_answer(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    word = flow.current.word
    feedback = say(flow, "no idea", response_ms=5000)
    assert not feedback.correct and feedback.answer is None and feedback.outcome is None
    probe = flow.current
    assert probe.kind is StepKind.CHOOSE and probe.word.id == word.id
    assert len(probe.options) == 4 and word.id in {o.id for o in probe.options}
    assert _logs(database, word.id) == [], "not rated before the probe is answered"

    right = next(i for i, o in enumerate(probe.options) if o.id == word.id)
    feedback = flow.choose(right)
    assert feedback.outcome.rating is Rating.HARD
    assert feedback.resolution.memory is MemoryResult.RECOGNIZED
    assert feedback.answer == word.word
    # Repair: taught at once, asked again three cards later.
    assert flow.current.kind is StepKind.TEACH
    assert flow.proceed()
    later = [s for s in flow._steps if s.word.id == word.id]
    assert len(later) == 1 and later[0].role is Role.RETRIEVAL
    assert flow._steps.index(later[0]) == 3

    attempts = AttemptRepository(database).for_word(word.id)
    assert [(a.role, a.task, a.success) for a in attempts] == [
        (Role.PRIMARY, Task.MEANING_TO_WORD, False),
        (Role.PROBE, Task.CHOOSE_WORD, True),
    ]


def test_practice_is_recorded_but_never_rated(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    word = flow.current.word
    say(flow, "", response_ms=4000)  # I don't know
    wrong = next(i for i, o in enumerate(flow.current.options) if o.id != word.id)
    feedback = flow.choose(wrong)
    assert feedback.outcome.rating is Rating.AGAIN
    assert feedback.resolution.memory is MemoryResult.FORGOTTEN
    card_after = CardRepository(database).get(word.id)

    assert flow.current.kind is StepKind.TEACH and flow.current.phase is Phase.RELEARN
    flow.proceed()
    _to(flow, word.word)
    retrieval = flow.current
    assert retrieval.phase is Phase.RELEARN and retrieval.role is Role.RETRIEVAL
    say(flow, word.word, response_ms=4000)
    assert CardRepository(database).get(word.id) == card_after
    assert len(_logs(database, word.id)) == 1
    practice = AttemptRepository(database).for_word(word.id)[-1]
    assert practice.phase is Phase.RELEARN and practice.success
    assert practice.review_log_id == _logs(database, word.id)[0].id


def test_relearning_stops_after_two_cycles(engine: LearningService) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    word = flow.current.word
    say(flow, "", response_ms=4000)
    flow.choose(next(i for i, o in enumerate(flow.current.options) if o.id != word.id))
    teaches = 0
    while flow.current is not None:
        step = flow.current
        if step.kind is StepKind.TEACH:
            teaches += step.word.id == word.id
            flow.proceed()
        elif step.kind is StepKind.RECALL:
            flow.assess(SelfReport.REMEMBERED)
        elif step.word.id == word.id:
            say(flow, "still no idea", response_ms=4000)
        else:
            say(flow, step.prompt.accepted[0], response_ms=6000)
    assert teaches == 2


def test_a_failed_context_is_probed_by_meaning_and_repaired_in_another_context(
    engine: LearningService, database: Database
) -> None:
    ids = _ids(database)
    word_id = ids["reluctant"]
    content = ContentRepository(database)
    content.add_contexts([
        WordContext(word_id=word_id, text="She was {{reluctant}} to leave."),
        WordContext(word_id=word_id, text="A {{reluctant}} yes, after a long sigh."),
    ])
    content.save_localization(
        WordLocalization(word_id=word_id, learner_language="de", core_meaning="widerwillig")
    )
    engine.save_settings({Setting.LEARNER_LANGUAGE: "de"})
    # Recalled once before, so today's first question is a context.
    AttemptRepository(database).add(LearningAttempt(
        word_id=word_id, at=datetime(2026, 9, 17, 5, tzinfo=UTC), on_day="2026-09-17",
        phase=Phase.REVIEW, role=Role.PRIMARY, task=Task.MEANING_TO_WORD, success=True,
    ))
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "reluctant")
    primary = flow.current
    assert primary.prompt.task is Task.CONTEXT_CLOZE and primary.prompt.novel_context
    failed_context = primary.prompt.context_id

    say(flow, "unwilling", response_ms=5000)
    probe = flow.current
    assert probe.role is Role.PROBE and probe.prompt.task is Task.MEANING_TO_WORD
    feedback = say(flow, "reluctant", response_ms=5000)
    assert feedback.outcome.rating is Rating.GOOD, "the memory held: case F"
    assert feedback.resolution.memory is MemoryResult.RECALLED
    flow.proceed()
    retrieval = next(s for s in flow._steps if s.word.id == word_id)
    assert retrieval.prompt.context_id not in (None, failed_context)


def test_a_word_with_nothing_to_ask_from_is_reported_on_before_anything_shows(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "gizmo")
    step = flow.current
    assert step.kind is StepKind.RECALL and not hasattr(flow, "reveal")
    feedback = flow.assess(SelfReport.FORGOT)
    assert feedback.outcome.rating is Rating.AGAIN
    (log,) = _logs(database, step.word.id)
    assert log.route_version == "v2" and log.memory_result == "FORGOTTEN"
    (attempt,) = AttemptRepository(database).for_word(step.word.id)
    assert attempt.task is Task.WORD_TO_MEANING and not attempt.success


def test_undo_takes_back_the_whole_word_and_asks_it_again(
    engine: LearningService, database: Database
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    word = flow.current.word
    say(flow, "", response_ms=4000)
    flow.choose(next(i for i, o in enumerate(flow.current.options) if o.id == word.id))
    assert flow.can_undo()
    assert flow.undo().id == word.id
    assert flow.current.word.id == word.id and flow.current.role is Role.PRIMARY
    assert [s for s in flow._steps if s.word.id == word.id and s.phase is not Phase.REVIEW] == []
    assert _logs(database, word.id) == []
    assert AttemptRepository(database).for_word(word.id) == []
    assert flow.answered == 0


def test_the_session_ends_when_every_word_is_rated_and_practised(
    engine: LearningService,
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    total = flow.total
    assert total == len(WORDS) + 1
    finished = False
    while not finished:
        step = flow.current
        if step.kind is StepKind.RECALL:
            finished = flow.assess(SelfReport.REMEMBERED).finished
        elif step.kind is StepKind.TEACH:
            flow.proceed()
            finished = flow.current is None
        else:
            finished = say(flow, step.prompt.accepted[0], response_ms=6000).finished
    assert flow.position == total
    summary = flow.finish()
    assert summary.answered == total and not flow.active


def test_an_open_session_is_restored_with_the_words_not_yet_rated(
    engine: LearningService,
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    first = flow.current.word
    say(flow, first.word, response_ms=6000)
    again = ReviewFlow.restore(engine, flow.session_id)
    assert again is not None
    assert again.total == flow.total and again.position == 1
    assert again.current.word.id == flow.current.word.id
    assert first.id not in {s.word.id for s in again._steps}


def test_an_easy_recall_is_followed_next_time_by_a_sentence(
    engine: LearningService, database: Database, clock: FrozenClock
) -> None:
    ids = _ids(database)
    word_id = ids["arid"]
    ContentRepository(database).add_contexts([
        WordContext(word_id=word_id, text="The land was {{arid}} after years without rain."),
    ])
    flow = ReviewFlow(engine)
    flow.start()
    _to(flow, "arid")
    step = flow.current
    assert step.prompt.task is Task.MEANING_TO_WORD
    assert step.reason.startswith("First question")
    say(flow, "arid", response_ms=1500)  # at once: Easy
    flow.finish()

    card = CardRepository(database).get(word_id)
    clock.advance_to_day_start(engine.clock.days_between(clock.now_utc(), card.due_at) + 1)
    clock.advance(hours=4)
    again = ReviewFlow(engine)
    assert again.start()
    _to(again, "arid")
    step = again.current
    assert step.prompt.task is Task.CONTEXT_CLOZE
    assert "one step harder" in step.reason


def test_the_meaning_is_asked_in_the_learners_chosen_language(
    engine: LearningService, database: Database
) -> None:
    """One word, two learner languages: the setting decides which one asks."""
    word_id = _ids(database)["barn"]
    content = ContentRepository(database)
    for language, meaning in (("de", "Scheune"), ("es", "granero")):
        content.save_localization(
            WordLocalization(word_id=word_id, learner_language=language, core_meaning=meaning)
        )

    def first_prompt() -> str:
        flow = ReviewFlow(engine)
        flow.start()
        _to(flow, "barn")
        text = flow.current.prompt.text
        flow.finish()
        return text

    engine.save_settings({Setting.LEARNER_LANGUAGE: "es"})
    assert first_prompt() == "granero"
    engine.save_settings({Setting.LEARNER_LANGUAGE: "de"})
    assert first_prompt() == "Scheune"
    engine.save_settings({Setting.LEARNER_LANGUAGE: ""})
    assert first_prompt() == WORDS["barn"], "no language chosen: the definition"
    assert database.connection.execute(
        "SELECT COUNT(*) FROM words WHERE normalized_word = 'barn'"
    ).fetchone()[0] == 1



def test_no_kind_of_question_comes_three_times_in_a_row(
    engine: LearningService, database: Database, monkeypatch
) -> None:
    """Three words that could each be asked by a sentence to complete: the
    third is asked another way — a situation — never a harder question."""
    from lexitrack.models.attempt import Level
    from lexitrack.models.content import ContextKind
    from lexitrack.models.skill import SkillStage, WordSkill
    from lexitrack.services.skill_tracker import SkillTracker

    ids = _ids(database)
    repo = ContentRepository(database)
    for word in ("arid", "attic", "avenue"):
        repo.add_contexts([
            WordContext(word_id=ids[word], text=f"It was {{{{{word}}}}} there."),
            WordContext(word_id=ids[word], text=f"Somewhere you would call {{{{{word}}}}}.",
                        kind=ContextKind.SITUATION),
        ])
    # Each of them can already do level 3.
    monkeypatch.setattr(
        SkillTracker, "skill",
        lambda self, word_id: WordSkill(word_id, SkillStage.RECALLED,
                                        level=Level.CONTEXT_TO_WORD),
    )
    flow = ReviewFlow(engine)
    assert flow.start(include_new=False)
    firsts = {step.word.word: step for step in flow._steps if step.role is Role.PRIMARY}
    ordered = [firsts[w] for w in ("arid", "attic", "avenue")]
    tasks = [step.prompt.task for step in ordered]
    # The queue orders the words; check wherever the three stand together.
    in_session = [s.prompt.task for s in flow._steps
                  if s.role is Role.PRIMARY and s.prompt is not None]
    for first, second, third in zip(in_session, in_session[1:], in_session[2:], strict=False):
        assert not (first is second is third), in_session
    assert Task.SITUATION_TO_WORD in tasks
    assert all(step.prompt.task.level <= Level.CONTEXT_TO_WORD for step in ordered)


# -- a session restored whole (flow state version 2) --------------------------------


def _restored(engine: LearningService, flow: ReviewFlow) -> ReviewFlow:
    again = ReviewFlow.restore(engine, flow.session_id)
    assert again is not None
    return again


def test_a_word_interrupted_between_its_probes_goes_on_from_there(
    engine: LearningService, database: Database
) -> None:
    """Not back to its first question: the learner has seen the choices."""
    flow = ReviewFlow(engine)
    flow.start()
    word = flow.current.word
    flow.submit("no idea", response_ms=5000)
    probe = flow.current
    assert probe.kind is StepKind.CHOOSE

    again = _restored(engine, flow)
    assert again.current.kind is StepKind.CHOOSE and again.current.word.id == word.id
    assert [o.id for o in again.current.options] == [o.id for o in probe.options]
    assert again.step_number > flow.step_number, "a card from before is not this one"
    right = next(i for i, o in enumerate(again.current.options) if o.id == word.id)
    feedback = again.choose(right)
    assert feedback.resolution.memory is MemoryResult.RECOGNIZED
    attempts = AttemptRepository(database).for_word(word.id)
    assert [(a.role, a.success) for a in attempts] == [
        (Role.PRIMARY, False), (Role.PROBE, True)
    ], "the missed first question was kept through the restart"


def test_a_right_answer_waiting_for_its_report_survives_a_restart(
    engine: LearningService,
) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    first = flow.current
    assert flow.submit(first.word.word, response_ms=7000).awaiting
    again = _restored(engine, flow)
    assert again.awaiting and again.pending_feedback().answer == first.prompt.answer
    assert again.assess(SelfReport.INSTANT).outcome.rating is Rating.EASY


def test_repair_owed_and_the_order_to_come_are_restored(engine: LearningService) -> None:
    flow = ReviewFlow(engine)
    flow.start()
    word = flow.current.word
    flow.submit("", response_ms=4000)
    right = next(i for i, o in enumerate(flow.current.options) if o.id == word.id)
    flow.choose(right)
    assert flow.current.kind is StepKind.TEACH
    before = [(s.word.id, s.kind, s.phase, s.role) for s in flow._steps]
    again = _restored(engine, flow)
    assert [(s.word.id, s.kind, s.phase, s.role) for s in again._steps] == before
    assert again._runs[word.id].rated and again._runs[word.id].cycles == 1
    assert again.answered == flow.answered and again.can_undo() == flow.can_undo()


def test_a_state_saved_by_version_one_still_restores(engine: LearningService) -> None:
    import json

    flow = ReviewFlow(engine)
    flow.start()
    old = {key: value for key, value in flow.state().items()
           if key in ("route", "kind", "order", "pending", "new_pending", "answered",
                      "learned", "last_answer")}
    old["version"] = 1
    engine.save_flow_state(flow.session_id, json.dumps(old))
    again = _restored(engine, flow)
    assert again.current.role is Role.PRIMARY and again.total == flow.total
