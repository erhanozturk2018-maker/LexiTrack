# Decisions

Significant choices, with the reasoning and the alternatives that lost.

Decisions 1-22 were made for version 0.1, 23-41 for 0.2, 42-59 for 0.3 and
60 onwards for 0.4. Where a later decision refines an earlier one, the earlier
entry points to it.

---

## 1. PySide6 for the UI

**Decision.** PySide6 (Qt 6) for the desktop interface.

**Reason.** The review screen is the product, and it needs precise control over
typography, spacing and focus behaviour. Qt's stylesheet system lets the entire
visual language be defined once and generated per theme. PySide6 is the
official binding, LGPL, and mature on Windows.

**Alternatives.** *Tkinter* — bundled, but its widget set makes a considered
interface a fight. *Electron / web UI* — would mean shipping a browser and
building a server for a single-user local app. *Textual/TUI* — an interesting
fit for a keyboard-driven review loop, but the brief asks for a desktop
application.

---

## 2. SQLite for persistence

**Decision.** A single local SQLite file.

**Reason.** Single-user, single-process, local-first. SQLite needs no server,
supports transactions and `UNIQUE` constraints, ships with Python, and survives
restarts — which is the entire point of the resume feature.

**Alternatives.** *JSON or pickle* — no constraints, no transactions, and a
rewrite of the whole file on every answer. *PostgreSQL* — a server process for
a personal desktop app. *An ORM (SQLAlchemy)* — a dependency and a layer of
indirection for four tables and about a dozen queries.

---

## 3. Parsers belong to documents, not to words

**Decision.** Which parser runs is decided once per document, by
`ParserRegistry`, before any word exists.

**Reason.** The one thing that must survive a new PDF format is the vocabulary
engine. If parser choice were a per-word decision, format knowledge would leak
into every layer. Deciding once means adding a format is adding a class.

**Alternatives.** *Per-word heuristics* — format knowledge everywhere.
*One parser with mode flags* — a growing conditional that becomes unreadable at
the third format.

---

## 4. `WordEntry` as the boundary model

**Decision.** Every parser returns `list[WordEntry]`, a dataclass where all
metadata except the word itself is optional.

**Reason.** It is the contract that makes parser independence real. Downstream
code depends on `WordEntry`, never on a parser. Optional metadata is what lets
the generic parser be a first-class citizen rather than a degraded special case.

**Alternatives.** *Per-parser models* — every consumer would need to know which
parser ran. *A dict* — no type checking, no documentation, and typos become
runtime bugs.

---

## 5. Source metadata and user state in separate tables

**Decision.** `word_sources` holds what the document said; `user_word_state`
holds what the user answered.

**Reason.** They have different lifetimes and different owners. A word's CEFR
level comes from Oxford and never changes; whether you know it changes as you
learn. Keeping them together would mean a re-import could overwrite review
progress — the single worst bug this application could have.

**Alternatives.** *One row per word with a status column* — couples the two
lifetimes and makes re-import dangerous. *State in a separate file* — loses
transactional consistency with the vocabulary it describes.

---

## 6. A `word_sources` junction table

**Decision.** Many-to-many between words and sources, rather than the
specification's `words.source_id`.

**Reason.** The specification requires that `ability`, present in both Oxford
lists, be asked about once while both sources are recorded. With `source_id` on
`words` that is unsatisfiable: either two rows (two reviews) or one row that
silently loses the second source. The junction gives one vocabulary identity
and many source relationships, each with its own metadata.

**Verified.** Importing both lists yields 4,953 items, with the 21 shared words
asked about once.

**Alternatives.** *A comma-separated source column* — unqueryable, and no place
for per-source metadata. *Duplicate word rows* — the exact behaviour the
specification forbids.

---

## 7. A runtime `set`, never a `set` as storage

**Decision.** `set[str]` for deduplication during an import; the database's
`UNIQUE` constraint for identity across sessions.

**Reason.** They solve different problems. The set gives O(1) membership while
processing a document and is discarded afterwards. Persistence needs to survive
the process, and the database already enforces it at the storage layer, where
it cannot be bypassed.

**Alternatives.** *A set as the source of truth* — everything is lost on exit.
*A database query per word* — thousands of round trips per import.

---

## 8. Normalization stops short of lemmatization

**Decision.** Case, Unicode, apostrophes, hyphens and edge punctuation are
normalized. Inflected forms are not merged.

**Reason.** Normalization should remove *presentation* differences, not
*linguistic* ones. `Ability` and `ABILITY` are the same word typed differently.
`run` and `running` are different words. Merging them would mean showing a user
`run` and silently marking `running` answered — a word they were never asked
about.

**Alternatives.** *spaCy/NLTK lemmatization* — a large dependency, a model
download, and wrong for a vocabulary tool where inflected forms are separate
things to learn. *Stemming* — worse; it produces non-words.

**Revisit if** a "group word families" feature is ever wanted. It should be an
explicit, visible option, not a silent default.

---

## 9. Local-first, no network

**Decision.** No server, no API, no account, no sync.

**Reason.** The product is one person reviewing words on their own machine.
Every networked component would add failure modes, latency, privacy questions
and maintenance for no benefit to that person.

**Alternatives.** *Cloud sync* — solves a problem (multi-device) the MVP does
not have. *A translation API* — an API key, a cost and an internet dependency
for a feature not in scope.

Version 0.3 adds one optional use of the network, the Telegram bot; see §49.

---

## 10. No OCR

**Decision.** Text-based PDFs only. Image-only PDFs are detected and reported.

**Reason.** OCR means Tesseract or a cloud service — a heavy non-Python
dependency or a network call, plus a whole class of accuracy problems the rest
of the application would then have to tolerate. Out of scope for a first
version.

