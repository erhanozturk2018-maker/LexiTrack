# Project Status

**Last updated:** 18 September 2026
**Version:** 0.3.0 · schema version 3

---

## Current Phase

**Version 0.3 complete.** LexiTrack is now a learning engine as well as a
vocabulary manager: a study plan introduces 25 new words a day, FSRS schedules
their reviews, and an optional Telegram bot delivers the day's words and runs
review sessions from a phone. Version 0.2 databases upgrade automatically.

## Overall Progress

| Area | Status |
| --- | --- |
| Schema version 3 and the v2 → v3 migration | Done, run on the real database with a backup |
| Local learning day (00:00 Europe/Istanbul) and a test clock | Done |
| FSRS scheduling with day steps, no fuzz | Done |
| Study plans, 25 new words a day, review limit | Done |
| Struggling words and mastery (Known at 21 days) | Done |
| Workload simulator | Done — set the review limit to 250 |
| Study tab, Study Plan window, Settings | Done, redesigned in the app's visual language |
| Telegram bot: brief, reviews, reminders, commands | Done, tested with the real bot |
| Tray, single instance, Start with Windows, headless mode | Done |
| Daily backups (ten kept), integrity check, key pruning | Done |
| Debug log: off by default, one file a day in `logs/` | Done |
| Desktop shortcut with the app icon | Done |
| Installed copy keeps its data in `%LOCALAPPDATA%` | Done |
| Tests | 581 (6 marked `slow`) |
| Documentation | Updated for 0.3 |

---

## Completed

Version 0.1 and 0.2 are described in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md);
everything from them still works as described there.

### Learning engine

- **Study plans.** A plan is an ordered set of lists; words are offered in list
  order, 25 a day. A word already known or already scheduled is skipped.
- **Introduction.** The day's words are offered; a card is created only when
  the user confirms they have studied them. No rating is recorded for that.
- **Reviews.** FSRS 6 through `SrsScheduler`: one-day learning and relearning
  steps, due times snapped to a day start and never today, no fuzz. One card
  per word, whatever the plan.
- **Review limit.** When today's due reviews reach 250, new words pause and the
  Study page and the morning message say so.
- **Words you find hard.** Four Agains in a row, eight in total with a fresh
  one, or weak stability after four reviews; cleared only when the streak is
  gone *and* stability has recovered. They come first in every session.
- **Mastery.** A word becomes Known when its stability reaches 21 days. Known
  by hand archives the card.
- **Reset All Progress** also clears cards, sessions and review logs.

### Study, Study Plan and Settings

- **Study** (first tab): a Today panel with one primary button whose label is
  the next thing to do; today's words as chips with *Copy with meanings* and
  *Export*; the week ahead as day tiles; words you find hard; the last 30 days.
- **Review session** in the window with the flashcards' answer colours and the
  interval each answer would give.
- **Study Plan** window (Ctrl+P): choose and order lists, with a summary of
  how long the plan will take.
- **Settings** (Ctrl+,): Learning, Telegram, Appearance, Data, Advanced,
  About. Developer mode unlocks target retention, leech thresholds, the
  365-day simulator and Debug logging.

### Telegram

- Token from `.env` or `LEXITRACK_TELEGRAM_TOKEN`, never stored in the
  database. The first chat to send `/start` becomes the owner.
- Morning brief at 06:00 with the day's words; confirming starts the day.
  Review sessions card by card with Again / Hard / Good / Easy; a summary at
  the end; an evening reminder only if work is left.
- `/start`, `/today`, `/review`, `/help`. Repeated deliveries are ignored
  through idempotency keys.
- Runs on a thread inside the app with long polling: no server, no Docker.

### Running in the background

- Tray icon; the close button hides the window only while the bot is on;
  Quit (menu, Ctrl+Q, tray) always quits.
- One instance: a second launch brings the first forward.
- Start with Windows (`--minimized`), `--headless` for the bot alone,
  `--create-shortcut` for a desktop shortcut.

### Files

- A clone keeps `data/` and `logs/` in the project; an installed copy keeps
  them in `%LOCALAPPDATA%\LexiTrack`.
- Daily online backup to `data/backups/`, the newest ten kept, skipped if the
  integrity check fails. Export history writes the review log as CSV.
- The debug log is written only while Debug logging is on: one file a day,
  seven kept, tokens masked.

---

## In Progress

Nothing.

---

## Known Issues

- **"Task was destroyed but it is pending!"** can appear on the console when
  the bot is switched off and on again. It comes from `python-telegram-bot`'s
  shutdown and has no effect on the bot or the data.

---

## Known Limitations

Deliberate; see [DECISIONS.md](DECISIONS.md).

- **The bot runs only while the computer is on.** Telegram keeps undelivered
  updates for a day, so a sleeping laptop catches up.
- **The time zone is fixed** to Europe/Istanbul (a stored setting, no control
  in Settings).
- **FSRS uses its default parameters**; they are not fitted to the user's own
  reviews yet.
- **Word details cannot be edited after adding.** Status and notes can; POS,
  level, definition and example cannot.
