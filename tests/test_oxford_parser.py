"""Oxford parser behaviour.

Two layers of testing. The first uses small PDFs built from text taken verbatim
from the real published lists, so the tests run on any clone. The second runs
against the real PDFs when they are present locally, which is what proves the
parser handles the documents it was written for.
"""

from __future__ import annotations

import pytest

from lexitrack.parsers.document import Document
from lexitrack.parsers.generic import GenericTextParser
from lexitrack.parsers.oxford import OxfordParser, _expand_pos, _parse_entry_line
from lexitrack.parsers.registry import ParserRegistry

from .conftest import (
    OXFORD_3000,
    OXFORD_5000,
    requires_oxford_3000,
    requires_oxford_5000,
)

# ruff: noqa: E501 - the sample below is copied verbatim from the published PDF.

# Lifted verbatim from The Oxford 3000 by CEFR level, including its awkward
# cases: multi-form entries, non-breaking spaces, sense disambiguators that
# wrap across a line, and superscript homograph numbering.
SAMPLE_3000 = """© Oxford University Press
1 / 12
The Oxford 3000 by CEFR level
The Oxford 3000 is the list of the 3000 most important words to learn in English, from A1 to B2 level.
A1
a, an indefinite article
about prep., adv.
action n.
add v.
all det., pron.
be v., auxiliary v.
can1 modal v.
ice cream n.
another det./pron.
A2
bank (money) n.
lie1 v.
light (from the
sun/a lamp) n.
B1
match (contest/correspond) n., v.
the definite article
B2
abandon v.
zone n.
"""


def parse_sample(make_pdf, text: str = SAMPLE_3000):
    path = make_pdf([text], name="oxford_sample.pdf", fontsize=9)
    with Document.open(path) as document:
        return OxfordParser().parse(document)


# -- entry line grammar -----------------------------------------------------


def test_simple_entry_yields_word_pos_and_level() -> None:
    (result,) = _parse_entry_line("abandon v.", "oxford3000", "B2")
    assert result.word == "abandon"
    assert result.normalized_word == "abandon"
    assert result.part_of_speech == "verb"
    assert result.cefr_level == "B2"
    assert result.source_id == "oxford3000"


def test_multiple_parts_of_speech_are_expanded() -> None:
    (result,) = _parse_entry_line("about prep., adv.", "oxford3000", "A1")
    assert result.part_of_speech == "preposition, adverb"


def test_slash_separated_parts_of_speech_are_expanded() -> None:
    (result,) = _parse_entry_line("another det./pron.", "oxford3000", "A1")
    assert result.part_of_speech == "determiner, pronoun"


def test_multi_word_part_of_speech_is_recognised() -> None:
    (result,) = _parse_entry_line("can1 modal v.", "oxford3000", "A1")
    assert result.word == "can"
    assert result.part_of_speech == "modal verb"


def test_homograph_numbering_is_stripped() -> None:
    """can1 and can2 are one vocabulary item, so the user is asked once."""
    first = _parse_entry_line("can1 modal v.", "oxford3000", "A1")[0]
    second = _parse_entry_line("can2 modal v.", "oxford3000", "A2")[0]
    assert first.normalized_word == second.normalized_word == "can"
    assert first.metadata["homograph"] == "can1"


def test_sense_disambiguator_is_kept_as_metadata_not_as_the_word() -> None:
    (result,) = _parse_entry_line("bank (money) n.", "oxford3000", "A2")
    assert result.word == "bank"
    assert result.normalized_word == "bank"
    assert result.metadata["sense"] == "money"


def test_comma_separated_forms_become_separate_entries() -> None:
    results = _parse_entry_line("a, an indefinite article", "oxford3000", "A1")
    assert [r.normalized_word for r in results] == ["a", "an"]
    assert all(r.part_of_speech == "indefinite article" for r in results)


def test_non_breaking_space_multi_word_entry_is_one_word() -> None:
    (result,) = _parse_entry_line("ice cream n.", "oxford3000", "A1")
    assert result.normalized_word == "ice cream"


def test_a_line_that_is_not_an_entry_is_ignored() -> None:
    assert _parse_entry_line("The Oxford 3000 is the list of words", "k", None) == []
    assert _parse_entry_line("1 / 12", "k", None) == []


def test_unknown_abbreviation_passes_through_rather_than_being_dropped() -> None:
    assert _expand_pos("n., quux.") == "noun, quux."


def test_duplicate_abbreviations_collapse() -> None:
    assert _expand_pos("n., n.") == "noun"


# -- whole document ---------------------------------------------------------


def test_level_headings_apply_to_the_entries_beneath_them(make_pdf) -> None:
    by_word = {e.normalized_word: e for e in parse_sample(make_pdf)}
    assert by_word["about"].cefr_level == "A1"
    assert by_word["bank"].cefr_level == "A2"
    assert by_word["match"].cefr_level == "B1"
    assert by_word["abandon"].cefr_level == "B2"


def test_page_furniture_never_becomes_vocabulary(make_pdf) -> None:
    words = {e.normalized_word for e in parse_sample(make_pdf)}
    assert "oxford" not in words
    assert "press" not in words
    assert "important" not in words


