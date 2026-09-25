"""What a word's record shows the learner can *do* with it.

Skill is one of three things kept apart (docs/LEARNING_ENGINE.md):

* **memory** — when the word will be forgotten; FSRS, from review_logs;
* **skill** — which retrievals have succeeded; derived here, from
  learning_attempts;
* **status** — Known / Unknown / Not reviewed; the user's word, never set
  from either of the others.

A stage is the highest thing the record shows, and only *delayed* retrievals
count: a review, or the probe inside a review. A retrieval straight after
being taught (introduction, relearning, repair) is practice and is kept, but
it measures what is still in working memory, not what was learned.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class SkillStage(IntEnum):
    #: Never introduced, never answered.
    NONE = 0
    #: Introduced, or answered, but no delayed success yet.
    ENCOUNTERED = 1
    #: The meaning was retrieved from the word (level 1).
    RECOGNIZED = 2
    #: The word was retrieved from its meaning or a context (levels 2–3).
    RECALLED = 3
    #: The word was used — a collocation or a sentence (levels 4–5) — in at
    #: least two different contexts or tasks, so it is not one memorised line.
    PRODUCTIVE = 4

    @property
    def label(self) -> str:
        return {
            0: "Not started",
            1: "Encountered",
            2: "Recognised",
            3: "Recalled",
            4: "Productive",
        }[int(self)]


#: Instant successes above level 1 on this many different days are evidence
#: of automatic retrieval. Evidence, not a stage: it can come at any stage
#: above recognition and says how, not what.
AUTOMATIC_DAYS = 2
#: Productive needs this many different contexts or tasks at level 4–5.
PRODUCTIVE_VARIETY = 2


@dataclass(frozen=True, slots=True)
class WordSkill:
    word_id: int
    stage: SkillStage
    #: Delayed successes by what they showed.
    recognized: int = 0
    recalled: int = 0
    #: Different contexts or tasks the word was used in at level 4–5.
    produced_in: int = 0
    #: Delayed successes in a context not seen before: transfer.
    novel_context: int = 0
    #: Delayed failures of a first attempt.
    failures: int = 0
    #: Days with an instant success above level 1.
    automatic_days: int = 0
    #: Of the above, how many came from answers before schema 5, which only
    #: ever asked word → meaning and so count as recognition only.
    from_v1: int = 0
    last_on: str | None = None

    @property
    def automatic(self) -> bool:
        return self.automatic_days >= AUTOMATIC_DAYS
