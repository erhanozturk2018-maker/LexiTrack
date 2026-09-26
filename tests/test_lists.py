"""Lists, membership, language-aware identity and manual vocabulary."""

from __future__ import annotations

import pytest

from lexitrack.core.errors import (
    DuplicateListError,
    LanguageMismatchError,
    ListError,
    ListNotFoundError,
)
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.models.vocabulary_list import ListKind
from lexitrack.services.vocabulary_service import VocabularyService

# -- list lifecycle ---------------------------------------------------------


def test_creating_a_list(service: VocabularyService) -> None:
    created = service.create_list("German A1", "de", "Basic German vocabulary")

    assert created.name == "German A1"
    assert created.language == "de"
    assert created.language_name == "German"
    assert created.description == "Basic German vocabulary"
    assert created.kind is ListKind.CUSTOM
    assert created.progress.total == 0
    assert [lst.name for lst in service.lists()] == ["German A1"]


def test_list_names_are_unique_ignoring_case(service: VocabularyService) -> None:
    service.create_list("German A1")
    with pytest.raises(DuplicateListError):
        service.create_list("german a1")


def test_a_list_needs_a_name(service: VocabularyService) -> None:
    with pytest.raises(ListError):
        service.create_list("   ")


def test_whitespace_in_names_is_tidied(service: VocabularyService) -> None:
    assert service.create_list("  My   Difficult  Words ").name == "My Difficult Words"


def test_renaming_a_list(service: VocabularyService) -> None:
    created = service.create_list("Germn A1")
    renamed = service.update_list(created.id, name="German A1", description="Fixed typo")
    assert (renamed.name, renamed.description) == ("German A1", "Fixed typo")


def test_renaming_to_a_taken_name_is_refused(service: VocabularyService) -> None:
    service.create_list("IELTS Vocabulary")
    other = service.create_list("Programming Terms")
    with pytest.raises(DuplicateListError):
        service.update_list(other.id, name="ielts vocabulary")


def test_renaming_only_the_case_is_allowed(service: VocabularyService) -> None:
    created = service.create_list("ielts vocabulary")
    assert service.update_list(created.id, name="IELTS Vocabulary").name == "IELTS Vocabulary"


def test_lists_are_sorted_alphabetically(service: VocabularyService) -> None:
    for name in ("Oxford 5000", "German A1", "my difficult words"):
        service.create_list(name)
    assert [lst.name for lst in service.lists()] == [
        "German A1",
        "my difficult words",
        "Oxford 5000",
    ]


def test_an_operation_on_a_deleted_list_is_explained(service: VocabularyService) -> None:
    created = service.create_list("Temporary")
    service.delete_list(created.id)
    with pytest.raises(ListNotFoundError):
        service.add_word(created.id, "word")


# -- manual words -----------------------------------------------------------


def test_adding_a_word_by_hand(service: VocabularyService) -> None:
    german = service.create_list("German A1", "de")
    word, added = service.add_word(
        german.id, "Haus", part_of_speech="noun", definition="house",
        contexts=["Das Haus ist groß.", "Wir kaufen ein Haus."],
    )

    assert added
    assert (word.word, word.normalized_word, word.language) == ("Haus", "haus", "de")
    assert (word.part_of_speech, word.definition, word.length) == ("noun", "house", 4)
    assert [c.text for c in service.contexts(word.id)] == [
        "Das Haus ist groß.", "Wir kaufen ein Haus.",
    ]
    assert word.status is ReviewStatus.NOT_REVIEWED
    assert word.lists == ("German A1",)
    assert word.sources == ("Added manually",)


def test_adding_the_same_word_twice_is_reported_not_duplicated(
    service: VocabularyService,
) -> None:
    german = service.create_list("German A1", "de")
    service.add_word(german.id, "Haus")
    _, added = service.add_word(german.id, "HAUS")

    assert not added
    assert service.get_progress(german.id).total == 1


def test_text_that_is_not_a_word_is_refused(service: VocabularyService) -> None:
    lst = service.create_list("Numbers")
    with pytest.raises(ListError, match="cannot be added"):
        service.add_word(lst.id, "1234")


