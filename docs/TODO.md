# TODO

Kept in sync with the actual implementation. Only genuinely unstarted work is
listed; what exists is described in [PROJECT_STATUS.md](PROJECT_STATUS.md).

---

## Now

Nothing outstanding. Version 0.3 — study plans, FSRS, the Study tab, the
Telegram bot, the tray and the daily backups — is complete and in daily use.

---

## Next

Small, well-scoped, and likely to matter in the first weeks of real use.

- [ ] **Fit FSRS parameters** to `review_logs` once a few thousand reviews
      exist. `SrsScheduler` is the only file that would change.
- [ ] **Edit a word's details.** Part of speech, level, definition and example
      cannot be edited after adding. Needs a `word_sources` write for the
      manual source and a flattening rule that lets manual edits win.
- [ ] **Silence "Task was destroyed but it is pending!"** when the bot is
      restarted: wait for the polling task before closing the event loop.
- [ ] **Check a plain `pip install .`** end to end in a fresh environment:
      the data folder in `%LOCALAPPDATA%`, the shortcut, Start with Windows.
- [ ] **Run `pip-audit`** on the dependencies before each release.

## Later

Larger, and worth doing only once the engine has been used for a while.

- [ ] **Queue inspector** in Developer mode: every card with its due date,
      stability and why it is in today's queue.
- [ ] **A time-zone control** in Settings; the zone is a stored setting today.
- [ ] **Filter flashcards by CEFR level.** A parameter on
      `WordRepository.next_unreviewed` and a control in the Review strip.
- [ ] **Per-sense review.** Oxford's `bank (money)` and `bank (river)` as
      separate questions. Senses are stored in `word_sources.metadata`.
- [ ] **German-aware normalization.** Language-specific rules in
      `normalization/`, selected by word language.
- [ ] **OCR for scanned PDFs.** Detection and the error message exist.
- [ ] **Verify on macOS and Linux.** Autostart and the shortcut are Windows
      only; everything else should run.

## Ideas

Unscheduled. Recorded so they are not lost, not committed to.

- Anki export.
- Audio pronunciation.
- Drag and drop files onto the window to open the import dialog.
- Remember window size and position.
- More parsers: Cambridge, IELTS word lists, EPUB, plain text.
- A packaged Windows installer.
