# Architecture

This describes LexiTrack **as it is actually built**. Where the implementation
departs from the original specification
(`pdf_vocabulary_tracker_architecture.md`), the departure is named and
explained.

---

## 1. The shape of the thing

```text
                          ┌──────────────┐
                          │     PDF      │
                          └──────┬───────┘
                                 ▼
                          ┌──────────────┐
                          │   Document   │  opens, validates, extracts text
                          └──────┬───────┘  and layout-aware lines
                                 ▼
                          ┌──────────────┐
                          │ParserRegistry│  one decision, per document
                          └──────┬───────┘
                      ┌──────────┴──────────┐
                      ▼                     ▼
              ┌──────────────┐      ┌──────────────────┐
              │ OxfordParser │      │GenericTextParser │
              └──────┬───────┘      └────────┬─────────┘
                     └──────────┬────────────┘
                                ▼
                        ┌──────────────┐
                        │  WordEntry   │  the standard model
                        └──────┬───────┘
                               ▼
                        ┌──────────────┐
                        │  Normalizer  │  presentation → identity
                        └──────┬───────┘
                               ▼
                        ┌──────────────┐
                        │ Deduplicator │  runtime set
                        └──────┬───────┘
                               ▼
                    ┌────────────────────┐
                    │ VocabularyService  │  the UI's only surface
                    └──────────┬─────────┘
                               ▼
                    ┌────────────────────┐
                    │   Repositories     │  the only SQL in the codebase
                    └──────────┬─────────┘
                               ▼
                    ┌────────────────────┐
                    │      SQLite        │
                    └──────────┬─────────┘
                    ┌──────────┴─────────┐
                    ▼                    ▼
                  Known               Unknown
                                         │
                                         ▼
                                 PDF / CSV export
```

Dependencies point one way only. `ui` imports `services`; `services` import
`repositories`, `parsers`, `normalization` and `exporters`; `repositories`
import `database` and `models`. Nothing imports upward.

---

## 2. Module responsibilities

| Package | Responsibility | Depends on |
| --- | --- | --- |
| `core` | Paths, logging, the exception hierarchy | nothing |
| `models` | `WordEntry`, `Source`, `ReviewStatus`, `Progress` | `core` |
| `normalization` | Word identity, runtime deduplication | `models` |
| `parsers` | `Document`, the parser protocol, Oxford, generic, registry | `models`, `normalization`, `core` |
| `database` | Connection, schema creation, transactions | `core` |
| `repositories` | All SQL | `database`, `models` |
| `services` | Import, review and export workflows | `repositories`, `parsers`, `exporters` |
| `exporters` | PDF and CSV writers | `repositories` (for `StoredWord`), `core` |
| `ui` | PySide6 widgets, dialogs, theme system | `services`, `models` |

---

## 3. The parser layer

### `Document`

Wraps PyMuPDF. Parsers never open files; they are handed a `Document` that has
already answered the awkward questions:

- The file exists and is a file.
- It opens as a PDF and is not password protected.
- It has at least one page (`EmptyDocumentError` otherwise).
- At least one of its first ten pages yields a letter
  (`ImageOnlyDocumentError` otherwise).

`Document` exposes plain text per page, whole-document text, and
**layout-aware lines**: spans grouped by baseline, with a column detector that
finds vertical gutters via an occupancy histogram across the page width. The
Oxford parser does not currently need this — the published PDFs extract in
correct reading order — but it is the tool a future parser for a genuinely
multi-column document will reach for.

### `DocumentParser`

```python
class DocumentParser(ABC):
    key: str          # stored in sources.parser_type
    name: str         # shown in the import dialog
    description: str
    priority: int     # higher is asked first

    def can_parse(self, document) -> bool: ...
    def parse(self, document, progress=None) -> list[WordEntry]: ...
    def source_key(self, document) -> str:   # stable identity of the source
    def source_name(self, document) -> str:  # display name
```

`source_key` is what makes re-import safe. It must not depend on where the file
happens to live, so `OxfordParser` derives `oxford3000` / `oxford5000` from the
document's title, and the base class falls back to a slug of the document name.

**Deviation from the specification:** the spec sketches a `Protocol`. An
abstract base class is used instead, because parsers share real behaviour
(`source_key`, `source_name`, `info`) that a `Protocol` cannot provide.

### `OxfordParser`

Rules derived from the real published PDFs, which were inspected before a line
of the parser was written. Structure:

```text
© Oxford University Press      ← furniture
1 / 12                         ← furniture
The Oxford 3000™ by CEFR level ← furniture
The Oxford 3000 is the list…   ← furniture
A1                             ← level heading, applies until the next one
a, an indefinite article       ← entry
about prep., adv.              ← entry
```

An entry is `word` followed by a comma- or slash-separated list of
part-of-speech abbreviations. Four real quirks are handled:

