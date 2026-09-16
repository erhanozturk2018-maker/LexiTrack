# Decisions

Significant choices, with the reasoning and the alternatives that lost.

Decisions 1-22 were made for version 0.1; 23 onwards for 0.2. Where a later
decision refines an earlier one, the earlier entry points to it.

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
