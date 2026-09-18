"""Keeping the data safe: daily backups, an integrity check, the review log.

Everything here runs on its own — at startup and once a day after that — and
is idempotent: calling it twice on the same day does nothing the second time.

**Backups** use SQLite's online backup API, which copies a consistent snapshot
while the database is in use, even with the Telegram thread writing. They go
to ``data/backups`` as one file per day, and only the newest
:data:`KEEP_BACKUPS` are kept: enough to go back ten days, not enough to fill
a disk. The ``.env`` file is never included; it is not in the database.

**The integrity check** is ``PRAGMA quick_check``, which reads every page but
skips the slow index cross-checks. It takes well under a second on a few
thousand words and turns silent corruption into a message at startup, when a
backup from yesterday still exists.

**The review log** is exported as CSV from ``review_logs``, which remains the
source of truth; the file is a copy for a spreadsheet, not a second record.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..core import paths
from ..core.clock import DayClock
from ..database.connection import Database
from ..repositories import RuntimeRepository, SessionRepository

log = logging.getLogger(__name__)

#: How many daily backups are kept.
KEEP_BACKUPS = 10
#: How long Telegram idempotency keys are kept.
KEEP_TELEGRAM_KEYS_DAYS = 14

_LAST_BACKUP_ON = "last_backup_on"
_LAST_PRUNE_ON = "last_prune_on"
_BACKUP_PREFIX = "vocabulary-"


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    detail: str = "ok"


def backups_dir() -> Path:
    return paths.data_dir() / "backups"


class Maintenance:
    """Daily housekeeping for one database."""

    def __init__(
        self,
        database: Database,
        clock: DayClock | None = None,
        directory: Path | None = None,
    ) -> None:
        self._db = database
        self._clock = clock or DayClock()
        self._runtime = RuntimeRepository(database)
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory or backups_dir()

    # -- the daily pass ------------------------------------------------------

    def run_daily(self) -> Path | None:
        """Back up and prune if today's pass has not run yet.

        Returns the new backup's path when one was taken.
        """
        today = self._clock.today()
        backup = None
        if not self._runtime.was_done(_LAST_BACKUP_ON, today):
            backup = self.backup(today)
            if backup is not None:
                self._runtime.mark_done(_LAST_BACKUP_ON, today)
        if self._runtime.mark_done(_LAST_PRUNE_ON, today):
            removed = SessionRepository(self._db).prune_updates(KEEP_TELEGRAM_KEYS_DAYS)
            if removed:
                log.info("Pruned %d old Telegram keys", removed)
        return backup

    # -- backups -------------------------------------------------------------

    def backup(self, local_date: str | None = None) -> Path | None:
        """Copy the database to ``backups/vocabulary-<date>.db``.

        A backup failing is logged, not raised: it must never stop the user
        from studying. The next start tries again.
        """
        stamp = local_date or self._clock.today()
        if not self.check_integrity().ok:
            # Copying a damaged file would, ten days later, have pushed
            # the last good copy out of the rotation.
            log.error("Skipping the backup: the database failed its integrity check")
            return None
        directory = self.directory
        target = directory / f"{_BACKUP_PREFIX}{stamp}.db"
        partial = target.with_suffix(".db.partial")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with self._db.lock:
                destination = sqlite3.connect(partial)
                try:
                    self._db.connection.backup(destination)
                finally:
                    destination.close()
            # Only a finished copy gets the real name, so a crash mid-copy
            # never leaves a truncated file that looks like a good backup.
            partial.replace(target)
        except (OSError, sqlite3.Error):
            log.exception("The daily backup failed")
            partial.unlink(missing_ok=True)
            return None
        log.info("Backed up the database to %s", target)
        self.prune_backups()
        return target

    def backups(self) -> list[Path]:
        """Existing daily backups, newest first."""
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob(f"{_BACKUP_PREFIX}*.db"), reverse=True)

    def prune_backups(self, keep: int = KEEP_BACKUPS) -> list[Path]:
        """Delete all but the newest ``keep`` daily backups.

        Only files this class named are considered: the migration backups
        that sit next to the database are never touched.
        """
        removed = []
        for old in self.backups()[max(keep, 1) :]:
            try:
                old.unlink()
                removed.append(old)
            except OSError:
                log.warning("Could not remove old backup %s", old)
        return removed

    # -- integrity -----------------------------------------------------------

    def check_integrity(self) -> IntegrityReport:
        try:
            rows = self._db.connection.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error as exc:
            return IntegrityReport(False, str(exc))
        messages = [str(row[0]) for row in rows]
        if messages == ["ok"]:
            return IntegrityReport(True)
        return IntegrityReport(False, "; ".join(messages[:5]))

    # -- the review log ------------------------------------------------------

    def export_review_log(self, path: Path | str) -> int:
        """Write every review as a CSV row. Returns the number of rows."""
        rows = self._db.connection.execute(
            """
            SELECT r.reviewed_on, r.reviewed_at, w.display_word AS word, r.rating,
                   r.channel, r.state_before, r.state_after, r.elapsed_days,
                   r.scheduled_days, r.stability_after, r.difficulty_after,
                   r.due_after, r.session_id
            FROM review_logs r
            JOIN words w ON w.id = r.word_id
            ORDER BY r.reviewed_at, r.id
            """
        ).fetchall()
        labels = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "date", "time_utc", "word", "rating", "answer", "channel",
                    "state_before", "state_after", "elapsed_days", "interval_days",
                    "stability", "difficulty", "next_due_utc", "session",
                ]
            )
            for row in rows:
                writer.writerow(
                    [
                        row["reviewed_on"], row["reviewed_at"], row["word"], row["rating"],
                        labels.get(int(row["rating"]), ""), row["channel"],
                        row["state_before"] or "", row["state_after"] or "",
                        _num(row["elapsed_days"]), _num(row["scheduled_days"]),
                        _num(row["stability_after"]), _num(row["difficulty_after"]),
                        row["due_after"] or "", row["session_id"] or "",
                    ]
                )
        return len(rows)


def _num(value: float | None) -> str:
    return "" if value is None else f"{float(value):g}"