- **Learning status is shared across lists**, and one card per word is shared
  across plans.
- **Deleting a list deletes words that are only in it**, with their status.
- **No lemmatization, no language-specific normalization, no OCR.**
- **Windows only, in practice.**

---

## Blockers

None.

---

## Tests

```bash
pytest
pytest -m slow
ruff check lexitrack tests tools
```

**575 run by default and 6 more with `-m slow`** (year-long simulations), lint
clean. Tests needing the real Oxford PDFs in `pdfs/` skip when the files are
absent.

| File | Tests | Covers |
| --- | --- | --- |
| `test_normalizer.py` | 30 | Case, Unicode, apostrophes, hyphens, non-words |
| `test_deduplication.py` | 9 | Collapsing, ordering, metadata merging |
| `test_generic_parser.py` | 16 | Tokenising, punctuation, document failures |
| `test_oxford_parser.py` | 28 | Entry grammar, real PDF quirks |
| `test_database.py` | 22 | Schema, constraints, rollback, the transaction lock |
| `test_migrations.py` | 23 | v1 → v2 → v3, backups, parity with a fresh schema |
| `test_lists.py` | 29 | Lists, membership, language identity |
| `test_json.py` | 36 | JSON parsing, export, round trip |
| `test_import_workflow.py` | 20 | Preview, targets, atomic rollback |
| `test_review_session.py` | 18 | Multi-step Backspace, forward, reset |
| `test_vocabulary_service.py` | 31 | Import, review, export, reset |
| `test_clock.py` | 12 | Learning day, day start, time zone fallback |
| `test_srs_scheduler.py` | 17 | Day steps, snapping, previews, state round trip |
| `test_learning_repositories.py` | 29 | Plans, cards, logs, sessions, settings, keys |
| `test_learning_service.py` | 37 | Intake, queue, review limit, leeches, mastery, missed days |
| `test_simulation.py` | 14 + 6 `slow` | Workload over time |
| `test_telegram.py` | 45 | Owner binding, brief, sessions, idempotency, notifications |
| `test_maintenance.py` | 12 | Backups, integrity check, pruning |
| `test_logging.py` | 9 | Off by default, daily files, token masking |
| `test_paths.py` | 5 | Clone vs installed data folder |
| `test_repository_hygiene.py` | 3 | Git tracks no database, `.env` or log |
| `test_study_ui.py` | 42 | Study page, session, Study Plan, Settings, tray, shortcut |
| `test_ui.py` | 88 | The 0.2 screens, dialogs and themes |

### What has genuinely been verified

- **The migration on the real database**, with its v2 backup kept.
- **The real bot** with the user's own chat: `/start`, the morning brief,
  confirmation, a review session and the summary.
- **Every new screen in both themes**, by rendering and looking; several
  defects were found that way and fixed (unreadable dark-mode buttons,
  stretched key caps, unequal button widths).
- **The public repository after the history rewrite**: the removed backup and
  the old commits are no longer reachable.

---

## Next Exact Steps

1. **Use it for a few weeks** before changing the engine; the defaults came
   from a simulation, not from real reviews.
2. **Fit FSRS parameters** once a few thousand reviews exist in `review_logs`.
3. **Edit word details** (see [TODO.md](TODO.md)).

---

## How to Continue

1. Read this file.
2. Read [ARCHITECTURE.md](ARCHITECTURE.md), especially §10–13 for the learning
   engine, Telegram, the process and files.
3. Read [DECISIONS.md](DECISIONS.md) 42–59 before changing the engine or the bot,
   and [LEARNING_ENGINE.md](LEARNING_ENGINE.md) for the reasoning and numbers.
4. Read the latest [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) entry.
5. Set up and test:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[dev]"
   pytest
   ```

6. Run: `lexitrack`

### Where things live

| Looking for | Go to |
| --- | --- |
| Schema and upgrades | `lexitrack/database/schema.sql`, `learning.sql`, `migrations.py` |
| The learning day | `lexitrack/core/clock.py` |
| FSRS | `lexitrack/services/srs_scheduler.py` |
| Intake, queue, leeches, mastery | `lexitrack/services/learning_service.py` |
| Workload simulation | `lexitrack/services/simulation.py` |
| Backups and pruning | `lexitrack/services/maintenance.py` |
| Telegram | `lexitrack/telegram/` (`core.py` decides, `runtime.py` talks) |
| Paths, logs, autostart, shortcut | `lexitrack/core/` |
| Screens | `lexitrack/ui/study_page.py`, `settings_dialog.py`, `study_plan_dialog.py`, and the 0.2 pages |
| Styles | `lexitrack/ui/theme/` |

### Conventions to keep

- The UI and the bot never issue SQL; both go through `LearningService`.
- Days are local learning days from `DayClock`; timestamps are stored in UTC.
- The token never goes into the database, the log or a test.
- Nothing under `data/` or `logs/` is ever committed.
- Seeing a word never changes its status; navigation never changes status.
- Schema changes need a migration step and keep the parity test passing.
- No feature without a use. Status is never communicated by colour alone.
- Never add an AI co-author trailer to a commit.