def test_a_manual_word_and_an_imported_word_are_the_same_item(
    service: VocabularyService, make_pdf
) -> None:
    result = service.import_document(make_pdf(["ability to cope"], name="notes.pdf"))
    target = service.lists()[0]
    word, added = service.add_word(target.id, "Ability")

    assert not added
    assert word.id in result.word_ids


# -- language-aware identity -----------------------------------------------


def test_the_same_spelling_in_two_languages_is_two_words(service: VocabularyService) -> None:
    english = service.create_list("English", "en")
    german = service.create_list("German", "de")

    gift_en, _ = service.add_word(english.id, "gift")
    gift_de, _ = service.add_word(german.id, "Gift")

    assert gift_en.id != gift_de.id
    assert (gift_en.language, gift_de.language) == ("en", "de")

    service.mark_known(gift_en.id)
    assert service.get_word(gift_de.id).status is ReviewStatus.NOT_REVIEWED


def test_case_variants_in_one_language_are_one_word(service: VocabularyService) -> None:
    english = service.create_list("English", "en")
    first, _ = service.add_word(english.id, "Ability")
    other = service.create_list("IELTS", "en")
    second, _ = service.add_word(other.id, "ABILITY")
    assert first.id == second.id


def test_a_word_cannot_join_a_list_in_another_language(service: VocabularyService) -> None:
    german = service.create_list("German", "de")
    english = service.create_list("English", "en")
    word, _ = service.add_word(german.id, "Haus")

    with pytest.raises(LanguageMismatchError, match="not English"):
        service.add_words_to_list(english.id, [word.id])


def test_an_unspecified_language_list_accepts_any_language(service: VocabularyService) -> None:
    german = service.create_list("German", "de")
    english = service.create_list("English", "en")
    mixed = service.create_list("My Difficult Words")
    haus, _ = service.add_word(german.id, "Haus")
    gift, _ = service.add_word(english.id, "gift")

    assert service.add_words_to_list(mixed.id, [haus.id, gift.id]) == 2
    assert {w.language for w in service.list_words(mixed.id)} == {"de", "en"}


def test_changing_a_list_language_is_refused_when_words_disagree(
    service: VocabularyService,
) -> None:
    lst = service.create_list("Words", "en")
    service.add_word(lst.id, "gift")
    with pytest.raises(LanguageMismatchError):
        service.update_list(lst.id, language="de")
    assert service.update_list(lst.id, language="und").language == "und"


# -- membership ------------------------------------------------------------


@pytest.fixture
def shared(service: VocabularyService):
    """'ability' in three lists; 'abandon' only in Oxford 3000."""
    oxford = service.create_list("Oxford 3000", "en")
    ielts = service.create_list("IELTS Vocabulary", "en")
    difficult = service.create_list("My Difficult Words")
    ability, _ = service.add_word(oxford.id, "ability")
    abandon, _ = service.add_word(oxford.id, "abandon")
    service.add_words_to_list(ielts.id, [ability.id])
    service.add_words_to_list(difficult.id, [ability.id])
    return service, oxford, ielts, difficult, ability, abandon


def test_one_word_can_belong_to_many_lists(shared) -> None:
    service, _, _, _, ability, _ = shared
    word = service.get_word(ability.id)
    assert word.lists == ("IELTS Vocabulary", "My Difficult Words", "Oxford 3000")
    assert service.database.connection.execute(
        "SELECT COUNT(*) FROM words WHERE normalized_word = 'ability'"
    ).fetchone()[0] == 1


def test_learning_status_is_shared_across_lists(shared) -> None:
    service, oxford, ielts, difficult, ability, _ = shared
    service.mark_known(ability.id)

    for lst in (oxford, ielts, difficult):
        assert service.get_progress(lst.id).known == 1


def test_adding_a_word_already_in_the_list_is_skipped(shared) -> None:
    service, _, ielts, _, ability, _ = shared
    assert service.add_words_to_list(ielts.id, [ability.id, ability.id]) == 0


