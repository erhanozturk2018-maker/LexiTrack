"""First learning: how a new word is taught before it is ever reviewed.

A new word is taught, then asked at once, then — if it needs it — asked again
a little later in the session, in a different way. How much teaching comes
first is the word's **depth**, decided from its content rather than set by
hand:

* **SHORT** — nothing is stored beyond the word's definition (or a meaning in
  the learner's language, with nothing else): the word and its meaning, then
  the word from its meaning. Most words start here until content is added.
* **LIGHT** — content exists and the word is straightforward: its meaning,
  pattern, collocations and one example, then the word from its meaning, and
  later from a context if there is one.
* **DEEP** — the content says the word needs more: a ``deep`` depth hint, or
  an abstract mnemonic (a contrast or a relation). Everything stored — nuance,
  usage, the mnemonic, two examples — then the word from its meaning, and
  later a second question in a different form.

**Adaptive**: a miss right after teaching means the word is taught again, one
depth deeper when there is more to show, and asked again a few cards later —
at most twice. None of this is rated: the answers are practice, recorded as
attempts of the introduction phase. The word's first rating is its first
review, on a later day, because an answer given a minute after being taught
measures working memory, not learning.

Words are taught in groups of four — four words shown, then the four asked —
so each question comes after a short gap filled by other words, which is
what makes it a retrieval rather than a repetition.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from ..models.attempt import Depth
from ..models.content import DepthHint, WordTeaching
from ..repositories.word_repository import StoredWord

#: Words shown together before they are asked.
GROUP_SIZE = 4

#: Rough seconds a review and a new word take, by depth, for the day's
#: estimate. Measured on the desktop card, answer typed; an estimate, shown
#: as "about".
SECONDS_PER_REVIEW = 20
SECONDS_PER_NEW = {Depth.SHORT: 45, Depth.LIGHT: 75, Depth.DEEP: 120}

_DEEPER = {Depth.SHORT: Depth.LIGHT, Depth.LIGHT: Depth.DEEP, Depth.DEEP: Depth.DEEP}


@dataclass(frozen=True, slots=True)
class DepthChoice:
    depth: Depth
    #: Why, in a sentence for the learner.
    reason: str


def has_content(teaching: WordTeaching) -> bool:
    """Anything beyond the definition and the core meaning."""
    content = teaching.content
    localization = teaching.localization
    return bool(
        teaching.contexts
        or (content is not None and not content.is_empty())
        or (
            localization is not None
            and any(
                getattr(localization, name)
                for name in ("nuance", "usage_note", "encoding_cue", "notes")
            )
        )
    )


def choose_depth(teaching: WordTeaching) -> DepthChoice:
    if not has_content(teaching):
        return DepthChoice(Depth.SHORT, "New word: its meaning, then the word from it.")
    content = teaching.content
    localization = teaching.localization
    deep_hint = content is not None and content.depth_hint is DepthHint.DEEP
    abstract = (
        localization is not None
        and localization.encoding_type is not None
        and localization.encoding_type.is_abstract
    )
    if deep_hint or abstract:
        return DepthChoice(
            Depth.DEEP,
            "New word that needs more: everything stored about it, and two questions.",
        )
    return DepthChoice(
        Depth.LIGHT, "New word: its meaning and how it is used, then two questions."
    )


def deeper(depth: Depth, teaching: WordTeaching) -> Depth:
    """The depth to teach again at after a miss; SHORT stays SHORT without content."""
    if depth is Depth.SHORT and not has_content(teaching):
        return Depth.SHORT
    return _DEEPER[depth]


def second_question(depth: Depth) -> bool:
    """Whether the word is asked again later in the session."""
    return depth is not Depth.SHORT


@dataclass(frozen=True, slots=True)
class Estimate:
    reviews: int
    new: int
    minutes: int

    @property
    def words(self) -> int:
        return self.reviews + self.new


def estimate(reviews: int, depths: Iterable[Depth]) -> Estimate:
    """About how long the day's session takes, in whole minutes."""
    depths = list(depths)
    seconds = reviews * SECONDS_PER_REVIEW + sum(SECONDS_PER_NEW[d] for d in depths)
    minutes = math.ceil(seconds / 60) if seconds else 0
    return Estimate(reviews=reviews, new=len(depths), minutes=minutes)


def groups(words: list[StoredWord], size: int = GROUP_SIZE) -> list[list[StoredWord]]:
    return [words[start : start + size] for start in range(0, len(words), size)]
