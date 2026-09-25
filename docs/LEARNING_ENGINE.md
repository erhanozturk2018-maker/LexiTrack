# Learning Engine — design and as built (version 0.3)

This is the design document for turning LexiTrack from a vocabulary manager
into a learning engine: study plans, a fixed daily intake of new words, an
FSRS review schedule, and Telegram as the daily client.

**Status: implemented in 0.3.0** — all four phases of §O. The design below is
kept as it was written, because its reasoning and its numbers (the workload
model in §H, the risks in §N) still explain the defaults. Where the built
engine differs, the difference is listed first, here. For the code as it is,
read [ARCHITECTURE.md](ARCHITECTURE.md) §10–13; for why, [DECISIONS.md](DECISIONS.md)
§42–59.

## As built: what differs from the design below

| Design | As built | Why |
| --- | --- | --- |
| Six services: `StudyPlanService`, `NewWordSelector`, `ReviewQueueService`, `WorkloadPolicy`, `ReviewService`, `MasteryPolicy` | One `LearningService`, with the policies as its methods, over `SrsScheduler` and `DayClock` | One surface is what keeps the window and the bot from computing "today" differently |
| A **Today** page | The **Study** tab, first in the app bar | The interface is English and "Today" read as a date, not a place |
| Leech rule: weak stability after ≥ 6 reviews | After ≥ 4 reviews; the total-lapses rule needs a fresh lapse; a flag clears only when there is no failure streak *and* stability has recovered | Measured: the first version flagged almost every new word; without the recovery rule one lucky Good emptied the list |
| FSRS defaults | One-day learning and relearning steps, due times snapped to a day start and never today, fuzzing off | A once-a-day app: minute steps would always be missed, and fuzz would make the forecast unreproducible |
| Callback data `rev:<session>:<rating>` | `intro:<date>`, `start`, `ans:<session>:<word>:<rating>`, `end:<session>`; the idempotency key is `ans:<session>:<word>` | Carrying the word lets a tap on an old card be recognised; carrying the date stops an old morning message confirming the next day's words |
| An end-of-day summary message | A summary when a review session ends; an evening reminder only if work is left | The session end is when the numbers are fresh |
| Backups `data/backups/lexitrack-YYYY-MM-DD-HHMM.db`, keep N | `data/backups/vocabulary-YYYY-MM-DD.db`, one a day, the newest ten; skipped if the integrity check fails | One per day is what "go back N days" means; a bad copy must not push out a good one |
| Debug log `data/logs/debug/YYYY-MM-DD.log` | `logs/lexitrack-YYYY-MM-DD.log` beside `data/`, seven kept, only while Debug logging is on (which needs Developer mode). HTTP loggers held at WARNING and every line masked for tokens | Logs are not vocabulary and never belong in a backup; the first real test showed `httpx` writing the token in every URL |
| Daily CSV files `data/logs/reviews/…` written automatically | *Export history* in Settings → Data writes the whole review log as one CSV on request | `review_logs` is the record; a file per day nobody opens is clutter |
| The day's brief as Markdown / PDF / clipboard | *Copy with meanings* and *Export* (PDF / CSV / JSON, through the usual preview) on the Study page | The existing export dialog already does PDF with a preview |
| A Struggling Words view | *Words you find hard* on the Study page; they also come first in every session | Seen every day without a separate page |
| Statistics: population by state, Again rate, introduced over time | *The last 30 days* on Study (reviews, Again rate, words learned, in long-term memory); population by state in Settings → About | |
| A Developer page: simulation, queue inspector, scheduler info, runtime state, maintenance | Settings → Advanced: Developer mode unlocks target retention, the leech thresholds and a 365-day simulator; *Back up now* is in Settings → Data; the integrity check and pruning run by themselves | See "Not built" below |
| Settings layout: Learning / Appearance / Data / About | Learning, Telegram, Appearance, Data, Advanced, About; each row a setting with a one-line explanation | Telegram needed its own page for the token and status |
| Time zone Europe/Istanbul via `zoneinfo` | `zoneinfo` when available, otherwise a fixed UTC+3 offset | Windows ships no time-zone database; Türkiye has no daylight saving |
| — | A reentrant lock held for every transaction | The design assumed one; it did not exist, and a second thread could join the first's transaction |
| — | Close hides to the tray only while the bot is on; Quit always quits | An app lingering in the tray with nothing to do gets killed |
| — | An installed copy keeps its data in `%LOCALAPPDATA%\LexiTrack`; a clone keeps `data/` | The old rule would have put the database in `site-packages` |
| — | Reset All Progress also clears cards, sessions and review logs | Clearing statuses alone left cards scheduled for "not reviewed" words |

### Not built

- **A queue inspector** as its own screen. Since 0.4 a word's history answers
  its question for any word — why it is due when it is, and the chance of
  remembering it now — and Progress lists every studied word.
- **A runtime-state view** in Developer mode.
- **A time-zone control** in Settings; the zone is a stored setting only.
- **A "send the brief now" button**; `/today` in Telegram does the same.

### Added in 0.4

- **Fitting FSRS parameters** to the user's own review log, gated at the
  optimizer's own minimum and used only with consent (DECISIONS §65).
- **The record**: every status change with its cause, answers taken back kept
  and marked, a word's history, the Progress tab and a calibration check
  (§60, §61, §64).

### Added in V2 (in progress)

Version 2 separates three things that 0.4 kept in one number:

| | Answers | Kept in | Decided by |
| --- | --- | --- | --- |
| **Memory** | *When will this word be forgotten?* | `srs_cards`, `review_logs` | FSRS, unchanged |
| **Skill** | *What can I do with it?* | `learning_attempts` | derived, never stored |
| **Status** | *Do I count it as known?* | `user_word_state` | the user |

