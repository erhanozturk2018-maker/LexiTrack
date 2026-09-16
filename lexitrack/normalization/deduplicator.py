"""Collapsing repeated words inside a single import.

This is *runtime* deduplication: a ``set`` of normalized words gives O(1)
membership checks while a document is being processed. It is not storage — the
set disappears when the import finishes. Cross-session identity is the
database's job (a ``UNIQUE`` constraint on ``words.normalized_word``).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from ..models.word_entry import WordEntry


def deduplicate(entries: Iterable[WordEntry]) -> list[WordEntry]:
    """Return ``entries`` with one record per normalized word.

    The first occurrence sets the display form and position; later occurrences
    are merged into it so metadata found further down the document is not lost.
    """
    by_identity: dict[str, WordEntry] = {}
    order: list[str] = []
    seen_words: set[str] = set()

    for entry in entries:
        key = entry.normalized_word
        if key in seen_words:
            by_identity[key] = by_identity[key].merged_with(entry)
            continue
        seen_words.add(key)
        by_identity[key] = entry
        order.append(key)

    return [by_identity[key] for key in order]


def iter_unique(entries: Iterable[WordEntry]) -> Iterator[WordEntry]:
    """Stream ``entries``, skipping repeats, without merging metadata.

    Useful for very large documents where holding every entry in memory is
    wasteful and the extra metadata from repeats is not needed.
    """
    seen_words: set[str] = set()
    for entry in entries:
        if entry.normalized_word in seen_words:
            continue
        seen_words.add(entry.normalized_word)
        yield entry
