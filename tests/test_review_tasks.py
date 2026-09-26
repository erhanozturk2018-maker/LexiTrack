"""The two questions, built from stored content (services/review_tasks.py).

Pure functions: which task a word gets, which context is shown, and which
three other options stand beside the right one — alike in form, never
unfair.
"""

from __future__ import annotations

from lexitrack.models.attempt import Task
from lexitrack.models.context import WordContext
from lexitrack.repositories.word_repository import Candidate, StoredWord
from lexitrack.services.review_tasks import (
    OptionPool,
    choose_task,
    context_to_definition,
    definition_to_word,
    pick_context,
    retry_task,
)

VOCABULARY = [
    # (word, part of speech, CEFR, definition)
    ("sleep in", "verb", "B1", "to sleep later than usual"),
    ("stay up", "verb", "B1", "to not go to bed until late"),
    ("wake up", "verb", "A1", "to stop sleeping"),
    ("lie down", "verb", "A2", "to put your body flat on a bed"),
    ("sleep", "verb", "A1", "to rest with your eyes closed"),
    ("late", "adjective", "A1", "after the usual time"),
    ("give up", "verb", "B1", "to stop trying"),
    ("set off", "verb", "B1", "to start a journey"),
    ("deliberate", "adjective", "B2", "done on purpose"),
    ("deliberately", "adverb", "B2", "on purpose"),
    ("careless", "adjective", "B1", "not taking enough care"),
    ("sudden", "adjective", "B1", "happening quickly and unexpectedly"),
    ("cautious", "adjective", "B2", "careful to avoid risks"),
    ("honest", "adjective", "A2", "telling the truth"),
    ("purposeful", "adjective", "C1", "having a clear aim"),
    ("acquire", "verb", "B2", "to get or gain something"),
    ("obtain", "verb", "B2", "to get something, especially with effort"),
    ("achieve", "verb", "B1", "to succeed in doing something"),
    ("gain", "verb", "B1", "to get something you want or need"),
    ("expand", "verb", "B2", "to make or become larger"),
    ("attic", "noun", "B1", "a room just below the roof of a house"),
    ("barn", "noun", "B1", "a large farm building for animals or crops"),
    ("cellar", "noun", "B1", "a room under a house"),
    ("porch", "noun", "B2", "a covered area at the entrance of a house"),
    ("meadow", "noun", "B2", "a field of grass and wild flowers"),
]


def _candidates() -> list[Candidate]:
    return [
        Candidate(id=index, word=word, normalized=word, language="en",
                  part_of_speech=pos, cefr_level=level, definition=definition)
        for index, (word, pos, level, definition) in enumerate(VOCABULARY, start=1)
    ]


def _word(text: str) -> StoredWord:
    candidate = next(c for c in _candidates() if c.word == text)
    return StoredWord(
        id=candidate.id, word=candidate.word, normalized_word=candidate.normalized,
        part_of_speech=candidate.part_of_speech, cefr_level=candidate.cefr_level,
        definition=candidate.definition, language="en",
    )


POOL = OptionPool(_candidates())


# -- which question --------------------------------------------------------------


def test_a_word_without_contexts_is_asked_from_its_definition() -> None:
    for last in (None, Task.DEFINITION_TO_WORD, Task.CONTEXT_TO_DEFINITION):
        assert choose_task(False, last) is Task.DEFINITION_TO_WORD


def test_a_word_with_contexts_alternates() -> None:
    assert choose_task(True, None) is Task.DEFINITION_TO_WORD
    assert choose_task(True, Task.DEFINITION_TO_WORD) is Task.CONTEXT_TO_DEFINITION
    assert choose_task(True, Task.CONTEXT_TO_DEFINITION) is Task.DEFINITION_TO_WORD
    # An answer from an earlier version counts as neither.
    assert choose_task(True, Task.MEANING_TO_WORD) is Task.DEFINITION_TO_WORD


