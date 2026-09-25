"""Review route V2: the rules (services/review_route.py).

The cases A–F are the ones in docs/LEARNING_ENGINE.md; each is one test here,
stated as the attempts and the rating they must produce.
"""

from __future__ import annotations

import pytest

from lexitrack.models.attempt import Effort, Level, MemoryResult, Task
from lexitrack.models.content import ContextKind, WordContent, WordContext, WordTeaching
from lexitrack.models.srs import Rating
from lexitrack.repositories.word_repository import StoredWord
from lexitrack.services.review_route import (
    FollowUp,
    Result,
    Source,
    answer_forms,
    check_typed,
    choice_options,
    collocation_prompt,
    context_prompt,
    effort_for,
    hint_for,
    mask_word,
    meaning_prompt,
    next_probe,
    normalize_answer,
    resolve,
)


def word(**kwargs) -> StoredWord:
    defaults = dict(
        id=1, word="reluctant", normalized_word="reluctant", part_of_speech="adjective",
        cefr_level="B2", definition="not willing to do something; showing reluctance",
    )
    return StoredWord(**{**defaults, **kwargs})


def teaching(**content) -> WordTeaching:
    contexts = (
        WordContext(word_id=1, id=11, text="She was {{reluctant}} to leave."),
        WordContext(word_id=1, id=12, text="A {{reluctant}} yes, after a long sigh.",
                    kind=ContextKind.SITUATION),
    )
    return WordTeaching(WordContent(word_id=1, **content) if content else None, contexts)


# -- typed answers ---------------------------------------------------------------------


def test_answers_are_compared_without_case_spacing_or_punctuation() -> None:
    assert normalize_answer("  Well-Known’s! ") == "well known's"
    assert check_typed(["reluctant"], "Reluctant").correct
    assert not check_typed(["reluctant"], "").correct
    assert not check_typed(["reluctant"], "willing").correct


def test_a_multi_word_entry_does_not_demand_its_frame() -> None:
    assert answer_forms("be expelled") == ("be expelled", "expelled")
    assert check_typed(["be expelled"], "expelled").correct
    assert check_typed(["to give up"], "give up").correct


def test_one_slip_is_accepted_as_effortful_and_a_short_word_must_be_exact() -> None:
    slip = check_typed(["reluctant"], "relutcant")
    assert slip.correct and slip.near_miss
    assert check_typed(["reluctant"], "relctant").near_miss  # a letter missing
    assert not check_typed(["reluctant"], "relxxtant").correct
    assert not check_typed(["cat"], "cut").correct
    assert check_typed(["responsibility"], "responsabilty").near_miss  # two slips, 14 letters


def test_effort_comes_from_hints_slips_and_time() -> None:
    assert effort_for(2000, "reluctant") is Effort.INSTANT
    assert effort_for(6000, "reluctant") is Effort.NORMAL
    assert effort_for(20000, "reluctant") is Effort.EFFORTFUL
    assert effort_for(1000, "reluctant", hinted=True) is Effort.EFFORTFUL
    assert effort_for(1000, "reluctant", near_miss=True) is Effort.EFFORTFUL
    assert effort_for(None, "reluctant") is Effort.NORMAL


def test_the_hint_is_the_first_letter_and_the_shape() -> None:
    assert hint_for("reluctant") == "r _ _ _ _ _ _ _ _"
    assert hint_for("give up") == "g _ _ _   _ _"


# -- prompts ---------------------------------------------------------------------------


def test_a_definition_never_gives_the_word_away() -> None:
    assert mask_word("showing reluctance", "reluctant") == "showing ___"
    assert mask_word("to be made to leave", "be expelled") == "to be made to leave"
    assert "expel" not in mask_word("the act of expelling someone", "be expelled")


def test_the_meaning_prompt_prefers_turkish_and_switches_when_asked() -> None:
    first = meaning_prompt(word(), teaching(core_meaning_tr="unwilling"))
    assert first.source is Source.TURKISH and first.text == "unwilling"
    other = meaning_prompt(word(), teaching(core_meaning_tr="unwilling"), avoid=Source.TURKISH)
    assert other.source is Source.DEFINITION and "reluctance" not in other.text
    plain = meaning_prompt(word(), teaching())
    assert plain.source is Source.DEFINITION and plain.task is Task.MEANING_TO_WORD
    assert meaning_prompt(word(definition=None), teaching()) is None


def test_a_context_prompt_hides_the_word_and_avoids_what_is_excluded() -> None:
    prompt = context_prompt(word(), teaching())
    assert prompt.text == "She was _____ to leave." and prompt.context_id == 11
    assert prompt.novel_context
    other = context_prompt(word(), teaching(), exclude={11})
    assert other.task is Task.SITUATION_TO_WORD and other.context_id == 12
    assert context_prompt(word(), teaching(), exclude={11, 12}) is None
    # Used before: the one used longest ago comes first, and is not novel.
    reused = context_prompt(word(), teaching(), used={11: "2026-09-24", 12: "2026-09-20"})
    assert reused.context_id == 12 and not reused.novel_context


