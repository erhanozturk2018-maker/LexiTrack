-- LexiTrack progress tables, schema version 4.
--
-- Run by both a new database (after schema.sql and learning.sql) and the
-- version 3 -> 4 migration, so the two paths produce the same tables.

-- ------------------------------------------------------------------ 0.4 --
-- Every change of a word's status, append-only.
--
-- user_word_state holds only the latest status, so "when did this word become
-- Known, and was it the schedule or a click?" had no answer. This table is
-- that answer. Rows are written by StateRepository in the same transaction as
-- the status change they describe, and only when the status actually changes.
--
-- cause:
--   mastery  the schedule judged the word learned (stability passed the threshold)
--   manual   a status button: the details panel, the list, Unknown Words
--   sorting  an answer on the Review tab's flashcards (I Know / I Don't Know)
--   undo     an answer taken back
-- reconstructed = 1 marks rows the upgrade inferred from review_logs for words
-- that became Known before this table existed. Their time is the review that
-- crossed the threshold, not a recorded event.
CREATE TABLE IF NOT EXISTS word_status_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    at            TEXT    NOT NULL,
    from_status   TEXT,
    to_status     TEXT    NOT NULL
                  CHECK (to_status IN ('not_reviewed', 'known', 'unknown')),
    cause         TEXT    NOT NULL
                  CHECK (cause IN ('mastery', 'manual', 'sorting', 'undo')),
    plan_id       INTEGER REFERENCES study_plans(id) ON DELETE SET NULL,
    reconstructed INTEGER NOT NULL DEFAULT 0 CHECK (reconstructed IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_word_status_events_word
    ON word_status_events(word_id, at);

CREATE INDEX IF NOT EXISTS idx_word_status_events_to
    ON word_status_events(to_status, cause, at);

-- An answer taken back with Undo stays in the history, marked, so the log
-- still says everything that happened; statistics and any fitting of the
-- scheduler to the user's reviews leave it out.
ALTER TABLE review_logs ADD COLUMN undone_at TEXT;

-- A plan that draws on every list, present and future: "all my unknown
-- words". Stored as a flag rather than as every list id, so a list imported
-- later joins the plan without the plan being edited.
ALTER TABLE study_plans ADD COLUMN all_lists INTEGER NOT NULL DEFAULT 0
    CHECK (all_lists IN (0, 1));
