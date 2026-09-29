"""PDFs LexiTrack exported import back from the word list inside them
(parsers/lexitrack_pdf.py, docs/DECISIONS.md 88).

Every exported PDF embeds ``lexitrack-words.json``, the file the phone embeds
too. The LexiTrack PDF parser reads only that file, never the page text; a PDF
without it goes to the Oxford and generic parsers as before.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from lexitrack.core.errors import InvalidFileError
from lexitrack.services.export_service import ExportFormat
from lexitrack.services.import_service import ImportTarget, NewList
from lexitrack.services.vocabulary_service import VocabularyService

FILE = "lexitrack-words.json"


@pytest.fixture
def sample(service: VocabularyService):
    """A list of two words: one with two contexts, one with none."""
    target = service.create_list("Sample", language="en", description="Two words")
    service.add_word(target.id, "reluctant", part_of_speech="adjective", cefr_level="B2",
                     definition="unwilling and hesitant",
                     contexts=["She was reluctant to leave.", "A reluctant witness spoke."])
    service.add_word(target.id, "naïve", definition="lacking experience — “trusting”")
    return service, target.id


def _export(service: VocabularyService, list_id: int, path: Path, **changes) -> Path:
    content = replace(service.export_content_for_list(list_id), **changes)
    return service.export(content, path, ExportFormat.PDF)


def _embedded(path: Path) -> dict:
    with pymupdf.open(path) as document:
        assert document.embfile_names() == [FILE]
        return json.loads(document.embfile_get(FILE))


def test_an_exported_pdf_carries_its_whole_word_list(sample, tmp_path: Path) -> None:
    service, list_id = sample
    # The default sheet prints no contexts; the embedded list has them anyway.
    data = _embedded(_export(service, list_id, tmp_path / "sheet.pdf"))
    assert (data["format"], data["version"]) == ("lexitrack-words", 1)
    assert (data["name"], data["language"], data["description"]) == ("Sample", "en", "Two words")
    assert data["words"] == [
        {"word": "reluctant", "length": 9, "part_of_speech": "adjective", "cefr_level": "B2",
         "definition": "unwilling and hesitant",
         "contexts": ["She was reluctant to leave.", "A reluctant witness spoke."]},
        {"word": "naïve", "length": 5, "definition": "lacking experience — “trusting”"},
    ]


def test_an_exported_pdf_imports_back_whole(sample, tmp_path: Path) -> None:
    service, list_id = sample
    path = _export(service, list_id, tmp_path / "sheet.pdf", columns=())

    preview = service.prepare_import(path)
    assert preview.parser_key == "lexitrack-pdf"
    assert preview.format_label == "PDF · LexiTrack PDF"
    assert (preview.suggested.name, preview.stated_language) == ("Sample", "en")
    assert preview.suggested.description == "Two words"
    assert preview.source.key == "lexitrack-pdf:sheet-pdf"
    assert {e.source_id for e in preview.entries} == {"lexitrack-pdf:sheet-pdf"}

    service.commit_import(preview, ImportTarget(new_list=NewList("Back", "en")))
    back = next(lst for lst in service.lists() if lst.name == "Back")
    words = {w.word: w for w in service.list_words(back.id)}
    assert set(words) == {"reluctant", "naïve"}
    reluctant = words["reluctant"]
    assert (reluctant.part_of_speech, reluctant.cefr_level) == ("adjective", "B2")
    assert reluctant.definition == "unwilling and hesitant"
    assert [c.text for c in service.contexts(reluctant.id)] == [
        "She was reluctant to leave.", "A reluctant witness spoke.",
    ]
    assert words["naïve"].definition == "lacking experience — “trusting”"


def test_a_pdf_the_phone_exported_imports(service: VocabularyService, tmp_path: Path) -> None:
    # The phone stores the file uncompressed; MuPDF reads it all the same.
    path = _phone_pdf(tmp_path / "phone.pdf", {
        "format": "lexitrack-words", "version": 1, "name": "From the phone", "language": "en",
        "words": [{"word": "abandon", "length": 7, "cefr_level": "B2",
                   "definition": "to leave and not return",
                   "contexts": ["They abandoned the car in the snow."]}],
    })
    preview = service.prepare_import(path)
    assert preview.parser_key == "lexitrack-pdf"
    assert preview.suggested.name == "From the phone"
    [entry] = preview.entries
    assert (entry.word, entry.cefr_level, entry.definition) == (
        "abandon", "B2", "to leave and not return",
    )
    assert entry.contexts == ("They abandoned the car in the snow.",)


def test_other_pdfs_still_go_to_their_parsers(service: VocabularyService, make_pdf) -> None:
    path = make_pdf(["The quick brown fox jumps over the lazy dog."])
    assert service.prepare_import(path).parser_key == "generic"


def test_an_embedded_file_that_is_not_a_lexitrack_list_is_not_read(
    service: VocabularyService, make_pdf
) -> None:
    path = make_pdf(["The quick brown fox jumps over the lazy dog."])
    with pymupdf.open(path) as document:
        document.embfile_add(FILE, json.dumps({"words": ["hidden"]}).encode())
        document.saveIncr()
    preview = service.prepare_import(path)
    assert preview.parser_key == "generic"
    assert "hidden" not in {e.word for e in preview.entries}


def test_choosing_it_for_another_pdf_says_why_not(service: VocabularyService, make_pdf) -> None:
    path = make_pdf(["The quick brown fox jumps over the lazy dog."], name="novel.pdf")
    with pytest.raises(InvalidFileError, match="novel.pdf was not exported by LexiTrack"):
        service.prepare_import(path, parser_key="lexitrack-pdf")


def _phone_pdf(path: Path, word_list: dict) -> Path:
    """A PDF laid out as the phone's ``pdf`` package writes one: a page of
    text, and the word list as an uncompressed embedded file in the catalog's
    name tree."""
    data = json.dumps(word_list, ensure_ascii=False).encode("utf-8")
    text = b"BT /F1 12 Tf 72 720 Td (From the phone) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Names 6 0 R /AF [7 0 R] >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(text), text),
        b"<< /EmbeddedFiles << /Names [(lexitrack-words.json) 7 0 R] >> >>",
        b"<< /Type /Filespec /F (lexitrack-words.json) /UF (lexitrack-words.json) "
        b"/EF << /F 8 0 R >> /AFRelationship /Alternative >>",
        b"<< /Type /EmbeddedFile /Subtype /application#2Fjson /Params << /Size %d >> "
        b"/Length %d >>\nstream\n%s\nendstream" % (len(data), len(data), data),
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref,
    )
    path.write_bytes(bytes(out))
    return path