**What matters more** is that the failure is honest. A scan produces
`ImageOnlyDocumentError` with a message naming OCR as the reason, never a
silently empty import.

**Alternatives.** *Bundle Tesseract* — large, platform-specific, and a big step
for a feature not yet needed. *Return nothing quietly* — the user concludes the
application is broken.

---

## 11. Scan detection by presence of text, not by amount

**Decision.** A PDF is image-only when its first ten pages yield no letters at
all — not when they yield "too few" letters.

**Reason.** The original threshold (20 characters per page) rejected
legitimately short documents: a one-page word list, or a test fixture reading
`ability`. There is no threshold that separates "short" from "scanned", because
the two are not on the same axis. Presence of text is the real signal.

**Consequence.** A scan carrying only page numbers passes this check and is
then reported by the import service as producing no words. Equally clear, and
no guessed constant.

---

## 12. Exporters depend on `StoredWord`, not on parsers

**Decision.** Export formatting is driven entirely by the read model, with `—`
for missing values.

**Reason.** Export format must be independent of how vocabulary was obtained. A
list built from a novel has no CEFR levels; that should produce a dash, not a
different code path or a broken layout.

**Alternatives.** *Per-parser templates* — combinatorial growth, and the
generic parser gets a second-class export.

---

## 13. `pyproject.toml`, no `requirements.txt`

**Decision.** PEP 621 metadata in `pyproject.toml`, with a `dev` extra.

**Reason.** Required by the implementation brief, and correct regardless: one
file declares dependencies, entry points, packaging and tool configuration.
`pip install -e .` gives a working install and the `lexitrack` command.

**Alternatives.** *Both files* — two sources of truth that drift. *Poetry/PDM* —
another tool to install before the project can be built.

---

## 14. `lexitrack/` rather than `app/`

**Decision.** The package is named after the product.

**Reason.** `app` is far too generic for a top-level installed import; it would
collide with almost anything. The brief also asks for `lexitrack` as the
project identifier.

**Alternatives.** *`src/lexitrack/`* — a valid layout, but an extra level for
no benefit in a single-package project.

---

## 15. One generated stylesheet, two hand-built palettes

**Decision.** `build_stylesheet(palette)` produces the entire application
stylesheet. Light and dark are designed separately.

**Reason.** Generating from tokens means a button looks identical everywhere
because it is described once, and adding a theme token is a single edit.
Designing the palettes separately matters because inverting a light theme
produces dark surfaces that glare and muted text that vanishes — dark mode
needs its own surface ramp and its own accent luminance.

**Alternatives.** *Per-widget styling in Python* — inconsistency by
construction. *Programmatic inversion* — a dark mode that looks like a bug.
*A `.qss` file per theme* — duplicated rules that drift apart.

---

## 16. Only light and dark

**Decision.** Two themes, no customisation.

**Reason.** Two themes can both be designed properly and both be tested. A
colour picker means every combination is possible and none is verified, for a
preference nobody asked for.

---

## 17. Answer buttons: equal weight, semantic hue

**Decision.** "I Know" and "I Don't Know" are the same size and weight,
distinguished by colour. Green for known, amber for unknown.

**Reason.** Neither is the correct answer, so styling one as primary would
suggest otherwise and bias the data. Amber rather than red because not knowing
a word is not a failure — it is how a word gets onto the study list.

**Alternatives.** *Primary/secondary* — implies a right answer. *Green/red* —
makes an honest answer feel like a mistake. *Identical buttons* — slower to
distinguish at a glance, over thousands of repetitions.

---

## 18. The review position is derived, never stored

*Still true in 0.2: the live word is always derived. 0.2 adds an in-memory
navigation history on top of it (see 26 and 27), not a stored cursor.*

**Decision.** `get_next_word()` queries for the first `not_reviewed` word
ordered by insertion. No cursor exists.

**Reason.** A stored index is state that can desynchronise from the data it
indexes — after a crash, after an import, after an undo. Deriving it means
resume is not a feature that had to be built and cannot break. The cost is one
indexed query per answer, which is negligible.

**Alternatives.** *A `current_index` column* — invalidated by every import.
*An in-memory list* — lost on exit, which is precisely what must not happen.

---

## 19. Senses are stored but not shown

**Decision.** Oxford's `bank (money)` and `bank (river)` deduplicate to one
word. The senses are kept in metadata but not displayed during review.

**Reason.** After deduplication the item represents *all* senses of the word.
Showing one of them would misrepresent the question being asked.

**Revisit if** a per-sense review mode is added; the data is already there.

---

## 20. Imports run on a worker thread

**Decision.** `ImportDialog` moves the work to a `QThread`, reports progress
and supports cancellation.

**Reason.** Parsing the Oxford 3000 takes a noticeable moment and a long book
takes much longer. A frozen window reads as a crash.

**Design consequence.** Nothing is written to the database until parsing
finishes, so cancelling needs no cleanup — there is nothing to roll back.

---

## 21. Git history in milestones, with no AI attribution

**Decision.** One commit per meaningful milestone, with messages explaining
*why*. No `Co-authored-by` trailer naming any AI, and no altered Git metadata.

**Reason.** The history should be readable as a narrative of how the project
was built. Authorship is the user's.

---

## 22. Testing behaviour, against real artefacts

**Decision.** Parser tests build real PDFs with PyMuPDF rather than stubbing
text extraction. Oxford tests run against samples copied verbatim from the
published lists, and against the real documents when present.

**Reason.** A test against a hand-written fake string proves the regex works on
that string, not that the parser reads the PDF. Building real PDFs exercises
extraction too.

