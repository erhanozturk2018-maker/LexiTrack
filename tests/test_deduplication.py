"""Runtime deduplication behaviour."""

from __future__ import annotations

from lexitrack.normalization.deduplicator import deduplicate, iter_unique

from .conftest import entry


def test_repeated_words_collapse_to_one_identity() -> None:
    entries = [entry(w) for w in ("ability", "Ability", "ABILITY", "ability")]
    assert [e.normalized_word for e in deduplicate(entries)] == ["ability"]


def test_first_occurrence_sets_the_display_form() -> None:
    result = deduplicate([entry("Ability"), entry("ability")])
    assert result[0].word == "Ability"


def test_document_order_is_preserved() -> None:
    entries = [entry(w) for w in ("zebra", "apple", "zebra", "mango")]
    assert [e.word for e in deduplicate(entries)] == ["zebra", "apple", "mango"]


def test_distinct_words_are_all_kept() -> None:
    entries = [entry(w) for w in ("one", "two", "three")]
    assert len(deduplicate(entries)) == 3


def test_metadata_from_a_later_occurrence_is_merged_in() -> None:
    """A second mention that carries more information must not be discarded."""
    first = entry("light", part_of_speech="noun", cefr_level="A1")
    second = entry("light", part_of_speech="adjective", cefr_level="A2")

    merged = deduplicate([first, second])[0]

    assert merged.part_of_speech == "noun, adjective"
    # The level a learner first meets the word at is the useful one.
    assert merged.cefr_level == "A1"


def test_lower_cefr_level_wins_regardless_of_order() -> None:
    merged = deduplicate(
        [entry("set", cefr_level="B2"), entry("set", cefr_level="A2")]
    )[0]
    assert merged.cefr_level == "A2"


def test_missing_metadata_is_filled_from_a_later_occurrence() -> None:
    merged = deduplicate(
        [entry("bank"), entry("bank", part_of_speech="noun", definition="a place")]
    )[0]
    assert merged.part_of_speech == "noun"
    assert merged.definition == "a place"


def test_empty_input_produces_empty_output() -> None:
    assert deduplicate([]) == []


def test_iter_unique_streams_without_merging() -> None:
    entries = [
        entry("word", part_of_speech="noun"),
        entry("word", part_of_speech="verb"),
        entry("other"),
    ]
    streamed = list(iter_unique(entries))

    assert [e.normalized_word for e in streamed] == ["word", "other"]
    # The streaming variant keeps the first record untouched.
    assert streamed[0].part_of_speech == "noun"
