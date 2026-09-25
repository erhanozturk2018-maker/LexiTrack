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
So is the skill record, ``learning_attempts``.

**Restoring** a daily backup copies it over the open database with the same
backup API, after checking it and after saving a copy of what is there now
(``before-restore-…``, kept out of the daily rotation). A backup from an
older version is upgraded as it is restored. The portable ``.lexitrack``
file is in ``portable.py``.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from ..core import paths
from ..core.clock import DayClock
from ..core.errors import StorageError
from ..database.connection import Database
from ..database.migrations import SCHEMA_VERSION, migrate, read_version
from ..repositories import RuntimeRepository, SessionRepository

log = logging.getLogger(__name__)

#: How many daily backups are kept.
KEEP_BACKUPS = 10
#: How long Telegram idempotency keys are kept.
KEEP_TELEGRAM_KEYS_DAYS = 14

_LAST_BACKUP_ON = "last_backup_on"
_LAST_PRUNE_ON = "last_prune_on"
_BACKUP_PREFIX = "vocabulary-"
#: Copies saved before a restore: never rotated away with the daily ones.
_SAFETY_PREFIX = "before-restore-"


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

    def safety_copy(self) -> Path:
        """Save what is there now before it is replaced. Raises if it cannot.

        Unlike the daily backup, failing here stops the restore: replacing
        the only copy of someone's data is not something to do on hope.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        directory = self.directory
        target = directory / f"{_SAFETY_PREFIX}{stamp}.db"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with self._db.lock:
                destination = sqlite3.connect(target)
                try:
                    self._db.connection.backup(destination)
                finally:
                    destination.close()
        except (OSError, sqlite3.Error) as exc:
            target.unlink(missing_ok=True)
            raise StorageError(f"A copy of your data could not be saved first: {exc}") from exc
        log.info("Saved the database to %s before restoring", target)
        return target

    def safety_copies(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob(f"{_SAFETY_PREFIX}*.db"), reverse=True)

    def restore_backup(self, backup: Path | str) -> Path:
        """Replace the database with a daily backup. Returns the safety copy.

        The backup is checked before anything is touched; a copy of the
        current database is saved; then the backup is copied in, and upgraded
        if an older version wrote it.
        """
        source = Path(backup)
        try:
            check = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            try:
                result = check.execute("PRAGMA quick_check").fetchone()[0]
                version = read_version(check)
            finally:
                check.close()
        except sqlite3.Error as exc:
            raise StorageError(f"{source.name} could not be read: {exc}") from exc
        if result != "ok":
            raise StorageError(f"{source.name} is damaged ({result}); nothing was changed.")
        if version is not None and version > SCHEMA_VERSION:
            raise StorageError(f"{source.name} was made by a newer LexiTrack.")
        safety = self.safety_copy()
        try:
            with self._db.lock:
                origin = sqlite3.connect(source)
                try:
                    origin.backup(self._db.connection)
                finally:
                    origin.close()
                restored = read_version(self._db.connection)
                if restored is not None and restored != SCHEMA_VERSION:
                    migrate(self._db.connection, self._db.path, restored)
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(
                f"{source.name} could not be restored ({exc}). Your data before the "
                f"attempt is in {safety.name}."
            ) from exc
        log.info("Restored the database from %s", source)
        return safety

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

    def export_review_log(self, path: Path | str, since: date | None = None) -> int:
        """Write every review as a CSV row. Returns the number of rows.

        With ``since``, only the reviews of that learning day and after.
        """
        rows = self._db.connection.execute(
            """
            SELECT r.reviewed_on, r.reviewed_at, w.display_word AS word, r.rating,
                   r.channel, r.state_before, r.state_after, r.elapsed_days,
                   r.scheduled_days, r.stability_after, r.difficulty_after,
                   r.due_after, r.session_id, r.undone_at, r.params_hash,
                   r.memory_result, r.route_version
            FROM review_logs r
            JOIN words w ON w.id = r.word_id
            WHERE r.reviewed_on >= ?
            ORDER BY r.reviewed_at, r.id
            """,
            (since.isoformat() if since else "",),
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
                    "undone_utc", "parameters", "memory_result", "route",
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
                        row["undone_at"] or "", row["params_hash"] or "",
                        row["memory_result"] or "", row["route_version"] or "",
                    ]
                )
        return len(rows)

    def export_attempts(self, path: Path | str, since: date | None = None) -> int:
        """Write every learning attempt — the skill record — as a CSV row.

        With ``since``, only the attempts of that learning day and after.
        """
        rows = self._db.connection.execute(
            """
            SELECT a.on_day, a.at, w.display_word AS word, a.phase, a.role, a.task,
                   a.level, a.success, a.effort, a.response_ms, a.novel_context,
                   a.depth, a.route_version, a.review_log_id, a.session_id, a.undone_at
            FROM learning_attempts a
            JOIN words w ON w.id = a.word_id
            WHERE a.on_day >= ?
            ORDER BY a.at, a.id
            """,
            (since.isoformat() if since else "",),
        ).fetchall()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "date", "time_utc", "word", "phase", "role", "task", "level", "success",
                "effort", "response_ms", "new_context", "depth", "route", "answer_id",
                "session", "undone_utc",
            ])
            for row in rows:
                writer.writerow([
                    row["on_day"], row["at"], row["word"], row["phase"], row["role"],
                    row["task"], row["level"], "yes" if row["success"] else "no",
                    row["effort"] or "", row["response_ms"] or "",
                    "yes" if row["novel_context"] else "no", row["depth"] or "",
                    row["route_version"], row["review_log_id"] or "",
                    row["session_id"] or "", row["undone_at"] or "",
                ])
        return len(rows)


def _num(value: float | None) -> str:
    return "" if value is None else f"{float(value):g}"