The most valuable test is
`test_no_line_of_a_real_document_is_silently_dropped`: it asserts that every
line of both real PDFs is recognised as an entry, a heading or known furniture.
If Oxford republishes with a changed layout, that test fails loudly instead of
the parser quietly losing vocabulary.

**Consequence.** The real PDFs are not committed — they are Oxford University
Press material — so those tests skip on a clean clone. The trade is deliberate:
correct licensing over unconditional coverage.

---

# Version 0.2

## 23. Lists are many-to-many with words

**Decision.** A `lists` table and a `list_words` junction. A word belongs to
any number of lists; lists never copy words.

**Reason.** "ability" can be in Oxford 3000, IELTS Vocabulary and My Difficult
Words at once. Copying it per list would split its learning status and make
"do I know this word?" depend on which list you asked from.

**Alternatives.** *A list column on `words`* — one list per word. *Tags as
text* — unqueryable, no ordering, no metadata.

---

## 24. Learning status stays word-level, not list-level

**Decision.** `user_word_state` is keyed by word. Marking "ability" known in
one list marks it known everywhere.

**Reason.** Knowledge belongs to the person and the word, not to the
collection it was met in. Per-list status would ask the same question again in
every list a word is added to — the duplication version 0.1 was built to avoid
across Oxford 3000 and 5000.

**Trade-off accepted.** A word cannot be "known for IELTS purposes only".
Nothing asks for that, and it could be added later as a separate per-list flag
without disturbing the global status.

**Alternatives.** *Status on `list_words`* — rejected for the reason above.
*Both* — two sources of truth that disagree.

---

## 25. Language is part of word identity

**Decision.** `UNIQUE(language, normalized_word)`. Languages are short codes;
`und` means unspecified.

**Reason.** English "gift" and German "Gift" (poison) are different words and
must not share a learning status. Normalization itself stays
language-agnostic — case, Unicode, punctuation; no stemming or German-specific
rules yet — so identity changed by adding a key, not by changing how spellings
are compared.

**On import** the language is the word's own, else the file's, else the one
chosen in the preview, else the target list's, else `und`. A file that states a
language cannot enter a list of another language; a list with language `und`
accepts any.

**Alternatives.** *Language only on lists* — a mixed list such as My Difficult
Words could not say which "gift" it holds. *A language library* — LexiTrack
stores and compares languages; it does not process them.

---

## 26. Backspace navigates; it never changes status

**Decision.** Backspace steps back through every word answered in the session
and shows each with its current status. Changing it takes an explicit answer
(K/U) or reset (R). This replaces 0.1's single-step Backspace, which reset the
word to Not Reviewed.

**Reason.** "Go back" and "undo my answer" are different intentions. Tying
them together meant looking back over five answers wiped all five. Separated,
Backspace is always safe to press.

**Enter** on an earlier word moves forward without changing it; on the live
word it still repeats the last answer. **R** resets explicitly.

**Alternatives.** *Backspace resets each word it passes* — destructive
navigation. *A separate status-undo stack (Ctrl+Z)* — redundant: going back and
pressing the right key does the same with one mechanism, and a second history
would disagree with the first once a word was re-answered.

---

## 27. Review history is per session and in memory

**Decision.** Navigation history lives in `ReviewSession`, not the database.
Reopening the app starts at the first unreviewed word with empty history.

**Reason.** The live position is already derived from the database (§18), so
resume works without history. History is for correcting recent answers, a
within-session need. Persisting it adds a table and the question of how stale
history should behave after days away, for little gain.

**Alternatives.** *Reconstruct history from `reviewed_at`* — re-marking an old
word from the table would move it to the front, so "back" would jump
unpredictably. *A history table* — see above.

---

## 28. No persistent "seen" state

**Decision.** Displaying a word — in a table, in word details, on a card you
navigated back to — changes nothing. There is no "Seen" flag.

**Reason.** Seeing a word is not knowing it, and Not Reviewed must keep meaning
"not answered", not "not looked at". A separate seen flag was rejected because
no feature would use it: the flashcard queue is driven by status, and the table
already shows everything.

---

## 29. Vocabulary is the union of the lists

**Decision.** Removing a word from its last list, or deleting its last list,
deletes the word and its status. Words still in another list are untouched.
The confirmation states both counts first.

**Reason.** Keeping orphaned words leaves vocabulary no screen shows but that
still counts in totals, exports and "already in your vocabulary" figures. An
invisible, undeletable residue is worse than an explicit, confirmed deletion.

**Trade-off accepted.** Deleting German A1 and re-importing it starts that
list's progress again. The dialog says so.

---

## 30. Source and list are shown as different things

**Decision.** The Review page's largest text is the **list**, which is also the
list switcher. The card shows provenance as "Source: …". JSON provenance is the
file name unless the file names a source. ARCHITECTURE §3 traces how 0.1
produced its "Oxford 3000" label.

**Reason.** In 0.1 the only label was provenance, and it looked like the
learning context. With user lists it would show a source unrelated to what the
user chose to study.

---

## 31. Migrations: versioned steps, backup first, never recreate

**Decision.** `schema_version` plus ordered steps. Back up with the SQLite
backup API, rebuild tables preserving ids, verify foreign keys and row counts,
commit or roll back. Refuse newer versions. Each existing source becomes a
list. Only Oxford-extracted words become `en`.

**Reason.** The database holds someone's review progress. "Delete it and import
again" is not acceptable, and neither is inventing a language version 1 never
recorded.

**Alternatives.** *Alembic* — a dependency and a metadata layer for six tables.
*`ALTER TABLE ADD COLUMN`* — cannot change a `UNIQUE` constraint in SQLite.

---

## 32. Import is prepare, preview, commit

