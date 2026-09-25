"""Export and backup (ui/export_center.py, services/word_filter.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lexitrack.database.connection import Database
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.services.learning_service import LearningService
from lexitrack.services.vocabulary_service import VocabularyService
from lexitrack.services.word_filter import LearningState, WordFilter, filter_words
from lexitrack.ui import export_center as module
from lexitrack.ui.export_center import ExportCenter


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture
def setup(qapp, database: Database):
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackExportTest")
    QSettings().clear()
    service = VocabularyService(database)
    first = service.create_list("First", language="en").id
    second = service.create_list("Second", language="en").id
    for word, level in (("apple", "A1"), ("bridge", "A2"), ("candle", "B1")):
        service.add_word(first, word, cefr_level=level)
    service.add_word(second, "dagger", cefr_level="B2")
    words = {w.word: w.id for lst in (first, second) for w in service.list_words(lst)}
    service.set_status([words["apple"], words["bridge"], words["dagger"]], ReviewStatus.UNKNOWN)
    service.set_status([words["candle"]], ReviewStatus.KNOWN)
    engine = LearningService(database)
    engine.create_plan("Plan", list_ids=[first, second])
    yield service, engine, words, first
    QSettings().clear()


def test_filters_narrow_together(setup) -> None:
    service, engine, words, first = setup

    def names(**kwargs) -> set[str]:
        return {w.word for w in filter_words(service, engine, WordFilter(**kwargs))}

    assert names() == {"apple", "bridge", "candle", "dagger"}
    assert names(list_id=first) == {"apple", "bridge", "candle"}
    assert names(status=ReviewStatus.UNKNOWN) == {"apple", "bridge", "dagger"}
    assert names(status=ReviewStatus.UNKNOWN, cefr="a2") == {"bridge"}
    engine.introduce([words["apple"]])
    assert names(state=LearningState.NOT_STARTED) == {"bridge", "candle", "dagger"}
    assert names(state=LearningState.IN_PROGRESS) == {"apple"}
    assert names(state=LearningState.HARD) == set()
    assert names(state=LearningState.LONG_TERM) == set()


def test_the_window_counts_exports_and_restores(
    setup, qtbot, tmp_path: Path, monkeypatch, database: Database
) -> None:
    service, engine, words, _first = setup
    engine.introduce([words["apple"]])
    center = ExportCenter(service, engine)
    qtbot.addWidget(center)
    assert center.words_count.text().startswith("4 words")
    center.status.setCurrentIndex(center.status.findData(ReviewStatus.UNKNOWN))
    assert center.words_count.text().startswith("3 words")

    target = tmp_path / "all.lexitrack"
    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    center._export_portable()
    assert target.exists() and "4 words" in center.message.text()

    # Something changes after the backup, then the backup is restored.
    database.connection.execute("DELETE FROM srs_cards")
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(module, "confirm", lambda *a, **k: True)
    center._restore_portable()
    assert center.changed
    assert "Restored from all.lexitrack" in center.message.text()
    assert database.connection.execute("SELECT COUNT(*) FROM srs_cards").fetchone()[0] == 1


def test_nothing_is_replaced_without_saying_yes(
    setup, qtbot, tmp_path: Path, monkeypatch, database: Database
) -> None:
    service, engine, _words, _first = setup
    center = ExportCenter(service, engine)
    qtbot.addWidget(center)
    target = tmp_path / "all.lexitrack"
    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    center._export_portable()
    database.connection.execute("DELETE FROM list_words")
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(module, "confirm", lambda *a, **k: False)
    center._restore_portable()
    assert not center.changed
    assert database.connection.execute("SELECT COUNT(*) FROM list_words").fetchone()[0] == 0


def test_answers_and_attempts_export(setup, qtbot, tmp_path: Path, monkeypatch) -> None:
    service, engine, _words, _first = setup
    center = ExportCenter(service, engine)
    qtbot.addWidget(center)
    target = tmp_path / "a.csv"
    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    center._export_answers()
    assert target.exists() and "answers written" in center.message.text()
    center._export_attempts()
    assert "attempts written" in center.message.text()
