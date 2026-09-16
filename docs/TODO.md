# TODO

Kept in sync with the actual implementation. Only genuinely unstarted work is
listed; what exists is described in [PROJECT_STATUS.md](PROJECT_STATUS.md).

---

## Now

Nothing outstanding. Version 0.2 — lists, JSON, flashcard and list modes,
review history, the Unknown Words manager and the migration — is complete and
verified.

---

## Next

Small, well-scoped, and likely to matter in the first weeks of real use.

- [ ] **Edit a word's details.** Words can be added with details and their
      status changed, but part of speech, level, definition and example cannot
      be edited afterwards. Needs a `word_sources` write for the manual source
      and a flattening rule that lets manual edits win over imported values.
- [ ] **Filter flashcards by CEFR level.** "Review only B2 in Oxford 5000."
      A parameter on `WordRepository.next_unreviewed` and a control in the
      Review context strip.
- [ ] **Session statistics.** Words reviewed today and a per-level breakdown.
      `reviewed_at` is already stored.
- [ ] **Remember window size and position** alongside the theme.
- [ ] **Drag and drop files onto the window** to open the import dialog with
      them; `ImportDialog` already accepts initial paths.

## Later

Larger, and worth doing only once the basic loop has been used in anger.

- [ ] **Spaced repetition.** A review-history table and a scheduling policy.
      The biggest addition, and the one most likely to be designed wrong
      without real usage data first.
- [ ] **Per-sense review.** Oxford's `bank (money)` and `bank (river)` as
      separate questions. Senses are stored in `word_sources.metadata`.
- [ ] **German-aware normalization.** Today `Straße` and `Strasse` are the same
      identity (Unicode case folding) and nouns are not distinguished by
      capitalisation. Language-specific rules would live in `normalization/`
      and be selected by word language.
- [ ] **OCR for scanned PDFs.** Detection and the error message exist.
- [ ] **Verify on macOS and Linux.** Nothing is Windows-specific; nothing has
      been run elsewhere.

## Ideas

Unscheduled. Recorded so they are not lost, not committed to.

- Anki export.
- Personal notes per word.
- Audio pronunciation.
- A daily review goal.
- More parsers: Cambridge, IELTS word lists, EPUB, plain text.
- A packaged Windows installer.
