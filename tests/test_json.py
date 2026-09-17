"""JSON import and export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lexitrack.core.errors import DocumentError, InvalidFileError, NoWordsFoundError
from lexitrack.database.connection import Database
from lexitrack.exporters.json_exporter import build_json_document
from lexitrack.parsers.json_document import JsonDocument, open_document
from lexitrack.parsers.json_parser import JsonParser
from lexitrack.parsers.registry import ParserRegistry
from lexitrack.services.export_service import ExportFormat
from lexitrack.services.import_service import ImportTarget
from lexitrack.services.vocabulary_service import VocabularyService


@pytest.fixture
def write_json(tmp_path: Path):
    def factory(data, name: str = "words.json", raw: bool = False) -> Path:
        path = tmp_path / name
        path.write_text(data if raw else json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    return factory


def parse(path: Path):
    with open_document(path) as document:
        parser = ParserRegistry().select(document)
        return parser, parser.list_metadata(document), parser.parse(document), document.warnings


# -- valid files -----------------------------------------------------------


def test_simple_string_words(write_json) -> None:
    path = write_json(
        {"name": "German A1", "language": "de", "words": ["Haus", "gehen", "kommen"]}
    )
    parser, meta, entries, warnings = parse(path)

    assert isinstance(parser, JsonParser)
    assert (meta.name, meta.language) == ("German A1", "de")
    assert [(e.word, e.normalized_word, e.language) for e in entries] == [
        ("Haus", "haus", "de"),
        ("gehen", "gehen", "de"),
        ("kommen", "kommen", "de"),
    ]
    assert warnings == []


def test_rich_word_objects(write_json) -> None:
    path = write_json(
        {
            "name": "German A1",
            "language": "de",
            "description": "German A1 vocabulary",
            "words": [
                {
                    "word": "Haus",
                    "part_of_speech": "noun",
                    "definition": "house",
                    "example": "Das Haus ist groß.",
                },
                {"word": "gehen", "part_of_speech": "verb", "definition": "to go"},
            ],
        }
    )
    _, meta, entries, _ = parse(path)

    assert meta.description == "German A1 vocabulary"
    haus, gehen = entries
    assert (haus.part_of_speech, haus.definition, haus.example) == (
        "noun",
        "house",
        "Das Haus ist groß.",
    )
    assert (gehen.part_of_speech, gehen.definition, gehen.example) == ("verb", "to go", None)


def test_objects_with_only_a_word(write_json) -> None:
    _, _, entries, _ = parse(write_json({"words": [{"word": "Haus"}, {"word": "gehen"}]}))
    assert [e.word for e in entries] == ["Haus", "gehen"]


def test_mixed_strings_and_objects(write_json) -> None:
    _, _, entries, _ = parse(write_json({"words": ["Haus", {"word": "gehen", "pos": "verb"}]}))
    assert [(e.word, e.part_of_speech) for e in entries] == [("Haus", None), ("gehen", "verb")]


def test_a_bare_array_is_accepted(write_json) -> None:
    _, meta, entries, _ = parse(write_json(["ability", "abandon"], name="my_words.json"))
    assert [e.word for e in entries] == ["ability", "abandon"]
    assert meta.name == "My Words"
    assert meta.language is None


def test_field_aliases(write_json) -> None:
    _, _, entries, _ = parse(
        write_json(
            {"words": [{"word": "abandon", "pos": "verb", "cefr": "b2", "meaning": "leave"}]}
        )
    )
    assert (entries[0].part_of_speech, entries[0].cefr_level, entries[0].definition) == (
        "verb",
        "B2",
        "leave",
    )


def test_language_names_and_regional_codes(write_json) -> None:
    assert parse(write_json({"language": "German", "words": ["Haus"]}))[1].language == "de"
    assert parse(write_json({"language": "en-GB", "words": ["colour"]}))[1].language == "en"


def test_missing_metadata_is_not_invented(write_json) -> None:
    path = write_json({"words": ["ability"]}, name="oxford_3000.json")
    with open_document(path) as document:
        parser = JsonParser()
        meta = parser.list_metadata(document)
        assert meta.language is None
        assert meta.description is None
        # Named like Oxford, but does not claim to be Oxford: provenance is the file.
        assert parser.source_name(document) == "oxford_3000.json"


def test_a_stated_source_is_used_as_provenance(write_json) -> None:
    path = write_json({"source": "Oxford 3000", "words": ["ability"]})
    with open_document(path) as document:
        assert JsonParser().source_name(document) == "Oxford 3000"


def test_a_utf8_bom_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"words": ["Haus"]}).encode())
    assert [e.word for e in parse(path)[2]] == ["Haus"]


def test_duplicate_words_are_kept_for_deduplication_later(write_json) -> None:
    _, _, entries, _ = parse(write_json({"words": ["Haus", "haus", "HAUS"]}))
    assert {e.normalized_word for e in entries} == {"haus"}


def test_per_word_language_overrides_the_file(write_json) -> None:
    _, _, entries, _ = parse(
        write_json(
            {"words": [{"word": "gift", "language": "en"}, {"word": "Gift", "language": "de"}]}
        )
    )
    assert [(e.normalized_word, e.language) for e in entries] == [("gift", "en"), ("gift", "de")]


# -- skipped items ---------------------------------------------------------


def test_non_words_are_skipped_with_a_warning(write_json) -> None:
    _, _, entries, warnings = parse(write_json({"words": ["Haus", "42", "", "   ", "gehen"]}))
    assert [e.word for e in entries] == ["Haus", "gehen"]
    assert len(warnings) == 1
    assert "3 items were skipped" in warnings[0]
    assert "“42”" in warnings[0]


def test_a_file_of_only_non_words_is_rejected(write_json) -> None:
    with pytest.raises(NoWordsFoundError):
        parse(write_json({"words": ["1", "2"]}))


# -- invalid files ---------------------------------------------------------


def test_malformed_json_names_the_location(write_json) -> None:
    path = write_json('{"words": ["Haus",]}', raw=True)
    with pytest.raises(InvalidFileError, match=r"not valid JSON.*line 1"):
        parse(path)


def test_an_empty_file(write_json) -> None:
    with pytest.raises(InvalidFileError, match="empty"):
        parse(write_json("   ", raw=True))


def test_missing_words(write_json) -> None:
    with pytest.raises(InvalidFileError, match="no “words” list"):
        parse(write_json({"name": "German A1"}))


def test_words_of_the_wrong_type(write_json) -> None:
    with pytest.raises(InvalidFileError, match="must be a list, not text"):
        parse(write_json({"words": "Haus, gehen"}))


def test_an_empty_words_list(write_json) -> None:
    with pytest.raises(NoWordsFoundError, match="contains no words"):
        parse(write_json({"words": []}))


def test_a_top_level_value_that_is_not_an_object(write_json) -> None:
    with pytest.raises(InvalidFileError, match="not a number"):
        parse(write_json("42", raw=True))


def test_an_object_without_a_word_field(write_json) -> None:
    with pytest.raises(InvalidFileError, match="Word 2 has no “word” field"):
        parse(write_json({"words": ["Haus", {"definition": "to go"}]}))


def test_an_item_of_the_wrong_type(write_json) -> None:
    with pytest.raises(InvalidFileError, match="Word 1 must be text or an object"):
        parse(write_json({"words": [7]}))


def test_a_field_of_the_wrong_type(write_json) -> None:
    with pytest.raises(InvalidFileError, match="“definition” must be text, not a list"):
        parse(write_json({"words": [{"word": "Haus", "definition": ["house", "home"]}]}))


def test_metadata_of_the_wrong_type(write_json) -> None:
    with pytest.raises(InvalidFileError, match="“name” must be text"):
        parse(write_json({"name": 5, "words": ["Haus"]}))


def test_an_unrecognised_language(write_json) -> None:
    with pytest.raises(InvalidFileError, match="not a language"):
        parse(write_json({"language": "Klingon!", "words": ["Haus"]}))


def test_several_problems_are_reported_together(write_json) -> None:
    path = write_json({"words": [1, 2, 3, 4, 5]})
    with pytest.raises(InvalidFileError) as info:
        parse(path)
    assert "Word 1" in str(info.value) and "and 2 more" in str(info.value)


def test_an_unsupported_file_type(tmp_path: Path) -> None:
    path = tmp_path / "words.txt"
    path.write_text("Haus gehen", encoding="utf-8")
    with pytest.raises(DocumentError, match="PDF and JSON"):
        open_document(path)


def test_json_content_with_another_extension_is_recognised(tmp_path: Path) -> None:
    path = tmp_path / "words.list"
    path.write_text('{"words": ["Haus"]}', encoding="utf-8")
    assert isinstance(open_document(path), JsonDocument)


def test_the_pdf_parsers_are_not_offered_json(write_json) -> None:
    from lexitrack.core.errors import NoParserError

    with open_document(write_json({"words": ["Haus"]})) as document:
        with pytest.raises(NoParserError, match="cannot read"):
            ParserRegistry().select(document, preferred_key="oxford")


# -- export ----------------------------------------------------------------


def test_export_writes_only_what_a_person_would(service: VocabularyService) -> None:
    lst = service.create_list("German A1", "de", "German A1 vocabulary")
    service.add_word(lst.id, "Haus", part_of_speech="noun", definition="house")
    word, _ = service.add_word(lst.id, "gehen")
    service.mark_known(word.id)

    document = build_json_document(
        service.list_words(lst.id), name=lst.name, language=lst.language,
        description=lst.description,
    )

    assert document == {
        "name": "German A1",
        "language": "de",
        "description": "German A1 vocabulary",
        "words": [{"word": "Haus", "part_of_speech": "noun", "definition": "house"}, "gehen"],
    }


def test_export_keeps_per_word_language_in_a_mixed_list(service: VocabularyService) -> None:
    german = service.create_list("German", "de")
    english = service.create_list("English", "en")
    mixed = service.create_list("Mixed")
    haus, _ = service.add_word(german.id, "Haus")
    gift, _ = service.add_word(english.id, "gift")
    service.add_words_to_list(mixed.id, [haus.id, gift.id])

    document = build_json_document(service.list_words(mixed.id), name="Mixed", language="und")

    assert "language" not in document
    assert document["words"] == [
        {"word": "Haus", "language": "de"},
        {"word": "gift", "language": "en"},
    ]


def test_export_file_is_utf8_and_readable(service: VocabularyService, tmp_path: Path) -> None:
    lst = service.create_list("German", "de")
    service.add_word(lst.id, "Straße")
    path = service.export(service.export_content_for_list(lst.id), tmp_path / "out.json", "json")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["words"] == ["Straße"]
    assert "Straße" in path.read_text(encoding="utf-8")  # not \u-escaped


def test_round_trip_through_a_fresh_database(
    service: VocabularyService, write_json, tmp_path: Path
) -> None:
    original = write_json(
        {
            "name": "German A1",
            "language": "de",
            "description": "Basics",
            "words": [
                {"word": "Haus", "part_of_speech": "noun", "definition": "house",
                 "example": "Das Haus ist groß.", "cefr_level": "A1"},
                "gehen",
                {"word": "kommen", "definition": "to come"},
            ],
        },
        name="german_a1.json",
    )
    service.import_document(original)
    first_list = service.lists()[0]
    exported = service.export(
        service.export_content_for_list(first_list.id), tmp_path / "exported.json", "json"
    )

    with VocabularyService(Database(tmp_path / "second.db")) as second:
        second.import_document(exported)
        (restored,) = second.lists()
        assert (restored.name, restored.language, restored.description) == (
            "German A1", "de", "Basics",
        )
        before = [
            (w.word, w.part_of_speech, w.cefr_level, w.definition, w.example, w.language)
            for w in service.list_words(first_list.id)
        ]
        after = [
            (w.word, w.part_of_speech, w.cefr_level, w.definition, w.example, w.language)
            for w in second.list_words(restored.id)
        ]
        assert after == before

    assert json.loads(exported.read_text(encoding="utf-8")) == json.loads(
        original.read_text(encoding="utf-8")
    )


def test_importing_an_export_back_changes_no_review_status(
    service: VocabularyService, tmp_path: Path
) -> None:
    lst = service.create_list("Words", "en")
    word, _ = service.add_word(lst.id, "ability")
    service.mark_unknown(word.id)
    path = service.export(service.export_content_for_list(lst.id), tmp_path / "w.json", "json")

    service.import_document(path, target=ImportTarget(list_ids=(lst.id,)))

    assert service.get_word(word.id).status.value == "unknown"


def test_every_scope_exports_to_json(service: VocabularyService, tmp_path: Path) -> None:
    lst = service.create_list("Words", "en")
    a, _ = service.add_word(lst.id, "ability")
    b, _ = service.add_word(lst.id, "abandon")
    service.mark_unknown(a.id)

    scopes = {
        "list": service.export_content_for_list(lst.id),
        "unknown_in_list": service.export_content_for_unknown(lst.id),
        "all_unknown": service.export_content_for_unknown(),
        "selection": service.export_content_for_selection([b.id], lst.id),
    }
    words = {}
    for key, content in scopes.items():
        path = service.export(content, tmp_path / f"{key}.json", ExportFormat.JSON)
        words[key] = [w if isinstance(w, str) else w["word"] for w in json.loads(
            path.read_text(encoding="utf-8"))["words"]]

    assert words == {
        "list": ["ability", "abandon"],
        "unknown_in_list": ["ability"],
        "all_unknown": ["ability"],
        "selection": ["abandon"],
    }


def test_notes_import_export_and_fill_in_a_word_that_had_none(
    service: VocabularyService, write_json, tmp_path: Path
) -> None:
    lst = service.create_list("English", "en")
    word, _ = service.add_word(lst.id, "crisps")
    assert service.get_word(word.id).note is None

    notes = write_json(
        {
            "name": "English",
            "language": "en",
            "words": [
                {"word": "crisps", "definition": "thin fried potato slices",
                 "note": "UK; chips in US"},
                {"word": "chips", "notes": "US; crisps in UK"},
            ],
        },
        name="notes.json",
    )
    service.import_document(notes)

    crisps = service.get_word(word.id)
    assert (crisps.definition, crisps.note) == ("thin fried potato slices", "UK; chips in US")
    document = build_json_document(service.list_words(lst.id), name="English", language="en")
    assert document["words"] == [
        {"word": "crisps", "definition": "thin fried potato slices", "note": "UK; chips in US"},
        {"word": "chips", "note": "US; crisps in UK"},
    ]
