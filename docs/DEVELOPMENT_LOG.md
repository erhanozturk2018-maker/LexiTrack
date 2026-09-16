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
