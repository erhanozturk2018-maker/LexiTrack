# Architecture

This describes LexiTrack **as it is actually built**, at version 0.4. Where the
implementation departs from a specification it was built from, the departure
is named and explained. The reasoning behind each choice is in
[DECISIONS.md](DECISIONS.md); the learning engine's design, with the numbers
behind its defaults, is in [LEARNING_ENGINE.md](LEARNING_ENGINE.md).

---

## 1. The shape of the thing

```text
            PDF            JSON           typed by hand
             │              │                  │
             ▼              ▼                  │
        ┌─────────────────────────┐            │
        │ open_document()         │            │
        │  Document / JsonDocument│            │
        └───────────┬─────────────┘            │
                    ▼                          │
        ┌─────────────────────────┐            │
        │ ParserRegistry          │            │
        │  JsonParser             │            │
        │  OxfordParser           │            │
        │  GenericTextParser      │            │
        └───────────┬─────────────┘            │
                    ▼                          ▼
               WordEntry  ◄─────────────── WordEntry
                    │
          normalize + deduplicate  (language, word)
                    │
                    ▼
        ┌─────────────────────────┐      ┌──────────────────────────┐
        │ VocabularyService       │      │ LearningService          │  used by the
        │  ImportService          │      │  SrsScheduler (FSRS)     │  window and by
        │  ReviewSession          │      │  DayClock                │  the Telegram
        │  ExportService          │      │  WorkloadSimulator       │  thread alike
        └───────────┬─────────────┘      └────────────┬─────────────┘
                    └──────────────┬──────────────────┘
                                   ▼
        ┌──────────────────────────────────────────────┐
        │ Repositories            the only SQL in the codebase
        └──────────────────────────┬───────────────────┘
                                   ▼
                  SQLite  (one connection, one reentrant write lock)
   ┌──────────┬──────────┬───────────────┬──────────────┬─────────────────────┐
 words      lists      user_word_state  study_plans    srs_cards, review_logs,
 + sources  + list_    Known / Unknown  + study_plan_  review_sessions,
 (provenance) words    / Not Reviewed     lists        telegram_updates,
            (membership)                (scope)        app_settings, runtime_state
```

Dependencies point one way. `ui` imports `services` (and, for the bot's
lifetime, `telegram`); `telegram` imports `services`; `services` import
`repositories`, `parsers`, `normalization` and `exporters`; `repositories`
import `database` and `models`. Nothing imports upward, and neither the UI nor
the bot constructs a repository or issues SQL — with one reading exception:
the bot counts a session's ratings for its summary.

---

## 2. Module responsibilities

| Package | Responsibility |
| --- | --- |
| `core` | Paths, the day clock, logging, autostart, the desktop shortcut, the user-facing exception hierarchy |
| `models` | `WordEntry`, `Source`, `VocabularyList`, `ReviewStatus`, `Progress`, language codes; `StudyPlan`, `SrsCard`, `Rating`, `CardState`, `ReviewLogEntry`; `LearningSettings` |
| `normalization` | Word identity and runtime deduplication |
| `parsers` | Opening files, the parser protocol, JSON / Oxford / generic parsers, registry |
| `database` | Connection, `schema.sql`, `learning.sql` and `progress.sql`, migrations, nested transactions under one lock |
| `repositories` | All SQL: words, sources, lists, review state; plans, cards and review logs, sessions and Telegram keys, settings and runtime state |
| `services` | Imports, free review sessions, exports, the `VocabularyService` facade; the learning engine (`LearningService`, `SrsScheduler`, `WorkloadSimulator`); the record (`ProgressService`) and fitting (`Personaliser`); backups and maintenance |
| `telegram` | The bot: configuration from `.env`, messages as data, when to speak, `BotCore`, the polling runtime |
| `exporters` | PDF, CSV and JSON writers |
| `ui` | Pages (Study, Progress, Home, Review, Unknown Words), dialogs, a word's history, the in-app help, shared components, the tray, single instance, the Telegram controller, theme system |

---

## 3. Five concepts, kept apart

Most of version 0.2 is about not confusing four things that version 0.1 did
not need to distinguish; version 0.3 adds a fifth.

| Concept | Question it answers | Stored in |
| --- | --- | --- |
| **Word** | What is the vocabulary item? | `words` — one row per `(language, normalized_word)` |
| **Source** | Where did it come from? | `sources`, `word_sources` |
| **List** | What is the user studying it as part of? | `lists`, `list_words` |
| **Learning status** | Does the user know it? | `user_word_state` |
| **Schedule** | When should it be asked again? | `srs_cards` — one card per word, never per list or plan |

**Navigation history** (where the user is in a flashcard session) is not
stored at all; see §8. A **study plan** is a scope — which lists the new words
and reviews come from — not an owner: deleting one leaves words, statuses and
cards untouched.

### Why "Oxford 3000" used to be ambiguous

In 0.1 the flashcard showed "Oxford 3000" in the corner of the card. That text
was provenance: `OxfordParser.source_name()` runs `The\s+Oxford\s+(3000|5000)`
over the extracted text of page 1 (not the filename, not PDF metadata — the
published PDFs have no title), the import service stores it as `sources.name`,
and the word query concatenates every source name for the word. A word in both
Oxford lists read "Oxford 3000 · Oxford 5000" whatever the user was studying.

In 0.2 the Review page shows the **list** as its largest text, and the card
shows provenance as a quiet "Source: …" line. A JSON list says
"Source: german_a1.json" unless the file itself names a source.

---

## 4. Data model (schema version 4)