**Decision.** Parsing writes nothing. The preview shows format, new and
existing counts, warnings and target lists; commit writes each file in one
transaction.

**Reason.** With lists, "where will these words go?" has more than one answer,
and answering it after writing is too late. It also makes cancelling trivial.

---

## 33. JSON: minimal, forgiving about items, strict about shape

**Decision.** Only `words` is required; items are strings or objects. A
structurally wrong file is rejected with located errors; unusable items are
skipped with a warning; unknown fields are ignored. Export writes no ids, no
status and no empty fields.

**Reason.** The format is for people. One stray `"42"` should not block a
list; a `definition` that is a list is a mistake worth stopping for. Status is
left out because importing never applies it, and a file that seemed to carry it
would mislead.

---

## 34. Design direction A: evolve, don't replace

**Decision.** Three directions were rendered from real components
(`docs/design/`): A evolves the existing app bar and card; B is a sidebar
dashboard; C a minimal workspace. A was chosen, without its separate Lists tab,
because Home already is the list hub.

**Reason.** A keeps LexiTrack's identity and the review screen people already
use. B's sidebar, stat tiles and activity feed read as a generic dashboard,
which the brief explicitly avoids. C hides list management and Unknown Words
too deeply for an app that now has several lists.

**Reversibility.** Pages know nothing about navigation; moving to B or C means
rewriting `main_window.py`, not the pages.

---

## 35. Unknown Words is a working list, not an archive

**Decision.** The manager shows exactly the words whose status is Unknown.
Marking one Known or resetting it removes it from the page; nothing there
deletes vocabulary. There is no separate "review unknown words" flashcard mode.

**Reason.** Leaving "unknown" means the status changed, not that the word
should disappear. For re-studying, the page uses actions that already exist:
reset a batch so it returns to flashcards, or add it to a list such as My
Difficult Words and review that.

---

## 36. No feature without a use

**Decision.** Code no feature uses is removed rather than kept for later: 0.1's
layout-aware PDF line extraction and column detection, an unused progress bar,
and several unused helpers and palette tokens.

**Reason.** Unused code is still read, maintained and worked around, and it
suggests to the next developer that it matters. Git history keeps it if a
future parser needs it.

---

## 37. Shortcuts live in one place

**Decision.** No shortcut hints are printed in the interface — not under the
flashcard, not on the answer buttons. Every key is listed in Keyboard
Shortcuts (F1) and next to its command in the Ctrl+K palette; tooltips still
name them.

**Reason.** Hints repeated on screen are read once and then become clutter on
the screen used most. The old shortcuts window was a message box too tall for
the screen. One scrollable, filterable window, built from the same command
list as the palette, cannot disagree with the app.

---

## 38. Commands are declared once; no menu bar

**Decision.** `MainWindow.commands()` lists every command with a description
and shortcut. The Ctrl+K palette, the "⋯" menu and Keyboard Shortcuts are all
built from it. The native menu bar is gone.

**Reason.** The menu bar took a row above the app bar and duplicated it. A
palette is faster for keyboard users and explains each command; the "⋯" menu
keeps everything reachable with the mouse.

---

## 39. The selection bar floats

**Decision.** The bar that appears with a selection floats over the bottom of
the table instead of being a row in the layout. Export and Remove moved under
More; Unknown Words has no status column.

**Reason.** A bar inserted above the table pushed every row down the moment a
row was clicked, so the next click landed on a different word. Seven
equal-weight buttons also put Remove beside Known. Every row on Unknown Words
said "Unknown".

---

## 40. Export shows the file before it is saved

**Decision.** The export window renders the file the settings produce — the
first page of the PDF, the first lines of CSV or JSON — and asks where to save
only after. Ordering is applied by the dialog to the words it passes on, so it
applies to every format.

**Reason.** The user wanted to choose alphabetical or CEFR order and to see
the result, while someone who wants the defaults presses Enter. Rendering a
real export of the first 40 words keeps the preview honest and fast.

---

## 41. Notes are metadata, not a column

**Decision.** A word's note is stored in `word_sources.metadata` (`note`, or
the Oxford parser's `sense`) and flattened like other details, not added as a
column.

**Reason.** Oxford senses were already stored there. Reading both keys gave
notes to 873 existing words with no migration, and a JSON import of notes or
definitions fills in words that lack them without touching status — which is
how the definitions for the user's 6,825 words were delivered.

---

# Version 0.3

## 42. FSRS, through the `fsrs` library

**Decision.** Reviews are scheduled by FSRS (`fsrs` 6), wrapped by
`SrsScheduler`, rather than by a hand-written SM-2 or fixed intervals.

**Reason.** FSRS models how memory decays for each word and is the current
state of the art; its parameters can later be fitted to the user's own
`review_logs`. The library's state is kept as JSON on the card and read by the
wrapper alone, so an upgrade or a parameter fit is a change to one file.

**Alternatives.** *SM-2* — simpler, known to over-review easy words and
under-review hard ones. *Fixed intervals (1, 3, 7, 14…)* — no notion of how
well a word is known.

---

## 43. A once-a-day scheduler: day steps, day boundaries, no fuzz

**Decision.** Learning and relearning steps are one day; every due time is
snapped to the start of a learning day and is never today; fuzzing is off.

**Reason.** The user answers once a day, often from a phone. FSRS's default
1- and 10-minute steps would be missed and every card would arrive overdue.
Snapping keeps the morning count stable through the day and an evening
session finite. With intervals already quantised to days, fuzzing would only
move cards between days at random and make the forecast unreproducible.

---

## 44. The introduction is not a review

**Decision.** New words are *offered*; a card exists only after the user
confirms they have studied them, and no rating is recorded for that step.
The first FSRS rating is the first real answer, the next day.

