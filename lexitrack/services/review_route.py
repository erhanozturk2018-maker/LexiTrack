"""Review route V2: the rules, with no state and no storage.

A V2 review asks for a word the hard way first and works down only when that
fails, so that the answer given to FSRS says how well the *memory* held, and
the attempts say what the learner could *do*:

1. **Primary** — the task picked for the word: the word from its meaning
   (level 2), from a context (3), a collocation (4) or a sentence (5).
2. **Probes**, only after a failure, each easier and each a fresh question:
   a failure at level 3 or above is followed by the word from its meaning
   (level 2); a failure there by choosing the word among four (level 1). The
   word is never shown before a probe, so a probe cannot be answered by
   having just seen the answer.
3. The **memory result** is the strongest success before the answer was
   shown (:func:`resolve`), and becomes the one rating FSRS hears.
4. **Relearning** after a word was forgotten, **repair** after a skill
   failed while the memory held: the word is taught, then asked again later
   in the session with a different prompt. That practice is recorded, but it
   never changes the rating.

Everything here is a pure function of its arguments, so the tests can state
the rules directly.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from ..models.attempt import Effort, Level, MemoryResult, Task
from ..models.content import ContextKind, WordContext, WordTeaching
from ..models.srs import Rating
from ..repositories.word_repository import StoredWord

#: At most this many teach-and-ask-again cycles per word per session.
MAX_CYCLES = 2
#: A relearning or repair question comes back after this many other cards.
REASK_GAP = 3

#: Words that carry no meaning of their own in a multi-word entry.
_FUNCTION_WORDS = {
    "a", "an", "the", "of", "to", "be", "in", "on", "at", "for", "with", "by",
    "from", "up", "out", "off", "sb", "sth", "somebody", "something", "one's",
}
_LEADING = ("to ", "be ", "a ", "an ", "the ")


# -- typed answers -------------------------------------------------------------------


def normalize_answer(text: str) -> str:
    """Case, accents' composition, apostrophes, hyphens and spacing made uniform."""
    text = unicodedata.normalize("NFC", text or "").casefold()
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = re.sub(r"[-‐-―_/]+", " ", text)
    text = re.sub(r"[^\w' ]+", "", text)
    return " ".join(text.split())


def answer_forms(answer: str) -> tuple[str, ...]:
    """The spellings accepted for ``answer``: itself, and without a leading
    ``to`` / ``be`` / article for a multi-word entry ("be expelled" →
    "expelled"), since a prompt cannot fairly demand the frame."""
    full = normalize_answer(answer)
    forms = [full]
    for lead in _LEADING:
        if full.startswith(lead) and len(full) > len(lead) + 2:
            forms.append(full[len(lead):])
    return tuple(dict.fromkeys(forms))


def _distance(a: str, b: str, limit: int) -> int:
    """Damerau-Levenshtein distance, stopping early past ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2: list[int] | None = None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if (
                previous2 is not None
                and i > 1
                and j > 1
                and ca == b[j - 2]
                and a[i - 2] == cb
            ):
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


@dataclass(frozen=True, slots=True)
class TypedCheck:
    correct: bool
    #: Accepted, but not spelled right: one slip (a letter wrong, missing,
    #: extra, or two swapped) in four to ten letters, two from eleven letters.
    #: Counts as effortful.
    near_miss: bool = False


def check_typed(accepted: Sequence[str], typed: str) -> TypedCheck:
    given = normalize_answer(typed)
    if not given:
        return TypedCheck(False)
    forms = [form for answer in accepted for form in answer_forms(answer)]
    if given in forms:
        return TypedCheck(True)
    for form in forms:
        allowed = 2 if len(form) >= 11 else 1 if len(form) >= 4 else 0
        if allowed and _distance(given, form, allowed) <= allowed:
            return TypedCheck(True, near_miss=True)
    return TypedCheck(False)


def effort_for(
    response_ms: int | None, answer: str, *, hinted: bool = False, near_miss: bool = False
) -> Effort:
    """How hard a correct typed answer was, from what can be observed.

    A hint or a misspelling makes it effortful. Otherwise the time from the
    prompt to the answer decides, allowing for the length of what was typed:
    within about 2.5 s plus 0.12 s a letter is instant, beyond 12 s plus 0.2 s
    a letter is effortful. Without a time it is normal.
    """
    if hinted or near_miss:
        return Effort.EFFORTFUL
    if response_ms is None:
        return Effort.NORMAL
    letters = len(normalize_answer(answer))
    if response_ms <= 2500 + 120 * letters:
        return Effort.INSTANT
    if response_ms > 12000 + 200 * letters:
        return Effort.EFFORTFUL
    return Effort.NORMAL


def hint_for(answer: str) -> str:
    """The first letter and the shape of the rest: ``r _ _ _ _ _ _ _ _``."""
    text = " ".join(answer.split())
    shape = []
    for index, ch in enumerate(text):
        if index == 0:
            shape.append(ch)
        elif ch == " ":
            shape.append(" ")
        else:
            shape.append("_" if ch.isalpha() else ch)
    return " ".join(shape)


# -- prompts ------------------------------------------------------------------------


def mask_word(text: str, word: str) -> str:
    """``text`` with the word and its forms hidden, for a definition used as a prompt.

    "the state of being reluctant" for *reluctance* → "the state of being ___".
    Each meaningful part of a multi-word entry is hidden the same way.
    """
    if not text:
        return text
    masked = text
    for token in normalize_answer(word).split():
        if token in _FUNCTION_WORDS or len(token) < 3:
            continue
        stem = token if len(token) < 5 else token[: max(4, len(token) - 2)]
        masked = re.sub(rf"(?i)\b{re.escape(stem)}\w*", "___", masked)
    return masked


class Source(StrEnum):
    """Where a prompt's meaning or text came from."""

    TURKISH = "turkish"
    DEFINITION = "definition"
    CONTEXT = "context"
    COLLOCATION = "collocation"
    WORD = "word"


