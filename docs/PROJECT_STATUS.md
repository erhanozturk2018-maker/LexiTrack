# Project Status

**Last updated:** 16 September 2026
**Version:** 0.2.0 · schema version 2

---

## Current Phase

**Version 0.2 complete.** LexiTrack has moved from "one PDF, one word at a
time" to a vocabulary manager with lists, JSON import and export, flashcard and
list review modes, multi-step review navigation and an Unknown Words manager.
Existing version 0.1 databases upgrade automatically.

## Overall Progress

| Area | Status |
| --- | --- |
| Lists and many-to-many membership | Done |
| Language-aware word identity | Done |
| Schema versioning and v1 → v2 migration | Done, verified on a copy of a real v1 database |
| JSON parser and exporter | Done, round-trip tested |
| Prepare → preview → commit import, multiple files and lists | Done |
| Review session with multi-step Backspace | Done |
| Flashcard and List modes | Done |
| Unknown Words manager | Done |
| Manual list creation and word entry | Done |
| Home, navigation, context-aware export | Done |
| Three design directions explored | Done — A chosen and implemented |
| Light and dark themes for every new component | Done |
| Tests | 308 passing |
| Documentation | Updated |

---

## Completed

### Data

- Schema version 2: `lists`, `list_words` (with position), `language` on
  `words`, `UNIQUE(language, normalized_word)`.
- Migration from version 1 with automatic backup, preserved ids, per-step
  transactions, foreign-key and row-count verification, and refusal of newer
  databases. Each old source becomes a list; Oxford words become English.
- Invariants: vocabulary is the union of the lists; a list with a language only
  holds that language; imports never overwrite metadata or status.
- Learning status remains word-level and shared across lists.

### Import and export

- `JsonDocument` + `JsonParser`; `open_document` picks PDF or JSON by
  extension or content. Parsers declare which document types they read.
- Structural JSON errors reported with location; unusable items skipped with a
  warning.
- Import preview: format, total, new, already existing, merged repeats,
  warnings, target lists, language. Language clashes block Import with a
  reason.
- Each file commits in one transaction; failure part-way writes nothing.
- Exports by scope — list, unknown in list, all unknown, selection — to PDF,
  CSV or JSON. JSON exports import back identically.

### Review

- `ReviewSession`: Backspace steps back through every answer without changing
  status; Enter moves forward on earlier words and repeats the last answer on
  the live word; R resets explicitly; answering an earlier word changes it
  explicitly.
- Flashcard card shows provenance as "Source: …", a status badge and a history
  banner when looking back.
- List mode: search, status filter, sorting, multi-select, bulk Known /
  Unknown / Reset, add to another list, remove from list, export selection,
  word details. K / U / R on the selection.
- Switching mode keeps the list and the session history.

### Interface

- Tabs: Home · Review · Unknown Words (Alt+H / Alt+R / Alt+U).
- Home: continue learning, overview totals, list cards with context menus.
- Review: list switcher in the context strip, List Actions menu, mode switch,
  per-list stats bar.
- Unknown Words: every unknown word with the lists it belongs to, list filter,
  bulk Known / Reset, add to list, export.
- Dialogs: import, export, new/edit list, add words, choose list, word details.
- Status always shown as symbol plus word. Two-tone progress bars.
- Current list and mode remembered between runs; app opens into Review when a
  list is part-way through.
- One-time notice after a database upgrade naming the backup file.

### Removed

Code no feature used: PDF layout-aware line extraction and column detection,
the old top progress bar, several unused helpers and palette tokens. A theme
bug that made tests write to the real LexiTrack settings is fixed.

---

## In Progress

Nothing.

---

## Known Issues

None open.

---

## Known Limitations

Deliberate; see [DECISIONS.md](DECISIONS.md).

- **Word details cannot be edited after adding.** Status can; POS, level,
  definition and example cannot. First item in [TODO.md](TODO.md).
- **Review history is per session.** Backspace does not reach answers from a
  previous run of the app.
- **Learning status is shared across lists.** A word cannot be known in one
  list and unknown in another.
- **Deleting a list deletes words that are only in it**, with their status;
  the confirmation states how many.
- **No lemmatization, no language-specific normalization.** `run`/`running`
  are separate; German `Straße` and `Strasse` are one identity.
- **No OCR, no spaced repetition.**
- **Neither Oxford PDF contains definitions or examples.**
- **Windows only, in practice.**

---

## Blockers

None.

---

## Tests

