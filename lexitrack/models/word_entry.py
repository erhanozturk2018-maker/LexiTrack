"""The standard model every parser produces.

``WordEntry`` is the boundary between the parser layer and the vocabulary
engine. Parsers fill in whatever the source document actually provides; the
rest of the application never learns how the document was structured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WordEntry:
    """A single vocabulary item as extracted from a source document.

    Attributes:
        word: The word exactly as it should be shown to the user.
        normalized_word: The identity key used for deduplication. Produced by
            :func:`lexitrack.normalization.word_normalizer.normalize_word`.
        source_id: Identifier of the document the entry came from.
        part_of_speech: e.g. ``"verb"``. ``None`` when the source has no
            grammatical information.
        cefr_level: e.g. ``"B2"``. ``None`` when the source is not levelled.
        definition: Short definition, when the source provides one.
        example: Example sentence, when the source provides one.
        metadata: Parser-specific extras. Never interpreted by the vocabulary
            engine — it exists so a parser can keep information without
            leaking its own vocabulary into the rest of the application.
    """

    word: str
    normalized_word: str
    source_id: str = ""
    part_of_speech: str | None = None
    cefr_level: str | None = None
    definition: str | None = None
    example: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.word or not self.word.strip():
            raise ValueError("WordEntry.word must not be empty")
        if not self.normalized_word:
            raise ValueError("WordEntry.normalized_word must not be empty")

    def merged_with(self, other: WordEntry) -> WordEntry:
        """Return a copy enriched with any metadata ``other`` has and this lacks.

        Used when the same word appears more than once inside one document —
        for instance ``light (noun)`` and ``light (adjective)`` — so the richer
        of the two records survives without creating a duplicate identity.
        """
        merged_metadata = {**other.metadata, **self.metadata}
        return WordEntry(
            word=self.word,
            normalized_word=self.normalized_word,
            source_id=self.source_id or other.source_id,
            part_of_speech=_combine_pos(self.part_of_speech, other.part_of_speech),
            cefr_level=_lowest_level(self.cefr_level, other.cefr_level),
            definition=self.definition or other.definition,
            example=self.example or other.example,
            metadata=merged_metadata,
        )


#: CEFR levels ordered from easiest to hardest.
CEFR_ORDER = ("A1", "A2", "B1", "B2", "C1", "C2")


def _lowest_level(a: str | None, b: str | None) -> str | None:
    """Return the easier of two CEFR levels.

    Oxford lists a word once per sense, and senses can sit at different levels.
    The level at which a learner first meets the word is the useful one, so the
    lowest wins.
    """
    if a is None:
        return b
    if b is None:
        return a
    try:
        return min(a, b, key=CEFR_ORDER.index)
    except ValueError:
        return a


def _combine_pos(a: str | None, b: str | None) -> str | None:
    """Merge two part-of-speech strings into one comma separated list."""
    if not a:
        return b
    if not b:
        return a
    seen: list[str] = []
    for part in [p.strip() for p in f"{a}, {b}".split(",")]:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen)