@dataclass(frozen=True, slots=True)
class Prompt:
    task: Task
    #: What the learner reads.
    text: str
    #: The answers accepted (typed tasks), or the word (others).
    accepted: tuple[str, ...]
    #: The answer as shown once revealed.
    answer: str
    source: Source
    #: A second line under the prompt: a translation, a part of speech.
    detail: str | None = None
    context_id: int | None = None
    #: True when the context was never used in an attempt before: transfer.
    novel_context: bool = False

    @property
    def level(self) -> Level:
        return self.task.level


def has_meaning(word: StoredWord, teaching: WordTeaching) -> bool:
    content = teaching.content
    return bool(word.definition) or bool(content and content.core_meaning_tr)


def meaning_prompt(
    word: StoredWord, teaching: WordTeaching, avoid: Source | None = None
) -> Prompt | None:
    """The word from its meaning: Turkish if there is one, the definition if not,
    and the other one when ``avoid`` names the one just used."""
    content = teaching.content
    turkish = content.core_meaning_tr if content else None
    options = []
    if turkish:
        options.append((Source.TURKISH, turkish))
    if word.definition:
        options.append((Source.DEFINITION, mask_word(word.definition, word.word)))
    if avoid is not None and len(options) > 1:
        options = [option for option in options if option[0] is not avoid] + [
            option for option in options if option[0] is avoid
        ]
    if not options:
        return None
    source, text = options[0]
    detail = " · ".join(part for part in (word.part_of_speech, word.cefr_level) if part)
    return Prompt(
        task=Task.MEANING_TO_WORD,
        text=text,
        accepted=(word.word,),
        answer=word.word,
        source=source,
        detail=detail or None,
    )


def context_prompt(
    word: StoredWord,
    teaching: WordTeaching,
    *,
    exclude: set[int] | frozenset[int] = frozenset(),
    used: dict[int, object] | None = None,
) -> Prompt | None:
    """The word from a context it fits: the least recently used one not excluded."""
    used = used or {}
    candidates = [
        context
        for context in teaching.contexts
        if context.id is not None and context.id not in exclude and context.target
    ]
    if not candidates:
        return None
    # Never used first, then the one used longest ago.
    candidates.sort(key=lambda c: (c.id in used, str(used.get(c.id, ""))))
    context = candidates[0]
    task = (
        Task.SITUATION_TO_WORD if context.kind is ContextKind.SITUATION else Task.CONTEXT_CLOZE
    )
    return Prompt(
        task=task,
        text=context.blanked(),
        accepted=(context.target, word.word) if context.target else (word.word,),
        answer=context.target or word.word,
        source=Source.CONTEXT,
        detail=context.translation_tr,
        context_id=context.id,
        novel_context=context.id not in used,
    )


def collocation_prompt(
    word: StoredWord, teaching: WordTeaching, exclude: set[str] | frozenset[str] = frozenset()
) -> Prompt | None:
    """A collocation with its other word hidden: "reluctant to ___" → admit.

    Knowing what a word goes with is the step from recalling it to using it.
    """
    content = teaching.content
    if not content:
        return None
    own = {token for token in normalize_answer(word.word).split()}
    for collocation in content.collocations:
        if collocation in exclude:
            continue
        tokens = collocation.split()
        partners = [
            (index, token)
            for index, token in enumerate(tokens)
            if normalize_answer(token) not in own
            and normalize_answer(token) not in _FUNCTION_WORDS
            and len(normalize_answer(token)) >= 3
        ]
        if not partners or all(normalize_answer(t) not in own for t in tokens):
            continue
        index, partner = max(partners, key=lambda item: len(item[1]))
        shown = " ".join("___" if i == index else t for i, t in enumerate(tokens))
        return Prompt(
            task=Task.COLLOCATION,
            text=shown,
            accepted=(partner,),
            answer=collocation,
            source=Source.COLLOCATION,
            detail=content.core_meaning_tr or word.definition,
        )
    return None


