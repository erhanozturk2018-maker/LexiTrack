"""Word exports with teaching content (exporters/, services/export_service.py).

A PDF or CSV shows the chosen columns; teaching content comes in the learner's
language; nothing about scheduling is ever in a word export.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lexitrack.database.connection import Database
from lexitrack.exporters import pdf_exporter
from lexitrack.models.content import WordContent, WordContext, WordLocalization
from lexitrack.repositories import ContentRepository, SettingsRepository
from lexitrack.services.export_service import (
    DEFAULT_COLUMNS,
    ExportColumn,
    ExportFormat,
)
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
def taught(database: Database):
    """Two words: one with content in Turkish and German, one with none."""
    service = VocabularyService(database)
    list_id = service.create_list("Sample", language="en").id
    service.add_word(list_id, "reluctant", part_of_speech="adjective", cefr_level="B2",
                     definition="unwilling and hesitant")
    service.add_word(list_id, "commute", part_of_speech="verb", cefr_level="B1",
                     definition="to travel between home and work")
    ids = {w.word: w.id for w in service.list_words(list_id)}
    repo = ContentRepository(database)
    word = ids["reluctant"]
    repo.save_content(WordContent(word_id=word, pattern="reluctant to do sth",
                                  collocations=("a reluctant hero", "reluctantly agree")))
    for language, meaning in (("tr", "meaning-in-tr ş ğ ı"), ("de", "meaning-in-de")):
        repo.save_localization(WordLocalization(word_id=word, learner_language=language,
                                                core_meaning=meaning, nuance=f"nuance-{language}",
                                                usage_note=f"usage-{language}"))
    first, second = repo.add_contexts([
        WordContext(word_id=word, text="She was {{reluctant}} to leave."),
        WordContext(word_id=word, text="A {{reluctant}} witness spoke."),
    ])
    repo.save_translation(first, "tr", "translation-in-tr")
    repo.save_translation(first, "de", "translation-in-de")
    SettingsRepository(database).set("learner_language", "tr")
    return service, list_id, ids


def _pdf_text(path: Path) -> str:
    with pymupdf.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_teaching_is_read_in_bulk_for_the_learner_language(taught, database) -> None:
    _service, _list_id, ids = taught
    repo = ContentRepository(database)
    many = repo.teachings(ids.values(), "de")
    assert set(many) == set(ids.values())
    word = many[ids["reluctant"]]
    assert word == repo.teaching(ids["reluctant"], "de")
    assert word.core_meaning == "meaning-in-de"
    assert word.translation(word.contexts[0]) == "translation-in-de"
    assert word.translation(word.contexts[1]) is None
    assert many[ids["commute"]].contexts == () and many[ids["commute"]].content is None


def test_the_default_export_is_the_study_sheet_it_always_was(taught, tmp_path: Path) -> None:
    service, list_id, _ids = taught
    content = service.export_content_for_list(list_id)
    assert content.columns == DEFAULT_COLUMNS
    rows = _csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))
    assert list(rows[0]) == [
        "Word", "Part of Speech", "CEFR", "Definition", "Example", "Note", "Sources",
    ]
    text = _pdf_text(service.export(content, tmp_path / "a.pdf", ExportFormat.PDF))
    assert "DEFINITION" in text and "meaning-in-tr" not in text


def test_a_csv_holds_the_chosen_columns_in_the_learner_language(
    taught, tmp_path: Path
) -> None:
    service, list_id, _ids = taught
    content = replace(service.export_content_for_list(list_id), columns=ALL)
    rows = _csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))
    first = next(row for row in rows if row["Word"] == "reluctant")
    assert first["Meaning (Turkish)"] == "meaning-in-tr ş ğ ı"
    assert first["Nuance (Turkish)"] == "nuance-tr"
    assert first["Usage Note (Turkish)"] == "usage-tr"
    assert first["Pattern"] == "reluctant to do sth"
    assert first["Collocations"] == "a reluctant hero; reluctantly agree"
    assert first["Examples"] == "She was reluctant to leave.\nA reluctant witness spoke."
    # Line for line with the examples; the second has no translation yet.
    assert first["Example Translations (Turkish)"] == "translation-in-tr\n"
    empty = next(row for row in rows if row["Word"] == "commute")
    assert empty["Meaning (Turkish)"] == "" and empty["Examples"] == ""

    only = replace(content, columns=(ExportColumn.PATTERN,))
    rows = _csv(service.export(only, tmp_path / "b.csv", ExportFormat.CSV))
    assert list(rows[0]) == ["Word", "Sources", "Pattern"]


def test_no_scheduling_data_is_ever_in_a_word_export(
    taught, tmp_path: Path, engine_for
) -> None:
    service, list_id, ids = taught
    engine = engine_for(service.database)
    engine.create_plan("Plan", list_ids=[list_id])
    engine.introduce()
    content = replace(service.export_content_for_list(list_id), columns=ALL)
    header = " ".join(_csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))[0])
    text = _pdf_text(service.export(content, tmp_path / "a.pdf", ExportFormat.PDF))
    for scheduling in ("due", "stability", "difficulty", "interval", "review", "state"):
        assert scheduling not in header.casefold()
        assert scheduling not in text.casefold()


def test_a_pdf_with_teaching_content_lists_each_word_as_an_entry(
    taught, tmp_path: Path
) -> None:
    service, list_id, _ids = taught
    content = replace(service.export_content_for_list(list_id), columns=ALL)
    target = service.export(content, tmp_path / "a.pdf", ExportFormat.PDF)
    text = _pdf_text(target)
    for expected in ("Meanings and translations in Turkish.", "MEANING", "meaning-in-tr ş ğ ı",
                     "NUANCE", "USAGE", "PATTERN", "COLLOCATIONS", "EXAMPLES",
                     "translation-in-tr", "unwilling and hesitant"):
        assert expected in text, expected
    assert "{{" not in text, "the marker is not printed"
    assert "meaning-in-de" not in text, "only the learner's language"
    # The word in an example is set in bold.
    with pymupdf.open(target) as document:
        spans = [
            span for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", []) for span in line["spans"]
        ]
    assert any(span["text"] == "reluctant" and "Bold" in span["font"] for span in spans)


def test_columns_of_a_learner_language_need_one(taught, tmp_path: Path, database) -> None:
    service, list_id, _ids = taught
    SettingsRepository(database).set("learner_language", "")
    content = replace(service.export_content_for_list(list_id), columns=ALL)
    header = list(_csv(service.export(content, tmp_path / "a.csv", ExportFormat.CSV))[0])
    assert "Pattern" in header and "Examples" in header
    assert not any(name.startswith(("Meaning", "Nuance", "Example Translations"))
                   for name in header)
    text = _pdf_text(service.export(content, tmp_path / "a.pdf", ExportFormat.PDF))
    assert "meaning-in-tr" not in text and "Meanings and translations" not in text


def test_the_pdf_font_covers_the_text(caplog, monkeypatch) -> None:
    assert pdf_exporter.fonts_for(["ş ğ ı İ ö ü ç ß ñ é"]).regular == "LexiVera"
    monkeypatch.setattr(pdf_exporter, "system_families", lambda: [])
    with caplog.at_level(logging.WARNING, logger=pdf_exporter.__name__):
        fonts = pdf_exporter.fonts_for(["неохотный"])
    assert fonts.regular == "LexiVera", "the export is still written"
    assert "No installed font" in caplog.text


@pytest.mark.skipif(
    not all(f.exists() for f in dict(pdf_exporter.system_families()).get("Arial", ())),
    reason="Arial is not installed",
)
def test_cyrillic_is_set_in_a_system_font_that_has_it() -> None:
    assert pdf_exporter.fonts_for(["неохотный"]).regular == "LexiArial"


def test_the_dialog_offers_the_columns_that_can_be_filled(
    qapp, qtbot, taught, database
) -> None:
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackColumnsTest")
    QSettings().clear()
    service, list_id, _ids = taught
    scope = ExportScope("All", lambda: service.export_content_for_list(list_id))
    dialog = ExportDialog(service, [scope])
    qtbot.addWidget(dialog)
    boxes = dialog._column_boxes
    assert dialog.selected_columns() == DEFAULT_COLUMNS
    assert "Turkish" in dialog.teaching_label.text()
    assert not boxes[ExportColumn.TRANSLATIONS].isEnabled(), "examples first"

    boxes[ExportColumn.MEANING].setChecked(True)
    boxes[ExportColumn.EXAMPLES].setChecked(True)
    assert boxes[ExportColumn.TRANSLATIONS].isEnabled()
    dialog._render_preview()
    assert "1 with teaching content" in dialog.summary.text()

    dialog._format_buttons[ExportFormat.JSON].click()
    assert not dialog.columns_box.isEnabled()
    assert "Word Content" in dialog.columns_hint.text()
    dialog._render_preview()
    assert "teaching" not in dialog.summary.text()

    SettingsRepository(database).set("learner_language", "")
    bare = ExportDialog(service, [scope])
    qtbot.addWidget(bare)
    assert not bare._column_boxes[ExportColumn.MEANING].isEnabled()
    assert bare._column_boxes[ExportColumn.PATTERN].isEnabled()
    assert "Settings" in bare.columns_hint.text()
    QSettings().clear()


def test_the_dialog_remembers_the_columns(
    qapp, qtbot, taught, tmp_path: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QFileDialog

    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackColumnsTest")
    QSettings().clear()
    service, list_id, _ids = taught
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "out.pdf"), "")))
    scope = ExportScope("All", lambda: service.export_content_for_list(list_id))
    first = ExportDialog(service, [scope])
    qtbot.addWidget(first)
    first._column_boxes[ExportColumn.PART_OF_SPEECH].setChecked(False)
    first._column_boxes[ExportColumn.PATTERN].setChecked(True)
    first._export()
    assert "PATTERN" in _pdf_text(tmp_path / "out.pdf")

    second = ExportDialog(service, [scope])
    qtbot.addWidget(second)
    assert second.selected_columns() == (
        ExportColumn.CEFR, ExportColumn.DEFINITION, ExportColumn.PATTERN,
    )
    QSettings().clear()


@pytest.fixture
def engine_for():
    return lambda database: LearningService(database)