```sql
sources         (id, key UNIQUE, name, parser_type, file_path, created_at)
words           (id, language, normalized_word, display_word, created_at,
                 UNIQUE (language, normalized_word))
word_sources    (word_id, source_id, part_of_speech, cefr_level, definition,
                 example, metadata, created_at, PRIMARY KEY (word_id, source_id))
lists           (id, name COLLATE NOCASE UNIQUE, language, description,
                 kind CHECK IN ('custom','imported'), created_at, updated_at)
list_words      (list_id, word_id, position, added_at,
                 PRIMARY KEY (list_id, word_id))
user_word_state (word_id PRIMARY KEY, status, reviewed_at)
schema_version  (version)
```

Indexes: `user_word_state(status, word_id)`, `word_sources(source_id)`,
`word_sources(word_id)`, `words(normalized_word)`,
`list_words(list_id, position)`, `list_words(word_id)`. All foreign keys cascade
on delete.

Version 3 adds the learning engine's tables, created from `learning.sql` both
for a new database and by the 2 → 3 migration, so the two paths cannot drift:

```sql
study_plans      (id, name COLLATE NOCASE UNIQUE, language, description,
                  is_active, created_at, updated_at)
study_plan_lists (plan_id, list_id, position, PRIMARY KEY (plan_id, list_id))
srs_cards        (word_id PRIMARY KEY, origin_plan_id, state, introduced_at,
                  introduced_on, due_at, first_review_at, last_review_at,
                  review_count, lapse_count, consecutive_lapses,
                  needs_relearning, stability, difficulty, fsrs_state,
                  scheduler_version, created_at, updated_at)
review_logs      (id, word_id, session_id, channel, reviewed_at, reviewed_on,
                  rating 1..4, state_before, state_after, due_before, due_after,
                  elapsed_days, scheduled_days, stability_after,
                  difficulty_after, scheduler_version, params_hash)
review_sessions  (id, channel, plan_id, started_at, started_on, finished_at,
                  chat_id, message_id, current_word_id, planned_count, done_count)
telegram_updates (update_key PRIMARY KEY, handled_at)
app_settings     (key PRIMARY KEY, value, updated_at)
runtime_state    (key PRIMARY KEY, value, updated_at)
```

Version 4 adds the record of how each word got where it is, from
`progress.sql`, again run by both a new database and the 3 → 4 migration:

```sql
word_status_events (id, word_id, at, from_status, to_status,
                    cause CHECK IN ('mastery','manual','sorting','undo'),
                    plan_id, reconstructed)
review_logs        + undone_at         -- an answer taken back, kept and marked
study_plans        + all_lists         -- a plan over every list, present and future
```

`params_hash` on `review_logs`, present since version 3, is now filled: a
short fingerprint of the FSRS parameters and retention that scheduled the
answer.

Timestamps are stored in UTC as `YYYY-MM-DDTHH:MM:SS` with no offset, one
format everywhere, because `due_at` is compared as text in SQL. Days are
stored as local dates (`introduced_on`, `reviewed_on`). `state` is one of
`introduced`, `learning`, `review`, `relearning`, `archived`.

### Teaching content and languages (schemas 5 and 6)

A word belongs to one **target language**, the language being learned:
`words.language`, part of its identity (English "gift" and German "Gift" are
two words). Teaching content is split by who it is true for:

```text
words (target language)          one record, one card, one review history
├── word_content                 shared by every learner: pattern, collocations,
│                                register, related words, depth
├── word_contexts                shared: examples in the target language
│   └── context_translations     per (context, learner language)
└── word_localizations           per (word, learner language): core meaning,
                                 nuance, usage note, mnemonic, notes, version
```

A **learner language** is data — a code in a row — never a column or a
table: `tr`, `de`, `es` coexist on the same word without duplicating it
(tests/test_content.py, tests/test_localization_migration.py). The learner
chooses theirs in Settings (`learner_language`); the review asks in it when
the word has content in it and falls back to the target-language definition
otherwise. Any pair works on this schema: English → Turkish, English →
German, German → English, Spanish → English.

Schema 5 kept one learner language (Turkish) in the shared rows; the 5 → 6
upgrade moved it into `tr` localizations and dropped those columns.

### Invariants the repositories maintain

1. **Vocabulary is the union of the lists.** A word removed from its last list,
   or whose last list is deleted, is deleted with its status. A word still in
   another list is never touched. The UI states the count before either
   happens.
2. **A list with a language only holds words of that language.** A list with
   language `und` (unspecified) holds anything.
3. **Existing metadata is never overwritten by a later import**; missing
   fields are filled in. Reading a word flattens `word_sources`, taking the
   first source that supplied each field.
4. **Importing never changes a learning status.**
5. **A card introduced today is never due today.** `due_cards` requires
   `introduced_on < today` in SQL, whatever `due_at` says.
6. **Introduction happens once.** Introducing a word that already has a card
   changes nothing, so a repeated confirmation cannot reset a schedule.
7. **Reset All Progress clears statuses and the schedule together** — cards,
   sessions, review logs and the status history — in one transaction, so they
   never disagree.
8. **No status changes without a record.** `StateRepository` writes the
   `word_status_events` row in the same transaction as the change, and only
   when the status really changes; callers state the cause.
9. **An answer is never deleted** except by Reset. Undo marks it `undone_at`;
   every count, the calibration and fitting leave such rows out, the history
   and the All answers table show them.

### Learning status is word-level

`user_word_state` is keyed by word, not by list. English "ability" known in
Oxford 3000 is known in IELTS Vocabulary and My Difficult Words too. See
DECISIONS §24 for why this, rather than per-list status.

