"""Which question a word gets first (services/task_selector.py).

The rules are stated one per test: harder after an easy success, the same
after effort or a miss, easier after forgetting, never harder on a weak
memory, and only what the word's content can ask.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lexitrack.models.attempt import Effort, LearningAttempt, Level, Phase, Role, Task
from lexitrack.models.content import WordContent, WordContext, WordTeaching
from lexitrack.repositories.word_repository import StoredWord
from lexitrack.services.task_selector import available_levels, choose_level, prompt_for

AT = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
ALL = {Level.MEANING_TO_WORD, Level.CONTEXT_TO_WORD, Level.COLLOCATION, Level.PRODUCTION}

_log = iter(range(1, 10_000))


def review(task: Task, success: bool, effort: Effort | None = Effort.NORMAL,
           probes: tuple[bool, ...] = ()) -> list[LearningAttempt]:
    """One past review: the primary and its probes, sharing a log row."""
    log_id = next(_log)
    attempts = [LearningAttempt(
        word_id=1, at=AT, on_day="2026-09-20", phase=Phase.REVIEW, role=Role.PRIMARY,
        task=task, success=success, effort=effort if success else None, review_log_id=log_id,
    )]
    for ok in probes:
        attempts.append(LearningAttempt(
            word_id=1, at=AT, on_day="2026-09-20", phase=Phase.REVIEW, role=Role.PROBE,
            task=Task.CHOOSE_WORD, success=ok, review_log_id=log_id,
        ))
    return attempts


def test_a_first_review_asks_the_word_from_its_meaning() -> None:
    choice = choose_level([], ALL)
    assert choice.level is Level.MEANING_TO_WORD and "First question" in choice.reason


def test_an_old_v1_answer_leads_to_the_word_from_its_meaning() -> None:
    assert choose_level(review(Task.WORD_TO_MEANING, True), ALL).level is Level.MEANING_TO_WORD


def test_an_easy_success_goes_one_step_harder() -> None:
    choice = choose_level(review(Task.MEANING_TO_WORD, True, Effort.INSTANT), ALL, 0.9)
    assert choice.level is Level.CONTEXT_TO_WORD and "harder" in choice.reason


def test_effort_or_a_miss_asks_the_same_kind_again() -> None:
    effort = choose_level(review(Task.CONTEXT_CLOZE, True, Effort.EFFORTFUL), ALL, 0.9)
    assert effort.level is Level.CONTEXT_TO_WORD and "effort" in effort.reason
    missed = review(Task.CONTEXT_CLOZE, False, probes=(True,))
    assert choose_level(missed, ALL, 0.9).level is Level.CONTEXT_TO_WORD


def test_forgetting_goes_one_step_easier_but_not_below_meaning() -> None:
    forgot = review(Task.COLLOCATION, False, probes=(False, False))
    choice = choose_level(forgot, ALL, 0.9)
    assert choice.level is Level.CONTEXT_TO_WORD and "forgot" in choice.reason
    assert choose_level(review(Task.MEANING_TO_WORD, False, probes=(False,)), ALL).level \
        is Level.MEANING_TO_WORD


def test_a_weak_memory_gets_no_harder_question() -> None:
    choice = choose_level(review(Task.MEANING_TO_WORD, True, Effort.INSTANT), ALL, 0.6)
    assert choice.level is Level.MEANING_TO_WORD and "60%" in choice.reason


def test_only_the_last_review_counts_and_practice_is_ignored() -> None:
    history = review(Task.CONTEXT_CLOZE, False, probes=(True,)) + review(
        Task.CONTEXT_CLOZE, True, Effort.NORMAL
    )
    history.append(LearningAttempt(
        word_id=1, at=AT, on_day="2026-09-20", phase=Phase.RELEARN, role=Role.RETRIEVAL,
        task=Task.MEANING_TO_WORD, success=False,
    ))
    assert choose_level(history, ALL, 0.9).level is Level.COLLOCATION


def test_a_level_without_content_is_stepped_over_going_up_and_down_from() -> None:
    no_collocations = {Level.MEANING_TO_WORD, Level.CONTEXT_TO_WORD, Level.PRODUCTION}
    up = choose_level(review(Task.CONTEXT_CLOZE, True, Effort.INSTANT), no_collocations, 0.9)
    assert up.level is Level.PRODUCTION and "Nothing stored" in up.reason
    only_meaning = {Level.MEANING_TO_WORD, Level.PRODUCTION}
    down = choose_level(review(Task.CONTEXT_CLOZE, True, Effort.EFFORTFUL), only_meaning, 0.9)
    assert down.level is Level.MEANING_TO_WORD


def test_nothing_to_ask_from_means_no_choice() -> None:
    assert choose_level([], set()) is None


def test_the_content_decides_what_is_available_and_contexts_take_turns() -> None:
    word = StoredWord(id=1, word="reluctant", normalized_word="reluctant",
                      definition="not willing")
    bare = WordTeaching(None, ())
    assert available_levels(word, bare) == {Level.MEANING_TO_WORD}, "the short route"
    rich = WordTeaching(
        WordContent(word_id=1, collocations=("reluctant to admit", "reluctant hero")),
        (WordContext(word_id=1, id=5, text="She was {{reluctant}}."),
         WordContext(word_id=1, id=6, text="A {{reluctant}} yes.")),
    )
    assert available_levels(word, rich) == ALL
    prompt = prompt_for(Level.CONTEXT_TO_WORD, word, rich, [], {5: "2026-09-24"})
    assert prompt.context_id == 6, "the one never used comes first"
    asked_once = review(Task.COLLOCATION, True)
    first = prompt_for(Level.COLLOCATION, word, rich, [], {})
    second = prompt_for(Level.COLLOCATION, word, rich, asked_once, {})
    assert first.answer != second.answer, "collocations take turns"
