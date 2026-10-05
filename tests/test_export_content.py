"""Word exports with contexts (exporters/, services/export_service.py).

A PDF or CSV shows the chosen columns — part of speech, level, length,
status, definition, contexts; a JSON word list holds every field; nothing about
scheduling is ever in a word export.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lexitrack.database.connection import Database
from lexitrack.exporters import pdf_exporter
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.services.export_service import DEFAULT_COLUMNS, ExportColumn, ExportFormat
from lexitrack.services.learning_service import LearningService
from lexitrack.services.vocabulary_service import VocabularyService
from lexitrack.ui.export_dialog import ExportDialog, ExportScope

ALL = tuple(ExportColumn)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture
def words(database: Database):
    """Two words: one with two contexts, one with none."""
    service = VocabularyService(database)
    list_id = service.create_list("Sample", language="en").id
    service.add_word(list_id, "reluctant", part_of_speech="adjective", cefr_level="B2",
                     definition="unwilling and hesitant",
                     contexts=["She was reluctant to leave.", "A reluctant witness spoke."])
    service.add_word(list_id, "commute", part_of_speech="verb", cefr_level="B1",
                     definition="to travel between home and work")
    ids = {w.word: w.id for w in service.list_words(list_id)}
    return service, list_id, ids


def _pdf_text(path: Path) -> str:
    with pymupdf.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_default_export_is_the_study_sheet_it_always_was(words, tmp_path: Path) -> None:
    service, list_id, _ids = words
    content = service.export_content_for_list(list_id)
    assert content.columns == DEFAULT_COLUMNS
    rows = _csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))
    assert list(rows[0]) == ["Word", "Part of Speech", "CEFR", "Definition", "Sources"]
    text = _pdf_text(service.export(content, tmp_path / "a.pdf", ExportFormat.PDF))
    assert "DEFINITION" in text and "She was reluctant" not in text


def test_a_csv_holds_the_chosen_columns(words, tmp_path: Path) -> None:
    service, list_id, _ids = words
    content = replace(service.export_content_for_list(list_id), columns=ALL)
    rows = _csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))
    assert list(rows[0]) == [
        "Word", "Part of Speech", "CEFR", "Length", "Status", "Definition", "Contexts",
        "Sources",
    ]
    first = next(row for row in rows if row["Word"] == "reluctant")
    assert first["Length"] == "9"
    assert first["Contexts"] == "She was reluctant to leave.\nA reluctant witness spoke."
    empty = next(row for row in rows if row["Word"] == "commute")
    assert empty["Contexts"] == ""

    only = replace(content, columns=(ExportColumn.CONTEXTS,))
    rows = _csv(service.export(only, tmp_path / "b.csv", ExportFormat.CSV))
    assert list(rows[0]) == ["Word", "Contexts", "Sources"]


def test_a_json_word_list_holds_every_field_with_the_contexts(words, tmp_path: Path) -> None:
    service, list_id, _ids = words
    content = replace(service.export_content_for_list(list_id), columns=())
    data = json.loads(service.export(content, tmp_path / "a.json", ExportFormat.JSON)
                      .read_text(encoding="utf-8"))
    assert data["words"][0] == {
        "word": "reluctant", "length": 9, "part_of_speech": "adjective", "cefr_level": "B2",
        "definition": "unwilling and hesitant",
        "contexts": ["She was reluctant to leave.", "A reluctant witness spoke."],
    }
    assert "contexts" not in data["words"][1]


def test_no_scheduling_data_is_ever_in_a_word_export(words, tmp_path: Path) -> None:
    service, list_id, _ids = words
    engine = LearningService(service.database)
    engine.create_plan("Plan", list_ids=[list_id])
    engine.introduce()
    # Every column but the status, whose "Not reviewed" is a status, not a schedule.
    columns = tuple(column for column in ALL if column is not ExportColumn.STATUS)
    content = replace(service.export_content_for_list(list_id), columns=columns)
    header = " ".join(_csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))[0])
    text = _pdf_text(service.export(content, tmp_path / "a.pdf", ExportFormat.PDF))
    for scheduling in ("due", "stability", "difficulty", "interval", "review", "state"):
        assert scheduling not in header.casefold()
        assert scheduling not in text.casefold()


def test_the_status_column_says_known_unknown_or_not_reviewed(words, tmp_path: Path) -> None:
    service, list_id, ids = words
    service.set_status([ids["reluctant"]], ReviewStatus.KNOWN)
    content = replace(
        service.export_content_for_list(list_id),
        columns=(ExportColumn.CEFR, ExportColumn.STATUS),
    )
    rows = _csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))
    assert list(rows[0]) == ["Word", "CEFR", "Status", "Sources"]
    assert {row["Word"]: row["Status"] for row in rows} == {
        "reluctant": "Known", "commute": "Not reviewed",
    }

    for columns in (content.columns, (*content.columns, ExportColumn.CONTEXTS)):
        text = _pdf_text(service.export(replace(content, columns=columns),
                                        tmp_path / "a.pdf", ExportFormat.PDF))
        assert "Known" in text and "Not reviewed" in text
        # The top of the sheet counts each status.
        assert "• Known 1      • Unknown 0      • Not reviewed 1" in text

    # Off by default; and the embedded word list never carries a status.
    plain = _pdf_text(service.export(service.export_content_for_list(list_id),
                                     tmp_path / "b.pdf", ExportFormat.PDF))
    assert "Known" not in plain
    data = json.loads(service.export(content, tmp_path / "a.json", ExportFormat.JSON)
                      .read_text(encoding="utf-8"))
    assert "status" not in json.dumps(data)


def test_a_pdf_with_contexts_lists_each_word_as_an_entry(words, tmp_path: Path) -> None:
    service, list_id, _ids = words
    content = replace(service.export_content_for_list(list_id), columns=ALL)
    target = service.export(content, tmp_path / "a.pdf", ExportFormat.PDF)
    text = _pdf_text(target)
    for expected in ("unwilling and hesitant", "A reluctant witness spoke.", "9 letters"):
        assert expected in text, expected
    assert "DEFINITION" not in text, "entries, not a table"
    # The word in a context is set in semibold.
    with pymupdf.open(target) as document:
        spans = [
            span for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", []) for span in line["spans"]
        ]
    assert any(
        span["text"] == "reluctant" and "SemiboldIta" in span["font"] for span in spans
    )


def test_the_pdf_font_covers_the_text(caplog, monkeypatch) -> None:
    serif = "LexiSourceSerif4"
    assert pdf_exporter.fonts_for(["ş ğ ı İ ö ü ç ß ñ é"]).regular == serif
    assert pdf_exporter.fonts_for(["неохотный", "απρόθυμος"]).regular == serif
    monkeypatch.setattr(pdf_exporter, "system_families", lambda: [])
    with caplog.at_level(logging.WARNING, logger=pdf_exporter.__name__):
        fonts = pdf_exporter.fonts_for(["متردد"])
    assert fonts.regular == serif, "the export is still written"
    assert "No installed font" in caplog.text


@pytest.mark.skipif(
    not all(f.exists() for f in dict(pdf_exporter.system_families()).get("Arial", ())),
    reason="Arial is not installed",
)
def test_arabic_is_set_in_a_system_font_that_has_it() -> None:
    assert pdf_exporter.fonts_for(["متردد"]).regular == "LexiArial"


def test_the_dialog_offers_every_column(qapp, qtbot, words) -> None:
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackColumnsTest")
    QSettings().clear()
    service, list_id, _ids = words
    scope = ExportScope("All", lambda: service.export_content_for_list(list_id))
    dialog = ExportDialog(service, [scope])
    qtbot.addWidget(dialog)
    boxes = dialog._column_boxes
    assert set(boxes) == set(ExportColumn)
    assert dialog.selected_columns() == DEFAULT_COLUMNS

    boxes[ExportColumn.CONTEXTS].setChecked(True)
    dialog._render_preview()
    assert "1 with contexts" in dialog.summary.text()

    dialog._format_buttons[ExportFormat.JSON].click()
    assert not dialog.columns_box.isEnabled()
    assert "contexts" in dialog.columns_hint.text()
    QSettings().clear()


def test_the_dialog_remembers_the_columns(
    qapp, qtbot, words, tmp_path: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QFileDialog

    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackColumnsTest")
    QSettings().clear()
    service, list_id, _ids = words
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "out.pdf"), "")))
    scope = ExportScope("All", lambda: service.export_content_for_list(list_id))
    first = ExportDialog(service, [scope])
    qtbot.addWidget(first)
    first._column_boxes[ExportColumn.PART_OF_SPEECH].setChecked(False)
    first._column_boxes[ExportColumn.CONTEXTS].setChecked(True)
    first._export()
    # Contexts make the sheet a list of entries, without the table's header.
    text = _pdf_text(tmp_path / "out.pdf")
    assert "She was reluctant to leave." in text and "DEFINITION" not in text

    second = ExportDialog(service, [scope])
    qtbot.addWidget(second)
    assert second.selected_columns() == (
        ExportColumn.CEFR, ExportColumn.DEFINITION, ExportColumn.CONTEXTS,
    )
    QSettings().clear()
