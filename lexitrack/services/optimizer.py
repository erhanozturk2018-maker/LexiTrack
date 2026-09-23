"""Fitting the scheduler to the user's own memory.

FSRS has 21 parameters. The published defaults were fitted to millions of
other people's reviews; fitted to one person's answers they predict that
person better, which means reviews at better moments. This module is the
whole of that feature, and it is careful about three things:

1. **Enough data, and the right kind.** The library's optimizer silently
   returns the defaults below 512 answers given on a later day than the
   previous answer to the same word. :meth:`Personaliser.readiness` counts
   exactly those, so the Settings page can say how far away fitting is,
   instead of offering a button that would do nothing.
2. **A fair comparison.** Before anything is used, both the current and the
   fitted parameters replay the user's whole history and are scored on how
   well they predicted each answer (log loss: lower is better). The two runs
   use the same replay, so the numbers compare like with like.
3. **Nothing without consent, and a way back.** Fitting returns a result; the
   user decides whether to use it, and the defaults are one button away.

The optimizer needs PyTorch, which is large, so it is optional:
``pip install "lexitrack[optimizer]"``. Answers taken back with Undo are left
out of fitting and scoring.
"""

from __future__ import annotations

import importlib.util
import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..database.connection import Database
from ..models.settings import Setting
from ..models.srs import Rating, ReviewLogEntry
from ..repositories import CardRepository, RuntimeRepository

#: Below this the library's optimizer returns its defaults unchanged.
MIN_REVIEWS = 512

#: Where the last fit is described, for Settings and Progress.
FIT_NOTE_KEY = "fsrs_fit"


class OptimizerMissing(RuntimeError):
    """The optional optimizer, and PyTorch with it, is not installed."""


@dataclass(frozen=True, slots=True)
class Readiness:
    #: Answers that count for fitting: a later day than the word's previous answer.
    usable: int
    required: int
    installed: bool
    first_day: str | None
    last_day: str | None

    @property
    def enough(self) -> bool:
        return self.usable >= self.required

    @property
    def ready(self) -> bool:
        return self.enough and self.installed


@dataclass(frozen=True, slots=True)
class FitResult:
    parameters: tuple[float, ...]
    reviews: int
    loss_before: float
    loss_after: float

    @property
    def improvement(self) -> float:
        """How much better the fitted parameters predict, as a share (0.08 = 8%)."""
        if self.loss_before <= 0:
            return 0.0
        return (self.loss_before - self.loss_after) / self.loss_before


def optimizer_installed() -> bool:
    return importlib.util.find_spec("torch") is not None