**Reason.** The user studies the day's words away from the app. Inventing a
Good for the introduction would teach the scheduler something that did not
happen. "Not yet introduced" is the absence of a card, so there is no pointer
to keep in step through missed days, partial days or restarts.

---

## 45. One card per word, whatever the plan

**Decision.** `srs_cards` is keyed by word. A study plan is a scope, not an
owner; a word in two lists, or two plans, has one schedule.

**Reason.** The same word reviewed twice under two plans would double the
workload and split its history. Keeping the schedule with the word, like the
status (§24), means switching or deleting a plan never loses progress.

---

## 46. The review limit is a brake, and 250 comes from a simulation

**Decision.** When today's due reviews reach the limit (250 by default), new
words pause, and the Study page and the morning message say so.

**Reason.** 25 new words a day is the user's fixed goal, so the workload had
to be measured rather than guessed. Running the real scheduler over a year on
the 6,825-word pool, a typical learner peaks at about 250 reviews a day and
settles near 110; a struggling one saturates the limit, which the simulator
reports. Pausing intake is the one automatic correction, because adding words
to a day that is already too big only makes the following days worse.

---

## 47. Struggling words: three ways in, one harder way out

**Decision.** A word is flagged after 4 Agains in a row, after 8 in total with
a fresh one, or with stability under 7 days after at least 4 reviews. It stays
flagged until it has no failure streak *and* its stability has recovered.

**Reason.** The first version flagged on low stability alone and marked
almost every new word, because every word starts with low stability. Without
the recovery rule a single lucky Good would empty the list.

---

## 48. Mastery is derived, and a manual Known archives

**Decision.** A word becomes Known by itself when its stability reaches 21
days. Marking a word Known by hand archives its card instead of deleting it.

**Reason.** Known should mean the schedule expects the word to stick, not that
a button was pressed once. Archiving keeps the history, so resetting the status
later resumes the schedule rather than starting the word over.

---

## 49. Telegram is a thread in the app, not a second process or a server

**Decision.** The bot runs on a thread inside LexiTrack, calls the same
services as the window, and uses long polling. No web API, no Docker.

**Reason.** Two processes on one SQLite file would be two writers; one process
with one connection and one lock has none of that. The bot only needs to run
while the computer is on, which was accepted. Long polling needs only an
outgoing connection, where a webhook needs a public HTTPS address. Telegram
keeps undelivered updates for a day, so a sleeping laptop catches up.

**Alternatives.** *A local API server with the bot as a client* — a second
process and a network boundary for a single user. *A hosted bot* — a server to
run and the vocabulary leaving the machine. This amends §9: the app is still
local-first, and the bot is its one optional use of the network.

---

## 50. The token lives in `.env`, never in the database or the log

**Decision.** The bot token is read from `LEXITRACK_TELEGRAM_TOKEN` or a
`.env` file, with `.env.example` as the committed template. The HTTP libraries
are held at WARNING and every log line masks anything shaped like a token.

**Reason.** The database is the file that gets backed up, copied and shared.
The masking came after the first real test: `httpx` logs each request URL at
INFO and the Bot API puts the token in the URL, so the token was being written
to the log every ten seconds. A test now checks the mask.

---

## 51. Idempotency keys for every answer from a phone

**Decision.** An answer from Telegram claims `ans:<session>:<word>` in
`telegram_updates` before anything is written, and is ignored unless that word
is the session's current card. Taps are acknowledged before the work.

**Reason.** Telegram re-delivers a callback it is unsure about; without the
key one tap could rate a card twice. The insert *is* the lock, so two
deliveries racing each other still produce one review. The permanent record
is `review_logs`; keys are pruned after 14 days.

---

## 52. Settings shared with the bot live in the database

**Decision.** Learning settings are rows in `app_settings`, read as one typed
snapshot. Only presentation (theme, window state, the last list) and Start
with Windows (a registry value) live elsewhere.

**Reason.** A setting in the Windows registry is invisible to anything that is
not the Qt window. Reading the whole snapshot at once means the queue builder
cannot see one daily count for one card and another for the next. A value
edited by hand into nonsense falls back to its default rather than stopping
the app.

---

## 53. Close hides to the tray only while the bot is on

**Decision.** With the bot on, the window's close button hides it to the tray;
Quit (menu, Ctrl+Q, tray) always quits. With the bot off, closing quits.

**Reason.** The morning message needs the app running, but an app that lingers
in the tray for no reason is one people learn to kill. Three switches stay
independent: the bot, Start with Windows, and Quit.

---

## 54. One instance, over a local socket

**Decision.** A second launch asks the running instance to come forward and
exits; the channel is a local socket named after the data folder.

**Reason.** Two instances would poll Telegram with one token (Telegram rejects
one of them) and write one database twice. A socket, unlike a lock file,
cannot outlive a crash.

---

## 55. Logs only when asked for, one file a day

**Decision.** Nothing is written to disk unless Debug logging is on, which
needs Developer mode. Then each day has its own file in `logs/`, seven are
kept, and the folder sits beside `data/`, not inside it.

**Reason.** Logs explain a problem after the fact, and most days there is
none; a log growing every ten seconds while the bot polled was disk spent on
nothing. Logs describe the program, not the vocabulary, so they are never in
a backup.

---

## 56. Ten daily backups, counted rather than sized

**Decision.** One online backup a day, the newest ten kept, no size cap.

**Reason.** The count is what matters to the user ("I can go back ten days"),
and it already bounds the space, about 30 MB today. A size cap would have to
delete backups early, or stop taking them, on the day it was reached. A
database that fails its integrity check is not backed up, so bad copies cannot
push out the last good one.

