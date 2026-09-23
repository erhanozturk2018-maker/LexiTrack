# Project Status

**Last updated:** 23 September 2026
**Version:** 0.4.0 · schema version 4

---

## Current Phase

**Version 0.4 complete.** LexiTrack now keeps, and shows, the whole record of
the learning: every change of a word's status with its cause, every answer
including those taken back, each word's history from introduction to Known,
and a Progress tab that separates words learned here from words known before.
The scheduler's forecasts are checked against the answers, and its parameters
can be fitted to the user once there are enough of them. Version 0.3 databases
upgrade automatically.

## Overall Progress

| Area | Status |
| --- | --- |
| Schema version 4 and the v3 → v4 upgrade | Done, run on a copy of the real database |
| Status history with causes; answers taken back kept and marked | Done |
| Undo at the desk and on Telegram | Done |
| Study Plan window in words to teach, one rule with the engine, All my lists | Done |
| A word's history | Done |
| Progress tab: groups, pipeline, over time, calibration, every word, every answer | Done |
| First-run setup, How LexiTrack Works | Done |
| Fitting FSRS to the user (optional extra) | Done; the fit itself untested here (no PyTorch) |
| Weekly Telegram summary | Done |
| Clearer wording: the Review tab sorts; the Known-words setting; Reset | Done |
| Everything from 0.3 (engine, Telegram, tray, backups) | Unchanged, still working |
| Tests | 645 (6 marked `slow`) |
| Documentation | Updated for 0.4 |

---

## Completed

Version 0.1 to 0.3 are described in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md);
everything from them still works as described there.

### The record

- **Status history** (`word_status_events`): every change of status with its
  cause — the schedule, a button, the Review tab, Undo — and plan, written in
  the same transaction as the change. The upgrade reconstructs the schedule's
  past Knowns from the review log and marks them so.
- **Every answer kept.** Undo marks an answer undone; counts, calibration and
  fitting leave it out; the history and All answers show it.
- **`params_hash`** on every answer: the parameters that scheduled it.

### Seeing it

- **Progress tab** (Alt+P): learned here, in progress, marked Known by hand,
  known before the plan; where the words stand; introduced and learned over
  time; does the schedule fit you; every studied word; every answer.
- **A word's history**: introduction and plan, every answer, stability after
  each against the Known line, status changes with causes, why it is due now
  and the chance of remembering it — from the details panel, the Study page
  and both Progress tables.
- **Export history** now includes answers taken back and the parameters.

### Doing it

- **Undo**: Ctrl+Z or the footer button in a Study session, the closing
  message after the last card, ↶ Undo on Telegram cards.
- **Study Plan window**: what each list would teach, in progress and known;
  a summary by the engine's own rule; *All my lists*; a warning when a list
  has never been sorted.
- **First-run setup** that creates the plan; **How LexiTrack Works**
  (Shift+F1), its central section identical to the README's.
- **Fitted to you** (Settings → Advanced): readiness against the optimizer's
  512-answer minimum, fitting in the background, a fair comparison, consent,
  one-click revert.
- **Weekly summary** on Telegram, Sunday evening, switchable.

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

- **Fitting needs 512 answers given on later days** and the optional
  optimizer (`pip install "lexitrack[optimizer]"`, which brings PyTorch).
  The fitting call itself has not been run in this environment.
- **Only the last answer can be undone**, once, in the session that gave it.
- **Manual Knowns from before 0.4 have no date**; the upgrade reconstructs
  only the schedule's Knowns.
- **The bot runs only while the computer is on.**
- **The time zone is fixed** to Europe/Istanbul (a stored setting only).
- **Word details cannot be edited after adding**; status and notes can.
- **Learning status is shared across lists**, one card per word across plans.
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

**639 run by default and 6 more with `-m slow`**, lint clean. Tests needing
the real Oxford PDFs in `pdfs/` skip when the files are absent.

