# LexiTrack

**Vocabulary Learning & Review**

LexiTrack is a desktop app for learning vocabulary from word lists. Import the
Oxford 3000, a German A1 list you wrote in JSON, or any text-based PDF; organise
words into lists; then go through them one word at a time — **I Know** or
**I Don't Know** — or work through them in a table. Everything you answer is
remembered, shared across every list a word belongs to, and exportable as a
printable PDF, a CSV or JSON.

It runs entirely on your machine. No account, no server, no network; your
vocabulary lives in a local SQLite file.

![Home: continue learning, overview and your lists](docs/screenshots/home.png)

<details>
<summary><b>Dark mode</b></summary>

<br>

![Home in dark mode](docs/screenshots/home-dark.png)

![Flashcards in dark mode](docs/screenshots/flashcard-dark.png)

</details>

---

## Features

- **Lists.** Create lists, import into them, add words by hand, rename, delete.
  One word can be in many lists — "ability" in Oxford 3000, IELTS Vocabulary
  and My Difficult Words — and what you know about it is shared between them.
- **Languages.** Lists and words have a language, so English "gift" and German
  "Gift" are different words with separate progress.
- **Flashcard mode.** One large word, two equal answer buttons, keyboard-first.
- **Multi-step Backspace.** Step back through every answer in a session.
  Going back never changes an answer; pressing K, U or R does.
- **List mode.** A searchable, sortable, filterable table of a list, with bulk
  Known / Unknown / Reset, add to another list, and remove.
- **Unknown Words manager.** Every word you did not know, across all lists,
  with the lists each belongs to.
- **Import PDF and JSON**, several files at once, into new or existing lists,
  with a preview of what is new before anything is written.
- **Export** a list, its unknown words, all unknown words or a selection — as
  PDF, CSV or JSON. JSON exports import back unchanged.
- **Oxford 3000 and 5000** parsed with part of speech and CEFR level.
- **Safe upgrades.** A version 0.1 database is upgraded automatically, with a
  backup, keeping every answer.
- **Light and dark themes**, remembered between runs.

## Requirements

- **Python 3.11 or newer**
- **Windows 10/11** — developed and tested here. Nothing in the code is
  Windows-specific, but other platforms are untested.
- About 250 MB of disk space, almost all of it PySide6.

## Installation

```bash
git clone <your-repository-url> LexiTrack
cd LexiTrack
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

On macOS or Linux activate with `source .venv/bin/activate`. Dependencies are
declared in `pyproject.toml`; there is no `requirements.txt`. For development:

```bash
pip install -e ".[dev]"
```

> **Windows note.** PySide6 unpacks deeply nested files, so installing into a
> folder whose path is already very long can fail with
> `[WinError 206] The filename or extension is too long`. Clone somewhere
> shorter, such as `C:\Projects\LexiTrack`, or enable long paths in Windows.

## Running

```bash
lexitrack
```

or `python -m lexitrack`, or `.venv\Scripts\lexitrack-gui.exe` to start without
a console window. The database is created on first launch.

**Upgrading from 0.1:** just start the new version. Your database is upgraded
in place, each document you imported becomes a list, and a copy of the old
file is kept in the data folder (`vocabulary.v1-backup-<date>.db`).

## Usage

### Importing

**Import** in the app bar, or **File → Import…** (`Ctrl+O`). Choose one or more
PDF or JSON files. Each file gets a preview: the detected format, how many
words it has, how many are new, any problems, and where the words should go —
a new list named from the file, any existing lists, or both.

![Import preview](docs/screenshots/import-preview.png)

If a file states its language and you choose a list in another language, the
preview explains the clash instead of importing. Nothing is written until you
press Import.

### Reviewing with flashcards

**Continue** on Home, or the **Review** tab. The list you are reviewing is the
name at the top — click it to switch lists. Where the word came from is the
small "Source" line on the card.

![Flashcards, stepped back to an earlier answer](docs/screenshots/flashcard.png)

Press **Backspace** to step back through your answers. The card shows the
earlier word with its current status and says so; nothing changes unless you
answer again or press R.

### Reviewing as a list

Switch to **List** (`Ctrl+2`). Search, filter by status, sort by any column,
select rows (`Shift`/`Ctrl`+click, `Ctrl+A`) and mark them together. Looking at
a word never marks it — only an action does.

![List mode with a selection](docs/screenshots/list-mode.png)

**More** adds the selection to another list, removes it from this one, or
exports it. **Add Words…** types words in by hand. **List Actions** in the
header edits, exports or deletes the list.

### Unknown Words

Every word you answered "I Don't Know", across all lists. Mark words Known or
reset them to Not Reviewed (they leave this page, and reset words come round
again in flashcards), collect them into a list such as My Difficult Words, or
export them.

![Unknown Words manager](docs/screenshots/unknown-words.png)

### Exporting

**File → Export…** (`Ctrl+E`) offers what fits where you are: the current list,
its unknown words, your selection, or all unknown words. Choose PDF for a
printable study sheet, CSV for spreadsheets or Anki, JSON to edit and import
again.

<p align="center">
  <img src="docs/screenshots/export-pdf.png" alt="A page of an exported PDF" width="560">
</p>

### Keyboard shortcuts

**Help → Keyboard Shortcuts** (`F1`) lists them in the app.

| Where | Key | Action |
| --- | --- | --- |
| Flashcards | `K` or `←` | I Know |
| | `U` or `→` | I Don't Know |
| | `Enter` / `Space` | Repeat your last answer; on an earlier word, move forward without changing it |
| | `Backspace` | Step back to the previous word (status unchanged) |
| | `R` | Reset the word on screen to Not Reviewed |
| Tables | `K` / `U` / `R` | Mark the selection Known / Unknown / Not Reviewed |
| | `Ctrl+A`, `Shift`+arrows | Select |
| | `Enter` | Word details |
| | `Delete` | Remove the selection from this list (asks first) |
| | `Ctrl+F` | Search |
| Everywhere | `Alt+H` / `Alt+R` / `Alt+U` | Home / Review / Unknown Words |
| | `Ctrl+1` / `Ctrl+2` | Flashcard / List mode |
| | `Ctrl+O` · `Ctrl+N` · `Ctrl+E` | Import · New list · Export |
| | `Ctrl+T` | Light / dark |

## Supported formats

| Format | Parser | What it extracts |
| --- | --- | --- |
| Oxford 3000 / 5000 "by CEFR level" PDF | `OxfordParser` | Word, part of speech, CEFR level; English |
| LexiTrack JSON | `JsonParser` | Word plus any of part of speech, CEFR, definition, example, language; list name, language, description, source |
| Any other text-based PDF | `GenericTextParser` | Every distinct word, nothing else |

The JSON format is documented in
[docs/formats/json-import-export.md](docs/formats/json-import-export.md), and
[`examples/`](examples) has files you can import straight away. Only `words` is
required; a typical file looks like:

```json
{ "name": "German A1", "language": "de", "words": ["Haus", "gehen", "kommen"] }
```

Limitations: the Oxford PDFs contain no definitions or examples; the generic
parser cannot tell headwords from inflected forms; scanned PDFs are detected
and reported, not read (no OCR).

## Architecture

```text
   PDF      JSON      typed in
     └────────┼────────────┘
              ▼
     parsers ─► WordEntry
              ▼
     VocabularyService          the only thing the UI talks to
              ▼
     repositories               the only code that issues SQL
              ▼
            SQLite
   ┌──────────┬──────────┬───────────────┐
 words      sources     lists          status
 (language, where it    what you       what you
  word)     came from   study          know