def production_prompt(word: StoredWord, teaching: WordTeaching) -> Prompt:
    """Write a sentence with the word; graded by the learner against examples."""
    content = teaching.content
    meaning = (content.core_meaning_tr if content else None) or word.definition
    return Prompt(
        task=Task.PRODUCTION,
        text=word.word,
        accepted=(word.word,),
        answer=word.word,
        source=Source.WORD,
        detail=meaning,
    )


def examples(teaching: WordTeaching, limit: int = 2) -> list[WordContext]:
    """Contexts to compare a written sentence with."""
    return [c for c in teaching.contexts if c.kind is ContextKind.SENTENCE][:limit]


def choice_options(
    word: StoredWord, pool: Sequence[StoredWord], seed: str, count: int = 4
) -> tuple[StoredWord, ...]:
    """The word and three others to choose from, in a stable shuffled order.

    Others of the same part of speech are preferred, so the choice turns on
    meaning rather than on grammar; none shares the word's spelling. The seed
    keeps the order the same if the session is restored.
    """
    own = normalize_answer(word.word)
    others = [
        other
        for other in pool
        if other.id != word.id and normalize_answer(other.word) != own
    ]
    rng = random.Random(seed)
    rng.shuffle(others)
    pos = word.part_of_speech
    same_pos = [o for o in others if pos and o.part_of_speech == pos]
    rest = [o for o in others if o not in same_pos]
    chosen = (same_pos + rest)[: count - 1]
    options = [word, *chosen]
    rng.shuffle(options)
    return tuple(options)


# -- deciding ---------------------------------------------------------------------------


class FollowUp(StrEnum):
    NONE = "none"
    #: The memory held but a skill failed: teach that, ask it again.
    REPAIR = "repair"
    #: The word was forgotten: teach it, ask it again.
    RELEARN = "relearn"


@dataclass(frozen=True, slots=True)
class Result:
    """One question answered before the answer was shown."""

    task: Task
    success: bool
    effort: Effort | None = None
    #: True for the probe after a failure, False for the primary question.
    probe: bool = False


@dataclass(frozen=True, slots=True)
class Resolution:
    memory: MemoryResult
    rating: Rating
    follow_up: FollowUp
    #: The level to repair: the lowest level that failed.
    repair_level: Level | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def resolve(results: Sequence[Result]) -> Resolution:
    """The memory result and the rating, from the primary and any probes.

    The strongest success before the answer was shown decides (cases in
    docs/LEARNING_ENGINE.md, *Review route V2*):

    * recalled at the first question → Good, or Easy when instant, or Hard
      when effortful (A, B, E);
    * a harder question failed but the word came from its meaning → Good (or
      Hard if that was effortful), and the harder skill is repaired (F);
    * only chosen among four → Recognised: Hard, and the recall is repaired (C);
    * not even chosen → Forgotten: Again, and the word is relearned (D).
    """
    if not results:
        raise ValueError("nothing was asked")
    primary = results[0]
    failed_levels = [r.task.level for r in results if not r.success]
    repair_level = min(failed_levels) if failed_levels else None
    recalled = [r for r in results if r.success and r.task.level >= Level.MEANING_TO_WORD]
    if recalled:
        best = recalled[0]
        effortful = best.effort is Effort.EFFORTFUL
        memory = MemoryResult.RECALLED_EFFORT if effortful else MemoryResult.RECALLED
        if best is primary and primary.success:
            rating = (
                Rating.HARD if effortful
                else Rating.EASY if best.effort is Effort.INSTANT
                else Rating.GOOD
            )
            return Resolution(memory, rating, FollowUp.NONE)
        return Resolution(
            memory, Rating.HARD if effortful else Rating.GOOD, FollowUp.REPAIR, repair_level
        )
    if any(r.success and r.task.level is Level.WORD_TO_MEANING for r in results):
        return Resolution(
            MemoryResult.RECOGNIZED, Rating.HARD, FollowUp.REPAIR, Level.MEANING_TO_WORD
        )
    return Resolution(MemoryResult.FORGOTTEN, Rating.AGAIN, FollowUp.RELEARN)


def next_probe(results: Sequence[Result]) -> Level | None:
    """The probe owed after the latest result, or None when the asking is over."""
    last = results[-1]
    if last.success:
        return None
    if last.task.level >= Level.CONTEXT_TO_WORD:
        return Level.MEANING_TO_WORD
    if last.task.level is Level.MEANING_TO_WORD:
        return Level.WORD_TO_MEANING
    return None
