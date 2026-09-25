"""The word-content window (ui/content_dialog.py).

Exporting a batch lists it as waiting and leaves its words out of the next;
the import preview shows what would change, and replaces only what is ticked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from lexitrack.database.connection import Database
from lexitrack.models.content import WordLocalization
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories import ContentRepository
from lexitrack.services.content_service import ContentService
from lexitrack.services.learning_service import LearningService
from lexitrack.services.vocabulary_service import VocabularyService
from lexitrack.ui import content_dialog as module
from lexitrack.ui.content_dialog import ContentDialog, ContentImportDialog


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
    for word in ("reluctant", "cramped", "apple"):
        service.add_word(list_id, word, definition=f"about {word}")
    ids = [w.id for w in service.list_words(list_id)]
    service.set_status(ids, ReviewStatus.UNKNOWN)
    engine = LearningService(database)
    engine.create_plan("Plan", list_ids=[list_id])
    yield service, engine, ids
    QSettings().clear()


def test_a_selection_is_exported_and_waits(setup, qtbot, tmp_path: Path, monkeypatch) -> None:
    service, engine, ids = setup
    dialog = ContentDialog(service, engine, word_ids=ids[:2])
    qtbot.addWidget(dialog)
    assert dialog.source.currentData() == "selection"
    assert "3 with nothing yet" not in dialog.status_label.text()
    assert dialog.status_scope.text() == "2 words"
    assert dialog.export_summary.text().startswith("batch_001: 2 words")

    target = tmp_path / "out.json"
    monkeypatch.setattr(
        module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "")
    )
    dialog.languages.setText("de")
    dialog._export()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert [w["word_id"] for w in document["words"]] == ids[:2]
    assert document["learner_languages"] == ["de"]
    assert dialog.open_group.isVisibleTo(dialog)
    assert "Written to out.json" in dialog.export_summary.text()

    # From the whole plan, the words out in batch 1 are skipped.
    dialog.source.setCurrentIndex(dialog.source.findData("plan"))
    assert dialog._candidates == [ids[2]]
    assert dialog.export_summary.text().startswith("batch_002: 1 word ")


def test_the_import_preview_replaces_only_what_is_ticked(
    setup, qtbot, tmp_path: Path, database: Database
) -> None:
    _service, _engine, ids = setup
    repo = ContentRepository(database)
    for word_id in ids[:2]:
        repo.save_localization(
            WordLocalization(word_id=word_id, learner_language="de", core_meaning="old")
        )
    path = tmp_path / "filled.json"
    path.write_text(json.dumps({
        "format": "lexitrack-content", "schema_version": 2, "batch": "batch_001",
        "words": [
            {"word_id": ids[0], "word": "reluctant",
             "localizations": {"de": {"core_meaning": "widerwillig"}}},
            {"word_id": ids[1], "word": "cramped",
             "localizations": {"de": {"core_meaning": "eng"}}},
            {"word_id": 999, "word": "ghost"},
        ],
    }), encoding="utf-8")
    content = ContentService(database)
    dialog = ContentImportDialog(content, content.preview_import(path))
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 2
    assert "reluctant · core meaning (German)" in dialog.table.item(0, 1).text()
    assert dialog.chosen() == [], "nothing replaced unless chosen"
    assert "Rejected" in dialog.notes.toPlainText()

    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    dialog.import_button.click()
    assert "1 replaced" in dialog.result_text
    assert repo.localization(ids[0], "de").core_meaning == "widerwillig"
    assert repo.localization(ids[1], "de").core_meaning == "old"