class Personaliser:
    """Readiness, fitting, scoring, and applying or reverting parameters."""

    def __init__(self, database: Database, engine) -> None:
        self._db = database
        self._engine = engine
        self._cards = CardRepository(database)
        self._runtime = RuntimeRepository(database)

    # -- how far away --------------------------------------------------------

    def readiness(self) -> Readiness:
        logs = self._cards.all_logs(include_undone=False)
        days = sorted({log.reviewed_on for log in logs})
        return Readiness(
            usable=_usable(logs),
            required=MIN_REVIEWS,
            installed=optimizer_installed(),
            first_day=days[0] if days else None,
            last_day=days[-1] if days else None,
        )

    # -- fitting ---------------------------------------------------------------

    def snapshot(self) -> list[ReviewLogEntry]:
        """The answers to fit and score, read once, under the database lock.

        Fitting runs on a worker thread so the window stays responsive; the
        worker is handed this list and never touches the shared connection.
        """
        with self._db.lock:
            return self._cards.all_logs(include_undone=False)

    def fit(self, logs: Sequence[ReviewLogEntry] | None = None) -> FitResult:
        """Fit parameters to the user's answers. Uses nothing until :meth:`apply`.

        Pure computation over ``logs`` (from :meth:`snapshot`), so it is safe
        on a worker thread.
        """
        logs = self.snapshot() if logs is None else list(logs)
        if not optimizer_installed():
            raise OptimizerMissing(
                'Fitting needs the optional optimizer: pip install "lexitrack[optimizer]"'
            )
        from fsrs import Optimizer, ReviewLog
        from fsrs import Rating as FsrsRating

        review_logs = [
            ReviewLog(
                card_id=log.word_id,
                rating=FsrsRating(int(log.rating)),
                review_datetime=_utc(log.reviewed_at),
                review_duration=None,
            )
            for log in logs
        ]
        fitted = tuple(float(v) for v in Optimizer(review_logs).compute_optimal_parameters())
        current = self._engine.scheduler.parameters
        return FitResult(
            parameters=fitted,
            reviews=_usable(logs),
            loss_before=self.score(current, logs),
            loss_after=self.score(fitted, logs),
        )

    def score(
        self, parameters: Sequence[float], logs: Sequence[ReviewLogEntry] | None = None
    ) -> float:
        """Mean log loss of ``parameters`` over the user's history. Lower is better.

        Every word's answers are replayed through a scheduler built with these
        parameters and LexiTrack's own steps; before each answer after the
        first, the chance of recall it would have predicted is compared with
        what happened: Again means forgotten, anything else remembered.
        """
        from fsrs import Card, Scheduler
        from fsrs import Rating as FsrsRating

        scheduler = Scheduler(
            parameters=tuple(parameters),
            desired_retention=self._engine.settings.desired_retention,
            learning_steps=(timedelta(days=1),),
            relearning_steps=(timedelta(days=1),),
            enable_fuzzing=False,
        )
        total, count = 0.0, 0
        logs = self.snapshot() if logs is None else logs
        for history in _by_word(logs).values():
            card = None
            for log in history:
                when = _utc(log.reviewed_at)
                if card is None:
                    card = Card(due=when)
                else:
                    p = min(max(scheduler.get_card_retrievability(card, when), 1e-6), 1 - 1e-6)
                    remembered = log.rating is not Rating.AGAIN
                    total -= math.log(p) if remembered else math.log(1 - p)
                    count += 1
                card, _ = scheduler.review_card(card, FsrsRating(int(log.rating)), when)
        return total / count if count else 0.0

    # -- using them ---------------------------------------------------------------

    def apply(self, result: FitResult) -> None:
        """Use fitted parameters from now on, and note when and from what."""
        self._engine.save_settings({Setting.FSRS_PARAMETERS: json.dumps(list(result.parameters))})
        self._runtime.set(
            FIT_NOTE_KEY,
            json.dumps(
                {
                    "on": self._engine.clock.today(),
                    "reviews": result.reviews,
                    "loss_before": round(result.loss_before, 4),
                    "loss_after": round(result.loss_after, 4),
                }
            ),
        )

    def revert(self) -> None:
        """Back to the published defaults. The fit note goes too."""
        self._engine.save_settings({Setting.FSRS_PARAMETERS: ""})
        self._runtime.clear(FIT_NOTE_KEY)

    def fit_note(self) -> dict | None:
        """When the parameters in use were fitted, or None for the defaults."""
        if not self._engine.scheduler.personalised:
            return None
        raw = self._runtime.get(FIT_NOTE_KEY)
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}


def _usable(logs: Sequence[ReviewLogEntry]) -> int:
    """Answers given on a later day than the same word's previous answer.

    What the library's optimizer counts before it will fit anything.
    """
    usable = 0
    for history in _by_word(logs).values():
        for previous, current in zip(history, history[1:], strict=False):
            if (current.reviewed_at - previous.reviewed_at).days > 0:
                usable += 1
    return usable


def _by_word(logs: Sequence[ReviewLogEntry]) -> dict[int, list[ReviewLogEntry]]:
    grouped: dict[int, list[ReviewLogEntry]] = defaultdict(list)
    for log in logs:
        grouped[log.word_id].append(log)
    for history in grouped.values():
        history.sort(key=lambda log: (log.reviewed_at, log.id or 0))
    return grouped


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
