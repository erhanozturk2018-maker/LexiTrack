-- LexiTrack learning engine V2 tables, schema version 5.
--
-- Run by both a new database (after schema.sql, learning.sql and progress.sql)
-- and the version 4 -> 5 migration, so the two paths produce the same tables.
-- Everything here is additive: no existing row is changed.

-- ------------------------------------------------------------------ 0.5 --
-- What we know about a word, for teaching it. One row per word, every field
-- optional: a word with no row, or an empty one, still works (the SHORT
-- route). Content is kept apart from word_sources, which records what each
-- imported document said; this is one curated, editable version.
CREATE TABLE IF NOT EXISTS word_content (
    word_id         INTEGER PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    core_meaning_tr TEXT,
    nuance          TEXT,
    pattern         TEXT,
    -- JSON array of strings: "make a decision", "decision to do sth"
    collocations    TEXT,
    register        TEXT,
    encoding_type   TEXT CHECK (encoding_type IN
                    ('IMAGE', 'SCENE', 'ACTION', 'CONTRAST', 'RELATION', 'SOUND', 'NONE')),
    encoding_cue    TEXT,
    -- JSON array of {"word": ..., "relation": ...}
    related         TEXT,
    depth_hint      TEXT CHECK (depth_hint IN ('light', 'deep')),
    source          TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Usage examples. Several per word, so reviews can rotate through them and
-- the word is learned, not the sentence. The target word is marked in the
-- text as {{word}}, which is what makes cloze and situation tasks possible.
CREATE TABLE IF NOT EXISTS word_contexts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id        INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    kind           TEXT    NOT NULL DEFAULT 'sentence'
                   CHECK (kind IN ('sentence', 'situation')),
    text           TEXT    NOT NULL,
    translation_tr TEXT,
    source         TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_word_contexts_word ON word_contexts(word_id);

-- The skill record: every attempt to retrieve or use a word, graded or not.
-- review_logs stays the memory record FSRS reads; this is what SkillTracker
-- reads. An attempt that produced the day's FSRS rating links to it.
--
-- phase:  introduction | review | relearn | repair
-- role:   primary (the first, measuring attempt) | probe (an easier check
--         after a failure) | retrieval (practice after teaching, ungraded)
-- level:  1 word -> meaning, 2 meaning -> word, 3 situation/context -> word,
--         4 collocation/pattern, 5 sentence production
-- effort: instant | normal | effortful, for successes only
CREATE TABLE IF NOT EXISTS learning_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    session_id    TEXT    REFERENCES review_sessions(id) ON DELETE SET NULL,
    at            TEXT    NOT NULL,
    on_day        TEXT    NOT NULL,
    phase         TEXT    NOT NULL
                  CHECK (phase IN ('introduction', 'review', 'relearn', 'repair')),
    role          TEXT    NOT NULL CHECK (role IN ('primary', 'probe', 'retrieval')),
    task          TEXT    NOT NULL,
    level         INTEGER NOT NULL CHECK (level BETWEEN 1 AND 5),
    context_id    INTEGER REFERENCES word_contexts(id) ON DELETE SET NULL,
    novel_context INTEGER NOT NULL DEFAULT 0 CHECK (novel_context IN (0, 1)),
    success       INTEGER NOT NULL CHECK (success IN (0, 1)),
    effort        TEXT    CHECK (effort IN ('instant', 'normal', 'effortful')),
    response_ms   INTEGER,
    review_log_id INTEGER REFERENCES review_logs(id) ON DELETE SET NULL,
    route_version TEXT    NOT NULL DEFAULT 'v2',
    depth         TEXT    CHECK (depth IN ('short', 'light', 'deep')),
    undone_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_learning_attempts_word ON learning_attempts(word_id, at);
CREATE INDEX IF NOT EXISTS idx_learning_attempts_day ON learning_attempts(on_day);

-- The memory result behind each FSRS rating (NULL for version 1 answers,
-- which had only the rating), and which learning route produced it, so the
-- old and new routes can be compared on real answers.
ALTER TABLE review_logs ADD COLUMN memory_result TEXT
    CHECK (memory_result IN ('RECALLED', 'RECALLED_EFFORT', 'RECOGNIZED', 'FORGOTTEN'));
ALTER TABLE review_logs ADD COLUMN route_version TEXT NOT NULL DEFAULT 'v1';

-- Where an interrupted session stands, as versioned JSON, so a Telegram tap
-- after a restart continues the same step.
ALTER TABLE review_sessions ADD COLUMN flow_state TEXT;