---

## 57. An installed copy keeps its data in `%LOCALAPPDATA%`

**Decision.** A source checkout, recognised by its `pyproject.toml`, keeps
`data/` and `logs/` in the clone; any other install uses
`%LOCALAPPDATA%\LexiTrack`.

**Reason.** The earlier rule, "the folder beside the code is writable", was
also true of `site-packages`, so a plain `pip install .` would have kept the
database there and lost it on the next upgrade. `%LOCALAPPDATA%` is where
Windows applications keep their own data, and it does not roam.

---

## 58. The new screens use the existing visual language

**Decision.** Study, the review card, Settings and the Study Plan window reuse
the patterns of Home and Review: one decision panel, section titles outside
their cards, stat tiles, the flashcards' answer colours, the list cards'
progress bar. Buttons use sentence case without an ellipsis.

**Reason.** The first versions used the app's parts but not its layout, and
read as a different program. One primary button whose label is the next
thing to do replaced two panels that each asked the user to choose.

---

## 59. Everything under `data/` is ignored

**Decision.** `.gitignore` ignores `data/` as a whole apart from its two
placeholders, and a test checks what git actually tracks.

**Reason.** The rule `data/*.db` matched only the top of the folder, and a
daily backup in `data/backups/` was committed to the public repository. It
held no token, but it held the word list with the user's statuses and file
paths containing their Windows user name. The history was rewritten and the
repository recreated; the test makes a repeat fail loudly.

---

# Version 0.4

## 60. Every status change is an event, written with the status

**Decision.** `word_status_events` records each change of a word's status with
its cause — the schedule (`mastery`), a button (`manual`), the Review tab
(`sorting`) or Undo — in the same transaction as the change, by
`StateRepository` itself.

**Reason.** `user_word_state` says what a word is, never since when or why,
so "which words did I learn here, and when?" had no answer. Writing the event
in the repository rather than in each caller makes a change without a record
impossible. Events are written only on a real change, so answering a Known
word Known again leaves no noise.

**Alternatives.** *Reconstruct everything from `review_logs`* — works for the
schedule's Knowns only; a click leaves no review. *A `known_at` column* — one
date, no cause, no history of going back to Unknown.

---

## 61. Undo marks an answer; it never deletes it

**Decision.** Taking back an answer restores the card exactly, reverts a Known
it caused, and sets `undone_at` on the log row. Counts, the calibration and
fitting leave such rows out; the history and the All answers table show them.

**Reason.** A mis-tap on a phone is common, and one wrong Easy moves a word a
week away — or, twice, retires it. It must be reversible. But the user asked
for everything recorded to be visible, and a slip is part of the record;
deleting it would make the history quietly incomplete.

---

## 62. One scope rule for the plan window, the day and the pool

**Decision.** `PlanRepository` builds every query about a plan's words from one
scope expression; the Study Plan window counts an unsaved selection with the
same rule (`selection_outlook`).

**Reason.** The window used to count words itself, and its "still to learn"
included words already being learned and words that would never be offered,
so its estimate of days disagreed with what the Study page then did. Two
implementations of one rule drift; one cannot.

---

## 63. A plan can draw on every list, as a flag

**Decision.** *All my lists* is `study_plans.all_lists`, not the list of every
list id at the time.

**Reason.** "Teach me every word I don't know" should include a list imported
next month without the plan being edited. The engine already teaches only
Unknown words, so a plan over every list is exactly the Unknown Words page.

---

## 64. Progress is a tab of its own, and history opens from anywhere

**Decision.** A Progress tab beside Study, and a word's history as a window
that opens from the details panel, the Study page and both Progress tables —
not a section of the Study page or of the Study Plan window.

**Reason.** Study is what to do today, and the Study Plan window is a setting;
the record is neither. A separate tab keeps both uncluttered, and opening the
same history from every place a word appears means it is never more than a
click away.

---

## 65. Measure the fit before fitting; fit only with enough, and consent

**Decision.** Progress shows a calibration — predicted recall against what
happened — from the first days. Fitting parameters is gated at 512 answers
given on a later day than the word's previous answer, needs an optional
install, compares both parameter sets on the whole history first, and changes
nothing until the user chooses; the defaults are one button away.

**Reason.** The library's optimizer returns its defaults below 512 such
answers, so an always-available button would appear to work and do nothing.
The calibration answers "does it fit me?" without training anything, and
says when there are too few answers for its verdict to mean much. PyTorch is
a large download few users need, so it is an extra.

---

## 66. The first run asks questions instead of giving a tour

**Decision.** With no plan, the Study page asks what to learn, how many a day
and about the phone, and *Start learning* creates the plan. Everything else is
in How LexiTrack Works, whose central section is the README's, word for word,
checked by a test.

**Reason.** A tour is clicked through and forgotten; three questions whose
answers make the plan teach while they explain. One text in two places is
kept identical by a test rather than by memory. Explaining the engine in its
own panel of the Settings window had been tried and removed (the user found
the documentation enough); the help page is for users who never open it.

---

## 67. The Review tab sorts, and says so

**Decision.** The Review tab's label reads SORTING, with one line under it:
the words marked Unknown are what the Study tab teaches, and nothing here is
scheduled. The setting that covered Known words reads *Keep reviewing words
learned here*, and says words known before are never scheduled.

**Reason.** Two things were called reviewing, and one setting read as if it
would pull thousands of long-known words into the schedule — which kept the
user from turning on the only thing that lets the scheduler test its
predictions. Both confusions came from words, so the fix was words.

---

## 68. The weekly summary is owed only on its day

**Decision.** Sunday at the evening reminder hour, once; a Sunday the app was
off gets no late summary, and a week with nothing in it stays quiet.