def test_wrapped_sense_disambiguator_is_rejoined(make_pdf) -> None:
    """``light (from the`` / ``sun/a lamp) n.`` is one entry, not two."""
    by_word = {e.normalized_word: e for e in parse_sample(make_pdf)}
    assert "light" in by_word
    assert by_word["light"].part_of_speech == "noun"
    assert by_word["light"].metadata["sense"] == "from the sun/a lamp"
    assert "sun" not in by_word


def test_every_sample_entry_is_extracted(make_pdf) -> None:
    words = {e.normalized_word for e in parse_sample(make_pdf)}
    expected = {
        "a", "an", "about", "action", "add", "all", "be", "can", "ice cream",
        "another", "bank", "lie", "light", "match", "the", "abandon", "zone",
    }
    assert expected <= words


def test_detection_recognises_an_oxford_document(make_pdf) -> None:
    path = make_pdf([SAMPLE_3000], name="oxford_sample.pdf", fontsize=9)
    with Document.open(path) as document:
        assert OxfordParser().can_parse(document) is True


def test_detection_rejects_ordinary_prose(make_pdf) -> None:
    path = make_pdf(["This is a normal paragraph of English prose about nothing."])
    with Document.open(path) as document:
        assert OxfordParser().can_parse(document) is False


def test_registry_prefers_oxford_over_generic(make_pdf) -> None:
    path = make_pdf([SAMPLE_3000], name="oxford_sample.pdf", fontsize=9)
    with Document.open(path) as document:
        assert isinstance(ParserRegistry().select(document), OxfordParser)


def test_registry_falls_back_to_generic_for_prose(make_pdf) -> None:
    path = make_pdf(["Just some ordinary prose in a document."])
    with Document.open(path) as document:
        assert isinstance(ParserRegistry().select(document), GenericTextParser)


def test_registry_honours_a_manual_override(make_pdf) -> None:
    path = make_pdf([SAMPLE_3000], name="oxford_sample.pdf", fontsize=9)
    with Document.open(path) as document:
        chosen = ParserRegistry().select(document, preferred_key="generic")
    assert isinstance(chosen, GenericTextParser)


# -- the real published documents -------------------------------------------


@requires_oxford_3000
def test_real_oxford_3000_is_detected_and_named() -> None:
    parser = OxfordParser()
    with Document.open(OXFORD_3000) as document:
        assert parser.can_parse(document) is True
        assert parser.source_key(document) == "oxford3000"
        assert parser.source_name(document) == "Oxford 3000"


@requires_oxford_3000
def test_real_oxford_3000_yields_about_three_thousand_words() -> None:
    from lexitrack.normalization.deduplicator import deduplicate

    with Document.open(OXFORD_3000) as document:
        entries = OxfordParser().parse(document)

    unique = deduplicate(entries)
    assert 2800 <= len(unique) <= 3100, f"got {len(unique)} unique words"
    assert all(e.cefr_level in {"A1", "A2", "B1", "B2"} for e in entries)
    assert all(e.part_of_speech for e in entries)


@requires_oxford_3000
def test_real_oxford_3000_contains_known_entries() -> None:
    with Document.open(OXFORD_3000) as document:
        by_word = {e.normalized_word: e for e in OxfordParser().parse(document)}

    assert by_word["abandon"].part_of_speech == "verb"
    assert by_word["abandon"].cefr_level == "B2"
    assert by_word["ability"].part_of_speech == "noun"
    assert by_word["ability"].cefr_level == "A2"


@requires_oxford_3000
def test_real_oxford_3000_has_no_definitions_or_examples() -> None:
    """The published PDF carries none, and the parser must not invent them."""
    with Document.open(OXFORD_3000) as document:
        entries = OxfordParser().parse(document)
    assert all(e.definition is None and e.example is None for e in entries)


@requires_oxford_5000
def test_real_oxford_5000_is_detected_and_named() -> None:
    parser = OxfordParser()
    with Document.open(OXFORD_5000) as document:
        assert parser.can_parse(document) is True
        assert parser.source_key(document) == "oxford5000"
        assert parser.source_name(document) == "Oxford 5000"


@requires_oxford_5000
def test_real_oxford_5000_covers_b2_and_c1() -> None:
    with Document.open(OXFORD_5000) as document:
        entries = OxfordParser().parse(document)

    levels = {e.cefr_level for e in entries}
    assert levels == {"B2", "C1"}
    assert 1900 <= len(entries) <= 2200, f"got {len(entries)} entries"


@pytest.mark.parametrize("path", [OXFORD_3000, OXFORD_5000])
def test_no_line_of_a_real_document_is_silently_dropped(path) -> None:
    """Every line must be an entry, a level heading or known page furniture.

    This is the test that would catch a future edition changing its layout:
    an unrecognised line means vocabulary is being lost in silence.
    """
    if not path.exists():
        pytest.skip(f"{path.name} is not present in pdfs/")

    from lexitrack.parsers import oxford as ox

    unrecognised: list[str] = []
    with Document.open(path) as document:
        for _page, text in document.iter_page_text():
            for line in ox._join_wrapped_lines(text.splitlines()):
                if ox._NOISE_RE.match(line) or ox._LEVEL_HEADING_RE.match(line):
                    continue
                if not ox._parse_entry_line(line, "k", None):
                    unrecognised.append(line)

    assert unrecognised == [], f"{len(unrecognised)} unrecognised lines"
