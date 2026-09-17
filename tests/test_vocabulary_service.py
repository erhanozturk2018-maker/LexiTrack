"""Service-level behaviour: the review loop, resume, re-import and export."""

from __future__ import annotations

import csv
from pathlib import Path

import pymupdf
import pytest

from lexitrack.core.errors import DocumentError, ImageOnlyDocumentError, NoParserError
from lexitrack.database.connection import Database
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.services.vocabulary_service import VocabularyService

from .conftest import OXFORD_3000, OXFORD_5000, requires_oxford_3000, requires_oxford_5000

SIMPLE_PDF = "alpha beta gamma delta epsilon"


@pytest.fixture
def loaded(service: VocabularyService, make_pdf):
    """A service holding five words from a generic PDF."""
    service.import_document(make_pdf([SIMPLE_PDF]))
    return service


# -- import -----------------------------------------------------------------


def test_a_new_database_is_empty(service: VocabularyService) -> None:
    assert service.is_empty()
    assert service.get_next_word() is None
    assert service.get_progress().total == 0


def test_importing_adds_words_and_a_source(loaded: VocabularyService) -> None:
    assert not loaded.is_empty()
    assert loaded.get_progress().total == 5
    assert [s.name for s in loaded.list_sources()]


def test_import_reports_what_it_changed(service: VocabularyService, make_pdf) -> None:
    result = service.import_document(make_pdf([SIMPLE_PDF]))
    assert result.parsed == 5
    assert result.new_words == 5
    assert result.existing_words == 0
    assert result.parser_type == "generic"


def test_a_manually_chosen_parser_is_used(service: VocabularyService, make_pdf) -> None:
    result = service.import_document(make_pdf([SIMPLE_PDF]), parser_key="generic")
    assert result.parser_type == "generic"


def test_an_unknown_parser_key_is_rejected(service: VocabularyService, make_pdf) -> None:
    with pytest.raises(NoParserError):
        service.import_document(make_pdf([SIMPLE_PDF]), parser_key="nonexistent")


def test_importing_a_missing_file_raises_a_readable_error(service: VocabularyService) -> None:
    with pytest.raises(DocumentError):
        service.import_document(Path("does-not-exist.pdf"))


def test_importing_a_scan_explains_that_ocr_is_unsupported(
    service: VocabularyService, image_only_pdf: Path
) -> None:
    with pytest.raises(ImageOnlyDocumentError):
        service.import_document(image_only_pdf)
    assert service.is_empty()


def test_import_progress_is_reported(service: VocabularyService, make_pdf) -> None:
    messages: list[tuple[str, int]] = []
    service.import_document(make_pdf([SIMPLE_PDF]), progress=lambda m, p: messages.append((m, p)))

    assert messages
    assert messages[-1] == ("Import complete.", 100)


def test_cancelling_writes_nothing(service: VocabularyService, make_pdf) -> None:
    service.import_document(make_pdf([SIMPLE_PDF]), should_cancel=lambda: True)
    assert service.is_empty()


# -- review -----------------------------------------------------------------


def test_marking_known_advances_to_the_next_word(loaded: VocabularyService) -> None:
    first = loaded.get_next_word()
    loaded.mark_known(first.id)

    second = loaded.get_next_word()
    assert second.id != first.id
    assert loaded.get_word(first.id).status is ReviewStatus.KNOWN


def test_marking_unknown_advances_to_the_next_word(loaded: VocabularyService) -> None:
    first = loaded.get_next_word()
    loaded.mark_unknown(first.id)
    assert loaded.get_word(first.id).status is ReviewStatus.UNKNOWN
    assert loaded.get_next_word().id != first.id


def test_a_reviewed_word_is_never_shown_again(loaded: VocabularyService) -> None:
    seen: list[int] = []
    while (word := loaded.get_next_word()) is not None:
        assert word.id not in seen
        seen.append(word.id)
        loaded.mark(word.id, known=len(seen) % 2 == 0)

    assert len(seen) == 5


def test_progress_counters_track_answers(loaded: VocabularyService) -> None:
    for index in range(3):
        loaded.mark(loaded.get_next_word().id, known=index == 0)

    progress = loaded.get_progress()
    assert (progress.total, progress.known, progress.unknown) == (5, 1, 2)
    assert progress.reviewed == 3
    assert progress.remaining == 2
    assert progress.percent_complete == pytest.approx(60.0)