| Quirk | Example | Handling |
| --- | --- | --- |
| Sense disambiguator | `bank (money) n.` | Word is `bank`; sense goes to metadata |
| Wrapped disambiguator | `light (from the` / `sun/a lamp) n.` | Lines with unbalanced brackets, or ending in `/` or `,`, are joined with the next |
| Homograph numbering | `can1`, `can2` | Trailing digit stripped, so both collapse to `can` |
| Multi-form entry | `a, an indefinite article` | Split on the comma into two entries |

Abbreviations are expanded (`n.` → `noun`) for display and export. Unknown
abbreviations pass through rather than being dropped, so a future edition that
adds one degrades gracefully.

`can_parse` checks the title first, then falls back to structure: at least one
CEFR heading, twenty or more candidate lines, and 60% of them matching the
entry grammar.

**Verified:** across both real documents, every line is an entry, a heading or
known furniture. Zero lines are dropped. `test_no_line_of_a_real_document_is_silently_dropped`
enforces this, and is the test that would catch a future edition changing
layout.

**Limitation:** neither PDF contains definitions or examples, so those fields
are always `None` for this source.

### `GenericTextParser`

Tokenises any text-based PDF: letters with internal apostrophes or hyphens,
words hyphenated across a line break repaired first, tokens shorter than two
characters skipped. It deduplicates as it goes using a `set`. Priority `-100`,
and `can_parse` always returns `True`, so it is the terminal fallback.

### `ParserRegistry`

Holds parsers sorted by descending priority and returns the first whose
`can_parse` accepts the document. A detector that raises is logged and skipped
rather than aborting selection. `select(document, preferred_key=...)` bypasses
detection entirely, which is what the import dialog's manual override uses.

---

## 4. `WordEntry`

```python
@dataclass(slots=True)
class WordEntry:
    word: str                        # as shown to the user
    normalized_word: str             # the identity key
    source_id: str = ""
    part_of_speech: str | None = None
    cefr_level: str | None = None
    definition: str | None = None
    example: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

This is the boundary. Parsers fill in what the document provides; missing
metadata is normal, not an error. `metadata` holds parser-specific extras
(`sense`, `homograph`) that the vocabulary engine never interprets — it exists
precisely so a parser can keep information without leaking its vocabulary
upward.

`merged_with` combines two records for the same word: parts of speech are
unioned, the **lower** CEFR level wins (the level at which a learner first
meets the word is the useful one), and the first non-empty definition and
example survive.

---

## 5. Normalization

`normalize_word` removes **presentation** differences only:

1. Unicode NFKC
2. Soft hyphens removed (justified text)
3. Apostrophe variants → `'`
4. Hyphen variants → `-`
5. Whitespace collapsed
6. Edge punctuation stripped
7. Case folded
8. Rejected unless the result is letters with internal `'`, `-` or spaces

So `Ability`, `ABILITY` and `  ability  ` are one identity, and `42`, `3.5` and
`covid19` are not words at all.

It deliberately stops short of lemmatization. `run`, `running` and `ran` stay
distinct: merging them is a linguistic judgement that loses meaning, and the
user would be asked about a word they never saw.

`display_form` performs the same cleanup **without** case folding, so the
review screen can show `Ability` while the identity remains `ability`.

---

## 6. Deduplication

Two layers, for two different jobs:

| Layer | Mechanism | Scope |
| --- | --- | --- |
| Runtime | `set[str]` of normalized words | One import |
| Persistent | `UNIQUE` on `words.normalized_word` | Forever |

The set gives O(1) membership while a document is processed and disappears when
the import ends. It is emphatically not storage. `deduplicate()` keeps document
order, lets the first occurrence set the display form, and merges metadata from
later ones. `iter_unique()` streams without merging, for very large documents.

---

## 7. Database

```sql
sources          (id, key UNIQUE, name, parser_type, file_path, created_at)
words            (id, normalized_word UNIQUE, display_word, created_at)
word_sources     (word_id, source_id, part_of_speech, cefr_level,
                  definition, example, metadata, created_at)
user_word_state  (word_id PK, status, reviewed_at)
```

**Deviation from the specification:** the spec puts `source_id` and the
metadata columns directly on `words`. That cannot satisfy its own requirement
that a word appearing in both Oxford lists be asked about once, because it
forces either two `words` rows (two reviews) or one row that loses the second
source. The `word_sources` junction resolves this: one vocabulary identity,
many source relationships, each carrying its own metadata.

Reading a word flattens `word_sources` with correlated subqueries, so the rest
of the application never has to think about multiple sources. The first source
to supply a field wins, meaning existing metadata is never overwritten by a
later import.

Other choices:

- `PRAGMA foreign_keys = ON`, with `ON DELETE CASCADE`.
- WAL journalling, so a read during a write does not block.
- `idx_user_word_state_status(status, word_id)` — the review loop asks "next
  not-reviewed word" on *every* interaction, so that lookup gets its own index.
- The schema is created on first connection. A clean clone needs no setup.
- One connection with `check_same_thread=False`, shared between the UI thread
  and the import worker; writes go through `Database.transaction()`.

---

## 8. Repositories