```bash
pytest
ruff check lexitrack tests tools
```

**308 passing**, lint clean. Tests needing the real Oxford PDFs in `pdfs/` skip
when the files are absent.

| File | Tests | Covers |
| --- | --- | --- |
| `test_normalizer.py` | 30 | Case, Unicode, apostrophes, hyphens, non-words, no lemmatization |
| `test_deduplication.py` | 9 | Collapsing, ordering, metadata merging |
| `test_generic_parser.py` | 16 | Tokenising, punctuation, document failures |
| `test_oxford_parser.py` | 28 | Entry grammar, real PDF quirks, zero dropped lines |
| `test_database.py` | 21 | Schema, constraints, rollback, review queue |
| `test_migrations.py` | 17 | v1 → v2: ids, statuses, timestamps, lists, backup, parity with fresh schema, failure rollback, newer-version refusal |
| `test_lists.py` | 29 | Lists, membership, language identity, orphan deletion, bulk status |
| `test_json.py` | 35 | Valid and invalid JSON, skipped items, metadata, export, round trip |
| `test_import_workflow.py` | 20 | Preview, targets, multi-list, re-import, language resolution, atomic rollback |
| `test_review_session.py` | 18 | Multi-step Backspace, forward, explicit changes, reset, Enter |
| `test_vocabulary_service.py` | 29 | 0.1 behaviour: import, review, resume, re-import, export |
| `test_ui.py` | 56 | Flashcard keys, table search/filter/sort/selection, pages, persistence, dialogs, import dialog, themes |

### What has genuinely been verified

- **Migration on real data:** a copy of an actual version 1 database (4,953
  words, 812 known, 118 unknown) upgraded with every id, status and review
  timestamp identical, two lists created, backup written, reopening a no-op.
- **The upgraded copy was opened in the new UI:** it resumed at the next
  unreviewed word, answering and multi-step Backspace behaved as specified, and
  statuses in the database matched.
- **Both themes** rendered and inspected for Home, Flashcard, List mode,
  Unknown Words and the import dialog.
- **Rendering bugs found by looking, then fixed:** clipped bold tab labels,
  unpainted status pills, missing combo arrows, grey blocks inside panels,
  a misleading "100%" on all-unknown lists.
- **Dead code** scanned for with vulture and removed where no feature or test
  used it.

---

## Next Exact Steps

1. **Edit word details** (see TODO). Add `VocabularyService.update_word`,
   write to the manual source's `word_sources` row, and change the flattening
   in `WordRepository._SELECT_WORD` so `parser_type = 'manual'` wins. Add an
   Edit button to `WordDialog`.
2. **CEFR filter for flashcards.** Optional `levels` on
   `WordRepository.next_unreviewed` and `ReviewSession`; a small combo in the
   Review context strip.
3. **Session statistics** on Home from `user_word_state.reviewed_at`.

---

## How to Continue

1. Read this file.
2. Read [ARCHITECTURE.md](ARCHITECTURE.md) — especially §3 (word, source, list,
   status) and §8 (navigation versus status).
3. Read [DECISIONS.md](DECISIONS.md) 23–36 before changing lists, identity,
   review or migrations.
4. Read the latest [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) entry.
5. Set up and test:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[dev]"
   pytest
   ```

6. Run: `lexitrack`
7. Continue from **Next Exact Steps**.

### Where things live

| Looking for | Go to |
| --- | --- |
| Schema and upgrades | `lexitrack/database/schema.sql`, `migrations.py` |
| List rules (orphans, language) | `lexitrack/repositories/list_repository.py` |
| Import pipeline and language resolution | `lexitrack/services/import_service.py` |
| Backspace semantics | `lexitrack/services/review_session.py` |
| JSON format | `lexitrack/parsers/json_parser.py`, `docs/formats/json-import-export.md` |
| What the UI may call | `lexitrack/services/vocabulary_service.py` |
| Screens | `lexitrack/ui/home_page.py`, `review_page.py`, `unknown_page.py` |
| Shared table | `lexitrack/ui/components/vocabulary_table.py` |
| Colours and component styles | `lexitrack/ui/theme/palette.py`, `component_styles.py` |

### Conventions to keep

- The UI never issues SQL and never constructs a repository.
- Seeing a word never changes its status; navigation never changes status.
- Schema changes need a migration step and keep the parity test passing.
- No feature without a use.
- Status is never communicated by colour alone.
- Never add an AI co-author trailer to a commit.
