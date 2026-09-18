"""The log file: off unless asked for, one file a day, a week kept, no token."""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from lexitrack.core import logging_config, paths
from lexitrack.core.logging_config import prune_logs, remove_legacy_logs, set_file_logging


@pytest.fixture(autouse=True)
def file_logging_off():
    yield
    set_file_logging(False)


def test_nothing_is_written_to_disk_by_default(tmp_path: Path) -> None:
    logging.getLogger("lexitrack.test").warning("an ordinary day")
    assert logging_config.file_logging_enabled() is False
    assert not list(tmp_path.rglob("*.log"))


def test_debug_logging_writes_todays_file(tmp_path: Path) -> None:
    target = set_file_logging(True, tmp_path)
    assert target == tmp_path / f"lexitrack-{time.strftime('%Y-%m-%d')}.log"
    logging.getLogger("lexitrack.test").debug("a scheduling decision")
    text = target.read_text(encoding="utf-8")
    assert "a scheduling decision" in text, "debug level, not just info"


def test_turning_it_off_stops_writing(tmp_path: Path) -> None:
    target = set_file_logging(True, tmp_path)
    logging.getLogger("lexitrack.test").info("before")
    set_file_logging(False)
    logging.getLogger("lexitrack.test").info("after")
    text = target.read_text(encoding="utf-8")
    assert "before" in text and "after" not in text


def test_the_token_never_reaches_the_file(tmp_path: Path) -> None:
    target = set_file_logging(True, tmp_path)
    fake = "123456789:" + "FAKE-test-secret-" + "x" * 20
    logging.getLogger("httpx").warning("POST https://api.telegram.org/bot%s/getMe", fake)
    text = target.read_text(encoding="utf-8")
    assert "FAKE-test-secret" not in text
    assert "123456789:[redacted]" in text


def test_the_http_libraries_do_not_log_every_request(tmp_path: Path) -> None:
    logging_config.configure_logging()
    target = set_file_logging(True, tmp_path)
    logging.getLogger("httpx").info("HTTP Request: POST ... getUpdates")
    logging.getLogger("lexitrack.test").info("our own line")
    text = target.read_text(encoding="utf-8")
    assert "our own line" in text
    assert "getUpdates" not in text


def test_a_week_of_files_is_kept(tmp_path: Path) -> None:
    for age in range(10):
        day = (date.today() - timedelta(days=age)).isoformat()
        (tmp_path / f"lexitrack-{day}.log").write_text("x", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("keep me", encoding="utf-8")
    removed = prune_logs(tmp_path, keep_days=7)
    assert len(removed) == 2
    assert len(list(tmp_path.glob("lexitrack-*.log"))) == 8
    assert (tmp_path / "notes.txt").exists()


def test_logs_live_beside_the_data_not_inside_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEXITRACK_DATA_DIR", raising=False)
    monkeypatch.delenv("LEXITRACK_LOG_DIR", raising=False)
    folder = paths.logs_dir()
    assert folder.name == "logs"
    assert paths.data_dir() not in folder.parents


def test_the_old_always_on_log_is_removed(isolated_data_dir: Path) -> None:
    isolated_data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("lexitrack.log", "lexitrack.log.1"):
        (isolated_data_dir / name).write_text("old", encoding="utf-8")
    (isolated_data_dir / "vocabulary.db").write_text("not a log", encoding="utf-8")
    removed = remove_legacy_logs()
    assert sorted(path.name for path in removed) == ["lexitrack.log", "lexitrack.log.1"]
    assert (isolated_data_dir / "vocabulary.db").exists()


def test_the_env_override_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEXITRACK_LOG_DIR", str(tmp_path / "custom"))
    assert paths.logs_dir() == (tmp_path / "custom").resolve()
    assert os.path.basename(paths.log_file("2026-09-18")) == "lexitrack-2026-09-18.log"
