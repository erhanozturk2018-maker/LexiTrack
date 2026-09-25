"""Teaching content and the skill record (schema 5).

Content is optional and arrives in batches from outside the application, so
the tests guard what a bad batch could do: write onto the wrong word, erase
what an earlier batch added, or be imported as a word list by mistake.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lexitrack.core.errors import InvalidFileError
from lexitrack.database.connection import Database
from lexitrack.models.attempt import Depth, Effort, LearningAttempt, Phase, Role, Task
from lexitrack.models.content import (
    ContentStatus,
    EncodingType,
    WordContent,
    WordContext,
    content_status,
)
from lexitrack.models.source import Source
from lexitrack.parsers.json_document import JsonDocument
from lexitrack.parsers.json_parser import JsonParser
from lexitrack.repositories import (
    AttemptRepository,
    ContentRepository,
    SourceRepository,
    WordRepository,
)
from lexitrack.services.content_service import FORMAT, ContentService

from .conftest import entry


@pytest.fixture
def words(database: Database) -> dict[str, int]:
    source = SourceRepository(database).upsert(
        Source(key="test", name="Test source", parser_type="generic")
    )
    WordRepository(database).add_entries(
        [entry("reluctant", cefr_level="B2"), entry("cramped", cefr_level="C1"), entry("apple")],
        source.id,
    )
    rows = database.connection.execute("SELECT id, normalized_word FROM words").fetchall()
    return {row["normalized_word"]: int(row["id"]) for row in rows}


def _entry(word_id: int, word: str, **content: object) -> dict:
    return {
        "word_id": word_id,
        "word": word,
        "content": content,
        "contexts": [
            {
                "kind": "sentence",
                "text": f"She was {{{{{word}}}}} to leave.",
                "translation_tr": "x",
            },
            {"kind": "situation", "text": f"A {{{{{word}}}}} moment at work."},
        ],
    }


def _file(tmp_path: Path, entries: list[dict], batch: str = "batch_001") -> Path:
    path = tmp_path / f"{batch}.json"
    path.write_text(
        json.dumps({"format": FORMAT, "schema_version": 1, "batch": batch, "words": entries}),
        encoding="utf-8",
    )
    return path


# -- the model -------------------------------------------------------------------


def test_content_status_needs_meaning_usage_and_two_contexts() -> None:
    assert content_status(None, 0) is ContentStatus.NONE
    assert content_status(WordContent(word_id=1), 0) is ContentStatus.NONE
    meaning = WordContent(word_id=1, core_meaning_tr="unwilling")
    assert content_status(meaning, 0) is ContentStatus.PARTIAL
    assert content_status(None, 1) is ContentStatus.PARTIAL
    full = WordContent(word_id=1, core_meaning_tr="unwilling", pattern="reluctant to do")
    assert content_status(full, 1) is ContentStatus.PARTIAL
    assert content_status(full, 2) is ContentStatus.COMPLETE


def test_context_marks_and_blanks_the_word() -> None:
    context = WordContext(word_id=1, text="The room was {{ cramped }} and hot.")
    assert context.target == "cramped"
    assert context.plain == "The room was cramped and hot."
    assert context.blanked() == "The room was _____ and hot."


def test_abstract_encodings() -> None:
    assert EncodingType.CONTRAST.is_abstract and EncodingType.RELATION.is_abstract
    assert not EncodingType.IMAGE.is_abstract


# -- repositories ----------------------------------------------------------------


def test_content_round_trip(database: Database, words: dict[str, int]) -> None:
    repo = ContentRepository(database)
    word_id = words["reluctant"]
    assert repo.teaching(word_id).status is ContentStatus.NONE

    repo.save_content(
        WordContent(
            word_id=word_id,
            core_meaning_tr="unwilling, hesitant",
            collocations=("reluctant to admit", "a reluctant hero"),
            encoding_type=EncodingType.CONTRAST,
            source="batch_001",
        )
    )
    repo.add_contexts(
        [
            WordContext(word_id=word_id, text="He was {{reluctant}} to go."),
            WordContext(word_id=word_id, text="A {{reluctant}} yes."),
        ]
    )
    teaching = repo.teaching(word_id)
    assert teaching.content is not None
    assert teaching.content.collocations == ("reluctant to admit", "a reluctant hero")
    assert teaching.content.encoding_type is EncodingType.CONTRAST
    assert teaching.content.updated_at
    assert teaching.status is ContentStatus.COMPLETE
    statuses = repo.statuses(words.values())
    assert statuses[word_id] is ContentStatus.COMPLETE
    assert statuses[words["apple"]] is ContentStatus.NONE


def test_attempts_are_kept_when_undone(database: Database, words: dict[str, int]) -> None:
    repo = AttemptRepository(database)
    at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    first = repo.add(
        LearningAttempt(
            word_id=words["cramped"], at=at, on_day="2026-09-25", phase=Phase.INTRODUCTION,
            role=Role.RETRIEVAL, task=Task.MEANING_TO_WORD, success=True,
            effort=Effort.NORMAL, depth=Depth.SHORT,
        )
    )
    repo.add(
        LearningAttempt(
            word_id=words["cramped"], at=at, on_day="2026-09-25", phase=Phase.REVIEW,
            role=Role.PRIMARY, task=Task.WORD_TO_MEANING, success=False,
        )
    )
    assert [a.task for a in repo.for_word(words["cramped"])] == [
        Task.MEANING_TO_WORD, Task.WORD_TO_MEANING,
    ]
    assert repo.mark_undone([first], at) == 1
    assert len(repo.for_word(words["cramped"])) == 1
    kept = repo.for_word(words["cramped"], include_undone=True)
    assert len(kept) == 2 and kept[0].undone_at == at
    assert kept[0].depth is Depth.SHORT and kept[0].level == 2


# -- the enrichment file ------------------------------------------------------------


def test_batch_lists_what_each_word_needs(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    path = service.export_batch(
        [words["reluctant"], words["apple"]], tmp_path / "out.json", "batch_001"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == FORMAT and document["schema_version"] == 1
    assert "encoding_type" in document["instructions"]
    assert [w["word"] for w in document["words"]] == ["reluctant", "apple"]
    assert "core_meaning_tr" in document["words"][0]["needs"]
    assert "contexts" in document["words"][0]["needs"]


def test_import_fills_empty_fields_and_records_the_batch(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    path = _file(
        tmp_path,
        [_entry(words["reluctant"], "reluctant", core_meaning_tr="unwilling",
                pattern="reluctant to do sth", encoding_type="contrast", depth_hint="Deep")],
    )
    preview = service.preview_import(path)
    assert not preview.rejected
    assert preview.fill_count == 4 and preview.context_count == 2
    result = service.apply_import(preview)
    assert result.words == 1 and result.contexts_added == 2

    teaching = service.teaching(words["reluctant"])
    assert teaching.content is not None
    assert teaching.content.encoding_type is EncodingType.CONTRAST
    assert teaching.content.source == "batch_001"
    assert teaching.status is ContentStatus.COMPLETE
    assert service.needing_content([words["reluctant"], words["apple"]]) == [words["apple"]]


def test_import_never_overwrites_without_being_told(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    word_id = words["reluctant"]
    service.apply_import(
        service.preview_import(_file(tmp_path, [_entry(word_id, "reluctant", core_meaning_tr="A")]))
    )
    second = service.preview_import(
        _file(tmp_path, [_entry(word_id, "reluctant", core_meaning_tr="B")], "batch_002")
    )
    assert second.conflict_count == 1
    # The same contexts again are recognised, not added twice.
    assert second.context_count == 0 and second.plans[0].duplicate_contexts == 2

    service.apply_import(second)
    assert service.teaching(word_id).content.core_meaning_tr == "A"

    service.apply_import(second, replace_fields=[(word_id, "core_meaning_tr")])
    assert service.teaching(word_id).content.core_meaning_tr == "B"


def test_import_refuses_an_id_that_names_another_word(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    preview = service.preview_import(
        _file(
            tmp_path,
            [
                _entry(words["apple"], "reluctant", core_meaning_tr="unwilling"),
                _entry(99_999, "ghost", core_meaning_tr="ghost"),
                _entry(words["cramped"], "cramped", core_meaning_tr="narrow"),
                _entry(words["cramped"], "cramped", core_meaning_tr="tight"),
            ],
        )
    )
    assert len(preview.rejected) == 3
    assert [plan.word for plan in preview.plans] == ["cramped"]
    service.apply_import(preview)
    assert service.teaching(words["apple"]).content is None


def test_bad_values_become_warnings_not_data(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    item = _entry(words["cramped"], "cramped", encoding_type="SMELL", core_meaning_tr="narrow")
    item["contexts"].append({"text": "No marker here."})
    preview = service.preview_import(_file(tmp_path, [item]))
    plan = preview.plans[0]
    assert "encoding_type" not in plan.fills
    assert len(plan.new_contexts) == 2
    assert len(plan.warnings) == 2


def test_other_files_are_not_content(tmp_path: Path, database: Database) -> None:
    path = tmp_path / "list.json"
    path.write_text(json.dumps({"words": ["apple"]}), encoding="utf-8")
    with pytest.raises(InvalidFileError):
        ContentService(database).preview_import(path)


def test_a_content_file_is_not_imported_as_a_word_list(tmp_path: Path) -> None:
    path = _file(tmp_path, [_entry(1, "reluctant")])
    with pytest.raises(InvalidFileError, match="content file"):
        JsonParser().parse(JsonDocument.open(path))
