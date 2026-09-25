# Development Log

Technical record of how LexiTrack was built. Newest entry last. Only findings
that would be expensive to rediscover are recorded.

---

## 2026-09-16 — Phases 1–3: foundations, storage, normalization

**Changes**

- Created the `lexitrack` package with `core`, `models`, `normalization`,
  `parsers`, `repositories`, `database`, `services`, `exporters`, `ui`.
- PEP 621 packaging in `pyproject.toml`; `pip install -e ".[dev]"`.
- `core.paths` resolves the data directory at runtime — `LEXITRACK_DATA_DIR`,
  else `<project>/data` when writable, else the per-user app data directory.
- Exception hierarchy where every message is safe to show a user.
- SQLite schema with four tables, created on first connection.
- Repositories for sources, words and review state.
- `normalize_word` and the runtime deduplicator.

**Files**

`pyproject.toml`, `.gitignore`, `.gitattributes`, `lexitrack/core/*`,
`lexitrack/models/*`, `lexitrack/database/*`, `lexitrack/repositories/*`,
`lexitrack/normalization/*`

**Discoveries**

- The specification's `words.source_id` cannot satisfy its own requirement that
  a word in both Oxford lists be reviewed once. Replaced with a `word_sources`
  junction. See DECISIONS §6.
- `import fitz` is deprecated in PyMuPDF 1.28; `import pymupdf` is the current
  name. Minimum pinned to 1.24.3, where the new name appeared.
- Installed versions: PySide6 6.11.2, PyMuPDF 1.28.2, ReportLab 5.0.1,
  pytest 9.1.1.

**Next step:** inspect the real Oxford PDFs before writing the Oxford parser.

---

## 2026-09-16 — Phases 4–5: parsers

**Changes**

- `Document`: PyMuPDF wrapper with validation and layout-aware line extraction
  including column detection.
- `DocumentParser` ABC, `ParserRegistry` with priority ordering and manual
  override.
- `GenericTextParser`, `OxfordParser`.

**Problem:** the Oxford PDFs were not on the machine at first, so the parser
would have had to be written blind — precisely what §33 of the specification
warns against. The user then supplied both files, and the parser was designed
from the extracted text instead.

**Discoveries — the real structure of the Oxford lists**

Extraction is clean: one entry per line, in correct reading order, no column
interleaving. A CEFR level is a section heading applying to everything beneath
it until the next.

```text
© Oxford University Press      ← furniture
1 / 12                         ← furniture
The Oxford 3000™ by CEFR level ← furniture
The Oxford 3000 is the list…   ← furniture (wraps to a second line in the 5000)
A1                             ← level heading
a, an indefinite article       ← entry
about prep., adv.              ← entry
```

Four quirks, all real and all in the data:

| Quirk | Example |
| --- | --- |
| Parenthesised sense | `bank (money) n.` |
| Sense wrapping across lines | `light (from the` / `sun/a lamp) n.` |
| Superscript homograph digits | `can1`, `can2`, `lie1`, `lie2` |
| Non-breaking space and multi-form entries | `ice\xa0cream n.`, `a,\xa0an indefinite article` |

Also present: 15 distinct part-of-speech abbreviations, separated by either
`,` or `/`; and two typographical errors in the source (`n.,adj.` with no
space, `v. , n., adj.` with an extra one), both of which the splitter absorbs.

Neither document contains definitions or example sentences. Those fields stay
`None` for this source — documented rather than faked.

**Validation**

```text
Oxford 3000: 3309 entries parsed, 2978 unique, levels A1–B2, 0 lines dropped
Oxford 5000: 2015 entries parsed, 1996 unique, levels B2–C1, 0 lines dropped
```

Every line of both documents is an entry, a level heading or known furniture.
Nothing is silently discarded.

**Next step:** services, then the UI.

---

## 2026-09-16 — Phases 6 and 9: services and exporters

**Changes**

