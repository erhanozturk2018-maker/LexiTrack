"""The two questions LexiTrack asks, built from stored content only.

* **Definition → Word** — the definition shown; the word chosen among four.
* **Context → Definition** — one of the word's contexts shown, the word
  picked out in it; its definition chosen among four.

Nothing is generated: the prompt is the word's own definition or context, and
the three other options are other words of the vocabulary, or their
definitions. They are chosen to look like the answer — the same part of
speech, a similar level, a similar shape (a phrase among phrases, a
definition with as many senses) — so the choice turns on meaning, not on
form. Options that would make the question unfair are never used: another
form of the same word, a word the definition itself contains, a definition
that names the word, the same definition twice.

A word without contexts is asked Definition → Word. A word with contexts
alternates: the task not asked last time.

Everything here is a pure function of its arguments and a seed, so a
question can be rebuilt exactly (a session restored) and the rules tested
directly.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from ..models.attempt import Task
from ..models.context import WordContext, find_word
from ..models.word_entry import CEFR_ORDER
from ..normalization.word_normalizer import normalize_word
from ..repositories.word_repository import Candidate, StoredWord

#: Options in every question.
OPTION_COUNT = 4
#: At most this many ask-again cycles per word per session after a wrong
#: answer (or Again).
MAX_CYCLES = 2
#: A question asked again comes back after this many other cards.
REASK_GAP = 3
#: The best-matching candidates the three others are drawn from, so the same
#: word does not always get the same three.
_SHORTLIST = 12

_FUNCTION_WORDS = {
    "a", "an", "the", "of", "to", "be", "in", "on", "at", "for", "with", "by",
    "from", "up", "out", "off", "and", "or", "sb", "sth", "somebody", "something",
    "one's", "my", "your", "it", "is",
}


@dataclass(frozen=True, slots=True)
class Option:
    """One of the four: a word (Definition → Word) or a definition
    (Context → Definition), and the word it belongs to."""

    word_id: int
    text: str


@dataclass(frozen=True, slots=True)
class Question:
    task: Task
    #: What the learner reads: the definition, or the context.
    prompt: str
    options: tuple[Option, ...]
    #: The index of the right option.
    answer: int
    #: The context shown (Context → Definition).
    context_id: int | None = None
    #: Where the word stands in the context, to pick it out; None when it
    #: cannot be found there.
    highlight: tuple[int, int] | None = None

    @property
    def right(self) -> Option:
        return self.options[self.answer]


# -- which question ---------------------------------------------------------------


def choose_task(has_contexts: bool, last: Task | None) -> Task:
    """The day's question for a word.

    No contexts: Definition → Word. With contexts, the one not asked last
    time, so the two alternate; Definition → Word when there is no history.
    """
    if not has_contexts:
        return Task.DEFINITION_TO_WORD
    if last is Task.DEFINITION_TO_WORD:
        return Task.CONTEXT_TO_DEFINITION
    return Task.DEFINITION_TO_WORD


def retry_task(failed: Task, has_contexts: bool) -> Task:
    """The question asked again after a wrong answer: the other one when the
    word has contexts, so the word is met from another side."""
    if has_contexts and failed is Task.DEFINITION_TO_WORD:
        return Task.CONTEXT_TO_DEFINITION
    return Task.DEFINITION_TO_WORD


def pick_context(
    contexts: Sequence[WordContext], used: Sequence[int] = (), seed: str = ""
) -> WordContext | None:
    """The context to show: one never shown before, else the one shown
    longest ago. ``used`` is the ids shown before, oldest first."""
    if not contexts:
        return None
    last_seen = {context_id: index for index, context_id in enumerate(used)}
    fresh = [c for c in contexts if c.id not in last_seen]
    if fresh:
        return fresh[random.Random(seed).randrange(len(fresh))] if seed else fresh[0]
    return min(contexts, key=lambda c: last_seen.get(c.id, -1))


# -- building it -------------------------------------------------------------------------


class OptionPool:
    """The vocabulary as options: every word with a definition, with what
    likeness is judged by worked out once, so a session of hundreds of
    questions is built in moments."""

    def __init__(self, candidates: Sequence[Candidate]) -> None:
        self._entries = [_Entry.of(candidate) for candidate in candidates if candidate.definition]

    def __len__(self) -> int:
        return len(self._entries)

    def others_for_word(self, word: StoredWord, seed: str) -> list[Candidate]:
        """Three other words for Definition → Word."""
        target = _Entry.of_word(word)

        def score(e: _Entry) -> float:
            return (
                _pos_distance(target.pos, e.pos) * 3
                + _level_distance(target.level, e.level)
                + min(abs(e.tokens - target.tokens), 2) * 1.5
                + abs(math.log((e.letters + 2) / (target.letters + 2)))
            )

        def fair(e: _Entry, chosen: list[_Entry]) -> bool:
            return (
                not _same_family(target, e)
                # A word the definition itself uses would be a trap: "sleep"
                # for "to sleep later than usual".
                and find_word(target.candidate.definition, e.candidate.word) is None
                and target.definition_key != e.definition_key
                and all(
                    not _same_family(o, e) and o.definition_key != e.definition_key
                    for o in chosen
                )
            )

        return self._draw(target, score, fair, seed)

    def others_for_definition(self, word: StoredWord, context: str, seed: str) -> list[Candidate]:
        """Three other definitions for Context → Definition."""
        target = _Entry.of_word(word)

        def score(e: _Entry) -> float:
            return (
                _pos_distance(target.pos, e.pos) * 3
                + min(abs(e.senses - target.senses), 2) * 2
                + abs(math.log((e.definition_length + 10) / (target.definition_length + 10))) * 2
                + _level_distance(target.level, e.level) * 0.5
            )

        def fair(e: _Entry, chosen: list[_Entry]) -> bool:
            return (
                not _same_family(target, e)
                and target.definition_key != e.definition_key
                # A definition that names the word, or a word the sentence
                # contains, would make the choice about something else.
                and find_word(e.candidate.definition, word.word) is None
                and find_word(context, e.candidate.word) is None
                and all(
                    o.definition_key != e.definition_key and not _same_family(o, e)
                    for o in chosen
                )
            )

        return self._draw(target, score, fair, seed)

    def _draw(self, target: _Entry, score, fair, seed: str) -> list[Candidate]:
        """The three: drawn at random from the best-matching fair candidates."""
        language = target.candidate.language
        pool = [
            e for e in self._entries
            if e.candidate.id != target.candidate.id and e.candidate.language == language
        ] or [e for e in self._entries if e.candidate.id != target.candidate.id]
        ranked = sorted(pool, key=lambda e: (score(e), e.candidate.id))
        shortlist: list[_Entry] = []
        for entry in ranked:
            if fair(entry, shortlist):
                shortlist.append(entry)
                if len(shortlist) >= _SHORTLIST:
                    break
        rng = random.Random(seed)
        chosen: list[_Entry] = []
        for entry in rng.sample(shortlist, len(shortlist)):
            if fair(entry, chosen):
                chosen.append(entry)
            if len(chosen) == OPTION_COUNT - 1:
                break
        return [entry.candidate for entry in chosen]


def definition_to_word(word: StoredWord, pool: OptionPool, seed: str) -> Question | None:
    """The definition, and the word among four. None without a definition."""
    if not word.definition:
        return None
    others = pool.others_for_word(word, seed)
    options = [Option(word.id, word.word), *(Option(c.id, c.word) for c in others)]
    return _shuffled(Task.DEFINITION_TO_WORD, word.definition, options, seed)


def context_to_definition(
    word: StoredWord, context: WordContext, pool: OptionPool, seed: str
) -> Question | None:
    """A context with the word picked out, and its definition among four.
    None without a definition."""
    if not word.definition:
        return None
    others = pool.others_for_definition(word, context.text, seed)
    options = [Option(word.id, word.definition), *(Option(c.id, c.definition) for c in others)]
    question = _shuffled(Task.CONTEXT_TO_DEFINITION, context.text, options, seed)
    return Question(
        task=question.task,
        prompt=question.prompt,
        options=question.options,
        answer=question.answer,
        context_id=context.id,
        highlight=find_word(context.text, word.word),
    )


def _shuffled(task: Task, prompt: str, options: list[Option], seed: str) -> Question:
    order = list(range(len(options)))
    random.Random(f"{seed}:order").shuffle(order)
    shuffled = tuple(options[i] for i in order)
    return Question(task=task, prompt=prompt, options=shuffled, answer=order.index(0))


# -- likeness --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Entry:
    """A candidate with what likeness is judged by."""

    candidate: Candidate
    pos: frozenset[str]
    level: int | None
    tokens: int
    letters: int
    #: The words that carry meaning, for telling one word family from another.
    content: frozenset[str]
    senses: int
    definition_length: int
    definition_key: str

    @classmethod
    def of(cls, candidate: Candidate) -> _Entry:
        tokens = _tokens(candidate.word)
        definition = candidate.definition or ""
        return cls(
            candidate=candidate,
            pos=frozenset(_pos_set(candidate.part_of_speech)),
            level=_level(candidate.cefr_level),
            tokens=len(tokens),
            letters=len(candidate.word),
            content=frozenset(t for t in tokens if t not in _FUNCTION_WORDS and len(t) >= 3),
            senses=_senses(definition),
            definition_length=len(definition),
            definition_key=" ".join(definition.casefold().split()),
        )

    @classmethod
    def of_word(cls, word: StoredWord) -> _Entry:
        return cls.of(
            Candidate(
                id=word.id,
                word=word.word,
                normalized=word.normalized_word,
                language=word.language,
                part_of_speech=word.part_of_speech,
                cefr_level=word.cefr_level,
                definition=word.definition or "",
            )
        )


def _tokens(text: str) -> list[str]:
    return [t for t in normalize_word(text).replace("-", " ").split() if t]


def _same_family(a: _Entry, b: _Entry) -> bool:
    """Two entries of one word family: the same word, one inside the other
    ("sleep" and "sleep in"), or the same stem ("deliberate",
    "deliberately")."""
    if a.candidate.normalized == b.candidate.normalized:
        return True
    for x in a.content:
        for y in b.content:
            if x == y:
                return True
            shorter = min(len(x), len(y))
            if shorter >= 5 and x[: min(shorter, 6)] == y[: min(shorter, 6)]:
                return True
    return False


def _pos_set(value: str | None) -> set[str]:
    return {part.strip().casefold() for part in (value or "").split(",") if part.strip()}


def _pos_distance(a: frozenset[str], b: frozenset[str]) -> float:
    """0 for the same parts of speech, 0.5 sharing some, 1 sharing none."""
    if not a or not b:
        return 0.5
    if a == b:
        return 0.0
    if a & b:
        return 0.5
    return 1.0


def _level(value: str | None) -> int | None:
    try:
        return CEFR_ORDER.index((value or "").upper())
    except ValueError:
        return None


def _level_distance(a: int | None, b: int | None) -> float:
    if a is None or b is None:
        return 1.5
    return abs(a - b)


def _senses(definition: str) -> int:
    """How many senses a definition lists: parts between semicolons."""
    return max(1, sum(1 for part in definition.split(";") if part.strip()))
