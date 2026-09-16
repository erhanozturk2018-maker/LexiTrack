# Decisions

Significant choices, with the reasoning and the alternatives that lost.

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
