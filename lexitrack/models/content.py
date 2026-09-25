"""What we know about a word for teaching it, split by language.

A word belongs to one **target language** — the language being learned — and
that is part of its identity (``words.language``: English "gift" and German
"Gift" are two words). Teaching content comes in two kinds:

* **Target-language content**, shared by every learner whatever language
  they speak: the grammatical pattern, the collocations, the register, how
  the word relates to other words of its language, and the example contexts.
  "reluctant to do sth" is true of English for a speaker of any language.
  (:class:`WordContent`, :class:`WordContext`.)
* **Learner-language content**, one version per learner language: the core
  meaning in that language, the nuance explained, a usage note, a mnemonic,
  notes, and the translation of each context. (:class:`WordLocalization`,
  context translations.)

So a word has one record, one card and one review history, and any number of
localizations: English → Turkish and English → German learners of *commute*
share everything but the explanations. No language is special; a learner
language is a code such as ``tr``, ``de`` or ``es``.

Every field is optional. A word without content is taught by the SHORT
route: its definition and a retrieval.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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
    """A word's target-language content, shared by every learner language."""

    word_id: int
    #: The grammatical pattern(s), in the target language: "reluctant to do sth".
    pattern: str | None = None
    collocations: tuple[str, ...] = ()
    register: str | None = None
    #: Relations to other words of the target language.
    related: tuple[Related, ...] = ()
    depth_hint: DepthHint | None = None
    source: str | None = None
    updated_at: str | None = None

    #: The fields an enrichment fills, in the order they are shown.
    FIELDS = ("pattern", "collocations", "register", "related", "depth_hint")

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in self.FIELDS)


@dataclass(frozen=True, slots=True)
class WordLocalization:
    """A word explained in one learner language."""

    word_id: int
    #: The learner's language, a code such as ``tr``, ``de`` or ``es``.
    learner_language: str
    #: The core idea, in the learner's language, one line.
    core_meaning: str | None = None
    #: When this word and not a near synonym, explained in that language.
    nuance: str | None = None
    #: How it is used, explained for a speaker of that language.
    usage_note: str | None = None
    #: A mnemonic, and what kind of route into memory it is.
    encoding_type: EncodingType | None = None
    encoding_cue: str | None = None
    #: Anything else worth saying to a speaker of that language: a false
    #: friend, a confusion typical of it.
    notes: str | None = None
    source: str | None = None
    content_version: int = 1
    updated_at: str | None = None

    FIELDS = ("core_meaning", "nuance", "usage_note", "encoding_type", "encoding_cue", "notes")

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in self.FIELDS)


@dataclass(frozen=True, slots=True)
class WordContext:
    """An example of the word in use, in the target language."""

    word_id: int
    text: str
    kind: ContextKind = ContextKind.SENTENCE
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


def content_status(
    content: WordContent | None,
    localization: WordLocalization | None,
    contexts: int,
    learner_language: str | None = None,
) -> ContentStatus:
    """None, partial or complete, from what a word has for a language pair.

    Complete means enough for the LIGHT route and the higher tasks: a pattern
    or collocations, at least two contexts (so the second can test transfer),
    and — when the learner has chosen a language to be taught in — a core
    meaning in that language. Anything less but not nothing is partial.
    """
    has_usage = content is not None and bool(content.pattern or content.collocations)
    has_meaning = not learner_language or (
        localization is not None and bool(localization.core_meaning)
    )
    if has_meaning and has_usage and contexts >= 2:
        return ContentStatus.COMPLETE
    if (
        (content is not None and not content.is_empty())
        or (localization is not None and not localization.is_empty())
        or contexts
    ):
        return ContentStatus.PARTIAL
    return ContentStatus.NONE


@dataclass(frozen=True, slots=True)
class WordTeaching:
    """Everything the flow needs to teach one word to one learner, read in one go.

    ``learner_language`` is the language the learner is taught in, or None
    when none is chosen; ``localization`` and ``translations`` are that
    language's, keyed by context id.
    """

    content: WordContent | None
    contexts: tuple[WordContext, ...] = field(default_factory=tuple)
    learner_language: str | None = None
    localization: WordLocalization | None = None
    translations: Mapping[int, str] = field(default_factory=dict)

    @property
    def core_meaning(self) -> str | None:
        return self.localization.core_meaning if self.localization else None

    def translation(self, context: WordContext) -> str | None:
        return self.translations.get(context.id) if context.id is not None else None

    @property
    def status(self) -> ContentStatus:
        return content_status(
            self.content, self.localization, len(self.contexts), self.learner_language
        )