| File | Tests | Covers |
| --- | --- | --- |
| `test_normalizer.py` | 30 | Case, Unicode, apostrophes, hyphens, non-words |
| `test_deduplication.py` | 9 | Collapsing, ordering, metadata merging |
| `test_generic_parser.py` | 16 | Tokenising, punctuation, document failures |
| `test_oxford_parser.py` | 28 | Entry grammar, real PDF quirks |
| `test_database.py` | 22 | Schema, constraints, rollback, the transaction lock |
| `test_migrations.py` | 23 | v1 → v2 → v3 → v4, backups, parity with a fresh schema |
| `test_lists.py` | 29 | Lists, membership, language identity |
| `test_json.py` | 36 | JSON parsing, export, round trip |
| `test_import_workflow.py` | 20 | Preview, targets, atomic rollback |
| `test_review_session.py` | 18 | Multi-step Backspace, forward, reset |
| `test_vocabulary_service.py` | 31 | Import, review, export, reset |
| `test_clock.py` | 12 | Learning day, day start, time zone fallback |
| `test_srs_scheduler.py` | 17 | Day steps, snapping, previews, state round trip |
| `test_learning_repositories.py` | 29 | Plans, cards, logs, sessions, settings, keys |
| `test_learning_service.py` | 37 | Intake, queue, review limit, leeches, mastery, missed days |
| `test_progress_history.py` | 33 | Status events, reconstruction, parameters, undo, journeys, Progress views, calibration, fitting readiness and scoring |
| `test_simulation.py` | 14 + 6 `slow` | Workload over time |
| `test_telegram.py` | 53 | Owner binding, brief, sessions, idempotency, undo, notifications, weekly summary |
| `test_maintenance.py` | 12 | Backups, integrity check, pruning |
| `test_logging.py` | 9 | Off by default, daily files, token masking |
| `test_paths.py` | 5 | Clone vs installed data folder |
| `test_repository_hygiene.py` | 3 | Git tracks no database, `.env` or log |
| `test_study_ui.py` | 65 | Study, session and undo, Study Plan window, Settings, Progress, history, first run, help |
| `test_ui.py` | 88 | The 0.2 screens, dialogs and themes |

### What has genuinely been verified

- **The 3 → 4 upgrade on a copy of the real database**: all words, cards and
  answers intact, integrity check clean.
- **Every new and changed screen in both themes**, by rendering and looking;
  the defects found that way are in the development log.
- **Undo on Telegram** through the bot's core with a recorded outbox.
- **Not verified:** a real fit, because PyTorch is not installed here.

---

## Next Exact Steps

1. **Turn on *Keep reviewing words learned here*** so the scheduler's Known
   predictions are tested, and keep studying.
2. **Watch the calibration** on Progress; after about 100 checked answers its
   verdict starts to mean something.
3. **Fit the parameters** once *Fitted to you* says there are 512 answers:
   `pip install "lexitrack[optimizer]"`, then *Fit to my answers*.

---

## How to Continue

1. Read this file.
2. Read [ARCHITECTURE.md](ARCHITECTURE.md): §4 for schema 4, §10 for undo,
   the record and fitting, §14 for the screens.
3. Read [DECISIONS.md](DECISIONS.md) 60–68 before changing any of it.
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
| Schema and upgrades | `lexitrack/database/schema.sql`, `learning.sql`, `progress.sql`, `migrations.py` |
| Status changes and their history | `lexitrack/repositories/state_repository.py` |
| Intake, queue, undo, mastery | `lexitrack/services/learning_service.py` |
| FSRS, recall predictions | `lexitrack/services/srs_scheduler.py` |
| The record: groups, journeys, calibration, the week | `lexitrack/services/progress.py` |
| Fitting to the user | `lexitrack/services/optimizer.py` |
| Telegram | `lexitrack/telegram/` (`core.py` decides, `runtime.py` talks) |
| Screens | `lexitrack/ui/study_page.py`, `progress_page.py`, `study_plan_dialog.py`, `settings_dialog.py`, `components/word_history.py`, `help_dialog.py` |
| The help text | `lexitrack/help/how_lexitrack_works.md` |
| Styles | `lexitrack/ui/theme/` |

### Conventions to keep

- The UI and the bot never issue SQL; both go through the services.
- Every status change goes through `StateRepository`, with its cause.
- Answers are marked, never deleted, except by Reset All Progress.
- Figures shown to the user come from the rule the engine uses, never a copy.
- Days are local learning days from `DayClock`; timestamps are stored in UTC.
- The token never goes into the database, the log or a test.
- Nothing under `data/` or `logs/` is ever committed.
- Schema changes need a migration step and keep the parity test passing.
- No feature without a use. Status is never communicated by colour alone.
- Never add an AI co-author trailer to a commit.