def test_undo_returns_a_word_to_the_queue(loaded: VocabularyService) -> None:
    first = loaded.get_next_word()
    loaded.mark_known(first.id)
    assert loaded.get_next_word().id != first.id

    loaded.undo(first.id)

    assert loaded.get_next_word().id == first.id
    assert loaded.get_progress().known == 0


def test_reset_clears_every_answer_but_keeps_the_words(loaded: VocabularyService) -> None:
    for _ in range(3):
        loaded.mark_known(loaded.get_next_word().id)

    loaded.reset_progress()

    progress = loaded.get_progress()
    assert progress.total == 5
    assert progress.reviewed == 0
    assert loaded.get_next_word() is not None


# -- resume -----------------------------------------------------------------


def test_review_position_survives_closing_the_application(db_path: Path, make_pdf) -> None:
    """Close after two answers, reopen, and continue from the third word."""
    pdf = make_pdf([SIMPLE_PDF])

    first_session = VocabularyService(Database(db_path))
    first_session.import_document(pdf)
    for _ in range(2):
        first_session.mark_known(first_session.get_next_word().id)
    expected_next = first_session.get_next_word().id
    first_session.close()

    second_session = VocabularyService(Database(db_path))
    try:
        assert second_session.get_next_word().id == expected_next
        progress = second_session.get_progress()
        assert (progress.total, progress.known, progress.remaining) == (5, 2, 3)
    finally:
        second_session.close()


def test_a_completed_review_stays_completed_after_reopening(
    db_path: Path, make_pdf
) -> None:
    pdf = make_pdf([SIMPLE_PDF])

    session = VocabularyService(Database(db_path))
    session.import_document(pdf)
    while (word := session.get_next_word()) is not None:
        session.mark_unknown(word.id)
    session.close()

    reopened = VocabularyService(Database(db_path))
    try:
        assert reopened.get_next_word() is None
        assert reopened.get_progress().unknown == 5
    finally:
        reopened.close()


# -- re-import --------------------------------------------------------------


def test_reimporting_the_same_document_adds_nothing(
    loaded: VocabularyService, make_pdf
) -> None:
    pdf = make_pdf([SIMPLE_PDF])
    loaded.import_document(pdf)
    before = loaded.get_progress().total

    result = loaded.import_document(pdf)

    assert result.new_words == 0
    assert result.existing_words == before
    assert loaded.get_progress().total == before


def test_reimporting_preserves_review_answers(loaded: VocabularyService, make_pdf) -> None:
    first = loaded.get_next_word()
    loaded.mark_known(first.id)

    loaded.import_document(make_pdf([SIMPLE_PDF]))

    assert loaded.get_word(first.id).status is ReviewStatus.KNOWN
    assert loaded.get_progress().known == 1


def test_a_word_shared_by_two_documents_is_reviewed_once(
    service: VocabularyService, make_pdf
) -> None:
    service.import_document(make_pdf(["alpha beta"], name="one.pdf"))
    first = service.get_next_word()
    service.mark_known(first.id)

    service.import_document(make_pdf(["alpha gamma"], name="two.pdf"))

    assert service.get_word(first.id).status is ReviewStatus.KNOWN
    assert service.get_progress().total == 3
    remaining = []
    while (word := service.get_next_word()) is not None:
        remaining.append(word.normalized_word)
        service.mark_unknown(word.id)
    assert first.normalized_word not in remaining


# -- export -----------------------------------------------------------------


@pytest.fixture
def reviewed(loaded: VocabularyService):
    """Three words marked unknown, two marked known."""
    for index in range(5):
        loaded.mark(loaded.get_next_word().id, known=index < 2)
    return loaded


def test_csv_export_contains_only_unknown_words(reviewed: VocabularyService, tmp_path) -> None:
    target = reviewed.export_unknown_csv(tmp_path / "unknown.csv")

    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    exported = {row["Word"].casefold() for row in rows}
    assert exported == {w.normalized_word for w in reviewed.list_unknown_words()}
    assert exported.isdisjoint({w.normalized_word for w in reviewed.list_known_words()})
    assert len(rows) == 3


