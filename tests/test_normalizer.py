"""Normalization behaviour."""

from __future__ import annotations

import pytest

from lexitrack.normalization.word_normalizer import (
    display_form,
    is_wordlike,
    normalize_word,
)


@pytest.mark.parametrize(
    "raw",
    ["Ability", "ABILITY", "ability", "  ability  ", "aBiLiTy", " ability "],
)
def test_case_and_whitespace_collapse_to_one_identity(raw: str) -> None:
    assert normalize_word(raw) == "ability"


def test_unicode_variants_normalize_to_the_same_word() -> None:
    # NFKC folds the ligature and the fullwidth form onto plain letters.
    assert normalize_word("ﬁnd") == "find"
    assert normalize_word("ｗord") == "word"


def test_apostrophe_variants_unify() -> None:
    assert normalize_word("don’t") == "don't"
    assert normalize_word("donʼt") == "don't"
    assert normalize_word("don't") == "don't"


def test_hyphen_variants_unify() -> None:
    assert normalize_word("well‐known") == "well-known"
    assert normalize_word("well–known") == "well-known"
    assert normalize_word("well-known") == "well-known"


def test_soft_hyphen_is_removed() -> None:
    assert normalize_word("vocab­ulary") == "vocabulary"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("word.", "word"),
        ("(word)", "word"),
        ('"word"', "word"),
        ("word,", "word"),
        ("word!", "word"),
        ("“word”", "word"),
        ("word…", "word"),
    ],
)
def test_edge_punctuation_is_stripped(raw: str, expected: str) -> None:
    assert normalize_word(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "42", "3.5", "---", "...", "-", "'", "#@!"])
def test_non_words_normalize_to_empty(raw: str) -> None:
    assert normalize_word(raw) == ""
    assert not is_wordlike(raw)


def test_mixed_digit_tokens_are_rejected() -> None:
    # "covid19" is a word with a digit; the MVP keeps vocabulary alphabetic.
    assert normalize_word("covid19") == ""


def test_lemmatization_is_not_performed() -> None:
    """Inflected forms stay distinct.

    Collapsing them would be lemmatization, which loses meaning and is
    deliberately out of scope.
    """
    forms = {normalize_word(w) for w in ("run", "running", "ran", "runs")}
    assert forms == {"run", "running", "ran", "runs"}


def test_display_form_preserves_original_casing() -> None:
    assert display_form("Ability") == "Ability"
    assert display_form("  Oxford  ") == "Oxford"
    assert display_form("don’t") == "don't"


def test_display_form_and_identity_can_differ() -> None:
    assert display_form("Ability") != normalize_word("Ability")
    assert display_form("Ability").casefold() == normalize_word("Ability")
