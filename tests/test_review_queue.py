"""The day's review queue (services/review_queue.py).

Which words make the cut when more is due than the limit, and in what order
they come: a warm-up of easy words, then the fragile ones mixed in, never
more than one in every few.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lexitrack.models.srs import CardState, SrsCard
from lexitrack.services.review_queue import (
    HARD_CEILING,
    Bucket,
    bucket,
    build,
    effective_capacity,
)

NOW = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)


def card(word_id: int, stability: float = 10.0, *, relearning: bool = False,
         lapses: int = 0, flagged: bool = False) -> SrsCard:
    return SrsCard(
        word_id=word_id,
        state=CardState.RELEARNING if relearning else CardState.REVIEW,
        due_at=NOW,
        stability=stability,
        lapse_count=lapses,
        needs_relearning=flagged,
    )


def test_buckets() -> None:
    assert bucket(card(1, flagged=True), 0.95) is Bucket.FRAGILE
    assert bucket(card(1, relearning=True), 0.95) is Bucket.FRAGILE
    assert bucket(card(1, stability=1.0), 0.95) is Bucket.FRAGILE
    assert bucket(card(1), 0.7) is Bucket.AT_RISK
    assert bucket(card(1), 0.9) is Bucket.NORMAL


def test_the_limit_is_never_above_the_ceiling_and_never_none() -> None:
    assert effective_capacity(100) == 100
    assert effective_capacity(400) == HARD_CEILING == 250
    assert effective_capacity(0) == HARD_CEILING, "no limit still has a ceiling"


def test_over_the_limit_the_fragile_and_the_least_remembered_are_kept() -> None:
    cards = [card(i) for i in range(1, 7)] + [card(9, flagged=True)]
    recall = {1: 0.95, 2: 0.60, 3: 0.90, 4: 0.70, 5: 0.99, 6: 0.85, 9: 0.9}
    queue = build(cards, recall, capacity=4, warm_up=0)
    kept = {c.word_id for c in queue.cards}
    assert kept == {9, 2, 4, 6}, "the fragile word, then the lowest chances of recall"
    assert queue.left_over == 3
    assert queue.count(Bucket.FRAGILE) == 1 and queue.count(Bucket.AT_RISK) == 2


def test_a_warm_up_of_the_easiest_then_one_fragile_in_every_four() -> None:
    normal = [card(i) for i in range(1, 11)]
    fragile = [card(100 + i, flagged=True, lapses=i) for i in range(1, 4)]
    recall = {c.word_id: 0.80 + c.word_id / 100 for c in normal}
    recall.update({c.word_id: 0.5 for c in fragile})
    queue = build(normal + fragile, recall, capacity=250, warm_up=3, fragile_every=4)
    order = [c.word_id for c in queue.cards]
    assert order[:3] == [10, 9, 8], "the three most likely to be remembered"
    kinds = ["F" if queue.buckets[i] is Bucket.FRAGILE else "n" for i in order]
    # Warm-up, then one fragile after every three others; the last comes as
    # the others run out.
    assert "".join(kinds) == "nnnnnnFnnnFnF"
    assert [i for i in order if i > 100] == [103, 102, 101], "most lapses first"
    # Never two fragile words in a row while there is anything to put between.
    assert "FF" not in "".join(kinds)


def test_with_nothing_to_mix_them_into_the_fragile_come_together() -> None:
    fragile = [card(i, flagged=True) for i in range(1, 4)]
    queue = build(fragile, {}, capacity=250)
    assert [c.word_id for c in queue.cards] == [1, 2, 3]
