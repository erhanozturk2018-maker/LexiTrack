"""A word's contexts: added, deleted, imported and exported.

A word is its definition and one or more contexts. These tests hold the
rules for the second part: a context belongs to one word and goes with it; a
sentence is not stored twice; a word deleted and added again starts afresh;
a JSON file of contexts attaches them to words LexiTrack already has, and
never creates one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lexitrack.core.errors import InvalidFileError, WordError
from lexitrack.models.context import clean_context, contains_word, find_word, word_length
from lexitrack.services.content_service import ContentService
from lexitrack.services.vocabulary_service import VocabularyService


@pytest.fixture
def english(service: VocabularyService):
    return service.create_list("English", "en")


@pytest.fixture
def content(service: VocabularyService) -> ContentService:
    return ContentService(service.database)


def _write(tmp_path: Path, data, name: str = "contexts.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _texts(service: VocabularyService, word_id: int) -> list[str]:
    return [c.text for c in service.contexts(word_id)]


def _count(service: VocabularyService, table: str) -> int:
    return service.database.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# -- a word and its contexts -------------------------------------------------------


def test_a_new_word_has_its_length_level_part_of_speech_and_definition(
    service: VocabularyService, english
) -> None:
    word, added = service.add_word(
        english.id, "sleep in", part_of_speech="verb", cefr_level="b1",
        definition="to sleep later than usual",
    )
    assert added
    assert (word.word, word.length, word.cefr_level, word.part_of_speech, word.definition) == (
        "sleep in", 7, "B1", "verb", "to sleep later than usual",
    )
    assert service.contexts(word.id) == ()


def test_one_context_is_added(service: VocabularyService, english) -> None:
    word, _ = service.add_word(english.id, "sleep in", definition="to sleep later than usual")
    added = service.add_context(word.id, "  I usually   sleep in on Sundays. ")
    assert added is not None and added.text == "I usually sleep in on Sundays."
    assert _texts(service, word.id) == ["I usually sleep in on Sundays."]


def test_several_contexts_are_kept_in_order(service: VocabularyService, english) -> None:
    word, _ = service.add_word(
        english.id, "deliberate", definition="done on purpose; to think carefully",
        contexts=["It was a deliberate attempt to mislead us.",
                  "The jury deliberated for two days."],
    )
    service.add_context(word.id, "She made a deliberate choice.")
    assert _texts(service, word.id) == [
        "It was a deliberate attempt to mislead us.",
        "The jury deliberated for two days.",
        "She made a deliberate choice.",
    ]


def test_a_context_is_deleted(service: VocabularyService, english) -> None:
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof",
                               contexts=["The attic was dusty.", "We sleep in the attic."])
    first, second = service.contexts(word.id)
    assert service.delete_context(first.id)
    assert _texts(service, word.id) == ["We sleep in the attic."]
    assert not service.delete_context(first.id)


def test_the_same_context_twice_is_one_context(service: VocabularyService, english) -> None:
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof")
    assert service.add_context(word.id, "The attic was dusty.") is not None
    assert service.add_context(word.id, "the  ATTIC was dusty.") is None
    assert _texts(service, word.id) == ["The attic was dusty."]


def test_an_empty_or_overlong_context_is_refused(service: VocabularyService, english) -> None:
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof")
    with pytest.raises(WordError):
        service.add_context(word.id, "   ")
    with pytest.raises(WordError):
        service.add_context(word.id, "attic " * 100)
    assert service.contexts(word.id) == ()


def test_the_definition_is_replaced(service: VocabularyService, english) -> None:
    word, _ = service.add_word(english.id, "deliberate", definition="done on purpose")
    updated = service.set_definition(
        word.id, "done on purpose; to think carefully before deciding"
    )
    assert updated.definition == "done on purpose; to think carefully before deciding"
    with pytest.raises(WordError):
        service.set_definition(word.id, "  ")


# -- deleting and adding again ------------------------------------------------------


def test_a_deleted_word_takes_its_contexts_and_record_with_it(
    service: VocabularyService, english
) -> None:
    other = service.create_list("Other", "en")
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof",
                               contexts=["The attic was dusty."])
    service.add_words_to_list(other.id, [word.id])
    service.mark_unknown(word.id)
    assert service.delete_words([word.id]) == 1

    assert service.get_word(word.id) is None
    for table in ("word_contexts", "word_sources", "list_words", "user_word_state"):
        assert service.database.connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE word_id = ?", (word.id,)
        ).fetchone()[0] == 0
    assert service.database.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert service.list_words(other.id) == []


def test_removing_a_word_from_its_only_list_deletes_its_contexts(
    service: VocabularyService, english
) -> None:
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof",
                               contexts=["The attic was dusty."])
    service.remove_words_from_list(english.id, [word.id])
    assert _count(service, "word_contexts") == 0


def test_a_deleted_word_can_be_added_again_and_starts_afresh(
    service: VocabularyService, english
) -> None:
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof",
                               contexts=["The attic was dusty."])
    service.mark_known(word.id)
    service.delete_words([word.id])

    again, added = service.add_word(english.id, "attic", definition="the room under a roof")
    assert added and again.id != word.id
    assert again.status.value == "not_reviewed"
    assert service.contexts(again.id) == ()
    assert _count(service, "words") == 1
    # And contexts can be added to it again, the old sentence included.
    assert service.add_context(again.id, "The attic was dusty.") is not None
    assert _texts(service, again.id) == ["The attic was dusty."]


def test_adding_a_word_twice_keeps_one_word_and_adds_only_new_contexts(
    service: VocabularyService, english
) -> None:
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof",
                               contexts=["The attic was dusty."])
    again, added = service.add_word(
        english.id, "Attic", definition="a room under the roof",
        contexts=["The attic was dusty.", "We keep toys in the attic."],
    )
    assert not added and again.id == word.id
    assert _count(service, "words") == 1
    assert _texts(service, word.id) == ["The attic was dusty.", "We keep toys in the attic."]


# -- JSON import ------------------------------------------------------------------------


def test_contexts_are_imported_onto_the_words_they_name(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    sleep_in, _ = service.add_word(english.id, "sleep in", definition="to sleep later than usual")
    acquire, _ = service.add_word(english.id, "acquire", definition="to get or gain something")
    path = _write(tmp_path, [
        {"word": "sleep in", "contexts": [
            "I don't have to work tomorrow, so I can sleep in.",
            "I usually sleep in on Sundays."]},
        {"word": "acquire", "contexts": [
            "She acquired valuable experience during her first year at the company."]},
    ])
    preview = content.preview_import(path)
    assert (preview.words, preview.context_count, preview.rejected) == (2, 3, ())
    assert _count(service, "word_contexts") == 0  # a preview writes nothing

    result = content.apply_import(preview)
    assert (result.words, result.contexts_added, result.rejected) == (2, 3, 0)
    assert _texts(service, sleep_in.id) == [
        "I don't have to work tomorrow, so I can sleep in.", "I usually sleep in on Sundays.",
    ]
    assert len(service.contexts(acquire.id)) == 1
    assert _count(service, "words") == 2


def test_importing_the_same_file_twice_adds_nothing_the_second_time(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    service.add_word(english.id, "attic", definition="a room under the roof",
                     contexts=["The attic was dusty."])
    path = _write(tmp_path, [{"word": "attic", "contexts": [
        "The attic was dusty.", "We keep toys in the attic.", "we keep toys in the attic."]}])
    preview = content.preview_import(path)
    assert (preview.context_count, preview.duplicate_count) == (1, 2)
    content.apply_import(preview)
    again = content.preview_import(path)
    assert again.context_count == 0 and not again.changes_anything


def test_an_unknown_word_is_reported_and_never_created(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    service.add_word(english.id, "attic", definition="a room under the roof")
    path = _write(tmp_path, [
        {"word": "attic", "contexts": ["The attic was dusty."]},
        {"word": "flibbertigibbet", "contexts": ["A flibbertigibbet laughed."]},
        {"word": "", "contexts": ["No word here."]},
        42,
        {"word": "attic", "contexts": "not a list but one sentence about the attic"},
    ])
    preview = content.preview_import(path)
    problems = [(entry.index, entry.problem) for entry in preview.rejected]
    assert [index for index, _ in problems] == [2, 3, 4]
    assert "not in your vocabulary" in problems[0][1]
    result = content.apply_import(preview)
    assert result.rejected == 3
    assert _count(service, "words") == 1
    assert _count(service, "word_contexts") == 2


def test_a_context_that_does_not_contain_its_word_is_imported_with_a_warning(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    service.add_word(english.id, "attic", definition="a room under the roof")
    path = _write(tmp_path, [{"word": "attic", "contexts": ["The loft was dusty.", ""]}])
    (entry,) = content.preview_import(path).entries
    assert entry.new_contexts == ["The loft was dusty."]
    assert any("does not seem to contain" in w for w in entry.warnings)
    assert any("empty" in w for w in entry.warnings)


def test_a_definition_in_the_file_replaces_the_word_s(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    word, _ = service.add_word(english.id, "deliberate", definition="done on purpose")
    path = _write(tmp_path, [
        {"word": "deliberate",
         "definition": "done on purpose; to think carefully before deciding",
         "contexts": ["It was a deliberate lie.", "They deliberated for hours."]},
    ])
    preview = content.preview_import(path)
    assert preview.definition_count == 1
    content.apply_import(preview)
    assert service.get_word(word.id).definition == (
        "done on purpose; to think carefully before deciding"
    )
    # The same definition again is no change.
    assert content.preview_import(path).definition_count == 0


def test_a_file_that_is_not_a_list_of_words_is_refused(
    content: ContentService, tmp_path: Path
) -> None:
    for data in ({"name": "x"}, [], "just text"):
        with pytest.raises(InvalidFileError):
            content.preview_import(_write(tmp_path, data))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(InvalidFileError):
        content.preview_import(broken)


def test_a_file_from_an_earlier_version_is_read_too(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    service.add_word(english.id, "attic", definition="a room under the roof")
    path = _write(tmp_path, {
        "format": "lexitrack-content",
        "words": [{"word": "attic", "contexts": [
            {"text": "The {{attic}} was dusty.", "translations": {"tr": "…"}}]}],
    })
    content.apply_import(content.preview_import(path))
    (word,) = service.list_words(english.id)
    assert _texts(service, word.id) == ["The attic was dusty."]


# -- JSON export ------------------------------------------------------------------------


def test_the_export_holds_each_word_whole(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    service.add_word(english.id, "sleep in", part_of_speech="verb", cefr_level="B1",
                     definition="to sleep later than usual",
                     contexts=["I usually sleep in on Sundays."])
    service.add_word(english.id, "attic", definition="a room under the roof")
    target, count = content.export(tmp_path / "out.json")
    assert count == 2
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"word": "sleep in", "length": 7, "cefr_level": "B1", "part_of_speech": "verb",
         "definition": "to sleep later than usual",
         "contexts": ["I usually sleep in on Sundays."]},
        {"word": "attic", "length": 5, "definition": "a room under the roof", "contexts": []},
    ]


def test_an_exported_file_filled_in_imports_back(
    service: VocabularyService, english, content: ContentService, tmp_path: Path
) -> None:
    word, _ = service.add_word(english.id, "attic", definition="a room under the roof")
    target, _ = content.export(tmp_path / "out.json")
    data = json.loads(target.read_text(encoding="utf-8"))
    data[0]["contexts"] = ["We keep old toys in the attic."]
    target.write_text(json.dumps(data), encoding="utf-8")
    result = content.apply_import(content.preview_import(target))
    assert result.contexts_added == 1 and result.definitions_changed == 0
    assert _texts(service, word.id) == ["We keep old toys in the attic."]


def test_the_status_counts_words_with_contexts(
    service: VocabularyService, english, content: ContentService
) -> None:
    service.add_word(english.id, "attic", definition="a room", contexts=["The attic."])
    service.add_word(english.id, "barn", definition="a farm building")
    service.add_word(english.id, "cellar")
    status = content.status()
    assert (status.words, status.with_contexts, status.contexts, status.without_definition) == (
        3, 1, 1, 1,
    )


# -- finding the word in a sentence -------------------------------------------------------


@pytest.mark.parametrize(
    "word, sentence, found",
    [
        ("acquire", "She acquired valuable experience.", "acquired"),
        ("sleep in", "We slept in on Sunday.", "slept in"),
        ("look up", "Look it up in the dictionary.", "Look it up"),
        ("brush my teeth", "He brushes his teeth twice a day.", "brushes his teeth"),
        ("child", "The children played outside.", "children"),
        ("study", "She studies at night.", "studies"),
        ("stop", "He stopped the car.", "stopped"),
        ("fuel", "The news fuelled his anger.", "fuelled"),
        ("travel", "We travelled all night.", "travelled"),
        ("(be) able to", "She is able to swim.", "able to"),
        ("well-known", "A well-known writer.", "well-known"),
    ],
)
def test_the_word_is_found_in_its_forms(word: str, sentence: str, found: str) -> None:
    start, end = find_word(sentence, word)
    assert sentence[start:end] == found


def test_a_different_word_is_not_taken_for_it() -> None:
    assert not contains_word("Courageous people act anyway.", "courage")
    assert not contains_word("The loft was dusty.", "attic")


def test_contexts_are_cleaned_and_lengths_counted() -> None:
    assert clean_context("  The {{ attic }} was   dusty. ") == "The attic was dusty."
    assert word_length("sleep in") == 7
    assert word_length("well-known") == 9
    assert word_length("o'clock") == 6
