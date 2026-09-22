"""SQLite connection management, first-run schema creation and upgrades.

A clean clone has no database file. The first time a connection is requested
the file and schema are created automatically. An existing file from an older
LexiTrack is upgraded in place by :mod:`.migrations`, after a backup copy has
been taken, so a user never has to recreate their vocabulary.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..core import paths
from ..core.errors import StorageError
from .migrations import SCHEMA_VERSION, migrate, read_version, seed_settings

log = logging.getLogger(__name__)

#: Run in order for a new database. ``learning.sql`` and ``progress.sql`` are
#: also run by the 2 → 3 and 3 → 4 migrations, so both paths produce the same
#: tables.
_SCHEMA_FILES = (
    Path(__file__).with_name("schema.sql"),
    Path(__file__).with_name("learning.sql"),
    Path(__file__).with_name("progress.sql"),
)


class Database:
    """Owns one SQLite connection and the schema that lives in it.

    The application is single-user and single-process, so one connection is
    enough. ``check_same_thread=False`` plus the write lock in
    :meth:`transaction` lets the import worker and the Telegram thread share it
    with the UI thread.

    The lock is reentrant and held for the whole transaction. Without it the
    nesting counter below would be shared between threads, and a Telegram
    answer arriving while the UI was mid-import would silently join the
    import's transaction — and be rolled back with it if the import failed.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            paths.ensure_data_dirs()
            path = paths.database_path()
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._transaction_depth = 0
        #: Serialises transactions across threads. Reentrant, so a service can
        #: nest repository calls on one thread; held until the outermost
        #: transaction commits or rolls back.
        self.lock = threading.RLock()
        #: Set when opening this database upgraded it; the path of the backup.
        self.migration_backup: Path | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """Open the connection, creating the database file and schema if needed."""
        if self._connection is not None:
            return self._connection

        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            connection = sqlite3.connect(
                self.path, check_same_thread=False, isolation_level=None
            )
        except sqlite3.Error as exc:  # pragma: no cover - environment specific
            log.exception("Could not open database at %s", self.path)
            raise StorageError(
                f"The vocabulary database at {self.path} could not be opened."
            ) from exc

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        self._connection = connection
        self._create_schema()
        return connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self.connect()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- schema ------------------------------------------------------------

    def _create_schema(self) -> None:
        """Create a new database, or upgrade an existing one to the current schema.

        ``schema.sql`` is only ever run against an empty database. Running it
        against an older one would skip tables that already exist and then fail
        on indexes that refer to columns those tables do not have yet.
        """
        assert self._connection is not None
        try:
            version = read_version(self._connection)
        except sqlite3.Error as exc:
            log.exception("Could not read schema version of %s", self.path)
            raise StorageError(
                "The vocabulary database could not be read. It may be damaged."
            ) from exc

        if version is None:
            try:
                for schema_file in _SCHEMA_FILES:
                    self._connection.executescript(
                        schema_file.read_text(encoding="utf-8")
                    )
                self._connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
                seed_settings(self._connection)
            except (sqlite3.Error, OSError) as exc:
                log.exception("Schema creation failed for %s", self.path)
                raise StorageError(
                    "The vocabulary database could not be initialised."
                ) from exc
            log.info("Initialised vocabulary database at %s", self.path)
            return

        if version != SCHEMA_VERSION:
            self.migration_backup = migrate(self._connection, self.path, version)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block of statements as a single atomic transaction.

        Transactions nest: an inner ``transaction()`` joins the outer one, so a
        service can combine several repository calls into one atomic unit
        without the repositories needing to know about each other.
        """
        connection = self.connect()
        with self.lock:
            yield from self._transaction(connection)

    def _transaction(self, connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        outermost = self._transaction_depth == 0
        if outermost:
            try:
                connection.execute("BEGIN")
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                raise StorageError() from exc
        self._transaction_depth += 1
        try:
            yield connection
        except BaseException:
            self._transaction_depth -= 1
            if outermost:
                connection.execute("ROLLBACK")
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                connection.execute("COMMIT")