```

Four ideas are kept strictly apart: a **word**, the **source** it came from,
the **lists** it belongs to, and your **learning status**. Navigation in a
flashcard session is a fifth, and it is never stored as status. The UI talks
only to `VocabularyService`; only repositories issue SQL.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture and
[docs/DECISIONS.md](docs/DECISIONS.md) for why.

## Project structure

```text
lexitrack/
├── core/            paths, logging, errors
├── models/          WordEntry, VocabularyList, ReviewStatus, languages
├── parsers/         PDF and JSON documents, Oxford / JSON / generic parsers
├── normalization/   word identity and deduplication
├── database/        schema.sql and migrations
├── repositories/    words, sources, lists, review state — all the SQL
├── services/        import workflow, review sessions, exports
├── exporters/       PDF, CSV, JSON
└── ui/              pages, dialogs, components, theme

examples/            JSON word lists to try
tools/               screenshot and design-mockup generators
docs/                architecture, decisions, status, log, TODO, formats, design
tests/               308 tests
data/                your database and exports (created at runtime, not committed)
pdfs/                put your PDFs here (not committed)
```

## Testing

```bash
pytest
ruff check lexitrack tests tools
```

308 tests. Tests that need the real Oxford PDFs skip when `pdfs/` does not
contain them; the rest of the suite still runs.

## Adding a parser

```python
class CambridgeParser(DocumentParser):
    key = "cambridge"
    name = "Cambridge word list"
    description = "Cambridge vocabulary PDFs."
    priority = 90              # asked before generic (-100), after Oxford (100)
    language = "en"            # if every document of this kind is English

    def can_parse(self, document: Document) -> bool:
        return "Cambridge" in document.text(max_pages=1)

    def parse(self, document, progress=None) -> list[WordEntry]:
        ...
```

Register it in `default_parsers()` in `lexitrack/parsers/registry.py`. It
appears in the import dialog, and nothing downstream of `WordEntry` changes.
For a non-PDF, non-JSON format, add a document class and set `document_types`.

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | Where the project stands, what was verified, what is next |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The architecture as built |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Why each significant choice was made |
| [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md) | Chronological technical log |
| [docs/TODO.md](docs/TODO.md) | Next, later, ideas |
| [docs/formats/json-import-export.md](docs/formats/json-import-export.md) | The JSON format |
| [docs/design/](docs/design) | The three design directions explored for 0.2 |

## Known limitations

- **Word details cannot be edited after they are added** — status can.
- **Backspace history lasts one session.** Reopening starts at the next
  unreviewed word, as expected, but earlier answers are reached through the
  table rather than Backspace.
- **What you know is shared across lists.** A word cannot be known in one list
  and unknown in another.
- **Deleting a list deletes words that are in no other list**, with their
  status. The confirmation says how many.
- **No spaced repetition, no OCR, no lemmatization.** `run` and `running` are
  separate words.
- **No language-specific normalization yet.** German `Straße` and `Strasse`
  count as the same word.
- **Untested outside Windows.**

## License

MIT. The Oxford 3000 and Oxford 5000 word lists are © Oxford University Press
and are not distributed with this project.
