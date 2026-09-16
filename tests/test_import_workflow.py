"""The prepare → preview → commit import workflow, and list targets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lexitrack.core.errors import DuplicateListError, LanguageMismatchError, ListNotFoundError
from lexitrack.services.import_service import ImportTarget, NewList
from lexitrack.services.vocabulary_service import VocabularyService

from .conftest import OXFORD_3000, requires_oxford_3000


@pytest.fixture
def german_json(tmp_path: Path) -> Path:
    path = tmp_path / "german_a1.json"
    path.write_text(
        json.dumps(
            {
                "name": "German A1",
                "language": "de",
                "description": "German A1 vocabulary",
                "words": ["Haus", "gehen", "kommen", "Haus"],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def english_json(tmp_path: Path) -> Path:
    path = tmp_path / "ielts.json"
    path.write_text(
        json.dumps({"name": "IELTS Vocabulary", "language": "en", "words": ["ability", "gift"]}),
        encoding="utf-8",
    )
    return path


# -- preview ---------------------------------------------------------------


def test_prepare_reads_the_file_but_writes_nothing(
    service: VocabularyService, german_json: Path
) -> None:
    preview = service.prepare_import(german_json)

    assert preview.parser_key == "json"
    assert preview.format_label == "JSON · JSON word list"
    assert (preview.suggested.name, preview.stated_language) == ("German A1", "de")
    assert preview.suggested.description == "German A1 vocabulary"
    assert (preview.parsed_count, preview.unique_count) == (4, 3)
    assert preview.source.name == "german_a1.json"
    assert service.is_empty()


def test_preview_counts_new_and_existing_words_per_language(
    service: VocabularyService, german_json: Path
) -> None:
    german = service.create_list("Existing German", "de")
    service.add_word(german.id, "Haus")
    english = service.create_list("English", "en")
    service.add_word(english.id, "gehen")  # same spelling, different language

    check = service.check_import(service.prepare_import(german_json), "de")

    assert (check.total, check.existing, check.new) == (3, 1, 2)


def test_cancelling_prepare_returns_nothing(service: VocabularyService, german_json: Path) -> None:
    assert service.prepare_import(german_json, should_cancel=lambda: True) is None


# -- targets ---------------------------------------------------------------


def test_import_creates_a_new_list_from_file_metadata(
    service: VocabularyService, german_json: Path
) -> None:
    result = service.import_document(german_json)

    (created,) = service.lists()
    assert (created.name, created.language, created.description) == (
        "German A1", "de", "German A1 vocabulary",
    )
    assert created.kind.value == "imported"
    assert result.list_names == ("German A1",)
    assert (result.new_words, result.added_to_lists) == (3, 3)


def test_import_into_one_existing_list(service: VocabularyService, german_json: Path) -> None:
    target = service.create_list("My German", "de")
    preview = service.prepare_import(german_json)

    result = service.commit_import(preview, ImportTarget(list_ids=(target.id,)))

    assert [lst.name for lst in service.lists()] == ["My German"]
    assert result.list_names == ("My German",)
    assert service.get_progress(target.id).total == 3


def test_import_into_a_new_list_with_a_chosen_name(
    service: VocabularyService, german_json: Path
) -> None:
    preview = service.prepare_import(german_json)
    service.commit_import(preview, ImportTarget(new_list=NewList("Deutsch Basics", "de")))
    assert [lst.name for lst in service.lists()] == ["Deutsch Basics"]


def test_import_one_file_into_several_lists_creates_one_word_each(
    service: VocabularyService, german_json: Path
) -> None:
    german = service.create_list("German A1", "de")
    difficult = service.create_list("My Difficult Words")
    preview = service.prepare_import(german_json)

    result = service.commit_import(preview, ImportTarget(list_ids=(german.id, difficult.id)))

    assert result.list_names == ("German A1", "My Difficult Words")
    assert result.new_words == 3
    assert service.get_progress(german.id).total == 3
    assert service.get_progress(difficult.id).total == 3
    assert service.database.connection.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 3
    haus = next(w for w in service.list_words(german.id) if w.normalized_word == "haus")
    assert haus.lists == ("German A1", "My Difficult Words")


def test_import_into_existing_and_new_lists_together(
    service: VocabularyService, german_json: Path
) -> None:
    difficult = service.create_list("My Difficult Words")
    preview = service.prepare_import(german_json)

    service.commit_import(
        preview, ImportTarget(list_ids=(difficult.id,), new_list=NewList("German A1", "de"))
    )

    assert {lst.name: lst.progress.total for lst in service.lists()} == {
        "German A1": 3,
        "My Difficult Words": 3,
    }


def test_reimporting_the_same_file_reuses_its_list(
    service: VocabularyService, german_json: Path
) -> None:
    service.import_document(german_json)
    word = service.get_next_word()
    service.mark_known(word.id)

    result = service.import_document(german_json)

    assert [lst.name for lst in service.lists()] == ["German A1"]
    assert (result.new_words, result.existing_words, result.added_to_lists) == (0, 3, 0)
    assert service.get_word(word.id).status.value == "known"


def test_a_file_with_a_stated_language_cannot_go_into_a_different_language_list(
    service: VocabularyService, german_json: Path
) -> None:
    oxford = service.create_list("Oxford 3000", "en")
    preview = service.prepare_import(german_json)

    with pytest.raises(LanguageMismatchError, match="German.*Oxford 3000.*English"):
        service.commit_import(preview, ImportTarget(list_ids=(oxford.id,)))
    assert service.get_progress(oxford.id).total == 0


def test_a_failed_import_rolls_back_everything_it_had_written(
    service: VocabularyService, tmp_path: Path
) -> None:
    """The new list, the source and the words are written, then the last step fails.

    A German file carrying one English word: the list is created and the words
    stored before adding them to the German list is refused. None of it may
    survive.
    """
    service.create_list("Existing", "de")
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps({"language": "de", "words": ["Haus", {"word": "gift", "language": "en"}]}),
        encoding="utf-8",
    )
    preview = service.prepare_import(path)

    with pytest.raises(LanguageMismatchError):
        service.commit_import(preview, ImportTarget(new_list=NewList("German", "de")))

    assert [lst.name for lst in service.lists()] == ["Existing"]
    assert service.database.connection.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 0
    assert service.list_sources() == []


def test_a_taken_list_name_is_refused_before_anything_is_written(
    service: VocabularyService, german_json: Path
) -> None:
    service.create_list("Taken")
    preview = service.prepare_import(german_json)

    with pytest.raises(DuplicateListError):
        service.commit_import(preview, ImportTarget(new_list=NewList("taken", "de")))

    assert [lst.name for lst in service.lists()] == ["Taken"]
    assert service.list_sources() == []


def test_importing_into_a_deleted_list_is_explained(
    service: VocabularyService, german_json: Path
) -> None:
    gone = service.create_list("Gone", "de")
    service.delete_list(gone.id)
    with pytest.raises(ListNotFoundError):
        preview = service.prepare_import(german_json)
        service.commit_import(preview, ImportTarget(list_ids=(gone.id,)))


def test_a_file_without_a_language_takes_the_target_lists_language(
    service: VocabularyService, make_pdf
) -> None:
    english = service.create_list("Reading", "en")
    preview = service.prepare_import(make_pdf(["ability and gift"]))
    assert preview.stated_language is None

    result = service.commit_import(preview, ImportTarget(list_ids=(english.id,)))

    assert result.language == "en"
    assert {w.language for w in service.list_words(english.id)} == {"en"}


def test_a_chosen_language_applies_when_the_file_states_none(
    service: VocabularyService, make_pdf
) -> None:
    preview = service.prepare_import(make_pdf(["Haus und Garten"]))
    target = ImportTarget(new_list=NewList("Garden"))

    result = service.commit_import(preview, target, language="de")

    assert result.language == "de"
    assert service.lists()[0].language == "de"


def test_lists_in_different_languages_cannot_share_one_unlabelled_file(
    service: VocabularyService, make_pdf
) -> None:
    english = service.create_list("English", "en")
    german = service.create_list("German", "de")
    preview = service.prepare_import(make_pdf(["word"]))
    with pytest.raises(LanguageMismatchError, match="different languages"):
        service.commit_import(preview, ImportTarget(list_ids=(english.id, german.id)))


def test_two_languages_in_two_files_stay_distinct(
    service: VocabularyService, german_json: Path, tmp_path: Path
) -> None:
    english = tmp_path / "english.json"
    english.write_text(json.dumps({"language": "en", "words": ["Haus"]}), encoding="utf-8")

    service.import_document(german_json)
    service.import_document(english)

    rows = service.database.connection.execute(
        "SELECT language FROM words WHERE normalized_word = 'haus' ORDER BY language"
    ).fetchall()
    assert [r[0] for r in rows] == ["de", "en"]


def test_several_files_import_into_their_own_lists(
    service: VocabularyService, german_json: Path, english_json: Path, make_pdf
) -> None:
    for path in (german_json, english_json, make_pdf(["ability matters"], name="notes.pdf")):
        preview = service.prepare_import(path)
        service.commit_import(preview, service.default_import_target(preview))

    names = {lst.name: (lst.language, lst.progress.total) for lst in service.lists()}
    assert names == {
        "German A1": ("de", 3),
        "IELTS Vocabulary": ("en", 2),
        "notes": ("und", 2),
    }


def test_source_provenance_is_kept_separate_from_the_list(
    service: VocabularyService, english_json: Path
) -> None:
    oxford_like = service.create_list("Oxford 3000", "en")
    preview = service.prepare_import(english_json)
    service.commit_import(preview, ImportTarget(list_ids=(oxford_like.id,)))

    word = next(iter(service.list_words(oxford_like.id)))
    assert word.lists == ("Oxford 3000",)
    assert word.sources == ("ielts.json",)


@requires_oxford_3000
def test_oxford_import_suggests_an_english_oxford_list(service: VocabularyService) -> None:
    preview = service.prepare_import(OXFORD_3000)
    assert preview.parser_key == "oxford"
    assert (preview.suggested.name, preview.stated_language) == ("Oxford 3000", "en")
    assert preview.source.name == "Oxford 3000"

    result = service.commit_import(preview, service.default_import_target(preview))
    (oxford,) = service.lists()
    assert (oxford.name, oxford.language, oxford.progress.total) == (
        "Oxford 3000", "en", result.total_words,
    )
