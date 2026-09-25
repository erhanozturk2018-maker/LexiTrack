"""Which due words a day's session asks, and in what order.

Three kinds of due word, by what the schedule knows about each:

* **FRAGILE** — flagged as hard, relearning, or with less than
  :data:`FRAGILE_STABILITY` days of stability: a memory that has recently
  failed or never settled.
* **AT_RISK** — a settled memory whose chance of recall has fallen below
  :data:`AT_RISK_RECALL`: overdue, and slipping.
* **NORMAL** — everything else.

**When more is due than the limit allows**, the words kept are the fragile
ones first, then the rest by lowest chance of recall: the words left for
another day are the ones most likely to still be remembered then. The limit
itself is the learner's, but never above :data:`HARD_CEILING` — past that
number a session is too long to finish, and an unfinished session is where
reviewing stops for good.

**The order** starts with a warm-up of a few of the easiest words (highest
chance of recall), so a session does not open on failures, then mixes the
fragile words in, at most one in every few, so they get attention without
turning the session into a string of misses. Both numbers are settings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..models.srs import CardState, SrsCard

#: No day's limit goes above this, whatever the setting says.
HARD_CEILING = 250
#: Below this stability (days) a memory counts as fragile.
FRAGILE_STABILITY = 2.0
#: Below this chance of recall a settled memory counts as at risk.
AT_RISK_RECALL = 0.8


class Bucket(StrEnum):
    FRAGILE = "fragile"
    AT_RISK = "at_risk"
    NORMAL = "normal"


def effective_capacity(setting: int) -> int:
    """The day's limit: the setting, but never 0 ("no limit") nor above the ceiling."""
    if setting <= 0:
        return HARD_CEILING
    return min(setting, HARD_CEILING)


def bucket(card: SrsCard, recall: float | None) -> Bucket:
    stability = card.stability if card.stability is not None else 0.0
    if card.needs_relearning or card.state is CardState.RELEARNING or stability < FRAGILE_STABILITY:
        return Bucket.FRAGILE
    if recall is not None and recall < AT_RISK_RECALL:
        return Bucket.AT_RISK
    return Bucket.NORMAL


@dataclass(frozen=True, slots=True)
class Queue:
    """The day's review order, and what did not fit."""

    cards: tuple[SrsCard, ...]
    buckets: Mapping[int, Bucket]
    #: Due words left for another day by the limit.
    left_over: int = 0

    def count(self, kind: Bucket) -> int:
        return sum(1 for card in self.cards if self.buckets[card.word_id] is kind)


def build(
    cards: Sequence[SrsCard],
    recall: Mapping[int, float | None],
    capacity: int,
    *,
    warm_up: int = 3,
    fragile_every: int = 4,
) -> Queue:
    """Choose and order the day's reviews (see the module docstring)."""
    kinds = {card.word_id: bucket(card, recall.get(card.word_id)) for card in cards}

    def chance(card: SrsCard) -> float:
        value = recall.get(card.word_id)
        return 1.0 if value is None else value

    fragile = [c for c in cards if kinds[c.word_id] is Bucket.FRAGILE]
    others = [c for c in cards if kinds[c.word_id] is not Bucket.FRAGILE]
    # Worst first within each group: the most lapses among the fragile, the
    # lowest chance of recall among the rest.
    fragile.sort(key=lambda c: (-c.lapse_count, chance(c), c.word_id))
    others.sort(key=lambda c: (chance(c), c.word_id))

    limit = effective_capacity(capacity)
    kept_fragile = fragile[:limit]
    kept_others = others[: max(limit - len(kept_fragile), 0)]
    left_over = len(cards) - len(kept_fragile) - len(kept_others)

    # The warm-up: the easiest of what is kept.
    easiest = sorted(kept_others, key=lambda c: (-chance(c), c.word_id))[: max(warm_up, 0)]
    easy_ids = {c.word_id for c in easiest}
    rest = [c for c in kept_others if c.word_id not in easy_ids]

    ordered: list[SrsCard] = list(easiest)
    step = max(fragile_every, 1)
    pending_fragile = list(kept_fragile)
    since_fragile = 0
    for card in rest:
        if pending_fragile and since_fragile >= step - 1:
            ordered.append(pending_fragile.pop(0))
            since_fragile = 0
        ordered.append(card)
        since_fragile += 1
    if pending_fragile and since_fragile >= step - 1:
        ordered.append(pending_fragile.pop(0))
    # Nothing else left to mix them into: they come at the end, together.
    ordered.extend(pending_fragile)
    return Queue(cards=tuple(ordered), buckets=kinds, left_over=left_over)
