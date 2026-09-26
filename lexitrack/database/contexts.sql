-- LexiTrack schema version 7: a word is its definition and its contexts.
--
-- Run by both a new database (after localization.sql) and the version 6 -> 7
-- migration, so the two paths produce the same tables.
--
-- A word is: the word, its CEFR level, its part of speech, one definition
-- (covering every sense the word is learned in) and one or more contexts —
-- plain English sentences that show the definition in use. Reviews ask two
-- things only: the word from its definition, and the definition from a
-- context, each as a choice among four.
--
-- The teaching content of schemas 5 and 6 (patterns, collocations,
-- register, related words, meanings and notes in the learner's language,
-- translations) is dropped: nothing reads it any more. The backup taken
-- before this step keeps it.

-- ------------------------------------------------------------------ 0.7 --
-- Contexts: id, word, text. Nothing else. The same sentence twice for one
-- word is refused by the UNIQUE constraint.
CREATE TABLE IF NOT EXISTS word_contexts_v7 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    text    TEXT    NOT NULL CHECK (length(trim(text)) > 0),
    UNIQUE (word_id, text)
);

-- Schema 5 marked the word in a context as {{word}}; the marker goes, the
-- sentence stays.
INSERT OR IGNORE INTO word_contexts_v7 (id, word_id, text)
SELECT id, word_id,
       trim(replace(replace(replace(replace(text, '{{ ', '{{'), ' }}', '}}'), '{{', ''), '}}', ''))
FROM word_contexts
WHERE length(trim(replace(replace(text, '{{', ''), '}}', ''))) > 0;

DROP TABLE IF EXISTS context_translations;
DROP TABLE word_contexts;
ALTER TABLE word_contexts_v7 RENAME TO word_contexts;
CREATE INDEX IF NOT EXISTS idx_word_contexts_word ON word_contexts(word_id);

DROP TABLE IF EXISTS word_localizations;
DROP TABLE IF EXISTS word_content;

-- Every question answered: which task, whether the answer was correct, and
-- for a correct answer how it went (Again, Hard, Good or Easy, as the learner
-- chose). Levels, depths and "novel context" belonged to the old routes and
-- go; earlier efforts are carried over as the rating each one stood for.
CREATE TABLE learning_attempts_v7 (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    session_id    TEXT    REFERENCES review_sessions(id) ON DELETE SET NULL,
    at            TEXT    NOT NULL,
    on_day        TEXT    NOT NULL,
    phase         TEXT    NOT NULL
                  CHECK (phase IN ('introduction', 'review', 'relearn', 'repair')),
    role          TEXT    NOT NULL CHECK (role IN ('primary', 'probe', 'retrieval')),
    task          TEXT    NOT NULL,
    context_id    INTEGER REFERENCES word_contexts(id) ON DELETE SET NULL,
    correct       INTEGER NOT NULL CHECK (correct IN (0, 1)),
    effort        TEXT    CHECK (effort IN ('again', 'hard', 'good', 'easy')),
    response_ms   INTEGER,
    review_log_id INTEGER REFERENCES review_logs(id) ON DELETE SET NULL,
    route_version TEXT    NOT NULL DEFAULT 'v3',
    undone_at     TEXT
);

INSERT INTO learning_attempts_v7
    (id, word_id, session_id, at, on_day, phase, role, task, context_id, correct,
     effort, response_ms, review_log_id, route_version, undone_at)
SELECT id, word_id, session_id, at, on_day, phase, role, task,
       CASE WHEN context_id IN (SELECT id FROM word_contexts) THEN context_id END,
       success,
       CASE effort WHEN 'instant' THEN 'easy' WHEN 'normal' THEN 'good'
                   WHEN 'effortful' THEN 'hard' END,
       response_ms, review_log_id, route_version, undone_at
FROM learning_attempts;

DROP TABLE learning_attempts;
ALTER TABLE learning_attempts_v7 RENAME TO learning_attempts;
CREATE INDEX IF NOT EXISTS idx_learning_attempts_word ON learning_attempts(word_id, at);
CREATE INDEX IF NOT EXISTS idx_learning_attempts_day ON learning_attempts(on_day);

-- The day's rating now says which task was asked and whether the answer was
-- correct. NULL for answers from before: they were asked another way.
ALTER TABLE review_logs ADD COLUMN task TEXT;
ALTER TABLE review_logs ADD COLUMN correct INTEGER CHECK (correct IN (0, 1));

-- Settings of the teaching content, which is gone.
DELETE FROM app_settings WHERE key IN ('learner_language', 'hide_meaning_in_study');