- `ImportService` (pipeline, progress, cancellation), `VocabularyService`
  (the UI's facade), `ExportService`.
- CSV exporter (UTF-8 BOM for Excel) and PDF exporter (ReportLab).

**Problem:** `ImportResult.new_links` counted updates as inserts, because
SQLite reports `rowcount == 1` for the UPDATE branch of an upsert. Fixed by
checking for the link before writing.

**Verification — full pipeline against the real PDFs**

```text
import 3000  → 2978 new,    0 existing
import 5000  → 1975 new,   21 existing   ← the lists overlap by 21 words
total                        4953 items
re-import 3000 →   0 new, 2978 existing, progress untouched
resume (new service, same file) → identical position and counters
```

**Next step:** the UI.

---

## 2026-09-16 — Phases 7–8: interface and themes

**Changes**

- `MainWindow` with a three-page stack, app bar, menus and stats bar.
- `ReviewWidget`, `ProgressBarWidget`, `StatsBar`, `WelcomeState`,
  `CompletedState`, `ImportDialog` with a `QThread` worker.
- Theme system: two `Palette` dataclasses, `build_stylesheet`, `ThemeManager`
  persisting to `QSettings`.

**Problems found by actually launching it**

Three rendering bugs, none of which would have surfaced without running the
application and looking at it:

1. **Labels painted chips.** `QWidget { background-color: … }` is inherited by
   `QLabel`, so every label on a non-window surface drew a visible rectangle.
   Fixed with `QLabel { background: transparent; }`.
2. **The stats bar had no background.** A plain `QWidget` subclass does not
   paint a stylesheet background unless `WA_StyledBackground` is set. Set it on
   `StatsBar`; deliberately *not* on `_Stat`, which should show the bar through.
3. **Wrapped text was clipped and overlapped.** Qt sizes a word-wrapped
   `QLabel` as if the text had unlimited width, and adding an alignment flag
   stops the layout consulting `heightForWidth` entirely. Fixed by
   `WrappedLabel`, which pins the width and recomputes its minimum height on
   text, font and style changes — the last part matters because the label is
   built before the stylesheet's font is applied.

Two further fixes:

- The card was squeezed to ~455px by the centring stretches. Replaced the bare
  stretches with stretch factors.
- The checked radio indicator rendered as a square: a 5px border on a 16px box
  defeats `border-radius`. Replaced the ring with a filled circle.

Also removed the colour emoji from the welcome screen — it clashed with an
otherwise restrained design — and dropped the sense line from the review card,
because deduplication merges senses and showing one would misrepresent the
question.

**Verification:** driven headlessly through import, all three screens, every
shortcut, undo, both themes and export, with screenshots captured at each step.

**Next step:** tests.

---

## 2026-09-16 — Phase 10: tests

**Changes**

- 162 tests across seven files. Parser tests build real PDFs with PyMuPDF
  rather than stubbing extraction.
- `test_no_line_of_a_real_document_is_silently_dropped` asserts that every line
  of both real PDFs is recognised — the regression test for a future edition
  changing layout.
- UI tests drive real widgets via `pytest-qt`.

**Problem:** the scanned-PDF heuristic (20 characters per page) rejected
legitimately short documents — a one-page word list, or a fixture reading
`ability`. There is no threshold that separates "short" from "scanned", because
they are not on the same axis. Replaced with presence-of-text: no letters at
all across the first ten pages. A scan carrying only page numbers now falls
through to `NoWordsFoundError`, which is equally clear and needs no constant.

**Problem:** one test called `get_next_word()` twice before marking anything,
so it answered the same word twice. A bug in the test, not the product.

**Result:** 162 passing, `ruff check` clean.

---

## 2026-09-16 — Phases 11–12: polish and documentation

**Changes**

- README with a screenshot walkthrough of every screen, plus dark-mode variants
  and a rendered page of an exported study sheet.
- `docs/ARCHITECTURE.md`, `DECISIONS.md`, `PROJECT_STATUS.md`, `TODO.md` and
  this log.
- Export subtitles now use thousands separators, matching the UI.

**Repository hygiene**

`.gitignore` covers `.venv/`, caches, build artefacts, the runtime database and
logs, and `pdfs/*` — vocabulary PDFs are the user's own copies and may be
copyrighted. `data/`, `data/exports/` and `pdfs/` are kept in the tree via
`.gitkeep` so a clean clone has somewhere to write. `.gitattributes` normalises
line endings. Verified that no virtual environment, database, log or PDF is
tracked.

**Final state:** 162 tests passing, lint clean, application verified end to end
against the real Oxford documents.

---

# Version 0.2

## 2026-09-16 — Inspection before change

**Findings**

- The user's real `data/vocabulary.db` was at schema 1 with 4,953 words, 812
  known, 118 unknown and 2 sources. Backed up to the scratchpad before any
  work; all migration testing used copies.
- **Where "Oxford 3000" on the card came from:** `OxfordParser.source_name()`
  matches `The\s+Oxford\s+(3000|5000)` on page 1's extracted text →
  `sources.name` → `GROUP_CONCAT` in `WordRepository._SELECT_WORD` →
  `StoredWord.source_label` → `#SourceChip` at the top left of the card.
  Provenance, presented as if it were the learning context.
- `ThemeManager` hard-coded `QSettings("LexiTrack", "LexiTrack")`, so the theme
  tests had been writing to the developer's real settings.

**Plan:** data layer and migration first (independent of any design), then
JSON and the import workflow, then the review session, then components, design
exploration, and the UI last.

---

## 2026-09-16 — Lists, language identity, migration

**Changes:** schema v2, `migrations.py`, `ListRepository`, language on
`WordEntry` and `words`, nested transactions in `Database`, language-aware
runtime deduplication.

**Problems and solutions**

- SQLite cannot change a `UNIQUE` constraint in place. `words` is rebuilt with
  explicit ids so every foreign key stays valid.
- `schema.sql` used to run on every connect (`CREATE … IF NOT EXISTS`). Against
  a v1 database that would skip `words` and then fail creating an index on
  `language`. It now runs only on an empty database.
- The deduplicator keyed on the spelling alone, so a mixed JSON file with
  English "gift" and German "Gift" would have merged them. Keyed on
  `(language, normalized_word)` now.
- The first commit was checked for bisectability in a separate worktree; one
  migration test used the not-yet-committed service API and was moved to the
  next commit.

**Verification:** migrated a copy of the real database — all ids, spellings,
statuses and `reviewed_at` values identical; reopening a no-op.

---

## 2026-09-16 — JSON, import workflow, review session

**Changes:** `JsonDocument`, `open_document`, `JsonParser`, `json_exporter`,
`ImportService.prepare/check/resolve_language/commit`, `ReviewSession`,
`ExportService` scopes.

**Problems and solutions**

- Parsers only knew PDFs. Rather than a separate JSON import path, parsers
  declare `document_types` and the registry filters by them.
- A rollback test initially failed before writing anything (duplicate list
  name), so it did not prove atomicity. Replaced with one that fails after the
  list, source and words are written.
- `ImportResult.new_links` from 0.1 still relies on checking the link before
  the upsert; kept.

---

## 2026-09-16 — Components and design exploration

**Changes:** `VocabularyTable`, `StatusDelegate`, `StatusBadge`, `ListCard`,
`SegmentedProgress`, `StatTile`, `ModeSwitch`; `component_styles.py`; themed
SVG icons; `tools/design_mockups.py`; example JSON lists.

**Rendering problems found only by looking at screenshots**

| Problem | Cause | Fix |
| --- | --- | --- |
| Status column showed plain text | PySide returns a `StrEnum` from item data as `str`; `isinstance` failed | Role carries `status.value` |
| Status filter matched nothing | Same, compared with `is` | Convert and compare with `!=` |
| "Jnknown Word:" | Checked tab turned bold; width measured at normal weight | Same weight in both states |
| Broken combo arrows | Styling the drop-down removes Fusion's arrow | Themed chevron SVGs |
| All-unknown list shown as "100%" | Single reviewed bar | Two-tone known/unknown bar |

**Decision:** design A, without a separate Lists tab (the user agreed).

---

## 2026-09-16 — Screens, dialogs, cleanup

**Changes:** `HomePage`, `ReviewPage`, `UnknownPage`, `MainWindow`,
`ListActions`, `ImportDialog` rewrite, `ExportDialog`, list/word/add-word
dialogs, migration notice at startup.

**Problems and solutions**

- First draft of `ReviewPage` constructed a `WordRepository` in the UI. Replaced
  with `VocabularyService.get_words`.
- The R shortcut checked `isVisible()`, which is false whenever the window is
  hidden. Uses `isHidden()`.
- Fusion underlines `&` mnemonics permanently; tabs use explicit Alt shortcuts.
- Import panels painted a grey block (plain `QWidget` inside a panel) and had
  an unlabeled checkbox. `#PanelBody` is transparent; the file name is the
  checkbox label; checkboxes get a themed tick.

**Cleanup (user asked for no unused features):** vulture scan, then removed
PDF layout extraction and column detection, `ProgressBarWidget`, a no-op
signal handler, a one-item Review menu, `run_guarded`, `set_title`,
`visible_count`, `return_to_live`, `contains`, `lists_for_word`,
`accepts_any_language`, `Source.word_count`, `Document.metadata`,
`CEFR_LEVELS`, and unused palette tokens. Remaining vulture hits are tested
public API.

**Verification:** 308 tests passing; the upgraded copy of the real database
opened in the new UI, resumed at the next unreviewed word, and Backspace left
statuses unchanged in the database.

---

## 2026-09-17 — Interface audit, export preview, notes and definitions

**Asked for.** An export flow with a preview and a choice of alphabetical or
CEFR order; a critique of the selection bar and the other windows; the
approved proposals: floating selection bar, Unknown Words cleanup, details
panel, Ctrl+K palette, simpler app bar, shortcuts collected in one scrollable
window instead of printed under the flashcard; definitions in the PDF and a
note or definition for every word.

**Done.**

- `ExportDialog` rewritten as settings plus live preview; `ExportOrder`,
  `order_words`; remembered format and order; result as a toast with Open
  Folder (`notify` in `toast.py`).
- `VocabularyTable`: floating selection bar with More ▾, CEFR level chips,
  `WordPanel` replacing `WordDialog`; the table sits in `#TableFrame` whose
  bottom strip opens while the bar shows.
- `CommandPalette`, `ShortcutsDialog`, `MainWindow.commands()`; menu bar
  replaced by a "⋯" menu; theme toggle is an icon; the Unknown tile on Home is
  clickable; the flashcard no longer prints shortcuts.
- `StoredWord.note`; JSON `note`; CSV Note column; PDF definition column with
  the note beneath and `group_by_level` headings.
- Definitions written for all 6,825 words in the user's database, delivered as
  `lexitrack_definitions.json` for import. Trial import on a copy: 6,825
  definitions, no new words, no status changed.

**Problems and solutions**

- `QTableView` resets its viewport margins in `updateGeometries`, and QSS
  padding does not reach the viewport, so neither could reserve room under
  the floating bar. The view now sits in a frame with a spacer that grows.
- The PDF level heading had `keepWithNext`; with a level pages long, ReportLab
  moved the heading to page two and left page one blank. Replaced with a
  conditional page break; a test fails without the fix.
- With "Remember these settings" on by default, a scope's own default format
  was ignored before any format had ever been chosen. The dialog now follows
  scope defaults until a format is chosen or remembered.

**Verification:** 343 tests passing, lint clean; screenshots regenerated;
dialogs, palette and PDF inspected in both themes.


# Version 0.3

## 2026-09-17 — Designing the learning engine

**Asked for.** An honest review of a proposal to turn LexiTrack into a
learning engine: 25 new words a day from study plans, FSRS reviews, a Telegram
bot as the daily client, and FastAPI and Docker around it.

**Decided.** The engine, the plans and FSRS as proposed; 25 a day is fixed,
and a simulation replaced a first suggestion to lower it. The bot became a
thread inside the app rather than a client of a local server, because two
processes on one SQLite file would be two writers. Days end at 00:00
Europe/Istanbul; the morning message is at 06:00. The meaning is hidden only
in study reviews. Word-note editing was dropped. The result is
[LEARNING_ENGINE.md](LEARNING_ENGINE.md), with its process lifecycle (tray,
bot switch, Start with Windows, single instance).

## 2026-09-17 — Phase 1: schema 3, clock, scheduler, persistence

**Done.**

- Schema version 3 from one file, `learning.sql`, run by both a new database
  and the 2 → 3 migration, which also seeds settings and creates one plan from
  the largest list. `executescript` would commit the migration's transaction,
  so the file is run statement by statement.
- `DayClock` and `FrozenClock`; `SrsScheduler` over `fsrs` 6 with day steps,
  day snapping and no fuzz; repositories for plans, cards and review logs,
  sessions and Telegram keys, settings and runtime state.
- `LearningService` for the day, the queue, answers and mastery;
  `WorkloadSimulator` in memory.

**Problems and solutions**

- The forecast grouped cards by SQL `DATE(due_at)`, which is a UTC date, so a
  card due at 01:00 Istanbul landed on the previous day. The repository now
  returns instants and the clock buckets them.
- `refresh_settings` replaced the shared clock with a copy, so the test's
  frozen clock stopped advancing for the service. The clock is now
  reconfigured in place.
- The first leech rule flagged almost every new word (stability starts low).
  The weak-stability rule now waits for four reviews, with hysteresis on the
  way out.
- A year's simulation took 15 seconds per run; the long ones are marked
  `slow` and run with `pytest -m slow`.
- Simulated, 6,825 words at 25 a day: a typical learner peaks at 250 reviews
  a day and settles near 110. That set the review limit.

## 2026-09-18 — Phases 2–4: Study, Telegram, the tray, housekeeping

**Done.**

- The Study tab, the Study Plan window and Settings; export and copy of the
  day's words; words you find hard; the last 30 days.
- `Database.transaction()` now holds a real reentrant lock. The docstring had
  promised one; a test with two threads failed without it.
- Telegram in `lexitrack/telegram/`: `.env` configuration, messages as data,
  `BotCore` behind an outbox, notifications as once-a-day decisions, a polling
  runtime; the Qt controller and a Telegram page in Settings.
- Tray, close-to-tray while the bot is on, single instance over a local
  socket, Start with Windows, `--minimized`, `--headless`, an app icon.
- Daily online backups (ten kept), `quick_check` at start-up, key pruning,
  review history as CSV. Reset All Progress now also clears the schedule.

**The first real bot test**, and what it found:

- `/start` was sent before the bot ran and never arrived; resent, it bound the
  chat. The day's list then came twice: `/start` sends it and the tick sent it
  again. `/start` now counts as the morning message.
- `httpx` logs every request URL at INFO, and the token is in the URL, so it
  went into the log every ten seconds. HTTP loggers are now at WARNING and
  every line is masked. The regression test was first written with the real
  token's secret behind a fake bot id; that commit was pushed, the token was
  revoked, and the test now uses a fake value.
- "Quit" in the menu called `close()`, which hides to the tray while the bot
  is on, so the app kept running with the old code. Quit now quits.
- The log was written whether wanted or not. It is now off unless Debug
  logging is on, one file a day in `logs/`, seven kept.

## 2026-09-18 — Design pass, install paths, and a leaked backup

**Done.**

- The new screens redone in Home's language (DECISIONS §58): a Today panel with
  one primary button, chips for the day's words, a week of day tiles, the
  flashcards' answer colours; Settings as titled groups of explained rows; the
  Study Plan list with progress bars. Buttons lost their ellipsis.
- `lexitrack --create-shortcut` and `app.ico`; an AppUserModelID for the
  taskbar.
- An installed copy keeps its data in `%LOCALAPPDATA%\LexiTrack`: the old rule
  would have used `site-packages`.
- README, screenshots and these documents brought up to 0.3; version 0.3.0.

**Problems and solutions**

- Qt ignores `:hover` on an ancestor in a descendant selector, so the answer
  buttons' hover colours applied all the time and two labels were unreadable
  in dark mode. Hover now changes only the border.
- A final audit found `data/backups/vocabulary-2026-09-18.db` in the public
  repository: `data/*.db` did not match subfolders. It held the word list with
  statuses and paths with the Windows user name, but no token. The file and
  the revoked token's secret were removed from every commit with
  `git filter-repo`, all 46 commits keeping their messages and dates; the
  GitHub repository was deleted and recreated, so no cached copy remains.
  `data/` is now ignored as a whole and a test checks what git tracks.

**Verification:** 581 tests (6 of them `slow`), lint clean; every new screen
rendered in both themes; the first bot test on the real database; the public
repository checked from outside after the rewrite.

## 2026-09-19 — Hidden meanings in Telegram reviews

**Found in use.** The first card's meaning was hidden, but once tapped, every
later card showed its meaning uncovered. Each card was an edit of the same
message, and Telegram keeps a revealed spoiler open through later edits of
that message.

**Done.** Each card is now sent as a new message and the answered one is
deleted, so the chat still shows one card at a time. A message that cannot be
deleted keeps its text but loses its buttons. The last card still becomes the
session summary.

**Verification:** 576 tests (a new one pins that a card is never an edit of
the previous one), lint clean.

## 2026-09-20 — Saying plainly what an answer costs

**Found in use.** Two rounds of questions about the same thing: a card back in
`review` after an Again looks like recovered progress, and the state tables
did not say that Known reads stability and nothing else. A related worry was
whether a failed word comes back as a new word and eats one of the day's 25.

**Done.** ARCHITECTURE §10 now states that the state is not progress, and
carries a measured lapse table — 11.0 → 1.5 days on the first Again, Known
arriving on day 29 instead of day 14, the second and third lapses recovering
more slowly and flagging the word as struggling. The README gained *How a word
is learned* with the same numbers for the reader who never opens `docs/`.

Every figure was taken by running `SrsScheduler` rather than reasoned about:
an earlier draft of the state table claimed Hard takes a card out of
relearning, which the run disproved.

**Verification:** numbers reproduced from the scheduler; no code changed.

## 2026-09-20 — The schedule outlook panel, built and taken out again

**Asked for, then withdrawn.** A group at the bottom of Settings → Learning
computed three answering paths through the real scheduler and showed which
days a word would be asked on and when it would be Known, following the
controls before Save. On seeing it, the reading was that the documentation
covers this and the page did not need it; it was reverted the same day.

**Kept as the lesson**: the questions it answered were real, and they are now
answered in ARCHITECTURE §10 and the README's *How a word is learned*. If the
panel ever comes back, `git show f3539ce` has the whole of it — a pure
`services/schedule_preview.py` walking a throwaway card, with tests.

**Verification:** reverted, lint clean, 576 tests.


# Version 0.4

## 2026-09-22 — Asked for: see what was learned, and how

**Asked for.** A way to follow the learning: which words went from Unknown to
Known, when and through which answers; the words failed again and again; the
difference between words known before and words learned through the plan;
transparency about everything the app records, the review log included; and
the scheduler trained on the user's own data. Also: the Study Plan window
looked as if it took every word of every list, and a new user would not know
how the app worked.

**What an audit of the existing data found.**

- `review_logs` already held every Study and Telegram answer with the state
  before and after and the stability; `srs_cards` held the introduction date
  and plan. A word's path to Known was there, unshown.
- **The gap:** `user_word_state` held only the latest status. When a word
  became Known, and whether by the schedule or a click, was not recorded.
- Answers on the Review tab and status buttons are not answers to the
  scheduler and never reached `review_logs`; Reset All Progress deleted the
  whole log without saying it was also the training data.
- `params_hash` existed on every log row and was never filled.
- Fitting FSRS was not built: the installed `fsrs` has an optimizer, but it
  needs PyTorch and silently returns the defaults below 512 answers given on
  a later day than the word's previous answer. The user had 174 answers over
  four days; with *Keep reviewing known words* off, Known words were never
  asked again, so no prediction about them could ever be tested.
- The setting's own wording was why it was off: it read as if it would pull
  4,907 long-known words into the schedule. It never could; they have no card.
- The Study Plan window counted every word not Known as "still to learn",
  words already being learned included.

## 2026-09-22 to 23 — Done

In order, each step committed and, for every screen, rendered in both themes
and reviewed before the next.

1. **Schema 4.** `word_status_events` (cause and plan, written with the
   status), `review_logs.undone_at`, `study_plans.all_lists`; the 3 → 4
   upgrade reconstructs mastery events from the log. `params_hash` filled.
2. **Wording.** The Review tab reads SORTING and says what it is for; the
   setting reads *Keep reviewing words learned here*; Reset names the answers
   and history it deletes and points at Export history.
3. **Study Plan window** in words to teach, one scope rule with the engine,
   *All my lists*, and a warning on lists nobody has sorted.
4. **Undo** at the desk (the card's footer names the answer; Ctrl+Z; the
   closing message) and on Telegram (every card after the first).
5. **A word's history**, from the details panel, the Study page and Progress.
6. **Progress tab**: groups, where the words are, over time, calibration,
   every word, every answer.
7. **First-run setup** and **How LexiTrack Works**.
8. **Fitted to you**: readiness, fitting on a worker thread, a fair
   comparison, consent, revert; the optimizer as an optional extra.
9. **Weekly summary** on Telegram.

**Problems and solutions**

- The Study Plan window, rendered, put *All my lists* inside the scrolling
  list, and the window opens scrolled to the plan's own list: the new option
  was out of sight. It now sits above the list.
- Rows stood aside under *All my lists* but still looked selectable; a
  `:disabled` rule on the row did nothing to its labels, because Qt ignores
  pseudo-states on an ancestor in a descendant selector (the 0.3 lesson). The
  labels carry the rule themselves.
- The first-run choices were clipped: centred by alignment, the column got
  its size hint, and wrapped text measured at another width left too little
  height. A fixed width fixed it.
- The Undo button was drawn at button size in a footer of 12-pixel text, and
  "Undo Easy on a" read as a sentence; it is footer-sized and quotes the word.
- A test hung: choosing "some of my lists" opens the real, modal Study Plan
  window. That path is tested on the page alone.
- Fitting on a worker thread must not touch the shared SQLite connection, and
  closing Settings mid-fit must not destroy a running thread: the answers are
  read first, under the lock, and the thread belongs to the application.
- Screenshots: the Progress page grabbed on its own came out on black (the
  page is transparent), and the sample's setup, marked on today's wall clock,
  put a stray "Marked Unknown by hand" into every word's history. The tool
  paints the page background and clears the setup's events.
- The history of a word with no card told an Unknown word it would be offered
  "once it is Unknown". Each status now gets its own sentence.

**Verification:** 645 tests (6 `slow`), lint clean; the 3 → 4 upgrade run on a
copy of the real database; every new and changed screen rendered in both
themes. Not verified: an actual fit, because PyTorch is not installed here —
readiness, scoring, apply and revert are tested, the optimizer call is not.

## 2026-09-25 — Learning Engine V2, phase 1: the data for content and skill

Schema 5, data only; nothing on screen changes.

- `word_content` (a Turkish core meaning, nuance, pattern, collocations,
  register, an encoding type and cue, related words, a depth hint) and
  `word_contexts` (sentences and situations marking the word as `{{word}}`).
  Both optional: a word with neither is taught by the short route.
- `learning_attempts`, the skill record: every retrieval with its task, level
  1–5, phase, role (primary, probe, retrieval), context, effort, and the
  review log row it belongs to. Append-only, marked on undo.
- `review_logs.memory_result` and `route_version`; existing logs are `v1`
  with no memory result, so no skill evidence is invented for them.
- `review_sessions.flow_state` for a resumable, serialisable study flow.
- Each migration step now counts the rows of every vocabulary, list, card,
  log and status table before and after, and runs `quick_check`, before it
  commits.
- `ContentService`: export a batch of words that need content, validate and
  preview a filled file, import it (docs/formats/content-enrichment.md).
  Entries are matched by id *and* spelling; conflicts are replaced only when
  chosen; the word-list parser refuses content files.

**Verification:** the 4 → 5 upgrade on a copy of the real database: 6,825
words, 225 cards and 315 logs before and after, no settings changed,
integrity and foreign keys clean; the copy deleted.

## 2026-09-25 — A sidebar instead of tabs

Asked for: the tabs side by side looked crowded and dated, Progress was not
clear, there was no way to choose what to export, and the window layouts
needed work. A proposal with the current screens was approved in full; the
navigation, window layout and naming come first (decision 69), the Today
page, Progress and an export centre with the V2 phases that change them.

**Problems and solutions**

- With the sidebar, List mode at the default size needed 620 px of columns
  in 564: it scrolled sideways and cut the status column off. A stretching
  word column alone left the word 34 px wide once *Also in* was showing.
  Columns now fit by rule, shrinking to floors, with the word taking the rest.
- The `#` column is measured to its widest number; the fitting reset it to
  56 px until the measured width became its natural width.
- The selection bar, wider than the table, covered the details panel's
  buttons. Reserving room squeezed the panel into a scroll with half-cut
  buttons; hiding the buttons while the bar (with the same actions) covers
  them reads cleanly.
- The Lists page, rendered twice, showed two sets of cards: cards removed with
  `deleteLater` are painted until the event loop deletes them. Every rebuilt
  row, card and chip is now hidden as it is removed.
- The Review stats bar cut "2,978" to "2,97": laid out around "0", the label
  kept its old width for a frame. The value reserves its width when set.
- A plain `QWidget` in the sidebar's foot painted the window colour as a band;
  it is transparent by name.

**Verification:** full suite and lint clean; every page rendered in both
themes, at the default size, folded (880 px) and wide (1720 px); README
screenshots regenerated.

## 2026-09-25 — Learning Engine V2, phase 2: the session leaves the page

The review session lived in the Today page: queue, position, reveal, count,
Undo. It moved, unchanged, into `StudyFlow`, a state machine with no widgets
that saves itself on the session row after every step and can be restored.
The page's existing tests passed without a change, which is the check that
behaviour is the same; new tests cover the flow alone, its saved state,
restoring it, and a guard that the page holds no session state again.

## 2026-09-25 — Learning Engine V2, phase 3: skill, from the record

`SkillTracker` derives a word's stage — encountered, recognised, recalled,
productive — and automaticity evidence from `learning_attempts`, recomputed on
every read so it cannot drift and Undo shows at once. Every answer now writes
its attempt; the card, log, attempt, session count and status change of one
answer are one transaction, where they used to be separate writes. Reset
clears attempts with the logs they belonged to.

Old answers are not copied into attempts: the tracker reads them from the log
as recognition, the only thing they measured.

## 2026-09-25 — Learning Engine V2, phase 4: review route V2

Decided with the user first: L2–L3 answers are **typed** and checked, a
sentence (L5) is written and self-graded, and reaching long-term memory
**suggests** Known instead of marking it (decision 70, its own commit).

Built in layers, each tested before the next: the rules
(`review_route`), the engine (`review`, `record_practice`, one rating per
word per day for every client), the session (`ReviewFlow`), and the card
(`components/review_card.py`). Telegram still reviews the V1 way until
phase 12.

**Problems and solutions**

- Planned probe order was contaminated: after "meaning → type the word"
  fails, a "word shown, meaning hidden" probe asks about a meaning just
  read. The level 1 probe is choosing the word among four instead; the word
  is never shown before it.
- 405 of 6,825 definitions contain their own word ("showing reluctance");
  prompts hide the word's forms.
- Two slips were accepted from nine letters, which let "relxxtant" pass for
  *reluctant*; two now needs eleven letters.
- Enter in the answer box reached the page after submitting and moved past
  the feedback it had just produced; the box takes Enter itself.
- A successful probe was shown with a plain ✓, as if recalled; after a
  probe the line now says what the memory result says.
- The choice buttons were drawn 6 px taller than laid out (a stylesheet
  minimum overriding the fixed height) and cut off by the footer; a label
  kept a previous answer's colour because only its button was repolished.
- A new test module set the offscreen platform for the whole run and broke
  a layout test elsewhere; it no longer does.

**Verification:** full suite and lint clean; every face of the card
rendered in both themes; README screenshot regenerated.

## 2026-09-25 — Learning Engine V2, phase 5: the TaskSelector

The first question of a review now comes from the word's record: harder by
one level after an effortless success, the same after effort or a miss,
easier after forgetting, never harder below a 75 % chance of recall, and only
what the content can ask. Each choice carries a reason, shown on the card.

**Problem and solution:** stepping over levels with nothing to ask from sent
a word with only a definition from "meaning → word" straight to "write a
sentence" after one easy answer. A sentence of one's own now needs an example
sentence to compare it with, so a word without content stays on the short
route, as the design says.

## 2026-09-25 — English samples, and a language-neutral content model

Asked for: everything as English as possible, because the app is meant for
anyone; and a data model with no learner language built in.

- **Samples.** The V2 commits had Turkish sample text (the format doc's
  example, test data, and the README screenshot's sample meanings). At the
  user's choice the seven commits were rewritten with English samples — each
  commit differing from its original only in those files — and the README
  screenshot now shows a sample word asked from its English definition.
- **Schema 6.** Teaching content is split into target-language rows shared by
  every learner and learner-language rows keyed by a language code
  (decision 71). The enrichment format is schema 2, with a block per learner
  language; schema 1 files are read as `tr`. Settings has *Explain words
  in*. Tests prove two learner languages on one word without a second word
  record, and that the upgrade moves schema 5 content into `tr`.

**Found on the way:** the Learning settings page said "Times are in
Europe/Istanbul" whatever the setting was; it now shows the setting. The
time zone itself still defaults to Europe/Istanbul — a question for the
user, not a wording fix.

**Verification:** full suite and lint clean; the 5 → 6 upgrade run on a copy
of the real database (no row lost, no setting changed, one setting added,
integrity and foreign keys clean, copy deleted).

## 2026-09-25 — Learning Engine V2, phase 6: first learning, and the Today card

"I studied these 25" is replaced by learning the words in the session:
reviews first, then new words four at a time — taught at a depth their
content decides (SHORT, LIGHT, DEEP), asked, maybe asked again later, taught
again deeper after a miss, twice at most. Nothing of it is rated; each word
gets its card when its steps are done. "Mark as studied" keeps the old way
for words studied elsewhere.

The Today page is the approved proposal: one card — "40 words · about
15 min", the split, one button — the new words to look over, the week, and
the hard words. The 30-day numbers left it; Progress has them.

**Problem and solution:** a review run needed a card, and a new word has
none until it is learned; runs now carry their word, and a card only when
there is one.

## 2026-09-25 — Learning Engine V2, phase 7: the queue and the workload

Due words are sorted into fragile, at risk and normal; over the limit (never
above 250) the fragile and the least remembered are kept; a warm-up of easy
words opens the session and fragile words come at most one in four; new
words shrink when tomorrow would overflow. Settings shows the limit that
applies and the two order settings; the Today card says what waits.

**Problems and solutions**

- Known words were removed from the queue after it was cut at the limit, so
  a day could come in under its limit; they are now removed first.
- The FSRS fitting test drew its answers by position in the queue, so a new
  order changed its data and its verdict. It now draws each answer from the
  default model's own chance of recall, with a fixed seed — the data the
  test's claim is actually about.
- A test's expected order of 13 words was miscounted; the order was right.

## 2026-09-25 — Learning Engine V2, phase 8: the content workflow in the app

*Word Content…* (Export and backup, the ⋯ menu, Ctrl+K): how much of the
chosen words has content for a language; the next batch to export — from the
plan, today's new words, a list, or a table selection (*Export for
Content…*); the filled file to import, previewed, with conflicts unticked
until chosen; and the batches still out. Batches are resumable: their words
wait, importing closes them, forgetting frees them. The settings rows became
a shared component (`components/settings_rows.py`) so this window looks like
Settings.

## 2026-09-25 — Learning Engine V2, phase 9: export and backup

*Export and backup* in the sidebar is a window of its own instead of a menu:
words chosen by list, status, CEFR level and learning state
(`services/word_filter.py`) exported through the existing preview; word
content; every answer and every attempt as CSV (the answer CSV gains the
memory result and the route); the daily copy; a portable `.lexitrack` file
(`services/portable.py`); and restoring either, with a safety copy first.

**Problems and solutions**

- Qt returned the filter combos' enum values as plain strings, so "Any"
  compared unequal to `LearningState.ANY` and filtered everything out. The
  window turns them back into enums before building the filter.
- Restoring rows in table order could trip foreign keys midway; checks are
  deferred to the end of the transaction and a `foreign_key_check` runs
  before commit, so a file with broken references changes nothing.

## 2026-09-25 — Learning Engine V2, phase 10: teaching content in exports

Word exports take columns: the dictionary's three, and the teaching content
in the learner's language (`exporters/sheet.py`), read for all words at once
(`ContentRepository.teachings`). The PDF sets teaching content as entries,
examples with the word in bold and translations beneath; the CSV adds a cell
per field. The export dialog ticks columns and says how many words have
content; the export centre picks several lists, filters by content, and
limits learning data to a period.

**Problems and solutions**

- Helvetica cannot set Turkish letters; the sheet is now set in the Vera
  fonts ReportLab ships, embedded, with a system font taken only for scripts
  Vera lacks.
- The entry labels sat 6 pt left of the word: a table as wide as the frame
  overhangs its padding, so the entry's fields take the same cell inset as
  the word table.
- The columns block drew on the window colour inside the white settings
  column; it now takes the transparent panel style.

## 2026-09-25 — fix: long headwords were cut off on the card

A phrase such as "departures and arrivals board" wrapped onto two lines at
the card's 58 px, but the label was laid out one line tall, so both lines
were clipped. The headword is now a `WordLabel` (`components/word_label.py`):
it steps its size down until the text fits on at most two lines, and asks for
the height those lines need. The session card and Sort words both use it.

## 2026-09-25 — Learning Engine V2, phase 11: progress, memory and skill

Progress is four tabs. The Overview adds the words ready to mark Known (with
Mark Known, and Mark all), memory and skill as two bars with the evidence
beside them, and the last 30 days, which left the Today page in phase 6.
Words gains a skill column and a Ready for Known filter; Answers says what
each answer asked and what it showed, filters, and exports CSV; Scheduler is
the calibration on its own. A word's history names its skill and, for each
answer, what was asked. `ProgressService` gained `skill_overview`, `recent`
and a long-term stage in the memory pipeline.

**Problems and solutions**

- The tabs lived in a stacked widget, which is as tall as its tallest page:
  the short Scheduler tab was spread down the height of the Answers table.
  The tabs now share the column and only the shown one is visible.
- All table columns stretched equally, so "Recall the meaning" wrapped onto
  two lines; the text columns now take the spare width, the rest fit their
  content, and cells do not wrap.
- The memory pipeline sorted by "under 3 days" before the long-term
  threshold, so with a low threshold a word ready for Known was counted as
  fragile. The threshold is checked first.
- `setProperty("size", ...)` collides with `QWidget.size` and never applied.
  Rendered before and after, the header text buttons are now compact and the
  Details toggle keeps its height; see decision 75.
- The Known suggestions took eight tall rows and pushed memory and skill off
  the first screen: five compact rows now, and a link to the rest in Words.

## 2026-09-25 — Learning Engine V2, phase 12: Telegram on the review flow

The bot runs `ReviewFlow` on the Telegram channel: reviews first, then new
words taught and practised; typed steps answered by reply, choose-among-four
and ratings by buttons, a sentence written and graded. Each card opens with
how the last answer went (the wording shared with the desktop, moved to
`services/review_wording.py`), offers Mark Known when a word reaches
long-term memory, and carries Undo and Stop. Every button names its step;
the flow's state is saved each step, so a restart, a double tap and an old
card are all harmless. `StudyFlow` is removed.

**Problems and solutions**

- The session summary counted from the session's counter, which included an
  answer taken back; it now counts the record, leaving undone answers out.
- Phone answer times would have read as effort and rated honest answers
  Hard; the bot records no time (decision 76).
- A tap arriving after a restart could have answered a different question
  than the one on screen; it now shows the current step instead.

## 2026-09-25 — Learning Engine V2, phase 13: mobile preparation

Verified rather than built: the learning modules import with Qt, the PDF
libraries and the Telegram library blocked; they reach no platform glue;
a session's state is JSON of ids. `tests/test_mobile_ready.py` holds all
three.

**Problems and solutions**

- Importing any service — even the pure route rules — loaded ReportLab and
  PyMuPDF, because `services/__init__` imported the import and export
  services eagerly. Its names now resolve on first use.
- `database/connection.py` imported the desktop's path rules at module
  level for a default; the import now happens only when no path is given.

## 2026-09-26 — Learning plan gaps, step 1: skill regression

`derive_skill` walks the review events in order and keeps a current level
beside the evidence: Forgotten caps it at recognition, two misses in a row at
the level drop it one, a success raises it. `WordSkill` gains `level` and
`regressed`. The engine document said failing never lowered a stage; it now
describes the fall.

## 2026-09-26 — Learning plan gaps, step 2: the task selector

`choose_level` aims from the skill level (passed in, or read from the
attempts), takes the highest available level not above the target, and the
review flow keeps the last two question kinds to ask a third another way
(`repeats`, `lower_levels`, `context_prompt(avoid_task=…)`). Two tests
changed meaning with the rule: a miss at a level never reached is now a
stretch, and a level without content is no longer stepped over upwards.

## 2026-09-26 — Learning plan gaps: the learner's report

`SelfReport` (Forgot, Effortful, Remembered, Instant) and
`ReviewFlow.assess` replace inferred effort, `grade` and the
reveal-then-rate path. A right typed answer waits for the report
(`Feedback.awaiting`) before anything is recorded; a written sentence and a
word without a meaning take all four. The desktop card and the bot show the
same buttons, keys 1–4 in the same order. The *Hide the meaning* row left
Settings; the value stays stored.

## 2026-09-26 — Learning plan gaps, step 3: resume fidelity

Flow state version 2 serialises steps (prompts, options), runs (attempts,
results, used prompts, cycles, resolution) and the half-answered step;
`restore` rebuilds them, reading version 1 as before. The bot keeps a
written sentence in the flow (`note_written`) instead of its own memory; the
desktop resumes an open session and shows a half-answered step as it stood.

## 2026-09-26 — Learning plan gaps, step 4: repair and relearning

A repair TEACH step carries the level it repairs (`Step.focus`) and
`teaching_page` makes it short; relearning stays whole. Every follow-up
page holds back the context its re-ask uses (`Step.hold_back`) — before,
the page could print the very sentence the next question blanked out.