---

## 5. Migration

`database/migrations.py` owns schema versions. `Database.connect()` reads the
version and:

- **empty database** → runs `schema.sql`, records version 2;
- **older version** → backs the file up with SQLite's online backup API
  (`vocabulary.v1-backup-<timestamp>.db`, next to the original), then runs each
  step in order;
- **newer version** → refuses with a clear message rather than guessing.

`schema.sql` is never run against an existing database: `CREATE TABLE IF NOT
EXISTS` would skip old tables and then fail on indexes over columns they lack.

Each step runs in one `BEGIN IMMEDIATE` transaction with foreign keys off (the
table-rebuild procedure requires it), checks `PRAGMA foreign_key_check` and row
counts, and rolls back on any problem.

**Version 1 → 2:**

1. Rebuild `words` with `language` and `UNIQUE(language, normalized_word)`,
   copying every `id` explicitly so all references stay valid.
2. Language: `en` for words linked to a source with `parser_type = 'oxford'`,
   `und` for everything else. Version 1 never recorded language; only the
   Oxford data justifies a specific one.
3. Create `lists` and `list_words`. Each source becomes a list of the same name
   (made unique if two sources shared a name), with its words in id order.
4. Verify word and review-state counts are unchanged.

Verified against a copy of a real version 1 database: all 4,953 ids,
spellings, statuses and review timestamps identical afterwards. A test builds a
version 1 file from a frozen copy of the old schema
(`tests/fixtures/schema_v1.sql`) and asserts the migrated structure matches a
freshly created one.

**Version 2 → 3:**

1. Back up to `vocabulary.v2-backup-<timestamp>.db`.
2. Run `learning.sql` (the same file a new database uses), statement by
   statement: `executescript` would commit the migration's open transaction.
3. Seed `app_settings` with the defaults, `INSERT OR IGNORE`, so a value the
   user already changed survives a re-run.
4. Create one active study plan from the largest list, named
   "<list> Study Plan". Nothing is scheduled: the first card appears only when
   the user confirms new words.

Verified on a copy of the real 6,825-word database, then on the database
itself when the user first started 0.3.

**Version 3 → 4:**

1. Back up to `vocabulary.v3-backup-<timestamp>.db`.
2. Run `progress.sql`: the events table, `review_logs.undone_at`,
   `study_plans.all_lists`.
3. Reconstruct one `mastery` event, marked `reconstructed`, for every Known
   word whose review log shows its stability crossing the mastery threshold,
   dated by that review. Words marked Known by hand before version 4 left no
   trace and get no event: an invented date would be worse than a gap.

Verified on a copy of the real database: 6,825 words, 185 cards and 174
answers intact, integrity check clean, no event reconstructed because no word
had yet reached 21 days.

---

## 6. Parsers

### Documents

`open_document(path)` returns a `Document` (PDF, via PyMuPDF) or a
`JsonDocument`, choosing by extension and, for other extensions, by sniffing
the first bytes. Both validate before any parser sees them: missing file,
corrupt or password-protected PDF, no pages, image-only scan; unreadable,
empty or syntactically invalid JSON.

### The protocol

```python
class DocumentParser(ABC):
    key, name, description, priority
    document_types: tuple[type, ...]   # which documents it may be offered
    language: str | None               # fixed language, if the format has one

    def can_parse(self, document) -> bool
    def parse(self, document, progress=None) -> list[WordEntry]
    def source_key(self, document) -> str      # stable provenance identity
    def source_name(self, document) -> str     # provenance label
    def list_metadata(self, document) -> ListMetadata   # suggested name/language/description
```

`ParserRegistry.select(document, preferred_key)` offers the document to parsers
in priority order, skipping those whose `document_types` do not match.

| Parser | Reads | Priority | Language |
| --- | --- | --- | --- |
| `JsonParser` | `JsonDocument` | 200 | from the file, per word or per list |
| `OxfordParser` | PDF | 100 | `en` |
| `GenericTextParser` | PDF | −100 | none stated |

`OxfordParser` is unchanged in behaviour from 0.1 and still accounts for every
line of both published PDFs. The JSON format is specified in
[formats/json-import-export.md](formats/json-import-export.md).

---

## 7. Import workflow

```text
prepare(path)                     no writes
  open_document → registry → parser.list_metadata + parser.parse
  → deduplicate by (language, word) → ImportPreview

check(preview, language)          new vs existing counts for the preview

resolve_language(preview, target, chosen)
  word's own → file's → chosen in preview → target lists' → und
  raises LanguageMismatchError on a clash

commit(preview, target, language) one transaction
  create new list? → upsert source → upsert words → add to each target list
```

`ImportTarget` holds existing list ids and/or one `NewList`. With no target,
`default_target` reuses a list with the suggested name when its language is
compatible (which is what makes re-importing land in the same list) and
otherwise creates one with a free name.

A test proves atomicity by failing *after* the list, source and words are
written: nothing survives.

The UI runs `prepare` and `commit` on worker threads. Each file is committed in
its own transaction, so one bad file never half-imports another.

---

## 8. Review sessions: navigation versus status

`services/review_session.py`:

```python
class ReviewSession:
    list_id
    _history: list[int]   # word ids answered this session, oldest first
    _cursor: int          # index into history; len(history) means "live"
    last_answer: bool | None
```

