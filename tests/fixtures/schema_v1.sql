-- Frozen copy of LexiTrack schema version 1, used by the migration tests.
-- Never edit: it must stay identical to what version 0.1.0 created.

-- LexiTrack schema.
--
-- Three concerns, three tables:
--   sources           where vocabulary came from
--   words             the vocabulary identity (one row per normalized word)
--   word_sources      per-source metadata about a word (many-to-many)
--   user_word_state   what the user knows, independent of any source
--
-- The word_sources junction is what makes "ability in Oxford 3000" and
-- "ability in Oxford 5000" a single item the user is asked about once, while
-- still recording that it appears in both lists.

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

CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_word TEXT    NOT NULL UNIQUE,
    display_word    TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
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

CREATE TABLE IF NOT EXISTS user_word_state (
    word_id     INTEGER PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'not_reviewed'
                CHECK (status IN ('not_reviewed', 'known', 'unknown')),
    reviewed_at TEXT
);

-- The review loop asks "give me the next not_reviewed word" on every single
-- interaction, so that lookup gets its own index.
CREATE INDEX IF NOT EXISTS idx_user_word_state_status
    ON user_word_state(status, word_id);

CREATE INDEX IF NOT EXISTS idx_word_sources_source
    ON word_sources(source_id);

CREATE INDEX IF NOT EXISTS idx_word_sources_word
    ON word_sources(word_id);
