-- LexiTrack schema version 8: Known words keep their own pace.
--
-- Run by both a new database (after contexts.sql) and the version 7 -> 8
-- migration, so the two paths produce the same tables.

-- A card placed by the spreading of Known words that come back at once
-- (services/learning_service.py): moved again when the number allowed per
-- day changes, and cleared by the card's next answer.
ALTER TABLE srs_cards ADD COLUMN spread INTEGER NOT NULL DEFAULT 0 CHECK (spread IN (0, 1));

-- Status history: a new cause, 'forgotten' — a Known word answered wrong
-- that the learner chose to learn again. SQLite cannot change a CHECK
-- constraint in place, so the table is rebuilt with every row kept.
CREATE TABLE word_status_events_v8 (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    at            TEXT    NOT NULL,
    from_status   TEXT,
    to_status     TEXT    NOT NULL
                  CHECK (to_status IN ('not_reviewed', 'known', 'unknown')),
    cause         TEXT    NOT NULL
                  CHECK (cause IN ('mastery', 'manual', 'sorting', 'undo', 'forgotten')),
    plan_id       INTEGER REFERENCES study_plans(id) ON DELETE SET NULL,
    reconstructed INTEGER NOT NULL DEFAULT 0 CHECK (reconstructed IN (0, 1))
);

INSERT INTO word_status_events_v8
    (id, word_id, at, from_status, to_status, cause, plan_id, reconstructed)
SELECT id, word_id, at, from_status, to_status, cause, plan_id, reconstructed
FROM word_status_events;

DROP TABLE word_status_events;

ALTER TABLE word_status_events_v8 RENAME TO word_status_events;

CREATE INDEX IF NOT EXISTS idx_word_status_events_word
    ON word_status_events(word_id, at);

CREATE INDEX IF NOT EXISTS idx_word_status_events_to
    ON word_status_events(to_status, cause, at);