def test_a_collocation_prompt_hides_the_partner() -> None:
    prompt = collocation_prompt(word(), teaching(collocations=("reluctant to admit",)))
    assert prompt.text == "reluctant to ___" and prompt.accepted == ("admit",)
    assert collocation_prompt(word(), teaching()) is None


def test_the_choice_is_stable_and_prefers_the_same_part_of_speech() -> None:
    pool = [
        word(id=2, word="eager", normalized_word="eager"),
        word(id=3, word="willing", normalized_word="willing"),
        word(id=4, word="hesitate", normalized_word="hesitate", part_of_speech="verb"),
        word(id=5, word="calm", normalized_word="calm"),
        word(id=6, word="reluctant", normalized_word="reluctant"),  # a duplicate spelling
    ]
    options = choice_options(word(), pool, seed="1:2026-09-25")
    assert len(options) == 4 and word().id in {o.id for o in options}
    assert 4 not in {o.id for o in options} and 6 not in {o.id for o in options}
    assert options == choice_options(word(), pool, seed="1:2026-09-25")


# -- the cases -------------------------------------------------------------------------


def test_case_a_recalled_at_the_first_question_is_good() -> None:
    resolution = resolve([Result(Task.MEANING_TO_WORD, True, Effort.NORMAL)])
    assert resolution.memory is MemoryResult.RECALLED
    assert resolution.rating is Rating.GOOD and resolution.follow_up is FollowUp.NONE
    instant = resolve([Result(Task.MEANING_TO_WORD, True, Effort.INSTANT)])
    assert instant.rating is Rating.EASY


def test_case_b_recalled_with_effort_is_hard() -> None:
    resolution = resolve([Result(Task.MEANING_TO_WORD, True, Effort.EFFORTFUL)])
    assert resolution.memory is MemoryResult.RECALLED_EFFORT
    assert resolution.rating is Rating.HARD and resolution.follow_up is FollowUp.NONE


def test_case_c_only_recognised_is_hard_and_repaired() -> None:
    results = [
        Result(Task.SITUATION_TO_WORD, False),
        Result(Task.MEANING_TO_WORD, False, probe=True),
        Result(Task.CHOOSE_WORD, True, probe=True),
    ]
    resolution = resolve(results)
    assert resolution.memory is MemoryResult.RECOGNIZED
    assert resolution.rating is Rating.HARD
    assert resolution.follow_up is FollowUp.REPAIR
    assert resolution.repair_level is Level.MEANING_TO_WORD


def test_case_d_forgotten_is_again_and_relearned() -> None:
    results = [Result(Task.MEANING_TO_WORD, False), Result(Task.CHOOSE_WORD, False, probe=True)]
    resolution = resolve(results)
    assert resolution.memory is MemoryResult.FORGOTTEN
    assert resolution.rating is Rating.AGAIN and resolution.follow_up is FollowUp.RELEARN


def test_case_e_a_new_context_recalled_is_good() -> None:
    resolution = resolve([Result(Task.CONTEXT_CLOZE, True, Effort.NORMAL)])
    assert resolution.rating is Rating.GOOD and resolution.follow_up is FollowUp.NONE


def test_case_f_a_failed_skill_with_the_memory_intact_is_good_and_repaired() -> None:
    results = [
        Result(Task.COLLOCATION, False),
        Result(Task.MEANING_TO_WORD, True, Effort.NORMAL, probe=True),
    ]
    resolution = resolve(results)
    assert resolution.memory is MemoryResult.RECALLED
    assert resolution.rating is Rating.GOOD
    assert resolution.follow_up is FollowUp.REPAIR and resolution.repair_level is Level.COLLOCATION


def test_a_probe_success_is_never_easy() -> None:
    results = [
        Result(Task.CONTEXT_CLOZE, False),
        Result(Task.MEANING_TO_WORD, True, Effort.INSTANT, probe=True),
    ]
    assert resolve(results).rating is Rating.GOOD


def test_probes_step_down_one_level_at_a_time() -> None:
    assert next_probe([Result(Task.COLLOCATION, False)]) is Level.MEANING_TO_WORD
    assert next_probe([Result(Task.CONTEXT_CLOZE, False)]) is Level.MEANING_TO_WORD
    assert next_probe([Result(Task.MEANING_TO_WORD, False)]) is Level.WORD_TO_MEANING
    assert next_probe([Result(Task.CHOOSE_WORD, False)]) is None
    assert next_probe([Result(Task.MEANING_TO_WORD, True)]) is None


def test_nothing_asked_is_an_error() -> None:
    with pytest.raises(ValueError):
        resolve([])
