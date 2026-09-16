# Project Status

**Last updated:** 16 September 2026
**Version:** 0.1.0

---

## Current Phase

**Phase 12 — Documentation and GitHub readiness.** All twelve planned phases
are implemented and verified. The application is complete and usable for real
vocabulary study.

## Overall Progress

| Phase | Status |
| --- | --- |
| 1. Project structure, packaging, models | Done |
| 2. SQLite and repositories | Done |
| 3. Normalization and deduplication | Done |
| 4. Generic PDF parser | Done |
| 5. Oxford parser | Done, validated against both real PDFs |
| 6. Vocabulary service | Done |
| 7. PySide6 UI | Done, launched and driven |
| 8. Light/Dark themes and UX polish | Done |
| 9. PDF/CSV exporters | Done, output inspected |
| 10. Tests | Done — 162 passing |
| 11. Integration and polish | Done |
| 12. Documentation and GitHub readiness | Done |

---

## Completed

### Core

- `lexitrack/` package with one-directional layering: `ui → services →
  repositories → database`.
- PEP 621 packaging in `pyproject.toml` with a `dev` extra and a `lexitrack`
  console entry point. No `requirements.txt`.
- Runtime path resolution with no hard-coded machine paths; overridable via
  `LEXITRACK_DATA_DIR`.
- Rotating file log plus stderr, with a user-facing exception hierarchy —
  tracebacks go to the log, sentences go to dialogs.

### Data

- SQLite schema created automatically on first connection. A clean clone needs
  no setup.
- Four tables: `sources`, `words`, `word_sources`, `user_word_state`.
- Vocabulary identity is one row per normalized word; `word_sources` records
  every document a word came from.
- Repositories are the only code that issues SQL.

### Parsing

- `Document` wraps PyMuPDF and validates before any parser runs: missing file,
  non-PDF, corrupt, password-protected, no pages, image-only.
- Layout-aware line extraction with column detection (built, available, not
  currently needed by either parser).
- `OxfordParser` — handles both real published lists including sense
  disambiguators, wrapped entries, homograph numbering and multi-form entries.
- `GenericTextParser` — fallback for any text-based PDF.
- `ParserRegistry` — priority-ordered detection plus manual override.

### Application

- `VocabularyService` exposes 18 public operations, including `import_document`,
  `get_next_word`, `mark_known`, `mark_unknown`, `undo`, `get_progress`,
  `export_unknown_pdf` and `export_unknown_csv`.
- Import runs on a worker thread with progress and cancellation; nothing is
  written until parsing finishes.
- Review position is derived from the database, never stored.

### Interface

- Three screens in a stack (welcome, review, completed), switched by a single
  `refresh()` that re-reads the database.
- Keyboard: `K`/`←` known, `U`/`→` unknown, `Enter` repeat, `Backspace` undo,
  `Ctrl+T` theme, `Ctrl+O` import. Mouse path fully usable.
- Light and dark palettes, designed separately, generated into one stylesheet,
  remembered via `QSettings`.
- Menus for import, both exports, undo, reset progress, data folder and about.

### Output

- CSV export (UTF-8 BOM for Excel) and PDF export (ReportLab, repeating header,
  banded rows, page numbers, `—` for missing values).
- Both driven by `StoredWord` and independent of any parser.

---

## In Progress

Nothing. The last change committed was the documentation set.

---

## Known Issues

None open.

Two issues were found and fixed during UI verification, recorded here because
they are the kind that recur:

1. `QLabel` inherited the window background and painted visible chips behind
   text on other surfaces. Fixed with a global `QLabel { background: transparent; }`.
2. A word-wrapped `QLabel` was clipped because Qt sizes it as if the text had
   unlimited width, and an alignment flag stops the layout consulting
   `heightForWidth`. Fixed by `WrappedLabel`, which pins the width and
   recomputes its height on text, font and style changes.

---

## Known Limitations

These are deliberate, not defects. Each is explained in [DECISIONS.md](DECISIONS.md).

- **No OCR.** Scans are detected and reported, never silently imported empty.
- **No lemmatization.** `run`, `running` and `ran` are three items.
- **No spaced repetition.** A word is reviewed once; no scheduling or history.
- **Exports cover unknown words only.**
- **Senses are merged.** `bank (money)` and `bank (river)` are one word. The
  senses are stored but not shown during review.
