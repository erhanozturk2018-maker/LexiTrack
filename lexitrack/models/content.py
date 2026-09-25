"""What we know about a word for teaching it: content and contexts.

Every field is optional. A word without content is taught by the SHORT route
(its definition and a retrieval), so the 6,825 words already in the database
all work before any of them is enriched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class EncodingType(StrEnum):
    """How a word is given an extra route into memory.

    Not every word needs an image. Concrete words suit IMAGE, SCENE or
    ACTION; abstract ones CONTRAST ("despite": two facts that pull against
    each other) or RELATION; SOUND is a sound-alike; NONE skips the step.
    """

    IMAGE = "IMAGE"
    SCENE = "SCENE"
    ACTION = "ACTION"
    CONTRAST = "CONTRAST"
    RELATION = "RELATION"
    SOUND = "SOUND"
    NONE = "NONE"

    @property
    def is_abstract(self) -> bool:
        """The kinds chosen for abstract words: they tend to need a deeper route."""
        return self in (EncodingType.CONTRAST, EncodingType.RELATION)


class DepthHint(StrEnum):
    LIGHT = "light"
    DEEP = "deep"


class ContextKind(StrEnum):
    #: A sentence using the word: "The room was so {{cramped}} we could barely move."
    SENTENCE = "sentence"
    #: A described situation the word fits, for situation -> word retrieval.
    SITUATION = "situation"


class ContentStatus(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    COMPLETE = "complete"


#: The target word in a context: {{word}}.
TARGET = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


@dataclass(frozen=True, slots=True)
class Related:
    word: str
    relation: str


@dataclass(frozen=True, slots=True)
class WordContent:
    """One word's pedagogical content. Empty fields are None or empty tuples."""

    word_id: int
    core_meaning_tr: str | None = None
    nuance: str | None = None
    pattern: str | None = None
    collocations: tuple[str, ...] = ()
    register: str | None = None
    encoding_type: EncodingType | None = None
    encoding_cue: str | None = None
    related: tuple[Related, ...] = ()
    depth_hint: DepthHint | None = None
    source: str | None = None
    updated_at: str | None = None

    #: The fields an enrichment fills, in the order they are shown.
    FIELDS = (
        "core_meaning_tr",
        "nuance",
        "pattern",
        "collocations",
        "register",
        "encoding_type",
        "encoding_cue",
        "related",
        "depth_hint",
    )

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in self.FIELDS)


@dataclass(frozen=True, slots=True)
class WordContext:
    word_id: int
    text: str
    kind: ContextKind = ContextKind.SENTENCE
    translation_tr: str | None = None
    source: str | None = None
    id: int | None = None

    @property
    def target(self) -> str | None:
        """The marked word, or None when the text does not mark it."""
        match = TARGET.search(self.text)
        return match.group(1) if match else None

    @property
    def plain(self) -> str:
        """The text with the marker removed: "... so cramped we ..."."""
        return TARGET.sub(lambda m: m.group(1), self.text)

    def blanked(self, blank: str = "_____") -> str:
        """The text with the word hidden, for context -> word retrieval."""
        return TARGET.sub(blank, self.text)


def content_status(content: WordContent | None, contexts: int) -> ContentStatus:
    """None, partial or complete, from what a word has.

    Complete means enough for the LIGHT route and the higher tasks: a Turkish
    core meaning, at least two contexts (so the second can test transfer),
    and a pattern or collocations. Anything less but not nothing is partial.
    """
    has_meaning = content is not None and bool(content.core_meaning_tr)
    has_usage = content is not None and bool(content.pattern or content.collocations)
    if has_meaning and has_usage and contexts >= 2:
        return ContentStatus.COMPLETE
    if (content is not None and not content.is_empty()) or contexts:
        return ContentStatus.PARTIAL
    return ContentStatus.NONE


@dataclass(frozen=True, slots=True)
class WordTeaching:
    """Everything the flow needs to teach one word, read in one go."""

    content: WordContent | None
    contexts: tuple[WordContext, ...] = field(default_factory=tuple)

    @property
    def status(self) -> ContentStatus:
        return content_status(self.content, len(self.contexts))
