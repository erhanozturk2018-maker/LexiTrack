"""SQLite connection management and first-run schema creation.

A clean clone has no database file. The first time a connection is requested
the file and schema are created automatically, so nothing has to be set up by
hand before the application can start.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..core import paths
from ..core.errors import StorageError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class Database:
    """Owns one SQLite connection and the schema that lives in it.

    The application is single-user and single-process, so one connection is
    enough. ``check_same_thread=False`` plus the write lock in
    :meth:`transaction` lets the import worker thread share it with the UI
    thread.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            paths.ensure_data_dirs()
            path = paths.database_path()
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

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
        assert self._connection is not None
        try:
            self._connection.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
            current = self._connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone()
            if current is None:
                self._connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
                log.info("Initialised vocabulary database at %s", self.path)
        except (sqlite3.Error, OSError) as exc:
            log.exception("Schema creation failed for %s", self.path)
            raise StorageError("The vocabulary database could not be initialised.") from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block of statements as a single atomic transaction."""
        connection = self.connect()
        try:
            connection.execute("BEGIN")
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise StorageError() from exc
        try:
            yield connection
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