**Skill stages** (`models/skill.py`, `services/skill_tracker.py`), from the
word's **current level** — the hardest *delayed* retrieval it can do now:

| Stage | Shown by |
| --- | --- |
| Not started | nothing yet |
| Encountered | introduced or answered, no delayed success |
| Recognised | the meaning retrieved from the word (level 1) |
| Recalled | the word retrieved from its meaning or a context (levels 2–3), or used once |
| Productive | the word used (collocation, sentence: levels 4–5) in **two different** contexts or tasks |

- Only retrievals in a **review** count — the first attempt, or the probe
  inside it. Retrievals straight after teaching (introduction, relearning,
  repair) are recorded as practice but measure working memory, not learning.
- **Automatic** is evidence, not a stage: instant successes above level 1 on
  two different days.
- **Skill can fall.** The evidence (how often each thing succeeded) only grows,
  but the current level follows the record review by review:
  - a review that ends **Forgotten** (nothing succeeded, not even a probe)
    caps the level at recognition until a recall succeeds again;
  - **two missed first questions in a row at the current level** take it
    down one; a success at that level in between breaks the run, and a miss
    above the level is a stretch, not a fall;
  - a success raises the level to what succeeded. **Productive** must be
    shown again — two different contexts or tasks — after the level has
    been below 4.
- Every answer now records its attempt with its log, in one transaction, and
  Undo takes both back. A V1 review is recorded as what it is — word to
  meaning, level 1, route `v1` — with Easy / Good / Hard as instant / normal
  / effortful and Again as a failure.
- Answers from before schema 5 have no attempt. They are read from the log,
  and only as recognition: that is all a V1 review ever asked.

### Review route V2

A V2 review asks the hard way first and works down only after a failure, so
that the rating says how well the **memory** held and the attempts say what
the learner could **do** (`services/review_route.py` for the rules,
`services/review_flow.py` for the session).