def test_a_miss_is_asked_again_the_other_way_when_it_can_be() -> None:
    assert retry_task(Task.DEFINITION_TO_WORD, True) is Task.CONTEXT_TO_DEFINITION
    assert retry_task(Task.CONTEXT_TO_DEFINITION, True) is Task.DEFINITION_TO_WORD
    assert retry_task(Task.DEFINITION_TO_WORD, False) is Task.DEFINITION_TO_WORD


def test_a_context_never_shown_comes_first_then_the_one_shown_longest_ago() -> None:
    contexts = [WordContext(1, f"Sentence {n} with sleep in.", n) for n in (10, 11, 12)]
    assert pick_context(contexts, used=[10, 11]).id == 12
    assert pick_context(contexts, used=[10, 11, 12]).id == 10
    assert pick_context(contexts, used=[11, 12, 10]).id == 11
    assert pick_context([], used=[]) is None


# -- Definition → Word -----------------------------------------------------------------


def test_definition_to_word_has_four_different_words_one_of_them_right() -> None:
    word = _word("sleep in")
    question = definition_to_word(word, POOL, "seed")
    assert question.task is Task.DEFINITION_TO_WORD
    assert question.prompt == "to sleep later than usual"
    assert len(question.options) == 4
    assert len({o.word_id for o in question.options}) == 4
    assert [o.text for o in question.options].count("sleep in") == 1
    assert question.right.word_id == word.id


def test_the_other_words_look_like_the_answer() -> None:
    """A phrasal verb among phrasal verbs: the choice turns on meaning."""
    question = definition_to_word(_word("sleep in"), POOL, "seed")
    others = [o.text for o in question.options if o.text != "sleep in"]
    assert all(" " in text for text in others)


def test_no_option_is_another_form_of_the_word_or_a_word_the_definition_uses() -> None:
    for seed in range(30):
        texts = {o.text for o in definition_to_word(_word("sleep in"), POOL, str(seed)).options}
        # "sleep" is in its definition and in the word itself.
        assert "sleep" not in texts
        texts = {o.text for o in definition_to_word(_word("deliberate"), POOL, str(seed)).options}
        assert "deliberately" not in texts


def test_the_same_seed_gives_the_same_question() -> None:
    word = _word("acquire")
    assert definition_to_word(word, POOL, "a") == definition_to_word(word, POOL, "a")
    orders = {definition_to_word(word, POOL, str(n)).answer for n in range(20)}
    assert len(orders) > 1


def test_no_question_without_a_definition() -> None:
    word = StoredWord(id=99, word="gizmo", normalized_word="gizmo", language="en")
    assert definition_to_word(word, POOL, "s") is None
    assert context_to_definition(word, WordContext(99, "A gizmo.", 1), POOL, "s") is None


# -- Context → Definition -------------------------------------------------------------


def test_context_to_definition_has_four_different_definitions_one_of_them_right() -> None:
    word = _word("sleep in")
    context = WordContext(word.id, "I don't have to work tomorrow, so I can sleep in.", 7)
    question = context_to_definition(word, context, POOL, "seed")
    assert question.task is Task.CONTEXT_TO_DEFINITION
    assert question.prompt == context.text and question.context_id == 7
    assert len({o.text for o in question.options}) == 4
    assert question.right.text == "to sleep later than usual"
    start, end = question.highlight
    assert context.text[start:end] == "sleep in"


def test_no_definition_names_the_word_or_belongs_to_a_word_the_context_holds() -> None:
    word = _word("attic")
    context = WordContext(word.id, "The attic of the barn was full of hay.", 3)
    for seed in range(30):
        question = context_to_definition(word, context, POOL, str(seed))
        texts = {o.text for o in question.options}
        # "barn" is in the sentence: its definition would be a trap.
        assert "a large farm building for animals or crops" not in texts
        assert all("attic" not in text for text in texts)


def test_the_word_is_picked_out_in_its_other_forms() -> None:
    word = _word("acquire")
    context = WordContext(word.id, "She acquired a taste for coffee.", 1)
    question = context_to_definition(word, context, POOL, "s")
    start, end = question.highlight
    assert context.text[start:end] == "acquired"
