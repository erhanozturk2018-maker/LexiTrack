"""Nothing personal is committed: no database, no backup, no token, no log.

The repository is public. A database file carries the user's whole learning
history and the paths of their own files; ``.env`` carries the bot token. A
backup once slipped through because an ignore rule matched only the top of
``data/``, so this checks what git actually tracks rather than the rules.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tracked() -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.splitlines()


def test_no_database_or_backup_is_tracked() -> None:
    offenders = [
        name
        for name in _tracked()
        if name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"))
    ]
    assert offenders == []


def test_only_placeholders_are_tracked_under_data() -> None:
    under_data = [name for name in _tracked() if name.startswith("data/")]
    assert sorted(under_data) == ["data/.gitkeep", "data/exports/.gitkeep"]


def test_no_env_file_or_log_is_tracked() -> None:
    offenders = [
        name
        for name in _tracked()
        if Path(name).name == ".env" or name.endswith(".log") or name.startswith("logs/")
    ]
    assert offenders == []
