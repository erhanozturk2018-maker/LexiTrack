"""Generic parser behaviour, exercised against real generated PDFs."""

from __future__ import annotations

import pytest

from lexitrack.core.errors import DocumentError, ImageOnlyDocumentError
from lexitrack.parsers.document import Document
from lexitrack.parsers.generic import GenericTextParser


def words_in(path) -> list[str]:
    with Document.open(path) as document:
        return [e.normalized_word for e in GenericTextParser().parse(document)]


def test_extracts_the_words_of_a_sentence(make_pdf) -> None:
    path = make_pdf(["The ability to communicate effectively"])
    assert words_in(path) == ["the", "ability", "to", "communicate", "effectively"]


def test_punctuation_is_not_counted_as_words(make_pdf) -> None:
    path = make_pdf(["Hello, world! This -- is a test; really? Yes: it is."])
    words = words_in(path)
    assert words == ["hello", "world", "this", "is", "test", "really", "yes", "it"]
    assert not any(any(c in w for c in ",.;:!?-") for w in words)


def test_repeated_words_appear_once(make_pdf) -> None:
    path = make_pdf(["cat dog cat Cat CAT dog"])
    assert words_in(path) == ["cat", "dog"]


def test_words_repeated_across_pages_appear_once(make_pdf) -> None:
    path = make_pdf(["alpha beta", "beta gamma"])
    assert words_in(path) == ["alpha", "beta", "gamma"]


def test_apostrophes_are_kept_inside_words(make_pdf) -> None:
    path = make_pdf(["don't can't it's o'clock"])
    assert words_in(path) == ["don't", "can't", "it's", "o'clock"]


def test_hyphenated_words_stay_whole(make_pdf) -> None:
    path = make_pdf(["well-known state-of-the-art"])
    assert words_in(path) == ["well-known", "state-of-the-art"]


def test_numbers_are_excluded(make_pdf) -> None:
    path = make_pdf(["there are 42 items costing 3.5 each in 2024"])
    words = words_in(path)
    assert words == ["there", "are", "items", "costing", "each", "in"]
    assert not any(any(c.isdigit() for c in w) for w in words)


def test_single_letters_are_skipped(make_pdf) -> None:
    """Stray single letters are list markers and initials, not vocabulary."""
    path = make_pdf(["a b see the c word"])
    assert words_in(path) == ["see", "the", "word"]


def test_irregular_whitespace_is_normalised(make_pdf) -> None:
    path = make_pdf(["spaced     out\n\n\nwords\t\there"])
    assert words_in(path) == ["spaced", "out", "words", "here"]


def test_case_variants_produce_one_word(make_pdf) -> None:
    path = make_pdf(["Ability ABILITY ability"])
    assert words_in(path) == ["ability"]


def test_generic_parser_supplies_no_metadata(make_pdf) -> None:
    """A plain PDF has no levels or definitions, and the parser must not invent any."""
    path = make_pdf(["ability"])
    with Document.open(path) as document:
        result = GenericTextParser().parse(document)[0]
    assert result.part_of_speech is None
    assert result.cefr_level is None
    assert result.definition is None
    assert result.example is None


def test_generic_parser_accepts_any_text_pdf(make_pdf) -> None:
    path = make_pdf(["anything at all"])
    with Document.open(path) as document:
        assert GenericTextParser().can_parse(document) is True


def test_progress_is_reported_for_every_page(make_pdf) -> None:
    path = make_pdf(["one", "two", "three"])
    seen: list[tuple[int, int]] = []
    with Document.open(path) as document:
        GenericTextParser().parse(document, progress=lambda c, t: seen.append((c, t)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


# -- document level failures ------------------------------------------------


def test_missing_file_is_reported_clearly(tmp_path) -> None:
    with pytest.raises(DocumentError) as info:
        Document.open(tmp_path / "nope.pdf")
    assert "could not be found" in str(info.value)


def test_a_non_pdf_file_is_rejected(tmp_path) -> None:
    bogus = tmp_path / "notes.pdf"
    bogus.write_text("this is plain text, not a PDF", encoding="utf-8")
    with pytest.raises(DocumentError):
        Document.open(bogus)


def test_image_only_pdf_is_reported_rather_than_silently_empty(image_only_pdf) -> None:
    """A scan must produce an explanation, never an empty vocabulary."""
    with pytest.raises(ImageOnlyDocumentError) as info:
        Document.open(image_only_pdf)
    assert "OCR" in str(info.value)