def test_csv_export_has_the_expected_columns(reviewed: VocabularyService, tmp_path) -> None:
    target = reviewed.export_unknown_csv(tmp_path / "unknown.csv")
    with target.open(encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    assert header == [
        "Word", "Part of Speech", "CEFR", "Definition", "Example", "Note", "Sources",
    ]


def test_pdf_export_contains_only_unknown_words(reviewed: VocabularyService, tmp_path) -> None:
    target = reviewed.export_unknown_pdf(tmp_path / "unknown.pdf")

    assert target.exists() and target.stat().st_size > 0
    document = pymupdf.open(target)
    try:
        text = "\n".join(page.get_text() for page in document).casefold()
    finally:
        document.close()

    for word in reviewed.list_unknown_words():
        assert word.normalized_word in text
    for word in reviewed.list_known_words():
        assert word.normalized_word not in text


def test_pdf_export_is_titled_and_paginated(reviewed: VocabularyService, tmp_path) -> None:
    target = reviewed.export_unknown_pdf(tmp_path / "unknown.pdf")
    document = pymupdf.open(target)
    try:
        first_page = document[0].get_text()
    finally:
        document.close()

    assert "Unknown Words" in first_page
    assert "WORD" in first_page and "CEFR" in first_page


def test_export_creates_missing_directories(reviewed: VocabularyService, tmp_path) -> None:
    target = reviewed.export_unknown_csv(tmp_path / "deep" / "nested" / "unknown.csv")
    assert target.exists()


def test_exporting_with_nothing_unknown_still_produces_a_file(
    loaded: VocabularyService, tmp_path
) -> None:
    while (word := loaded.get_next_word()) is not None:
        loaded.mark_known(word.id)

    csv_target = loaded.export_unknown_csv(tmp_path / "empty.csv")
    pdf_target = loaded.export_unknown_pdf(tmp_path / "empty.pdf")

    with csv_target.open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.reader(handle))) == 1  # header only
    assert pdf_target.stat().st_size > 0


def test_missing_metadata_is_shown_as_a_dash_in_the_pdf(
    reviewed: VocabularyService, tmp_path
) -> None:
    """Generic-parser words have no level; the export must not leave a blank."""
    target = reviewed.export_unknown_pdf(tmp_path / "unknown.pdf")
    document = pymupdf.open(target)
    try:
        text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()
    assert "—" in text


# -- the real documents, end to end -----------------------------------------


@requires_oxford_3000
@requires_oxford_5000
def test_both_oxford_lists_import_into_one_vocabulary(service: VocabularyService) -> None:
    first = service.import_document(OXFORD_3000)
    second = service.import_document(OXFORD_5000)

    assert first.parser_type == "oxford"
    assert first.source_name == "Oxford 3000"
    assert second.source_name == "Oxford 5000"

    # The lists overlap, so the total is less than the sum of the parts.
    total = service.get_progress().total
    assert total == first.new_words + second.new_words
    assert second.existing_words > 0, "expected the two lists to share some words"
    assert total < first.parsed + second.parsed


@requires_oxford_3000
def test_a_real_import_produces_reviewable_words_with_metadata(
    service: VocabularyService,
) -> None:
    service.import_document(OXFORD_3000)

    word = service.get_next_word()
    assert word is not None
    assert word.part_of_speech
    assert word.cefr_level in {"A1", "A2", "B1", "B2"}
    assert word.sources == ("Oxford 3000",)


def test_pdf_export_can_start_each_level_with_a_heading(
    service: VocabularyService, tmp_path
) -> None:
    from dataclasses import replace

    lst = service.create_list("Levels", "en")
    service.add_word(lst.id, "apple", cefr_level="A1", definition="a round fruit")
    service.add_word(lst.id, "abandon", cefr_level="B2")
    content = service.export_content_for_list(lst.id)
    grouped = replace(content, group_by_level=True)

    target = service.export(grouped, tmp_path / "levels.pdf", "pdf")

    with pymupdf.open(target) as document:
        text = "\n".join(page.get_text() for page in document)
    # One heading per level, each counting its words, before that level's rows.
    assert text.count("1 word") == 2
    assert text.index("A1") < text.index("apple") < text.index("B2") < text.index("abandon")
    assert "a round fruit" in text


def test_a_long_level_does_not_push_its_heading_off_the_first_page(
    service: VocabularyService, tmp_path
) -> None:
    """Regression: keeping a heading with a pages-long table left page one blank."""
    from dataclasses import replace

    lst = service.create_list("Many", "en")
    for index in range(150):
        service.add_word(lst.id, f"word{'abcdefghij'[index // 15]}{'abcdefghijklmno'[index % 15]}",
                         cefr_level="A1")
    content = replace(service.export_content_for_list(lst.id), group_by_level=True)

    target = service.export(content, tmp_path / "many.pdf", "pdf")

    with pymupdf.open(target) as document:
        first_page = document[0].get_text()
    assert "150 words" in first_page and "worda" in first_page
