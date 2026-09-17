-- LexiTrack schema, version 3.
--
-- This file creates a *new* database. An existing database is never run
-- through it; it is upgraded step by step by migrations.py instead, and a test
-- asserts that both paths produce the same structure.
--
-- Five concerns, kept apart on purpose:
--
--   words             vocabulary identity: one row per (language, word)
--   sources           provenance: which file a word was extracted from
--   word_sources      what each source said about a word (POS, CEFR, ...)
--   lists             what the user has chosen to study
--   list_words        which words belong to which list (many-to-many)
--   user_word_state   what the user knows, per word, independent of lists
--
-- Version 3 adds the learning engine on top, without changing any of the
-- above:
--
--   study_plans       what the user is actively learning right now
--   study_plan_lists  which lists a plan draws its words from
--   srs_cards         the schedule of a word that has been introduced
--   review_logs       every rating ever given: the learning history
--   review_sessions   one sitting, so a Telegram session can be resumed
--   telegram_updates  handled callback ids, so one tap is one review
--   app_settings      settings both the UI and the bot must agree on
--   runtime_state     what the app knows about its own last run
--
-- Known/Unknown (user_word_state) and the schedule (srs_cards) are separate on
-- purpose: the first is the user's broad judgement of a word, the second only
-- exists for words that entered an active study plan.
--
-- "Oxford 3000" can be both a source and a list. They are still different
-- rows with different meanings: the source records where words came from, the
-- list is a collection the user can rename, edit and review.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    parser_type TEXT    NOT NULL,
    file_path   TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Language is part of identity: English "gift" and German "Gift" coexist.
CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    language        TEXT    NOT NULL DEFAULT 'und',
    normalized_word TEXT    NOT NULL,
    display_word    TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (language, normalized_word)
);

CREATE TABLE IF NOT EXISTS word_sources (
    word_id        INTEGER NOT NULL REFERENCES words(id)   ON DELETE CASCADE,
    source_id      INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    part_of_speech TEXT,
    cefr_level     TEXT,
    definition     TEXT,
    example        TEXT,
    metadata       TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (word_id, source_id)
);

CREATE TABLE IF NOT EXISTS lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    language    TEXT    NOT NULL DEFAULT 'und',
    description TEXT,
    kind        TEXT    NOT NULL DEFAULT 'custom'
                CHECK (kind IN ('custom', 'imported')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- position fixes review order inside a list, independent of word ids, so a
-- word added to a second list later is reviewed at the end of that list.
CREATE TABLE IF NOT EXISTS list_words (
    list_id  INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    word_id  INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    added_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (list_id, word_id)
);

CREATE TABLE IF NOT EXISTS user_word_state (
    word_id     INTEGER PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'not_reviewed'
                CHECK (status IN ('not_reviewed', 'known', 'unknown')),
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_word_state_status
    ON user_word_state(status, word_id);

CREATE INDEX IF NOT EXISTS idx_word_sources_source
    ON word_sources(source_id);

CREATE INDEX IF NOT EXISTS idx_word_sources_word
    ON word_sources(word_id);

CREATE INDEX IF NOT EXISTS idx_words_normalized
    ON words(normalized_word);

-- The review loop asks "next not-reviewed word in this list" on every answer.
CREATE INDEX IF NOT EXISTS idx_list_words_position
    ON list_words(list_id, position);

CREATE INDEX IF NOT EXISTS idx_list_words_word
    ON list_words(word_id);