**Reason.** The same rule as the daily messages (ARCHITECTURE §11, notifications
are decisions): a message is a decision made now, never a backlog replayed.

---

## 69. A sidebar, with names that say what each page does

**Decision.** The five tabs across the top became a sidebar: Today and
Progress, then a *Library* group (Lists, Sort words, Unknown), and at the
foot Export and backup, Settings, the theme and the ⋯ menu. Study is named
*Today*, Home *Lists*, Review *Sort words*; the session button reads *Start
session*. Today and Unknown carry counts. The sidebar folds to icons below
1000 px, and pages keep to a 1100 px column on wide windows.

**Reason.** The tabs were in no meaningful order (Home in the middle), the top
bar was crowded, and "Review" named both the sorting page and the reviews on
Study. A sidebar groups learning apart from the library it draws on, can say
whether anything is waiting, and has room for what V2 adds (backups, content)
without squeezing a tab strip. The internal page keys stay, so settings and
tests that name them are untouched.

**Trade-off.** 216 px less width for pages at the default size. Tables were
made to fit it (the word column absorbs the difference) rather than scroll
sideways; the details panel hides its buttons while the selection bar, which
carries the same actions, floats over them.

---

## 70. Long-term memory is suggested as Known; only the user marks it

**Decision.** When a word's stability passes the threshold (21 days by
default), the answer reports it and the word is offered as Known — a toast
with *Mark Known* now, a list on Progress later. The engine no longer changes
the status itself. Confirming records the change with cause `mastery`, so
*learned here* still means "reached long-term memory", now with the user's
agreement. Words made Known automatically before this are left as they are.

**Reason.** Memory, skill and status are three things (V2): FSRS predicts
retention of a word-to-meaning link, which is not the same as the user's
judgement that they know the word — and V2 will ask harder questions than
V1 did. A status the user did not set was also the one thing in the app they
could not trace back to a decision of theirs.

**Trade-off.** One more click per learned word, and words the user never
confirms keep being reviewed. That is intended: an unconfirmed word is one the
user has not called known.

---

## 71. Content is split by language; no language is built in

**Decision.** A word's target language stays part of its identity. Teaching
content is split in two: what is true of the word in its own language
(pattern, collocations, register, related words, example contexts) is stored
once, shared by every learner; what explains it to a learner (core meaning,
nuance, usage note, mnemonic, notes, context translations) is stored per
learner language, as rows keyed by a language code. The learner chooses
their language in Settings. The enrichment format (schema 2) carries the
shared part once and a block per learner language.

**Reason.** Schema 5 had `core_meaning_tr` and `translation_tr`: one learner
language wired into the columns, so a German speaker could not be served
without a new schema, and a second language would have duplicated the word.
The product is meant for any pair — English → Turkish, English → German,
German → English — on the same data model, on desktop and later on mobile.
Turkish content remains fully supported, as the first localization rather
than a special case.

**How it was done.** Schema 5 was already on the user's machine, so the change
is a 5 → 6 migration rather than an edit of history: it moves any schema 5
content into `tr` localizations and drops the language-specific columns.
Schema 1 enrichment files are still read, their fields mapped to `tr`.

---

## 72. A ceiling of 250 reviews, and a queue that keeps the fragile first

**Decision.** A day's reviews are capped at the learner's limit and never
above 250; "no limit" is read as 250. Over the limit, fragile words are kept
first, then the least likely to be remembered. The session opens with a
warm-up of three easy words and mixes fragile words in at most one in four.
New words shrink when they would take tomorrow over the limit.

**Reason.** An engineering and experience choice, not a research result: a
queue longer than a learner can finish is abandoned, and a backlog then
grows faster than it clears. Keeping the least-remembered words means what
waits is what is most likely to survive the wait. The warm-up and the
spacing of hard words are user-experience choices, made adjustable because
they are preferences. Protecting tomorrow follows from new words all falling
due the next day: intake is the one lever that prevents a pile-up rather
than reacting to it.

**Not silently changed.** A stored limit above 250 or "no limit" is read as
250 and shown as 250 in Settings; it is only rewritten if the user saves.

## 73. A portable backup of plain JSON, restored whole or not at all

**Decision.** Besides the daily database copies, *Export and backup* writes
everything that is the learner's to one `.lexitrack` file: a ZIP holding a
manifest and one JSON file per table, ids kept. Restoring one — or a daily
copy — replaces the whole database in a single transaction, after a copy of
the current database is saved as `before-restore-<time>.db`, which rotation
never removes. A `.lexitrack` file is read only by the schema version that
wrote it; a daily copy from an older version is upgraded as it is restored.

**Reason.** A backup has to be readable without LexiTrack to be trusted for
years, so the file is JSON, not SQLite. Keeping ids keeps every reference —
reviews to cards, translations to contexts — exact, which a re-import by
word text could not. A restore that half-applied would be worse than none,
so foreign keys are checked before commit and any failure rolls everything
back; the safety copy covers the one case the transaction cannot, the user
restoring the wrong file. Converting a JSON file between schema versions
would mean keeping every old migration in a second form; refusing it is
honest, and the daily copies, which are real databases, go through the
migrations that already exist.

**Left out.** Runtime state and Telegram bookkeeping belong to one machine;
the bot token was never in the database. PDF and CSV stay reading formats.

## 74. Teaching content in word exports; scheduling never

**Decision.** A PDF or CSV word export shows the columns the learner ticks:
the dictionary's fields and the teaching content — meaning and nuance in the
learner's language, pattern, collocations, examples and their translations.
The default is the three dictionary columns, the sheet LexiTrack always
printed. With any teaching column the PDF becomes a list of entries rather
than a table. No scheduling data — cards, intervals, due dates, reviews — is
ever a column. JSON stays the importable word list, dictionary fields only.

