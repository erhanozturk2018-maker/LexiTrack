# TODO

Kept in sync with the actual implementation. Everything under **Now** is
genuinely unstarted; everything the MVP promised is done and lives in
[PROJECT_STATUS.md](PROJECT_STATUS.md).

---

## Now

Nothing outstanding. The MVP is complete: import → review → persist → export
all work and are verified.

---

## Next

Small, well-scoped, and valuable on the first session of real use.

- [ ] **Session statistics.** Words reviewed today; a breakdown per CEFR level.
      `reviewed_at` is already stored, so this is a query and a screen.
- [ ] **Filter the queue by level or source.** "Review only B2", "review only
      Oxford 5000". Add a parameter to `WordRepository.next_unreviewed` and a
      selector to the app bar.
- [ ] **Export all words, not just unknown ones.** `ExportService` already
      works per status; the choice just needs surfacing.
- [ ] **Remember window size and position** alongside the theme in `QSettings`.
- [ ] **Show a word count per source** in a Sources view, using the
      `word_count` that `SourceRepository` already returns but nothing displays.

## Later

Larger, and worth doing only once the basic loop has been used in anger.

- [ ] **Vocabulary browser.** A searchable, filterable table of every word with
      its status, allowing an answer to be changed afterwards. Needs a new
      screen and a paginated repository query.
- [ ] **Spaced repetition.** A `review_history` table and a scheduling policy.
      The biggest addition, and the one most likely to be designed wrong
      without real usage data first.
- [ ] **Undo more than one step.** Currently only the last answer can be
      undone. A bounded stack in `MainWindow` would cover a mis-click run.
- [ ] **Per-sense review.** `bank (money)` and `bank (river)` as separate
      questions. The senses are already stored; this is a product decision
      about whether asking twice is wanted.
- [ ] **Definitions for the Oxford lists.** Neither PDF contains them. Would
      need a different source, which raises licensing questions.
- [ ] **OCR for scanned PDFs.** Detection and the error message already exist;
      this would replace the message with an actual code path.
- [ ] **Verify on macOS and Linux.** Nothing should be Windows-specific, but
      "should" is not "verified".

## Ideas

Unscheduled. Recorded so they are not lost, not committed to.

- Anki export (`.apkg` or a CSV shaped for Anki import).
- Turkish meanings, or another target language, as an optional column.
- Personal notes per word.
- A daily review goal with a streak.
- Audio pronunciation.
- Additional parsers: Cambridge, IELTS, domain-specific word lists.
- A "show me this again later" answer, between known and unknown.
- Import from plain text or EPUB, not only PDF.
- A packaged Windows installer, so PySide6 need not be installed by hand.