1. **The first question** is chosen from the word's own record by the
   TaskSelector (`services/task_selector.py`) — no calendar rule. One level
   harder after a first question answered without effort; the same after a
   miss or effort; one easier after forgetting; never harder while FSRS gives
   less than a 75 % chance of recall today; only what the content can ask (a
   context needs contexts, a collocation collocations, a sentence of one's own
   an example to compare it with — so a word with no content stays on the
   short route, the word from its meaning). The context used longest ago
   comes first and collocations take turns. The reason is shown on the card
   (hover the question's name). The meaning is the one in the learner's
   language when there is one, otherwise the English definition with the word
   and its forms hidden ("showing ___" for *reluctance*).
2. **Probes** follow a failure, each a fresh question: after level 3 or
   above, the word from its meaning; after that, **the word chosen among
   four**. The answer stays hidden until the word is rated, so no probe can
   be answered from having just seen it.
3. **One rating**, from the strongest success before the answer was shown:

| Case | What happened | Memory result | Rating | Then |
| --- | --- | --- | --- | --- |
| A | recalled at the first question | Recalled | Good (Easy if instant) | — |
| B | recalled, with a hint, a slip or slowly | Recalled with effort | Hard | — |
| C | only chosen among four | Recognised | Hard | repair recall |
| D | not even chosen | Forgotten | Again | relearn |
| E | recalled from a context never seen before | Recalled | Good | transfer counted |
| F | a harder question failed, the meaning → word held | Recalled | Good (Hard if effortful) | repair that skill |

4. **Relearning** (D) and **repair** (C, F): the word is taught again at
   once, and asked again three cards later with a *different* prompt — the
   other meaning source, another context, another collocation. At most two
   cycles. That practice is recorded, linked to the answer, and never
   changes the rating: a same-session success is not a day's memory.
5. **One rating per word per day**, from any client: a second answer the same
   day is recognised as a duplicate and changes nothing.

### The day's queue

`services/review_queue.py` chooses and orders the day's reviews. Each due word
is **fragile** (flagged as hard, relearning, or under 2 days of stability),
**at risk** (a settled memory now under an 80 % chance of recall) or
**normal**.

- **The limit** is the learner's (default 250) but never above **250**, and
  "no limit" is read as 250: past that a session is too long to finish, and
  an unfinished session is where reviewing stops. A stored value over the
  ceiling is read as the ceiling, not rewritten.
- **Over the limit**, the fragile words are kept first, then the rest by
  lowest chance of recall. What is left waits for another day, and the Today
  card says how many.
- **The order**: a warm-up of the 3 easiest words kept (highest chance of
  recall), so a session does not open on a miss; then the fragile words mixed
  in, at most one in every 4. Both numbers are settings (Advanced).
- **Intake** protects today and tomorrow: new words pause when today's due
  reviews already reach the limit, and shrink when today's new words — all
  due tomorrow — would take tomorrow past it. The card says which, and why.

### First learning

New words are learned in the same session as the day's reviews, after them
(`services/first_learning.py`): reviewing first measures each memory before
new material can interfere with it. Each new word gets a **depth**, decided
from its content:

| Depth | When | Taught | Asked |
| --- | --- | --- | --- |
| SHORT | nothing stored beyond the definition (or a bare meaning) | the meaning | the word from its meaning |
| LIGHT | content, and nothing that says the word is hard | + pattern, collocations, one example | + later, from a context if there is one |
| DEEP | a `deep` hint, or an abstract mnemonic (contrast, relation) | everything, two examples | + later, a second question in another form |

Words go in groups of four: four taught, then four asked, and the second
questions of a group after the next group is taught, so every question comes
after a gap. A miss means the word is taught again, one depth deeper when
there is more to show, and asked again three cards later — twice at most.

None of it is rated. The answers are recorded as attempts of the
introduction phase, with the depth; the word gets its card — first review
tomorrow — the moment its last step is done, so leaving early keeps the
words finished and offers the rest again. **Mark as studied** introduces the
day's words without the practice, for a learner who studied them another way.

The Today card estimates the session: 20 seconds a review, and 45, 75 or
120 seconds for a SHORT, LIGHT or DEEP new word.

**Typed answers.** Compared without case, spacing or punctuation; a
multi-word entry is accepted without its frame ("expelled" for *be
expelled*). One slip is accepted in four to ten letters, two from eleven,
and makes the answer effortful. So does a hint (the first letter and the
shape). Otherwise time decides: within about 2.5 s plus 0.12 s a letter is
instant, beyond 12 s plus 0.2 s a letter is effortful. An empty answer is
"I don't know".

**Level 5** (a sentence) cannot be checked by the app: you write one, see
example sentences, and grade it yourself.

**No meaning to ask from** — no definition and no meaning in the learner's
language — means the
word is reviewed the V1 way: shown, revealed, rated by you (route `v1`).

**Undo** takes back the last rated word whole: its rating, its probes and any
practice after it, and asks it again from the first question.

---

## The original design

It was written against the repository as it was at version 0.2.0 (schema
version 2, 343 tests), before any of it was implemented; sections A–C
describe that starting point.

---

## A. Current architecture

```text
            PDF            JSON           typed by hand
             │              │                  │
             ▼              ▼                  │
        open_document → ParserRegistry         │
                    ▼                          ▼
               WordEntry  ◄─────────────── WordEntry
                    │
          normalize + deduplicate  (language, word)
                    ▼
        VocabularyService          the UI's only surface
          ImportService · ReviewSession · ExportService
                    ▼
        Repositories               the only SQL in the codebase
                    ▼
                  SQLite
```

- `lexitrack/ui` is a PySide6 desktop app: Home, Review (flashcard and list
  modes), Unknown Words, plus an export window, an import wizard, a Ctrl+K
  command palette and a details panel.
- `lexitrack/services` holds the business logic behind one facade,
  `VocabularyService`. The UI never issues SQL and never constructs a
  repository.
- `lexitrack/repositories` is the only code that touches SQLite:
  `WordRepository`, `ListRepository`, `SourceRepository`, `StateRepository`.
- `lexitrack/database` owns `schema.sql`, `migrations.py` (versioned steps,
  online-backup before upgrading) and the connection (WAL, foreign keys on,
  `check_same_thread=False` with a write lock).
- Settings live in `QSettings` — the Windows registry — not in the database.
- `data/` holds the database, `data/exports/`, and one rolling log file.

**What matters for this design:** the service boundary already exists and is
clean, so a second client (a Telegram process) can use the same services
without an HTTP layer. Settings, however, are in the wrong place for that.

---

## B. Current data model (schema version 2)

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

Four concepts are kept apart and stay that way: the **word**, the **source**
it came from, the **lists** it belongs to, and the **learning status**. A
word's details (part of speech, level, definition, example, note) are
flattened from `word_sources`; the first source that supplies a field wins.

`user_word_state.status` is one of `known`, `unknown`, `not_reviewed`. It is
word-level and shared across lists, and it is the user's broad judgement — not
a scheduler state.

---

## C. Current review model

`ReviewSession` (services/review_session.py) is an in-memory session over one
list: it asks the repository for the next word whose status is `not_reviewed`,
keeps a `_history` list plus a `_cursor`, and lets the user step back and
forward without changing any status. Answering writes
`user_word_state.status` immediately.

There is no schedule of any kind. A word is reviewed once; after that it is
`known` or `unknown` and never comes back unless the user resets it. Unknown
Words is the manual replacement for a scheduler: a working list the user
collects, resets or exports.

That is the gap this design closes.

---

## D. Gap analysis

| Needed | Exists today | Missing |
| --- | --- | --- |
| Study plan | Lists, list membership | A plan entity, list selection, one active plan, plan-scoped queries |
| SRS | Nothing | Card state, FSRS wrapper, due dates, review logs |
| Daily queue | `next_unreviewed` for one list | New-word selector, review queue, workload policy, day boundary |
| Acquisition step | Nothing | An explicit "introduced" transition with no rating |
| Mastery | Manual Known/Unknown | A written, configurable rule; manual override kept authoritative |
| Settings | QSettings (registry) | Settings in the database, readable by a second process |
| Telegram | Nothing | Bot process, session state, callbacks, idempotency, notifications |
| Recovery | Nothing | Runtime state, downtime detection, no replay of missed days |
| Review history | One `reviewed_at` per word | Append-only review log, CSV export per day |
| Backup | Migration backup only | Scheduled local database backups with retention |
| Statistics | Counters (total/known/unknown) | Forecast, population over time, Again rate |

FastAPI and Docker are **not** in this table on purpose; see §L.

---

## E. Proposed architecture

```text
 Windows host — ONE process, one SQLite connection

+ LexiTrack application ---------------------------------------+
|                                                              |
|  Qt UI thread                 Telegram thread                |
|  admin | Today | stats       long polling, asyncio loop      |
|         |                             |                      |
|         +------ both are clients -----+                      |
|                        |                                     |
|                        v                                     |
|  services                                                    |
|    VocabularyService    the existing facade                  |
|    StudyPlanService     plan and scope                       |
|    NewWordSelector      never-introduced words               |
|    ReviewQueueService   introduced, due cards                |
|    WorkloadPolicy       daily limits and ordering            |
|    SrsScheduler         thin wrapper over fsrs               |
|    ReviewService        one review transaction               |
|    MasteryPolicy        Known transitions                    |
|    DayClock             'today' in Europe/Istanbul           |
|    RuntimeStateService  downtime and notifications           |
|                        |                                     |
|                        v                                     |
|  repositories          the only SQL in the codebase          |
|                        |                                     |
|                        v                                     |
|  SQLite                WAL, one connection, write lock       |
|                                                              |
+--------------------------------------------------------------+
```



**The Telegram side never writes.** It reads a queue through a service, shows
it, and posts the user's answer back through `ReviewService`. Every write goes
through the same repositories the UI uses, in the same process, over the one
connection the app already guards with a write lock
(`check_same_thread=False` plus a lock is how `database/connection.py` is
already built — the bot thread needs no new mechanism).

Because there is only one process, the "two writers on one SQLite file"
problem does not exist: no `database is locked`, no busy-timeout tuning, no
write queue of our own. The Qt thread and the Telegram thread are two callers
of the same serialized connection.

Consequence, accepted deliberately: **the bot runs while the application
runs.** The window can be closed to the tray and the app can start with
Windows, so in practice "the computer is on" and "the bot is on" are the same
thing — which is the constraint that was accepted anyway. A headless mode
(`python -m lexitrack --headless`, services plus bot, no Qt windows) can be
added later for a machine that is on without anybody at the keyboard; it is
still one process, so it keeps the single-writer property.

Rejected alternatives, for the record:

| Option | Why not |
| --- | --- |
| Bot as a second OS process on the same database | Two writers on one file: lock contention, `busy_timeout` tuning, and a race nobody can reproduce on demand. No benefit over a thread |
| Bot as a process talking to a local FastAPI server | Correct layering, but it adds a server, a port, serialization and a second deployment unit to solve a problem a thread already solves |
| Bot writing directly through its own repository instances | Business rules would end up in two places; the queue and the schedule must have one owner |

Responsibilities that must not blur:

- `SrsScheduler` answers *when should this card come back?* It never decides
  how many new words to introduce.
- `WorkloadPolicy` answers *how much work today, in what order?* It never
  computes intervals.
- `MasteryPolicy` answers *is this word Known now?* It never schedules.
- The Telegram process contains no learning logic; it renders queues and
  posts answers back through `ReviewService`.

---

## F. Proposed schema (version 3)

Additive only: no existing table changes shape, so the migration is short and
the existing 343 tests keep passing.

```sql
study_plans        (id, name UNIQUE, language, is_active INTEGER NOT NULL DEFAULT 0,
                    created_at, updated_at)
study_plan_lists   (plan_id, list_id, position, PRIMARY KEY (plan_id, list_id))

srs_cards          (word_id PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
                    origin_plan_id REFERENCES study_plans(id),
                    state TEXT NOT NULL,          -- introduced|learning|review|relearning|archived
                    introduced_at TEXT NOT NULL,  -- UTC
                    introduced_on TEXT NOT NULL,  -- local date, e.g. 2026-09-18
                    due_at TEXT NOT NULL,         -- UTC
                    first_review_at TEXT,
                    last_review_at TEXT,
                    review_count INTEGER NOT NULL DEFAULT 0,
                    lapse_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_lapses INTEGER NOT NULL DEFAULT 0,
                    needs_relearning INTEGER NOT NULL DEFAULT 0,
                    fsrs_state TEXT,              -- JSON: the library's card state
                    scheduler_version TEXT,       -- e.g. fsrs 6.3.2
                    created_at, updated_at)

review_logs        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word_id NOT NULL, session_id, channel TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL, reviewed_on TEXT NOT NULL,
                    rating INTEGER NOT NULL,      -- 1 again, 2 hard, 3 good, 4 easy
                    state_before, state_after,
                    due_before, due_after,
                    elapsed_days REAL, scheduled_days REAL,
                    scheduler_version, params_hash)

review_sessions    (id TEXT PRIMARY KEY, channel, started_at, finished_at,
                    plan_id, chat_id, message_id, current_word_id,
                    planned_count, done_count)

telegram_updates   (update_key TEXT PRIMARY KEY, handled_at)   -- idempotency, pruned
app_settings       (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at)
runtime_state      (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at)
```

Notes on the choices:

- **One card per word.** `srs_cards.word_id` is the primary key, so the same
  word in two selected lists cannot produce two cards. `origin_plan_id` is
  provenance only ("introduced while studying this plan"), never a scope key.
  A future multi-profile version would rebuild this table with
  `(profile_id, word_id)`; the migration framework already does table rebuilds
  (v1 → v2 did exactly that), so nothing is pre-built for it today.
- **`fsrs_state` as JSON.** The library owns the shape of its own state; a
  blob plus `scheduler_version` means a library upgrade cannot silently
  reinterpret old numbers.
- **Local date columns** (`introduced_on`, `reviewed_on`) exist so day queries
  and CSV exports are plain string comparisons instead of timezone arithmetic
  in SQL.
- **`app_settings` in the database**, not `QSettings`: the bot process must
  see the same limits as the desktop app. UI preferences (theme, window
  geometry, which list was last open) stay in `QSettings`.

Settings with their defaults:

| Key | Default | Meaning |
| --- | --- | --- |
| `new_words_per_day` | 25 | Fixed daily intake, always allocated first |
| `review_capacity_per_day` | 250 | Soft cap on non-new presentations |
| `day_start_hour` | 0 | Day boundary, local time |
| `timezone` | `Europe/Istanbul` | Local zone for day arithmetic |
| `notify_hour` | 6 | Morning message |
| `evening_reminder_hour` | 21 | One reminder, only if work is unfinished |
| `desired_retention` | 0.9 | Passed to FSRS |
| `leech_consecutive` | 4 | Consecutive Again → needs relearning |
| `leech_total_lapses` | 8 | Total Again over the card's life → needs relearning |
| `leech_weak_stability_days` | 7 | With ≥ 6 reviews and stability under this → needs relearning |
| `hide_meaning_in_study` | true | Study-plan reviews hide the meaning until revealed |
| `developer_mode` | false | Shows the Developer page and advanced settings |
| `debug_logging` | false | Only settable while Developer Mode is on |
| `mastery_stability_days` | 21 | Auto-Known threshold |
| `review_known_words` | true | Keep Known cards in long-term review |
| `active_plan_id` | — | The one active plan |

---

## G. State machine

```text
        not in any plan                     ┌─────────────┐
              │                             │  archived   │
              ▼                             └──────▲──────┘
      ┌───────────────┐  plan selected             │ manual Known
      │ candidate     │  status = unknown          │ (user override)
      │ (no card yet) │                            │
      └──────┬────────┘                            │
             │ learned in a session, or marked     │
             │ as studied                          │
             ▼                                     │
      ┌───────────────┐   due = next day start     │
      │ introduced    │   no rating recorded       │
      └──────┬────────┘                            │
             │ first real rating                   │
             ▼                                     │
      ┌───────────────┐  Good/Easy, stability ≥ N  │
      │ learning      │───────────────┐            │
      └──┬─────────▲──┘               ▼            │
         │ Again   │           ┌─────────────┐     │
         ▼         │           │   review    │─────┘
   ┌───────────┐   │           └──┬──────────┘
   │relearning │◄──┴──────────────┘ Again
   └───────────┘   consecutive_lapses ≥ leech_threshold
                   → needs_relearning = 1 (stays in relearning queue)
```

Transitions in words:

- **candidate → introduced.** The only way in is the user confirming that the
  day's new words were studied. Sending a Telegram message does not do it. On
  introduction: `introduced_at = now`, `introduced_on = local date`,
  `due_at = next day start`, `state = introduced`, **no rating is written** —
  there is no fabricated "Good".
- **introduced → learning.** The first real rating (Again/Hard/Good/Easy),
  which can only happen on a later day, hands the card to FSRS for the first
  time.
- **learning ⇄ relearning.** An Again increments `lapse_count` and
  `consecutive_lapses`; a non-Again resets `consecutive_lapses`. When
  any of the three leech conditions below is met, `needs_relearning = 1`,
  which puts the card at the front of the review queue and lists it under
  Struggling Words so the user can study it again externally. It **never**
  returns to the new-word queue: `introduced_at` is already set, so the
  selector cannot see it.

### When is a word "struggling"? (leech rule)

A card is marked `needs_relearning` when **any** of these holds:

| Condition | Default | What it catches |
| --- | --- | --- |
| `consecutive_lapses ≥ leech_consecutive` | 4 | A word that is failing right now |
| `lapse_count ≥ leech_total_lapses` | 8 | A word that has always been trouble |
| `review_count ≥ 6` and FSRS stability `< leech_weak_stability_days` | 7 days | A word that is answered correctly but never sticks |

All three are settings, so the rule can be tightened without touching code.
For reference, Anki's default leech threshold is 8 lapses, after which it
tags and suspends the card; this design does not suspend anything, because
the user's own reaction (studying the word again externally) is the point.

The flag clears when the card earns a non-Again answer **and** its stability
passes `leech_weak_stability_days`, so a word does not bounce in and out of
the list after a single lucky answer. Clearing is recorded in `review_logs`
like any other state change.

Struggling Words is a filtered view, not a new queue: the same cards are in
the review queue, just ordered first and listed together so the user can send
them to the day's brief and study them again.
- **review → archived.** Only a manual Known does this. Automatic mastery
  (stability ≥ `mastery_stability_days`) sets `user_word_state.status = known`
  but keeps the card scheduled, because `review_known_words` defaults to true.
  A Known card that starts failing goes back to relearning like any other.
- **archived → review.** If the user resets the word's status, the card
  resumes with its stored FSRS state. The card is never deleted while the word
  exists.

The new-word pointer never moves backward, because there is no pointer to move:
a card's existence *is* the marker.

---

## H. Daily queue algorithm

```text
build_today(now):
    day      = local_date(now)                # Europe/Istanbul, day_start_hour
    day_start= start_of_day(day)
    plan     = active_plan()
    limits   = settings(new_words_per_day, review_capacity_per_day)

    # 1. NEW — always allocated first, never consumed by reviews
    introduced_today = count(srs_cards where introduced_on = day)
    new_slots        = max(limits.new - introduced_today, 0)
    new_words        = words in plan
                       where status = 'unknown'
                         and no srs_card exists
                       order by cefr_rank, list position, word_id
                       limit new_slots

    # 2. REVIEW — only cards introduced before today
    due = srs_cards
          where word_id in plan_words
            and introduced_on < day                # hard invariant
            and due_at <= now
          order by needs_relearning desc,          # leeches first
                   state = 'relearning' desc,
                   due_at asc,                     # most overdue next
                   word_id
    review = due[: limits.review_capacity]

    return DailyPlan(day, new_words, review,
                     overdue_total = len(due),
                     skipped = len(due) - len(review))
```

Two properties this guarantees:

1. `new_words ∩ review = ∅` on any given day, and not because of a filter
   bolted on at the end: a card introduced today has `due_at` at the next day
   start, so it cannot be due today, and the `introduced_on < day` condition
   makes it impossible even if a future scheduler change misbehaves.
2. Reviews can never eat the 25 new slots, and new words can never be delayed
   by a review backlog.

Capacity is a cap on *presentations offered*, not a deletion: skipped cards
stay overdue and come first tomorrow. FSRS accounts for the extra elapsed
days when the card is finally answered.

### Expected workload (model, not measurement)

A simulation with an FSRS-like interval ladder (1, 3, 8, 20, 45, 100, 210,
400 days) and an 18% Again rate on young cards, introducing 25 words a day:

| Day | Reviews/day, current unknown pool (1,918) | Reviews/day, whole vocabulary as the plan (6,825) |
| --- | --- | --- |
| 7 | 60 | 60 |
| 30 | 130 | 130 |
| 60 | 140 | 140 |
| 90 | 71 | 154 |
| 180 | 16 | 210 |
| 365 | 7 | 48 |
| peak | ~159 (day 78) | ~243 (day 257) |

At six seconds a card that peak is 16–24 minutes a day. This is why
`review_capacity_per_day` defaults to 250 and not 75: a cap of 75 would start
accumulating a permanent backlog in the second week. The desktop app should
show the real forecast from `due_at` values (next seven days) so the number
is observed rather than assumed.

---

## I. Pointer behaviour

There is no integer pointer. "Not introduced" means *no row in `srs_cards`*.
That survives every case the requirements list:

| Situation | Result |
| --- | --- |
| Studied 0 new words today | No cards created; tomorrow offers the same candidates |
| Studied 8 of 25 | 8 cards created; the other 17 are still candidates tomorrow |
| Restart, crash, five days offline | Nothing to rebuild; the candidate set is a query |
| Word removed from a list | It leaves the plan scope; if it has a card, the card stays but is out of scope until the word is back in a selected list |
| Word deleted with its last list | `ON DELETE CASCADE` removes the card, as it should |
| New list added to the plan | Its unknown words simply join the candidate ordering |
| Word marked Known manually | It leaves the candidate set (`status = 'unknown'` fails) |
| A card fails repeatedly | Still has `introduced_at`, so it can never reappear as new |

Missed days create no new-word debt: `new_slots` is computed from *today's*
introductions only, never from a running total. Overdue review cards are a
different thing and are allowed to accumulate.

Ordering of candidates is deterministic: CEFR rank (A1 → C2, unknown level
last), then the word's position in the first selected list, then `word_id`.
No randomisation unless a setting asks for it.

---

## J. Telegram architecture

```text
06:00  the morning brief (telegram/schedule.py decides it is owed)
         → today's new words with a short meaning, the review count
         → [▶ Start session (N)]  [Mark the 25 new words as studied]
         → runtime_state: sent today

user taps [Start session]  (or sends /review)
         → an open Telegram session?  restore its ReviewFlow and show its step
           else ReviewFlow(engine, Channel.TELEGRAM).start()
         → one message per step, the last one deleted:

             MEANING → WORD
             unwilling and hesitant
             Reply with the word.
             3 / 33
             [I don't know]
             [↶ Undo] [Stop here]

         → a reply answers a typed step; buttons answer the rest:
           "st:<session>:<step number>:<value>"
         → answerCallbackQuery immediately (Telegram's ~10 s window)
         → the step number must be the one on screen, else ignored
         → ReviewFlow.submit / choose / rate / grade / proceed
              → LearningService.review (FSRS, logs, attempts) once per word
         → the flow's state saved to review_sessions.flow_state
         → the next card, opening with how that answer went
21:00  one reminder, only if today's work is unfinished
end    summary: reviews done, Again rate, new words learned, what is left
```

A step's buttons carry its session and its number; the number changes with
every step, so a double tap, a re-delivered update or an old card finds a
different number and does nothing. After a restart the flow is rebuilt from
its saved state, and the first tap or reply shows the current step instead of
acting on it. `review_logs` is the permanent record.

### Why the callback is acknowledged before the work

When the user taps a button, Telegram expects a short "got it" reply
(`answerCallbackQuery`) within a few seconds. If it does not arrive, the
button keeps spinning in the client and Telegram re-delivers the same update,
which is how one tap turns into two reviews. So the order is: acknowledge
first, then do the work (schedule the card, write the log, edit the message).
Combined with the step number on every button, a re-delivered tap changes nothing.

### Where the bot token lives

The token is the password of the bot: anyone holding it can read and send
messages as that bot. It is therefore not written into `app_settings`,
because the database is the file that gets copied into `data/backups/`,
attached to a bug report or synced to another machine. It is read from an
environment variable (`LEXITRACK_TELEGRAM_TOKEN`) or from a file in the data
folder that is excluded from git and from backups. If the token leaks, it is
revoked in BotFather and replaced — no database change needed.

---

## K. Offline recovery

The bot only runs while the computer is on, and that is accepted. The design
therefore treats a daily plan as *derived state*, never as a queue of tasks
that must all be executed.

On startup: open and integrity-check the database, run migrations, load
`runtime_state`, compare `last_notification_on` with today, then build today's
plan from the database.

| Downtime | What happens on startup |
| --- | --- |
| 1 hour, same day, already notified | No new message; `[Start review]` still works; review queue recomputed |
| 1 hour, same day, not yet notified, past `notify_hour` | Send today's message now |
| 1 day | Send today's message only. Yesterday's 25 candidates are still candidates; nothing was lost and nothing is owed |
| 5 days | Send **one** message for today. No replay. Overdue cards are simply overdue and sorted first; the new-word allocation is still 25 |

`runtime_state` keys: `last_started_at`, `last_clean_shutdown_at`,
`last_heartbeat_at`, `last_notification_on`, `last_plan_built_on`,
`schema_version_seen`. An unfinished `review_sessions` row older than the
current day is closed on startup rather than resumed.

---

## L. Docker, FastAPI and why they are not here

Both were in the original brief; both are deliberately out of scope for 0.3.

- **FastAPI** would add an HTTP boundary between two processes on the same
  machine, each of which can already import `lexitrack.services` directly.
  The rule the API was meant to enforce — "both clients use the same logic" —
  is enforced by the service layer itself. Adding endpoints now means writing
  every feature twice (service plus route plus schema) for no behavioural gain.
- **Docker** would put the SQLite file behind a Windows bind mount, which is
  the one place SQLite's locking and fsync guarantees get fragile, and would
  do so while the container's whole value proposition ("always available") is
  already voided by the machine being off at night.

If the engine later moves to an always-on host (a small VPS or a Raspberry
Pi), that is the moment to add a FastAPI boundary and a container — and by
then the services will already be the right shape for it.

---

## M. Logging, history and backups

Four separate things, deliberately not one:

| Concern | Where | Lifetime |
| --- | --- | --- |
| Learning history | `review_logs` in SQLite | Permanent; the source of truth |
| Daily review statistics | `data/logs/reviews/YYYY-MM-DD.csv`, written from `review_logs` | Derived, regenerable |
| Developer diagnostics | `data/logs/debug/YYYY-MM-DD.log` | Only when Debug Logging is on |
| Database backups | `data/backups/lexitrack-YYYY-MM-DD-HHMM.db` via SQLite's online backup API | Rolling, keep N |

Developer Mode and Debug Logging are two settings, not one: entering
Developer Mode does not start writing debug logs. Normal use writes no debug
log. The existing migration code already uses the online backup API, so the
backup job reuses it rather than copying files while they are open.

---

## N. Risks and trade-offs

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Telegram thread blocking the UI | A long query on the bot thread could freeze the window | Services are called off the UI thread; writes are single rows; the existing write lock serializes them |
| App closed means no bot | The daily message stops arriving | Close to tray instead of quitting; optional start-with-Windows; a headless mode later |
| Two instances polling Telegram | Telegram rejects one poller and updates are lost | Single-instance lock file; a second launch raises the existing window |
| Review backlog | A cap of 75 would silently accumulate work | Default 250, overdue-first ordering, seven-day forecast in the UI |
| Day boundary at 00:00 | A session at 00:30 counts as the new day, so that day's 25 unlock immediately | Accepted, and `day_start_hour` is a setting if it turns out to be annoying |
| FSRS library upgrade | Card state shape may change | Store state as JSON with `scheduler_version`; never silently reinterpret |
| Known vs SRS conflict | Two sources of truth for "I know this" | Manual Known wins and archives the card; automatic mastery only sets the status and leaves scheduling alone |
| Definitions visible on the card | Now that every word has one, recall is not being tested | Hide the meaning until revealed **in study-plan reviews only**; ordinary flashcard and list browsing keep showing it |
| Duplicate Telegram callbacks | A review applied twice | Every button names its step; a tap for any other step does nothing |
| Bot token leakage | Account takeover of the bot | Environment variable or local file; never in the database or the repository |
| Scope creep into an app that teaches | The brief explicitly keeps content generation out | The engine answers *what and when*; the day's brief is exported for an external AI workflow |

---

## O. Roadmap

Each phase leaves the repository testable and usable.

**Phase 1 — engine in the desktop app (schema v3)**
Tables, repositories, `SrsScheduler` over `fsrs`, `StudyPlanService`,
`NewWordSelector`, `ReviewQueueService`, `WorkloadPolicy`, `ReviewService`,
`MasteryPolicy`, `DayClock`; settings moved into the database; a Today page
(25 new, review queue, Again/Hard/Good/Easy), a plan editor, a settings page,
and the seven-day forecast.

**Phase 2 — the workflow around it**
The day's brief as Markdown/PDF/clipboard for the external AI study session;
CSV review logs; the Struggling Words view; hide the meaning until revealed in
study-plan reviews; the first statistics (population by state, Again rate,
introduced over time). Editing a word's own notes is deliberately **not** part
of this: the user does not want it, and the brief plus the existing definition
and note fields already carry what an external study session needs.

**Phase 3 — Telegram**
A background thread inside the application: long polling on its own asyncio
loop, the morning message, introduction confirmation, an editable review
session, idempotency, the evening reminder, the summary, `runtime_state`
recovery. Plus a tray icon so closing the window keeps the bot alive.

**Phase 4 — backups and housekeeping**
Scheduled online backups with retention, `telegram_updates` pruning, a
database integrity check on startup.

**Not planned:** FastAPI, Docker, PostgreSQL, Redis, a web UI. See §L.

---

## Process lifecycle: window, bot and autostart

Three switches, deliberately independent. Mixing them is what makes
background apps feel out of control.

| Switch | Where | Stored in | Meaning |
| --- | --- | --- | --- |
| The application is running | Tray menu: Quit | — (a process) | Everything stops, including the bot |
| The bot is on | Tray menu or Settings → Learning | `app_settings.telegram_enabled` | Polling and notifications; survives restarts |
| Start with Windows | Settings → Data | A shortcut in the Startup folder | Whether the app comes back after a reboot |

```text
Tray icon menu
├─ Open LexiTrack            show the window
├─ Today: 25 new · 140 due   read-only summary, click to open Today
├─ Telegram bot  [on/off]    toggles app_settings.telegram_enabled
├─ Start with Windows [x]    creates/removes the Startup shortcut
├─ Settings…
└─ Quit LexiTrack            ends the process; nothing runs afterwards
```

Rules that follow from this:

- **Closing the window does not quit.** The X button hides to the tray and
  the bot keeps working. Quit is an explicit menu item, so the state is never
  ambiguous.
- **Turning the bot off is permanent until turned back on.** It is a setting
  in the database, not a runtime flag: reboot the machine and the app comes
  back with the bot still off. This is the answer to "I stopped it — will it
  come back on its own?" It will not.
- **Turning off Start with Windows is a separate decision.** Bot off means
  the app runs quietly and sends nothing; autostart off means the app does not
  run at all until it is opened by hand.
- **First run needs the window once:** put the token in `.env`, choose the
  study plan, set the two daily limits, tick Start with Windows. After that
  the app can live in the tray and never be opened again except to look at
  statistics.
- **Headless mode** (`python -m lexitrack --headless`) exists for a machine
  where no window is wanted at all: same single process, same services, no Qt
  windows, stopped with Ctrl+C or by ending the process.
- **Single instance.** The app takes a lock file in the data folder on start.
  A second launch does not start a second process; it raises the running
  window instead. This is not cosmetic: two processes polling Telegram with
  the same token make Telegram reject one of them
  (`Conflict: terminated by other getUpdates request`), and two processes
  writing one database is exactly what this design avoids.
- **Killing the process is safe.** Each review is written in its own
  transaction, so Task Manager, a power cut or a Windows update loses at most
  the card currently on screen.

---

## Developer Mode

Two separate switches, as required: entering Developer Mode does not start
writing debug logs.

```text
Settings window (Ctrl+,)
├── Learning      new words per day, review capacity, active plan,
│                 day start hour, desired retention, mastery threshold,
│                 leech thresholds, hide meaning in study
├── Appearance    theme
├── Data          data folder, backups, retention, exports
└── About         version, database, [ ] Developer mode
                                        └─ [ ] Debug logging (only when on)
```

Everything a normal user needs to tune the daily workload — the two limits
above all — stays on the Learning page, in normal mode. Developer Mode adds
one more page:

```text
Developer page (hidden unless developer_mode = true)
├── Workload simulation   run N days forward with the current settings,
│                         show the daily load curve; read-only, never writes
├── Queue inspector       today's new / review / skipped lists with reasons
├── Scheduler             fsrs version, parameters, params hash
├── Runtime state         last started, last notification, downtime detected
└── Maintenance           integrity check, backup now, prune telegram_updates
```

The simulation in the app is a planning toy and lives here. It is not the
same thing as the workload test in the suite (§Test plan), which runs in CI
with a fake clock and needs no mode at all — a scheduler change that explodes
the daily load should fail the tests whether or not anybody opens the
Developer page.

The seven-day forecast stays in **normal** mode on the Today page: it is
read from the real `due_at` values, it is the information the user needs to
decide whether 25 a day is comfortable, and it is not a simulation.

---

## Test plan

Beyond the existing 343 tests, with an injectable clock so nothing depends on
the wall time the suite happens to run at:

- **Intake:** 0 studied → 0 cards; 8 studied → 8 cards, 17 still candidates;
  25 studied → the next 25 candidates tomorrow.
- **Same-day exclusion:** a word introduced today never appears in today's
  review queue, even with `due_at` forced into the past.
- **No fabricated rating:** introduction writes no `review_logs` row.
- **Relearning:** repeated Again sets `needs_relearning` and puts the card
  first in the review queue, and the word never returns to the new-word queue.
- **Plan scope:** a word outside the active plan gets no card and no queue
  entry; adding its list to the plan makes it a candidate.
- **Duplicate membership:** the same word in two selected lists yields one
  card.
- **Missed days:** five days of silence produces 25 new slots, not 125, and
  exactly one notification.
- **Day boundary:** 23:59 and 00:01 fall on different days; a review at 00:30
  is logged on the new date.
- **Idempotency:** the same Telegram callback twice applies one review.
- **Persistence:** restart mid-session; state and queue are rebuilt from the
  database.
- **Mastery:** stability ≥ threshold sets Known without unscheduling; manual
  Known archives the card; resetting the status resumes it.
- **Workload simulation:** a 400-day simulation asserts the daily load stays
  within an expected band, so a scheduler change that explodes the workload
  fails the suite.

---

## Decisions already taken

| Question | Decision |
| --- | --- |
| New words per day | 25, fixed, always allocated before reviews |
| Review capacity | Configurable, default 250; overdue-first when capped |
| SRS card ownership | One card per word; the plan is scope, not a key |
| Acquisition | Explicit introduced state, `due_at` = next day start, no rating |
| Introduction | Learned in the session (taught, then practised), or marked as studied with one tap |
| Known after mastery | Cards keep being reviewed at long intervals; a setting can turn it off |
| Manual Known | Wins over the scheduler, archives the card, never deletes it |
| Struggling words | Flagged by three configurable leech conditions; a filtered view, not a separate queue |
| Meaning on the card | Hidden until revealed in study-plan reviews only |
| Word notes editing | Not planned; the user does not want it |
| Developer Mode | Settings → About; adds a Developer page (simulation, inspector, maintenance). Debug logging is a second, separate switch |
| Day boundary | 00:00 Europe/Istanbul (`day_start_hour` = 0, adjustable) |
| Morning message | 06:00 local |
| Settings | Learning settings in the database; UI preferences stay in `QSettings` |
| Bot lifecycle | Tray icon; closing the window hides it; Quit stops everything; the bot has its own persistent on/off setting; autostart is a separate setting; single instance enforced by a lock file |
| Telegram | A thread inside the app, not a second process; it never writes directly, it calls services; long polling; runs while the app runs (tray) |
| FastAPI / Docker | Not in 0.3 |