**Reason.** A printed sheet is for studying, and the teaching content is what
is worth studying from; the schedule is the program's bookkeeping, private to
it and meaningless on paper, and it already leaves through the learning-data
CSVs and the backup. Meanings, patterns and several examples do not fit in
table cells, so entries replace the table only when they are needed. Keeping
JSON to the dictionary fields keeps its promise of importing back exactly;
content has its own round-trip format.

**Fonts.** The sheet is set in Bitstream Vera, shipped with ReportLab and
embedded, so it looks the same everywhere and covers the Latin alphabets —
Helvetica, the PDF built-in used before, cannot set ş, ğ or ı. Text Vera
cannot set (Cyrillic, Greek) switches the whole sheet to a system font that
has it (Arial, Segoe UI, DejaVu or Noto); with none installed the export is
still written and the log says which characters are missing.

## 75. Progress in four tabs, memory and skill on two bars

**Decision.** Progress has four tabs: Overview, Words, Answers, Scheduler.
The Overview shows memory (how long each word is expected to be remembered)
and skill (what delayed answers showed the learner can do) as two separate
bars, with the evidence that is neither — instant retrieval on several days,
recall after 30+ days without a review, recognition in an unseen sentence —
counted beside them. Words in long-term memory are listed as ready to mark
Known, with a button for each and one for all; nothing is marked without it.

**Reason.** One page had grown to answer four different questions, and the
expert one — does the scheduler fit — sat between the learner's two. The
engine keeps memory and skill apart because they diverge: a word can be
remembered for weeks and still only be recognised. Showing one number would
hide exactly what the learner can act on. Known is the learner's decision
(decision 70), so the suggestions need a place to be decided, and the
Overview is where the question "am I there yet?" is asked.

**Honest about old answers.** Answers from before version 5 asked only word
→ meaning, so they count as recognition at most, and the page says how many
words rest on them alone.

**Found on the way.** `QWidget` already has a `size` property, so
`setProperty("size", "small")` never set anything and the `[size="small"]`
style never matched. It is replaced by `[compact="true"]`, compared on screen
before and after: the text buttons beside a section title (Copy, Export and
Mark as studied on Today; List actions) are compact, which lines them up with
the title; the Details toggle keeps full height, because it stands in a row
of full-height controls and looked out of place shrunk.

## 76. Telegram runs the desktop's session, without timing answers

**Decision.** The bot drives the same `ReviewFlow` as the desktop: the same
questions, probes, relearning and new-word teaching, recorded as Telegram
answers. A typed step is answered by replying with the word, a sentence by
replying and then grading it with buttons; the rest are buttons. The flow's
state is saved after every step; after a restart the first tap or reply
shows the current step instead of acting on it, and `/review` resumes the
open session. The effort of a typed answer is not taken from its timing on
the phone: it counts as normal unless it slipped.

**Reason.** Two routes would mean two meanings for one rating: a word
reviewed on the bus must count exactly as one reviewed at the desk, or the
schedule and the skill record drift apart. A typed reply is the phone's
natural way of typing an answer. Timing is left out because a phone's
notification delay and typing speed would read as effort and push honest
answers towards Hard, which schedules them too soon; no measure is better
than a biased one. Acting on a tap after a restart could apply it to a
different question than the one the learner saw, so the step is shown again
and the learner answers what is really asked.

**Removed.** `StudyFlow`, the route-V1 flow Telegram's session was meant to
move to, had no user left once both clients ran `ReviewFlow`. A session saved
by it is closed and a new one started, as any state from another route is.

## 77. The engine stays portable; a phone is a separate project

**Decision.** No mobile client is built in this repository. The learning
engine is kept free of desktop libraries and platform calls, and its session
state is plain data, and tests hold both. What a port would still have to
change — global ids for merging, device versus learner settings, the default
time zone, translatable wording — is documented, not pre-built.

**Reason.** A phone app is a different product decision (platform, language,
sync) that should be made with its own requirements in hand. What can be
done now at no cost to the desktop is to keep the rules portable, so either
reusing them or re-implementing them against the same behaviour stays
possible. Building sync or a second UI speculatively would add complexity
the desktop does not need.

## 78. Skill can fall

**Decision.** A word's skill stage is read from its current level, which
follows the record review by review: a review ending Forgotten caps it at
recognition until a recall succeeds again, and two missed first questions in
a row at its level take it down one. The counts of past successes stay; only
the current level falls. Productive must be shown again after a fall.

**Reason.** Replaces "failing never lowers a stage". A stage that only rises
says what was once shown, not what the learner can do now, and the task
selector (decision 79) aims each question from it: a word that has fallen
must be asked at the level it can actually reach. The two-in-a-row rule
keeps a single bad day from undoing a level.

## 79. The question aims from skill, and never above its target

**Decision.** The first question's target is the word's current skill level
(decision 78), moved by the last first question: up one after Remembered or
Instant, the same after Effortful or a miss, down one after Forgotten, never
up while recall is below 75 %. The question is at the highest level the
content can ask that is not above the target. The third first question of
one kind in a row in a session is asked another way when the content allows
(the other kind of context, or a lower level), never a harder one.

**Reason.** Replaces aiming from the last level asked and stepping over a
level with no content. The level last asked is what was *tried*; the skill
level is what was *shown*, so a failed stretch is not treated as the word's
level. Stepping up past missing content made a question harder than the
evidence supported. "Three in a row" is read as the questions the learner
sees consecutively; where the content offers no other kind (a word with only
a meaning), the same kind is asked, and the reason says so only when it did
change something.
