-- LexiTrack schema version 6: teaching content split by language.
--
-- Run by both a new database (after content.sql) and the version 5 -> 6
-- migration, so the two paths produce the same tables.
--
-- A word belongs to one target language (words.language). What is true of
-- the word in that language stays in word_content and word_contexts, shared
-- by every learner. What explains it to a learner is kept per learner
-- language, so English -> Turkish and English -> German learners share the
-- word, its card and its history, and differ only in the explanations.

-- ------------------------------------------------------------------ 0.5 --
-- A word explained in one learner language. One row per (word, language).
CREATE TABLE IF NOT EXISTS word_localizations (
    word_id          INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    -- The learner's language: "tr", "de", "es", ...
    learner_language TEXT    NOT NULL,
    core_meaning     TEXT,
    nuance           TEXT,
    usage_note       TEXT,
    encoding_type    TEXT CHECK (encoding_type IN
                     ('IMAGE', 'SCENE', 'ACTION', 'CONTRAST', 'RELATION', 'SOUND', 'NONE')),
    encoding_cue     TEXT,
    notes            TEXT,
    source           TEXT,
    content_version  INTEGER NOT NULL DEFAULT 1,
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (word_id, learner_language)
);

-- A context translated into one learner language.
CREATE TABLE IF NOT EXISTS context_translations (
    context_id       INTEGER NOT NULL REFERENCES word_contexts(id) ON DELETE CASCADE,
    learner_language TEXT    NOT NULL,
    text             TEXT    NOT NULL,
    source           TEXT,
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (context_id, learner_language)
);

-- Schema 5 kept one learner language in the shared rows: its meanings,
-- nuances, mnemonics and translations were written for Turkish-speaking
-- learners. They move to that language's localization, nothing dropped.
INSERT OR IGNORE INTO word_localizations
    (word_id, learner_language, core_meaning, nuance, encoding_type, encoding_cue,
     source, updated_at)
SELECT word_id, 'tr', core_meaning_tr, nuance, encoding_type, encoding_cue,
       source, updated_at
FROM word_content
WHERE core_meaning_tr IS NOT NULL OR nuance IS NOT NULL
   OR encoding_type IS NOT NULL OR encoding_cue IS NOT NULL;

INSERT OR IGNORE INTO context_translations (context_id, learner_language, text, source)
SELECT id, 'tr', translation_tr, source
FROM word_contexts
WHERE translation_tr IS NOT NULL AND translation_tr <> '';

ALTER TABLE word_content DROP COLUMN core_meaning_tr;
ALTER TABLE word_content DROP COLUMN nuance;
ALTER TABLE word_content DROP COLUMN encoding_type;
ALTER TABLE word_content DROP COLUMN encoding_cue;
ALTER TABLE word_contexts DROP COLUMN translation_tr;