- **Neither Oxford PDF contains definitions or examples**, so those columns are
  empty for that source. A property of the documents, not the parser.
- **Windows only, in practice.** Nothing is Windows-specific, but it has not
  been run elsewhere.

---

## Blockers

None.

---

## Tests

```bash
pytest
```

**162 passing.** `ruff check lexitrack tests` is clean.

| File | Covers |
| --- | --- |
| `test_normalizer.py` | Case, Unicode, apostrophes, hyphens, punctuation, non-words, no lemmatization |
| `test_deduplication.py` | Collapsing, ordering, metadata merging, CEFR precedence |
| `test_generic_parser.py` | Tokenising, punctuation, repeats, apostrophes, hyphens, numbers, document failures |
| `test_oxford_parser.py` | Entry grammar, all four real quirks, detection, registry, and the real PDFs |
| `test_database.py` | Schema creation, constraints, rollback, cross-source identity, the review queue |
| `test_vocabulary_service.py` | Import, review, resume, re-import, export |
| `test_ui.py` | Widgets, shortcuts, screen switching, themes, the window |

Tests needing the real Oxford PDFs skip when `pdfs/` does not contain them. Put
your own copies there to run them.

### What has genuinely been verified

- **Both real Oxford PDFs were inspected before the parser was designed.**
- **Zero lines dropped** across both documents — every line is an entry, a
  heading or known page furniture, enforced by a test.
- **A full import** of both lists gives 4,953 items; the 21 shared words are
  asked about once.
- **Re-import** of the same document adds 0 new words and preserves all answers.
- **Resume** verified by closing a service and reopening the same database file.
- **The UI was launched and driven**: both themes, the review loop, keyboard
  shortcuts, undo, all three screens, and the import dialog.
- **Exports were generated from real data** and their contents checked: unknown
  words present, known words absent.

---

## Next Exact Steps

The MVP is complete, so these are enhancements rather than remaining work. In
the order that adds most value:

1. **Session statistics.** Words reviewed today, and a per-CEFR-level
   breakdown. `StateRepository.progress()` is the place to extend;
   `reviewed_at` is already stored.
2. **Filter the review queue by level or source.** Add a parameter to
   `WordRepository.next_unreviewed`, expose it on `VocabularyService`, and add
   a selector to the app bar.
3. **A vocabulary browser.** A searchable table of every word with its status,
   allowing an answer to be changed after the fact. Needs a new screen and a
   paginated repository query.
4. **Export all words, not only unknown ones.** `ExportService` already takes a
   status; expose the choice in the export flow.
5. **Spaced repetition.** The largest addition. Needs a `review_history` table
   and a scheduling policy; deliberately deferred until the basic loop has been
   used in anger.

---

## How to Continue

1. Read this file.
2. Read [ARCHITECTURE.md](ARCHITECTURE.md) for how the layers fit together.
3. Read [DECISIONS.md](DECISIONS.md) before changing anything structural — most
   surprising choices are deliberate and explained there.
4. Read the latest entry in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).
5. Set up and run the tests:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[dev]"
   pytest
   ```

6. Launch it: `lexitrack`
7. Pick an item from **Next Exact Steps** or from [TODO.md](TODO.md).

### Where things live

| Looking for | Go to |
| --- | --- |
| How a PDF becomes words | `lexitrack/parsers/` |
| What the UI is allowed to call | `lexitrack/services/vocabulary_service.py` |
| Every SQL statement | `lexitrack/repositories/` |
| The schema | `lexitrack/database/schema.sql` |
| Colours, spacing, fonts | `lexitrack/ui/theme/palette.py` |
| The review screen | `lexitrack/ui/review_widget.py` |
| Screen switching and menus | `lexitrack/ui/main_window.py` |

### Conventions to keep

- The UI never issues SQL and never imports a parser.
- Parsers never open files; they receive a `Document`.
- Exceptions carry messages that can be shown to a user; tracebacks go to the log.
- No hard-coded machine paths — use `lexitrack.core.paths`.
- Tests assert behaviour, not that code exists.
- Never add an AI co-author trailer to a commit.