| Action | Status change | Navigation |
| --- | --- | --- |
| Answer (K/U) on the live word | set Known/Unknown | append to history, stay live |
| Answer on an earlier word | set Known/Unknown (explicit) | cursor + 1 |
| ← or Backspace | **none** | cursor − 1 |
| → on an earlier word | **none** | cursor + 1 |
| Enter on the live word | repeat last answer | as answer |
| Enter on an earlier word | **none** | cursor + 1 |
| R | set Not Reviewed (explicit) | stay |

The live word is always `next_unreviewed(list_id)` from the database, so
resume needs no stored cursor. History stores word ids, not snapshots: going
back shows the word's *current* status. A word reset and answered again moves
to the end of history rather than appearing twice. A word deleted mid-session
drops out of history.

History lives in memory for the session; see DECISIONS §27.

---

## 9. Services

`VocabularyService` is the facade the UI uses:

- **Import:** `prepare_import`, `check_import`, `resolve_import_language`,
  `default_import_target`, `commit_import`, `import_document` (one call).
- **Lists:** `lists`, `get_list`, `create_list`, `update_list`, `delete_list`,
  `exclusive_word_count`, `list_words`, `add_words_to_list`,
  `remove_words_from_list`, `add_word` (manual entry).
- **Review:** `start_review`, `get_next_word`, `mark_known`, `mark_unknown`,
  `set_status` (bulk), `undo` (reset one word).
- **Queries:** `get_word`, `get_words`, `get_progress`, `list_unknown_words`,
  `list_known_words`, `unknown_count`, `list_sources`.
- **Export:** `export_content_for_list`, `export_content_for_unknown`,
  `export_content_for_selection`, `export`.

Methods with an optional `list_id` work on one list or on the whole
vocabulary; the whole-vocabulary forms are the 0.1 API and still behave the
same.

Manually added words go through the same `WordEntry`, normalization and
repositories as imported ones, with provenance "Added manually".

`ExportService` resolves a *scope* (list, unknown in a list, all unknown,
selection) into `ExportContent`, which any exporter can write. The words are
written in the order given, so ordering (A → Z, CEFR, list order) is applied by
the caller. `ExportContent.group_by_level` asks the PDF exporter for a heading
wherever the CEFR level changes.

### The study flow

