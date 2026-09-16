"""Turning a raw string from a PDF into a stable vocabulary identity.

The rule this module follows: normalization removes *presentation* differences
only. ``Ability``, ``ABILITY`` and ``ability`` are the same vocabulary item, so
they collapse. ``run``, ``running`` and ``ran`` are different vocabulary items,
so they do not — collapsing them would be lemmatization, which loses meaning and
is deliberately out of scope for the MVP.
"""

from __future__ import annotations

import re
import unicodedata

#: Unicode apostrophe variants that PDFs use interchangeably.
_APOSTROPHES = "‘’ʼʻ′`´"
#: Unicode dash/hyphen variants, including the soft hyphen used for line breaks.
_HYPHENS = "‐‑‒–—−"

_APOSTROPHE_RE = re.compile(f"[{_APOSTROPHES}]")
_HYPHEN_RE = re.compile(f"[{_HYPHENS}]")
_WHITESPACE_RE = re.compile(r"\s+")
#: Punctuation that may cling to a token but is never part of the word itself.
_EDGE_PUNCTUATION = ".,;:!?\"()[]{}<>«»„“”‘’*†‡§¶…/\|_=+~^%$#@&"

#: A token that survives normalization: letters, with internal apostrophes or
#: hyphens allowed, plus spaces for multi-word entries such as ``a, an``.
_ACCEPTABLE_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|['\- ])*[^\W\d_]$|^[^\W\d_]$", re.UNICODE)


def normalize_word(word: str) -> str:
    """Return the identity key for ``word``, or ``""`` if it is not a word.

    The transformation is: Unicode NFKC, soft-hyphen removal, apostrophe and
    hyphen unification, whitespace collapse, edge-punctuation stripping and
    case folding.

    >>> normalize_word("Ability")
    'ability'
    >>> normalize_word("  ABILITY  ")
    'ability'
    >>> normalize_word("don’t")
    "don't"
    >>> normalize_word("42")
    ''
    """
    if not word:
        return ""

    text = unicodedata.normalize("NFKC", word)
    text = text.replace("­", "")  # soft hyphen from justified text
    text = _APOSTROPHE_RE.sub("'", text)
    text = _HYPHEN_RE.sub("-", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.strip(_EDGE_PUNCTUATION + " '-")

    if not text:
        return ""

    text = text.casefold()
    if not _ACCEPTABLE_RE.match(text):
        return ""
    return text


def is_wordlike(token: str) -> bool:
    """True when ``token`` normalizes to a usable vocabulary identity."""
    return bool(normalize_word(token))


def display_form(word: str) -> str:
    """Return ``word`` cleaned for display, preserving its original casing.

    Used for the form shown on the review screen: ``Ability`` from a PDF stays
    ``Ability`` on screen even though its identity is ``ability``.
    """
    text = unicodedata.normalize("NFKC", word).replace("­", "")
    text = _APOSTROPHE_RE.sub("'", text)
    text = _HYPHEN_RE.sub("-", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text.strip(_EDGE_PUNCTUATION + " ")