The only code in the project that issues SQL.

- **`SourceRepository`** — upsert by `key`, so re-importing reuses the row.
- **`WordRepository`** — inserts entries and links in one transaction, returns
  an `ImportResult` (`new_words`, `existing_words`, `new_links`), and owns the
  review queue (`next_unreviewed`, ordered by `words.id`).
- **`StateRepository`** — review status and the aggregate `Progress`. It never
  touches source metadata.

`StoredWord` is the read model: a vocabulary identity with its flattened
metadata, its status and the names of every source it came from.

---

## 9. Services

**`ImportService`** runs the pipeline: open → select parser → parse →
deduplicate → upsert source → store. It reports progress through a callback and
polls a cancellation callback between stages. Nothing is written until parsing
completes, so cancelling leaves no partial import.

**`VocabularyService`** is the facade the UI uses: `import_document`,
`get_next_word`, `mark_known`, `mark_unknown`, `undo`, `get_progress`,
`export_unknown_pdf`, `export_unknown_csv`, `reset_progress`.

**`ExportService`** decides *what* is exported; the exporters decide *how* it
is formatted.

### Why the review position is not stored

`get_next_word()` asks the database for the first word with status
`not_reviewed`, ordered by insertion. There is no cursor, no index and no
"current position" anywhere. Consequently a crash cannot desynchronise it, and
resume is not a feature that had to be built — it is a consequence of the
design.

---

## 10. UI

```text
MainWindow
├── AppBar          title · progress bar · Import · Export · theme toggle
├── QStackedWidget
│   ├── WelcomeState     (0) nothing imported yet
│   ├── ReviewWidget     (1) a word to review
│   └── CompletedState   (2) everything reviewed
└── StatsBar        Known · Unknown · Remaining · Total
```

`MainWindow.refresh()` is the single point of truth: it reads progress and the
next word from the service, updates every counter, enables or disables the
export actions, and picks the page. Every mutation ends by calling it, so the
UI cannot drift from the database.

`ReviewWidget` emits `answered(word_id, known)` and `undo_requested()`; it
performs no persistence itself. The answer buttons disable on click so a double
click cannot consume two words.

### Theme system

`palette.py` holds two `Palette` dataclasses, designed independently rather
than derived from one another. `stylesheet.py` generates the whole application
stylesheet from a palette. `ThemeManager` applies it, aligns Qt's own
`QPalette` (for the pieces Qt draws itself, such as text selection), and
persists the choice in `QSettings`.

Two rendering details worth knowing, both learned the hard way:

- `QLabel` inherits the window background, painting a visible chip behind text
  on a different surface. The stylesheet resets `QLabel { background: transparent; }`.
- A plain `QWidget` subclass does not paint a stylesheet background unless
  `WA_StyledBackground` is set. `StatsBar` sets it; `_Stat` deliberately does
  not, so it shows the bar's surface through.

`WrappedLabel` exists because Qt sizes a word-wrapped `QLabel` as if the text
had unlimited width, and adding an alignment flag stops the layout consulting
`heightForWidth` at all. It pins the width and recomputes its minimum height on
every text, font and style change.

---

## 11. Exporters

Both take `Sequence[StoredWord]` and know nothing about parsers, so a word list
built from a novel exports exactly as well as one from Oxford.

- **CSV** — UTF-8 with BOM, because Excel on Windows otherwise misreads
  accented characters. Missing values are empty cells.
- **PDF** — ReportLab `BaseDocTemplate` with a repeating header row, banded
  rows, a footer rule and page numbers. Missing values show `—`.

---

## 12. Extension points

| To add… | Do this |
| --- | --- |
| A new document format | Subclass `DocumentParser`, add it to `default_parsers()` |
| A new export format | Add a writer in `exporters/`, expose it on `ExportService` |
| A new review mode | Add a query to `WordRepository`, expose it on `VocabularyService` |
| A new screen | Add a widget to the stack in `MainWindow._build_body` |
| A new theme token | Add a field to `Palette`, set it in `LIGHT` and `DARK`, use it in `build_stylesheet` |

The constraint that keeps these cheap is the one-directional dependency rule.
Adding a parser touches `parsers/` only; adding a screen touches `ui/` only.

---

## 13. Deviations from the specification, summarised

| Specification | Implementation | Why |
| --- | --- | --- |
| `app/` package | `lexitrack/` package | `app` is too generic to install as a top-level import |
| `words.source_id` | `word_sources` junction table | The spec's own cross-source requirement is unsatisfiable without it |
| `DocumentParser` as `Protocol` | Abstract base class | Parsers share real behaviour |
| `requirements.txt` | `pyproject.toml` only | Required by the implementation brief |
| Threshold-based scan detection | Presence-of-text detection | A threshold rejects legitimately short documents |
| Sense shown during review | Sense stored, not shown | Deduplication merges senses; showing one would misrepresent the question |

Each is recorded in [DECISIONS.md](DECISIONS.md) with the alternatives that
were considered.
