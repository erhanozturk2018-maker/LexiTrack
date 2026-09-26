"""The word-contexts window (ui/content_dialog.py).

It counts the words with contexts, exports words with their definition and
contexts as JSON, and previews an import before anything is written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lexitrack.database.connection import Database
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.services.content_service import ContentService
from lexitrack.services.learning_service import LearningService
from lexitrack.services.vocabulary_service import VocabularyService
from lexitrack.ui import content_dialog as module
from lexitrack.ui.content_dialog import ContentDialog, ContextImportDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture
def setup(qapp, database: Database):
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackContentTest")
    QSettings().clear()
    service = VocabularyService(database)
    list_id = service.create_list("Words", language="en").id
    service.add_word(list_id, "reluctant", definition="not willing",
                     contexts=["She was reluctant to leave."])
    for word in ("cramped", "apple"):
        service.add_word(list_id, word, definition=f"about {word}")
    ids = [w.id for w in service.list_words(list_id)]
    service.set_status(ids, ReviewStatus.UNKNOWN)
    engine = LearningService(database)
    engine.create_plan("Plan", list_ids=[list_id])
    yield service, engine, ids
    QSettings().clear()


def test_the_window_counts_contexts_and_exports_the_words_chosen(
    setup, qtbot, tmp_path: Path, monkeypatch
) -> None:
    service, engine, ids = setup
    dialog = ContentDialog(service, engine, word_ids=ids[:2])
    qtbot.addWidget(dialog)
    assert dialog.source.currentData() == "selection"
    assert dialog.status_label.text().startswith("1 of 2 words have contexts")
    assert dialog.export_summary.text().startswith("2 words")

    dialog.only_missing.setChecked(True)
    assert dialog.export_summary.text().startswith("1 word ")

    target = tmp_path / "out.json"
    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    dialog._export()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document == [
        {"word": "cramped", "length": 7, "definition": "about cramped", "contexts": []},
    ]
    assert "written to out.json" in dialog.export_summary.text()


def test_the_import_preview_shows_what_would_be_added(
    setup, qtbot, tmp_path: Path, database: Database
) -> None:
    service, _engine, ids = setup
    path = tmp_path / "filled.json"
    path.write_text(json.dumps([
        {"word": "cramped", "definition": "too small for the people in it",
         "contexts": ["The room was cramped.", "It felt cramped."]},
        {"word": "reluctant", "contexts": ["She was reluctant to leave."]},
        {"word": "ghost", "contexts": ["A ghost."]},
    ]), encoding="utf-8")
    content = ContentService(database)
    dialog = ContextImportDialog(content, content.preview_import(path))
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "cramped"
    assert "not in your vocabulary" in dialog.notes.toPlainText()

    dialog.import_button.click()
    assert dialog.result_text.startswith("Imported 2 contexts for 1 word, and 1 definition.")
    assert [c.text for c in service.contexts(ids[1])] == [
        "The room was cramped.", "It felt cramped.",
    ]