`StudyFlow` (`services/study_flow.py`) is a review session as a state machine
with no widgets: the queue, the card on screen, whether its meaning is
revealed, how many were answered, and what Undo would take back. The Today
page shows it and passes on key presses; it holds no session state of its own
(a test reads the page's source to keep it that way). Every answer still goes
through `LearningService.answer` and every Undo through `undo_last_answer`:
the flow decides what to show next, never what an answer means.

After every step the flow writes its state to `review_sessions.flow_state` as
versioned JSON (`version`, `route`, `kind`, the queue as word ids, `index`,
`revealed`, `answered`, `last_answer`), and clears it when the session ends.
`StudyFlow.restore` rebuilds an open session from it; a state written by a
newer version is not guessed at. Route V1 is the desktop's review exactly as
it was; route V2 is built on this object.

### Content

`ContentService` (`services/content_service.py`) reads a word's teaching
content for a learner language and moves it in and out in batches:
`build_batch` / `export_batch` write the words that need content, asking for
a block in each learner language given, with an instructions prompt,
`preview_import` validates a filled file and reports fills, conflicts, new and
duplicate contexts and rejected entries, and `apply_import` writes it in one
transaction, replacing a conflicting field only when told to. The format is
in `docs/formats/content-enrichment.md`.

### Notes

`StoredWord.note` is a short note about a word — a sense (*bank*: money), a
UK/US variant, an opposite. It is not a column: it lives in
`word_sources.metadata` as `note` (from a JSON `note` field) or `sense` (from
an Oxford-format entry such as `bank (money) n.`), and is flattened like every
other detail — the first source that supplies one wins. No schema change was
needed. The table search, the details panel, the flashcard, the PDF definition
column, CSV (a Note column) and JSON (`note`) all carry it.

---

## 10. The learning engine

`services/learning_service.py` is the engine's single surface. The Study page,
the Telegram bot, the daily brief and the simulator all ask it, and nothing
else, what today looks like — two clients that computed "today" separately
would drift apart. It holds no Qt and no network code and is given a clock.

```text
daily_plan()        → DailyPlan: offered new words, introduced today, due count,
                      reviews done, pool left, the 7-day forecast, intake note
introduce(ids?)     → cards for the offered words, first due at the next day start
review_queue()      → due cards in the plan, struggling first, capped at the limit
answer(word, rating, session, channel, update_key)
                    → schedule, save the card, append review_logs, maybe Known
start/finish_session, struggling_words, forecast, preview_intervals,
mark_known (archives the card), resume, rating and daily statistics
```

**The day.** `core/clock.py` answers "which learning day is it?" in one place.
Instants are UTC; days are local, Europe/Istanbul by default, resolved without
a system time-zone database (Windows ships none, and Türkiye has had a fixed
UTC+3 without daylight saving since 2016). A configurable day start (00:00 by
default) decides which day 01:30 belongs to. `FrozenClock` has the same
interface and is what makes a year of study testable in seconds. The clock is
configured in place when settings change, because every holder shares it.

**Scheduling.** `services/srs_scheduler.py` wraps the `fsrs` library and adds
what the library cannot decide for a once-a-day app:

- learning and relearning steps are one day, not minutes;
- due times are snapped to the start of a learning day, and never to today;
- fuzzing is off, so the forecast is reproducible;
- the library's own state travels as JSON in `srs_cards.fsrs_state` and is
  read nowhere else.

The introduction is not a review: no rating is invented, and a word's first
FSRS rating is the first real answer.

**There is one queue, not two.** "Learning", "review" and "relearning" are
labels on a card, not separate lists. Everything due today — a word introduced
yesterday, a word failed last night, a word last seen a month ago — is in the
same queue and is asked the same way. A card's state changes which interval
the next answer earns, not which queue it is in.

**The life of a word.** The days below are what the real scheduler produces
with the default settings, counted from the day the word was introduced.

| Day | What happens | State | Next |
| --- | --- | --- | --- |
| 0 | Offered in the day's 25 and confirmed. No rating, no card history yet | `introduced` | Tomorrow |
| 1 | First real answer. Good | `review` | Day 3 |
| 3 | Good | `review` | Day 13 |
| 13 | Good. Stability passes 21 days | `review` | Marked **Known** |

So a word introduced on day 0 *is* reviewed from day 1 onwards; there is no
separate learning phase it has to finish first. Answering Easy each time
reaches Known in two answers (day 1 and day 8), Good in three, and Hard
alone never does: at two-day steps its stability is still under 9 days after
eight answers.

Failing resets much of that. On the same word, Again on day 13 drops it to
`relearning` with a stability of 1.5 days; Good on days 14, 18 and 28 rebuild
it, and Known arrives on day 28 instead of day 13.

**Known is a prediction, not a confirmation.** The word is marked Known on the
answer whose stability crosses the threshold, and the long interval that same
answer scheduled — 74 days on the Easy path — is never used while *Keep
reviewing known words* is off, because Known words leave the queue. Two
settings decide how much evidence Known needs:

- *Count as known after* (21 days) is the threshold itself. Raising it to 45
  or 60 asks for another answer or two before a word is retired.
- *Keep reviewing words learned here* puts that last long interval back: the word
  stays in the schedule and is asked again months later, which is the only way
  the prediction is ever tested.

**What each answer does.** Only Again counts as a lapse.

| Answer | Card state | Next due | Counters |
| --- | --- | --- | --- |
| Again | `relearning` from `review`; `learning` stays `learning` | The next day | `lapse_count` and `consecutive_lapses` both +1 |
| Hard | Unchanged: a `learning` or `relearning` card repeats its step | The next day while on a step, a short interval from `review` | `consecutive_lapses` reset to 0 |
| Good | `review` once the step is passed | The interval FSRS gives | `consecutive_lapses` reset to 0 |
| Easy | `review` | The longest of the four | `consecutive_lapses` reset to 0 |

Put as one rule: **only Good or Easy move a card forward.** Confirming the
day's new words is not an answer, so the first rating is still owed the next
day.

| Card now | Answer | Becomes |
| --- | --- | --- |
| `introduced` (never answered) | Good, Easy | `review` |
| `introduced` | Hard, Again | `learning`, asked again tomorrow |
| `learning` | Good, Easy | `review` |
| `learning` | Hard, Again | `learning`, asked again tomorrow |
| `review` | Again | `relearning`, asked again tomorrow |
| `relearning` | Good, Easy | `review` |
| `relearning` | Hard | `relearning`, asked again tomorrow |

Only Good or Easy take a card out of `learning` or `relearning`; Hard repeats
the one-day step. **The state is not progress.** Known is decided by stability
alone, so a card back in `review` after a lapse is no closer to Known than it
was the day before — the number, not the label, is what mastery reads.

**What a lapse actually costs.** Measured on one word with the defaults:

| Day | Answer | Stability | Also |
| --- | --- | --- | --- |
| 3 | Good | 11.0 days | |
| 14 | Again | **1.5 days**, `relearning` | First lapse |
| 15 | Good | 3.6 days, `review` again | Not Known: 3.6 < 21 |
| 19 | Good | 9.7 days | |
| 29 | Good | 22.9 days | **Known**, 15 days later than without the lapse |
| 52 | Again | 22.9 → **2.0 days** | Second lapse: flagged as struggling |
| 56 | Again | 3.1 → **0.7 days** | Third: now asked every day |

Each lapse also raises the card's difficulty, so recovery is slower every
time: the same word rebuilt 11 days of stability in two answers before its
first lapse and needed three after it. A lapsed word is a review, not a new
word — it returns tomorrow in the day's due queue and never costs one of the
day's 25 introductions. Stability restarts lower than it was before the lapse, so a
word that was due in a week comes back in a day or two and has to earn the
long interval again.

**A word never returns to the new-word queue.** Intake offers only words with
no card at all, so a lapse — however many times it happens — keeps the word in
the review queue instead of costing one of the day's 25 new words. The two
ways back are deliberate and both are the user's: *Reset All Progress*, which
clears every card, and archiving through a manual Known, which can be undone
with `resume` rather than starting the word over.

A word is flagged as **struggling**
after a run of Agains (4 by default), a long failure history that has just
repeated, or a low stability after at least four reviews; it stays flagged
until it has no current failure streak *and* its stability has recovered.
**Mastery** — stability of 21 days by default — marks the word Known by
itself, once.

**Intake.** New words come from the plan's lists — or every list, for a plan
over *All my lists* — deduplicated, in CEFR order then list order, from words
marked Unknown (optionally also never-answered ones). `PlanRepository` builds
every query from one scope expression, so the saved plan, an unsaved selection
in the Study Plan window (`selection_outlook`) and the day's intake count words
by the same rule. There is no pointer to keep in step: "not yet introduced" is simply the
absence of a card. When today's due reviews already reach the review limit
(250 by default) intake pauses, and the plan says so in words.

**Undo.** `LearningService.answer` keeps the card as it was before the
answer, the log id and the status before. `undo_last_answer(session)` restores
the card, marks the log row undone, reverts a Known the answer caused (as an
`undo` event), steps the session back and releases the Telegram idempotency
key, so the same word can be answered again. Only the last answer, only once,
only in the session and client that gave it, and not if the word has been
answered since from elsewhere.

**Reading the record.** `services/progress.py` (`ProgressService`) derives,
read-only, what the Progress page and a word's history show: the studied
words in three groups (learned here — Known by `mastery`; marked Known — by
hand or on the Review tab; in progress) plus the count known before any plan;
where the words stand by stability; running totals by day; the week for the
Telegram summary; every answer; one word's journey; and the calibration —
for each answer after a word's first, the recall FSRS predicted from the
previous stability and the elapsed days (`SrsScheduler.predicted_recall`, the
library's own curve and parameters) against whether it was remembered.

**Fitting to the user.** `services/optimizer.py` (`Personaliser`) counts
answers given on a later day than the word's previous answer — the library's
optimizer returns the defaults below 512 of them — fits on a snapshot of the
log (the fsrs optimizer, an optional extra that needs PyTorch), and scores the
current and fitted parameters by replaying the whole history through a
scheduler built with LexiTrack's own steps, as mean log loss. Nothing is used
until `apply`, which stores the parameters as the `fsrs_parameters` setting;
`revert` returns to the defaults. `SrsScheduler` builds FSRS with stored
parameters when present.

**The simulator.** `services/simulation.py` runs the real scheduler forward
over an imaginary pool with a fixed seed and one of three answer profiles, in
memory only. It is what set the 250 limit and is exposed in Developer mode.

---

## 11. Telegram

The bot is a thread inside the application, not a second process: one
database, one writer. Five modules:

| Module | Role |
| --- | --- |
| `telegram/config.py` | The token and optional chat id, from `LEXITRACK_TELEGRAM_TOKEN` / `LEXITRACK_TELEGRAM_CHAT_ID` or a `.env` in the data folder (and the clone, when running from one). Never from the database. |
| `telegram/messages.py` | Every message as plain data: Telegram HTML text and button rows. Callback data: `intro:<date>`, `start`, `ans:<session>:<word>:<rating>`, `end:<session>`, `undo:<session>`. Each card is a new message, so a spoiler starts hidden. |
| `telegram/schedule.py` | Whether the morning brief, the evening reminder or the Sunday weekly summary is owed now, from the clock, the settings and what `runtime_state` says was sent. |
| `telegram/core.py` | `BotCore`: commands, button presses and the half-minute tick, spoken through an `Outbox` protocol. No Telegram types, so it is tested with a list. |
| `telegram/runtime.py` | The only module importing `python-telegram-bot`: an asyncio loop on a daemon thread, long polling, every tap acknowledged before it is handled, a clean stop. |

Rules `BotCore` keeps:

- **Only the owner is served.** The first chat to send `/start` is remembered
  in `runtime_state` (unless `.env` names one); every other chat gets a
  refusal and nothing else.
- **Engine calls run under the database lock**, so a tap's read–answer–read is
  not interleaved with a desktop answer.
- **A button does only what it said on its day.** A confirm button carries its
  date; an answer carries its session and word, is ignored unless that word is
  the session's current card, and claims the idempotency key
  `ans:<session>:<word>` in `telegram_updates` before anything is written.
- **Notifications are decisions, not timers.** A brief is owed once a day after
  the notify hour; five days offline produce one brief; a brief sent after the
  reminder hour, or on `/start`, also counts for the day. The weekly summary
  is owed only on Sunday after the reminder hour, once, and a week with
  nothing in it stays quiet.
- **Undo reaches one answer.** Every card after the first carries Undo; it
  sends the word's card back as a new message and deletes the current one.

`ui/telegram_controller.py` starts and stops the thread from two facts — the
`telegram_enabled` setting and whether a token exists — and turns the
thread's callbacks into Qt signals delivered on the UI thread.

---

## 12. Process, threads and lifetime

- **One process.** `ui/single_instance.py` listens on a local socket named
  after the data folder; a second launch asks the first to come forward and
  exits. Two instances would be two pollers on one token and two writers on
  one file.
- **Threads.** The UI thread, the Telegram thread and the import workers share
  one SQLite connection. `Database.transaction()` holds a reentrant lock for
  the whole transaction; before 0.3 the nesting counter was shared between
  threads, so a second thread's transaction could silently join the first.
- **The tray.** `ui/tray.py`: open, today's counts, the bot switch, Start with
  Windows, Settings, Quit. With the bot on, the window's close button hides to
  the tray; Quit (menu, Ctrl+Q, tray) ends everything.
- **Start with Windows.** `core/autostart.py` writes one value under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, running
  `pythonw -m lexitrack --minimized`.
- **Entry options.** `--minimized`, `--headless` (the bot only, until Ctrl+C,
  under the same single-instance rule) and `--create-shortcut`
  (`core/shortcut.py`: a desktop `.lnk` with `app.ico`, written through
  Windows' shell object via PowerShell). The process sets an AppUserModelID so
  the taskbar shows LexiTrack rather than Python.

---

## 13. Files, backups and logs

`core/paths.py` decides where everything lives. A **source checkout** —
recognised by the `pyproject.toml` beside the package — keeps `data/` and
`logs/` in the clone. Any other install uses `%LOCALAPPDATA%\LexiTrack`.
(The earlier rule, "the folder beside the code is writable", was also true of
`site-packages`, which would have put the database there.) `LEXITRACK_DATA_DIR`
overrides both.

`services/maintenance.py` runs at start-up and hourly, and does each thing once
a day:

- a `PRAGMA quick_check`, with restore instructions if it fails;
- an online backup to `data/backups/vocabulary-<date>.db`, written under a
  temporary name and renamed, skipped if the check failed; the newest ten are
  kept, and nothing else in the folder is touched;
- pruning of Telegram idempotency keys older than 14 days;
- on request, the review history as CSV from `review_logs`.

Logging (`core/logging_config.py`) writes to the console of a development run
only. A file is written only while *Debug logging* is on: one
`logs/lexitrack-<date>.log` a day, seven kept. The HTTP libraries are held at
WARNING because the Bot API puts the token in every URL, and every line passes
through a formatter that masks anything shaped like a bot token.

---

## 14. UI

```text
MainWindow
├── Sidebar     LexiTrack · Search (Ctrl+K) · Today (count) · Progress
│               · LIBRARY: Lists · Sort words · Unknown (count)
│               · foot: Export and backup (menu) · Settings · theme icon · ⋯ menu
│               Folds to icons below 1000 px; counts become dots, names tooltips
├── StudyPage   (on screen: Today)
│   ├── no plan             the first-run setup: what to learn (all Unknown
│   │                       words, or some lists), how many a day, the phone;
│   │                       Start learning creates the plan
│   ├── the day             Today panel (two steps, a progress line, one primary
│   │                       button whose label is the next thing to do) · New
│   │                       words as CEFR-grouped chips · This week (day tiles)
│   │                       · Words you find hard · The last 30 days (stat tiles)
│   └── session             one card: progress line, word, meaning behind Space,
│                           Again / Hard / Good / Easy with keys and intervals,
│                           "Undo <answer> on <word>" in the footer (Ctrl+Z)
├── ProgressPage            four tiles · where the words are · over time · does
│                           the schedule fit you (and which parameters are in
│                           use) · Words table with filters · All answers table
├── HomePage    (on screen: Lists)
│   ├── Continue learning   current list, progress, Continue, Flashcard|List
│   ├── Overview            four totals; the Unknown tile opens Unknown Words
│   └── Your lists          ListCard grid (click: current; double-click: open;
│                           right-click: review, open, add words, import into,
│                           export, edit, delete)
├── ReviewPage  (on screen: Sort words)
│   ├── context strip       SORTING · <list ▾ switcher> · language · List Actions
│   │                       · Flashcard|List, and one line saying that this page
│   │                       sorts and Today teaches
│   ├── Flashcard mode      ReviewWidget / list complete / empty list
│   ├── List mode           VocabularyTable (details panel, floating bar) + Add Words
│   └── StatsBar            known · unknown · remaining · total for the list
└── UnknownPage             VocabularyTable (all unknown words, no status
                            column) + list filter
```

The page keys in code (`STUDY`, `HOME`, `REVIEW`…) predate the names on
screen and are kept: `QSettings`, tests and signals name them. The names, icons
and Alt shortcuts live in one table, `main_window.PAGES`.

The sidebar (`ui/components/sidebar.py`) paints its rows: icon, label and
count change colour together from the palette, the icons are 24-unit outline
SVGs coloured at paint time, so there is one icon per item rather than one per
theme and state. The window folds it by width in `resizeEvent`.

Pages keep their content in a centred column no wider than
`METRICS.page_max_width` (1100 px) through `widgets.PageColumn`, which grows
the page's side margins on a wide window. Scroll bars stay at the window edge.
Tables fit their width with `vocabulary_table.fit_columns`: natural widths when
there is room, the word taking the rest; when there is not, the word, part of
speech, status and lists shrink in proportion to floors, and only then does the
table scroll sideways.

A word's history (`ui/components/word_history.py`, `WordHistoryDialog`) opens
from the details panel — which now summarises the word's learning — from
learned and hard words on the Study page, and from both Progress tables. How
LexiTrack Works (`ui/help_dialog.py`, Shift+F1) renders `lexitrack/help/
how_lexitrack_works.md`, whose central section is the README's *How a word is
learned*, word for word; a test fails if they drift.

`MainWindow` owns only shared context: the current list and review mode
(persisted in `QSettings`), the theme and the commands. There is no menu bar:
`MainWindow.commands()` declares everything the app can do, with a description
and a shortcut, and three things are built from that one list — the Ctrl+K
`CommandPalette`, the sidebar's "⋯" menu and the `ShortcutsDialog`. Window-wide
shortcuts are `QAction`s added to the window itself. Pages re-read the service
whenever shown. Pages do not know how they are navigated to, so changing the
navigation style means changing `main_window.py` only — the move from tabs
to the sidebar did exactly that.

The app opens on Today when the plan has work waiting, otherwise on Sort words
when the current list is part-way through, and on Lists otherwise. With a tray, the
window can start hidden (`--minimized`).

### Shared components (`ui/components`)

- **`VocabularyTable`** — a `QAbstractTableModel` of `StoredWord` behind a
  `QSortFilterProxyModel` for search (word and definition), status filter and
  sorting (CEFR by level order, status by what needs attention first), and CEFR
  level chips built from the levels present. Selecting rows shows a
  **floating selection bar** — Known, Unknown, Reset, Copy to ▾, Move to ▾,
  More ▾ (Export, Remove), × — a child of the table positioned over the bottom
  of its frame. The frame grows an empty strip under the rows while the bar is
  showing, so no row is hidden under it and nothing above moves. The same
  actions are on a right-click menu. K / U / R set status, C / M open the list
  picker, Enter opens the details panel, Delete removes after confirmation.
  Displaying a row never changes status.
- **`WordPanel`** — the details panel beside the table, following the current
  row: status, word, part of speech and level, definition, note, example,
  lists, source, language, and status buttons that go through the page exactly
  as K / U / R do. Open or closed is remembered.
- **`Toast`** — a short message floating over a page, with Undo or another
  action (Open Folder after an export). It keeps clear of the selection bar.
- **`StatusDelegate` / `StatusBadge`** — status as symbol plus word
  (✓ Known, ? Unknown, – Not reviewed), never colour alone.
- **`ListCard`, `SegmentedProgress`, `StatTile`, `ModeSwitch`.**
- **`chips.py`** — the Study page's parts, each reusing an existing look: a
  wrapping `FlowLayout` and `ChipFlow` of pill labels, `DayProgress` (the list
  cards' segmented bar), `DayTile` / `WeekStrip` (small stat tiles).

`ListActions` holds the list operations Home and Review share, so both use the
same dialogs, confirmations and error handling.

`word_transfer.py` holds copying and moving words between lists, shared by
Review and Unknown Words:

- `compatible_lists` offers only lists that can hold every selected word
  (unspecified language, or the words' one language).
- `ListPicker` is a type-to-filter popup driven by arrows and Enter, used for
  C, M and Ctrl+L.
- `WordTransfer.copy` / `move` compose the existing service calls. Move adds
  to the target before removing from the source, so no word is ever orphaned
  and deleted. Results show in a `Toast` with Undo (also Ctrl+Z), which
  removes only the words the action newly added. Undoing a move returns the
  words to the end of the source list, since list positions are not
  restored.

### Keyboard model

Arrows move; letters act. In flashcards ← and → navigate the session history
and never answer. On Home the arrows move focus across the card grid (Up from
the top row returns to Continue). Ctrl+Tab cycles pages in sidebar order; Alt+T,
P, L, S and U go to Today, Progress, Lists, Sort words and Unknown; Ctrl+L
switches list.

Shortcuts are listed in one place, Keyboard Shortcuts (F1), and shown next to
each command in the Ctrl+K palette. They are not printed under the flashcard
or on buttons; tooltips still name them.

### Dialogs

`ImportDialog` (choose → read → preview per file → import → result),
`ExportDialog` (settings on the left — scope, format, order, remember; a live
preview on the right rendered from a real export of the first 40 words, the PDF
drawn with PyMuPDF; Enter saves with the defaults; the result is a toast),
`ListDialog` (create/edit), `AddWordDialog` (stays open for the next word),
`CommandPalette` and `ShortcutsDialog` (scrollable, filterable, sized to the
screen), `StudyPlanDialog` (a plan's name and lists as rows with progress bars,
and what the selection means in words and days) and `SettingsDialog` (pages of
titled groups; each row is a setting's name and one-line explanation beside
its control; everything but the theme and Start with Windows is written to
`app_settings`, where the
bot reads it too). Validation errors are shown inline and keep the user's
input. Button labels are in sentence case without an ellipsis; menu items keep
the ellipsis for commands that open a window.

### Theme system

Two `Palette`s designed independently; `build_stylesheet` (base rules) plus
`component_rules` (0.2 components) and `learning_rules` (the 0.3 screens)
generate one stylesheet per theme.
`current_palette()` serves painted components (status pills, progress bars)
that a stylesheet cannot reach. Combo-box chevrons and checkbox ticks are
themed SVGs in `ui/theme/icons`, since styling those sub-controls removes
Fusion's own glyphs.

Rendering lessons recorded because they recur:

- Checked tabs and mode buttons keep the same font weight as unchecked ones;
  a bolder label is wider than the space measured for it and gets clipped.
- Plain `QWidget` containers inside a panel paint the window colour unless
  made transparent (`#PanelBody`).
- PySide returns a `StrEnum` stored in item data as a plain `str`; roles carry
  `status.value` and are converted back.
- `isVisible()` is false whenever the window is hidden; logic asks
  `isHidden()` about deliberate visibility.
- Qt ignores a pseudo-state on an ancestor in a descendant selector:
  `QPushButton:hover #Label` applies all the time. The answer buttons keep
  their fill on hover and change only the border instead.
- A centred, word-wrapping `QLabel` stops being asked `heightForWidth` and
  clips its last line; `WrappedLabel` pins the width and measures.

---

## 15. Extension points

| To add… | Do this |
| --- | --- |
| A file format | Subclass `DocumentParser`, set `document_types`, register in `default_parsers()` (add a document class if it is not PDF or JSON) |
| An export format | Add a writer in `exporters/`, a member to `ExportFormat`, a branch in `ExportService.write` |
| A schema change | Update `schema.sql` or `learning.sql`, add a step to `migrations._STEPS`, bump `SCHEMA_VERSION`; the parity test catches drift |
| A learning setting | Add it to `DEFAULT_SETTINGS`, `Setting` and `LearningSettings` (with clamping); new keys are seeded on the next start |
| A bot command | Register it in `runtime.py` and handle it in `BotCore.command` |
| A screen | Add a page widget and a tab in `MainWindow` |
| A language name | Add it to `LANGUAGE_NAMES`; any valid code already works |

---

## 16. Tooling

- `tools/design_mockups.py` renders the three design directions explored for
  0.2 into `docs/design/`.
- `tools/screenshots.py` regenerates `docs/screenshots/` from the running app,
  using sample data in a temporary folder, a week of simulated study on a
  frozen clock, and an empty Telegram configuration, so neither a real `.env`
  nor a user name can reach an image.
- `tools/make_icon.py` builds `app.ico` (nine sizes) from `app.svg`.

Neither is part of the application.