def test_words_added_later_go_to_the_end_of_the_review_order(shared) -> None:
    service, _, ielts, _, _, abandon = shared
    service.add_words_to_list(ielts.id, [abandon.id])
    assert [w.normalized_word for w in service.list_words(ielts.id)] == ["ability", "abandon"]
    assert service.get_next_word(ielts.id).normalized_word == "ability"


def test_removing_a_shared_word_keeps_it_and_its_status(shared) -> None:
    service, oxford, ielts, _, ability, _ = shared
    service.mark_unknown(ability.id)

    removed, deleted = service.remove_words_from_list(ielts.id, [ability.id])

    assert (removed, deleted) == (1, 0)
    word = service.get_word(ability.id)
    assert word is not None and word.status is ReviewStatus.UNKNOWN
    assert "IELTS Vocabulary" not in word.lists
    assert service.get_progress(oxford.id).unknown == 1


def test_removing_a_word_from_its_last_list_deletes_it(shared) -> None:
    service, oxford, _, _, _, abandon = shared
    assert service.exclusive_word_count(oxford.id, [abandon.id]) == 1

    removed, deleted = service.remove_words_from_list(oxford.id, [abandon.id])

    assert (removed, deleted) == (1, 1)
    assert service.get_word(abandon.id) is None


def test_deleting_a_list_keeps_words_other_lists_use(shared) -> None:
    service, oxford, ielts, difficult, ability, abandon = shared
    service.mark_known(ability.id)
    assert service.exclusive_word_count(oxford.id) == 1

    deleted = service.delete_list(oxford.id)

    assert deleted == 1
    assert service.get_list(oxford.id) is None
    assert service.get_word(abandon.id) is None
    survivor = service.get_word(ability.id)
    assert survivor.status is ReviewStatus.KNOWN
    assert survivor.lists == ("IELTS Vocabulary", "My Difficult Words")
    assert {lst.id for lst in service.lists()} == {ielts.id, difficult.id}


def test_list_progress_counts_only_that_lists_words(shared) -> None:
    service, oxford, ielts, _, ability, abandon = shared
    service.mark_known(abandon.id)
    service.mark_unknown(ability.id)

    oxford_progress = service.get_progress(oxford.id)
    ielts_progress = service.get_progress(ielts.id)
    assert (oxford_progress.total, oxford_progress.known, oxford_progress.unknown) == (2, 1, 1)
    assert (ielts_progress.total, ielts_progress.known, ielts_progress.unknown) == (1, 0, 1)

    progress_by_name = {lst.name: lst.progress for lst in service.lists()}
    assert progress_by_name["Oxford 3000"] == oxford_progress


# -- bulk status -----------------------------------------------------------


def test_bulk_status_changes(shared) -> None:
    service, oxford, _, _, ability, abandon = shared

    assert service.set_status([ability.id, abandon.id], ReviewStatus.KNOWN) == 2
    assert service.get_progress(oxford.id).known == 2

    assert service.set_status([ability.id], ReviewStatus.NOT_REVIEWED) == 1
    assert service.get_word(ability.id).status is ReviewStatus.NOT_REVIEWED
    assert service.get_word(ability.id).reviewed_at is None


def test_setting_the_status_a_word_already_has_changes_nothing(shared) -> None:
    service, _, _, _, ability, _ = shared
    service.set_status([ability.id], ReviewStatus.KNOWN)
    stamp = service.get_word(ability.id).reviewed_at

    assert service.set_status([ability.id], ReviewStatus.KNOWN) == 0
    assert service.get_word(ability.id).reviewed_at == stamp


def test_unknown_words_can_be_listed_per_list_or_overall(shared) -> None:
    service, oxford, ielts, _, ability, abandon = shared
    service.set_status([ability.id, abandon.id], ReviewStatus.UNKNOWN)

    assert {w.normalized_word for w in service.list_unknown_words()} == {"ability", "abandon"}
    assert [w.normalized_word for w in service.list_unknown_words(ielts.id)] == ["ability"]
    assert service.unknown_count(oxford.id) == 2
