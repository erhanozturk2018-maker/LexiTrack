# Architecture

This describes LexiTrack **as it is actually built**, at version 0.2. Where the
implementation departs from a specification it was built from, the departure
is named and explained. The reasoning behind each choice is in
[DECISIONS.md](DECISIONS.md).

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
        ┌─────────────────────────┐
        │ VocabularyService       │  the UI's only surface
        │  ImportService          │
        │  ReviewSession          │
        │  ExportService          │
        └───────────┬─────────────┘
                    ▼
        ┌─────────────────────────┐
        │ Repositories            │  the only SQL in the codebase
        └───────────┬─────────────┘
                    ▼
                  SQLite
         ┌──────────┼──────────────┐
         ▼          ▼              ▼
       words      lists       user_word_state
     + sources  + list_words   Known / Unknown /
    (provenance) (membership)   Not Reviewed
```

Dependencies point one way. `ui` imports `services`; `services` import
`repositories`, `parsers`, `normalization` and `exporters`; `repositories`
import `database` and `models`. Nothing imports upward, and the UI never
constructs a repository or issues SQL.

---

## 2. Module responsibilities

| Package | Responsibility |
| --- | --- |
| `core` | Paths, logging, the user-facing exception hierarchy |
| `models` | `WordEntry`, `Source`, `VocabularyList`, `ReviewStatus`, `Progress`, language codes |
| `normalization` | Word identity and runtime deduplication |
| `parsers` | Opening files, the parser protocol, JSON / Oxford / generic parsers, registry |
| `database` | Connection, schema creation, migrations, nested transactions |
| `repositories` | All SQL: words, sources, lists, review state |
| `services` | Import workflow, review sessions, exports, the `VocabularyService` facade |
| `exporters` | PDF, CSV and JSON writers |
| `ui` | Pages, dialogs, shared components, theme system |

---

## 3. Four concepts, kept apart

Most of version 0.2 is about not confusing four things that version 0.1 did
not need to distinguish.

| Concept | Question it answers | Stored in |
| --- | --- | --- |
| **Word** | What is the vocabulary item? | `words` — one row per `(language, normalized_word)` |
| **Source** | Where did it come from? | `sources`, `word_sources` |
| **List** | What is the user studying it as part of? | `lists`, `list_words` |
| **Learning status** | Does the user know it? | `user_word_state` |

A fifth, **navigation history** (where the user is in a flashcard session), is
not stored at all; see §8.

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

## 4. Data model (schema version 2)

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

### Notes

`StoredWord.note` is a short note about a word — a sense (*bank*: money), a
UK/US variant, an opposite. It is not a column: it lives in
`word_sources.metadata` as `note` (from a JSON `note` field) or `sense` (from
an Oxford-format entry such as `bank (money) n.`), and is flattened like every
other detail — the first source that supplies one wins. No schema change was
needed. The table search, the details panel, the flashcard, the PDF definition
column, CSV (a Note column) and JSON (`note`) all carry it.

---

## 10. UI

```text
MainWindow
├── app bar     LexiTrack · Home | Review | Unknown Words
│               · Search or run a command (Ctrl+K) · Import · theme icon · ⋯ menu
├── HomePage
│   ├── Continue learning   current list, progress, Continue, Flashcard|List
│   ├── Overview            four totals; the Unknown tile opens Unknown Words
│   └── Your lists          ListCard grid (click: current; double-click: open;
│                           right-click: review, open, add words, import into,
│                           export, edit, delete)
├── ReviewPage
│   ├── context strip       REVIEWING · <list ▾ switcher> · language · List Actions
│   │                       · Flashcard|List
│   ├── Flashcard mode      ReviewWidget / list complete / empty list
│   ├── List mode           VocabularyTable (details panel, floating bar) + Add Words
│   └── StatsBar            known · unknown · remaining · total for the list
└── UnknownPage             VocabularyTable (all unknown words, no status
                            column) + list filter
```

`MainWindow` owns only shared context: the current list and review mode
(persisted in `QSettings`), the theme and the commands. There is no menu bar:
`MainWindow.commands()` declares everything the app can do, with a description
and a shortcut, and three things are built from that one list — the Ctrl+K
`CommandPalette`, the "⋯" menu and the `ShortcutsDialog`. Window-wide
shortcuts are `QAction`s added to the window itself. Pages re-read the service
whenever shown. Pages do not know how they are navigated to, so changing the
navigation style means changing `main_window.py` only.

The app opens straight into Review when the current list is part-way through,
and on Home otherwise.

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
the top row returns to Continue). Ctrl+Tab cycles pages; Ctrl+L switches list.

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
screen). Validation errors are shown inline and keep the user's input.

### Theme system

Two `Palette`s designed independently; `build_stylesheet` (base rules) plus
`component_rules` (0.2 components) generate one stylesheet per theme.
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

---

## 11. Extension points

| To add… | Do this |
| --- | --- |
| A file format | Subclass `DocumentParser`, set `document_types`, register in `default_parsers()` (add a document class if it is not PDF or JSON) |
| An export format | Add a writer in `exporters/`, a member to `ExportFormat`, a branch in `ExportService.write` |
| A schema change | Update `schema.sql`, add a step to `migrations._STEPS`, bump `SCHEMA_VERSION`; the parity test catches drift |
| A screen | Add a page widget and a tab in `MainWindow` |
| A language name | Add it to `LANGUAGE_NAMES`; any valid code already works |

---

## 12. Tooling

- `tools/design_mockups.py` renders the three design directions explored for
  0.2 into `docs/design/`.
- `tools/screenshots.py` regenerates `docs/screenshots/` from the running app,
  using sample data in a temporary folder.

Neither is part of the application.
