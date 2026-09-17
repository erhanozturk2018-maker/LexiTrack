-- LexiTrack learning engine tables, schema version 3.
--
-- Kept in its own file because two paths must produce exactly the same
-- tables: a fresh database (schema.sql + this file) and an upgrade from
-- version 2 (migrations.py runs this file). One source, no drift.

-- ------------------------------------------------------------------ 0.3 --
-- The learning engine. Everything below is additive: nothing above changes.

CREATE TABLE IF NOT EXISTS study_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    language    TEXT    NOT NULL DEFAULT 'und',
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- A plan is the union of the lists it selects; position orders the new-word
-- pool when two words share a CEFR level.
CREATE TABLE IF NOT EXISTS study_plan_lists (
    plan_id  INTEGER NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    list_id  INTEGER NOT NULL REFERENCES lists(id)       ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (plan_id, list_id)
);

-- One card per word, never per plan: the same word in two selected lists must
-- not be reviewed twice. origin_plan_id is provenance only.
CREATE TABLE IF NOT EXISTS srs_cards (
    word_id            INTEGER PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    origin_plan_id     INTEGER REFERENCES study_plans(id) ON DELETE SET NULL,
    state              TEXT    NOT NULL DEFAULT 'introduced'
                       CHECK (state IN ('introduced', 'learning', 'review',
                                        'relearning', 'archived')),
    introduced_at      TEXT    NOT NULL,
    introduced_on      TEXT    NOT NULL,
    due_at             TEXT    NOT NULL,
    first_review_at    TEXT,
    last_review_at     TEXT,
    review_count       INTEGER NOT NULL DEFAULT 0,
    lapse_count        INTEGER NOT NULL DEFAULT 0,
    consecutive_lapses INTEGER NOT NULL DEFAULT 0,
    needs_relearning   INTEGER NOT NULL DEFAULT 0 CHECK (needs_relearning IN (0, 1)),
    stability          REAL,
    difficulty         REAL,
    fsrs_state         TEXT,
    scheduler_version  TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Append-only history. The database is the source of truth; the CSV files in
-- data/logs/reviews are generated from this table.
CREATE TABLE IF NOT EXISTS review_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id           INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    session_id        TEXT    REFERENCES review_sessions(id) ON DELETE SET NULL,
    channel           TEXT    NOT NULL DEFAULT 'desktop'
                      CHECK (channel IN ('desktop', 'telegram')),
    reviewed_at       TEXT    NOT NULL,
    reviewed_on       TEXT    NOT NULL,
    rating            INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
    state_before      TEXT,
    state_after       TEXT,
    due_before        TEXT,
    due_after         TEXT,
    elapsed_days      REAL,
    scheduled_days    REAL,
    stability_after   REAL,
    difficulty_after  REAL,
    scheduler_version TEXT,
    params_hash       TEXT
);

CREATE TABLE IF NOT EXISTS review_sessions (
    id             TEXT PRIMARY KEY,
    channel        TEXT NOT NULL DEFAULT 'desktop'
                   CHECK (channel IN ('desktop', 'telegram')),
    plan_id        INTEGER REFERENCES study_plans(id) ON DELETE SET NULL,
    started_at     TEXT NOT NULL,
    started_on     TEXT NOT NULL,
    finished_at    TEXT,
    chat_id        TEXT,
    message_id     TEXT,
    current_word_id INTEGER REFERENCES words(id) ON DELETE SET NULL,
    planned_count  INTEGER NOT NULL DEFAULT 0,
    done_count     INTEGER NOT NULL DEFAULT 0
);

-- Idempotency for Telegram callbacks. Pruned regularly; the permanent record
-- of what happened is review_logs.
CREATE TABLE IF NOT EXISTS telegram_updates (
    update_key TEXT PRIMARY KEY,
    handled_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Settings the UI and the Telegram thread must both see. UI preferences
-- (theme, window state, last opened list) stay in QSettings.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- What the application knows about its own last run: used to detect downtime
-- and to avoid replaying missed daily notifications.
CREATE TABLE IF NOT EXISTS runtime_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The daily queue asks for "introduced before today and due now", ordered.
CREATE INDEX IF NOT EXISTS idx_srs_cards_due
    ON srs_cards(due_at, state);

CREATE INDEX IF NOT EXISTS idx_srs_cards_introduced_on
    ON srs_cards(introduced_on);

CREATE INDEX IF NOT EXISTS idx_srs_cards_relearning
    ON srs_cards(needs_relearning, due_at);

CREATE INDEX IF NOT EXISTS idx_review_logs_word
    ON review_logs(word_id, reviewed_at);

CREATE INDEX IF NOT EXISTS idx_review_logs_day
    ON review_logs(reviewed_on);

CREATE INDEX IF NOT EXISTS idx_study_plan_lists_list
    ON study_plan_lists(list_id);
