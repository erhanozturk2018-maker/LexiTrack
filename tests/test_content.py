"""Teaching content, by language, and the skill record.

Content is optional and arrives in batches from outside the application, so
the tests guard what a bad batch could do: write onto the wrong word, erase
what an earlier batch added, or be imported as a word list by mistake.

And no language is special: what is true of a word in its own language is
stored once, and any number of learner languages explain it — tested here
with two on the same word, which stays one word.
"""

from __future__ import annotations

import json
from dataclasses import replace
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
    WordLocalization,
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
        [
            entry("reluctant", cefr_level="B2", language="en"),
            entry("cramped", cefr_level="C1", language="en"),
            entry("apple", language="en"),
            entry("commute", cefr_level="B1", language="en",
                  definition="to travel regularly between home and work"),
        ],
        source.id,
    )
    rows = database.connection.execute("SELECT id, normalized_word FROM words").fetchall()
    return {row["normalized_word"]: int(row["id"]) for row in rows}


def _entry(word_id: int, word: str, localizations: dict | None = None, **target) -> dict:
    return {
        "word_id": word_id,
        "word": word,
        "target_language": "en",
        "target": target,
        "contexts": [
            {
                "kind": "sentence",
                "text": f"She was {{{{{word}}}}} to leave.",
                "translations": {"de": "x"},
            },
            {"kind": "situation", "text": f"A {{{{{word}}}}} moment at work."},
        ],
        "localizations": localizations or {},
    }


def _file(tmp_path: Path, entries: list[dict], batch: str = "batch_001",
          schema_version: int = 2) -> Path:
    path = tmp_path / f"{batch}.json"
    path.write_text(
        json.dumps({"format": FORMAT, "schema_version": schema_version, "batch": batch,
                    "words": entries}),
        encoding="utf-8",
    )
    return path


# -- the model -------------------------------------------------------------------


def test_content_status_is_for_a_language_pair() -> None:
    usage = WordContent(word_id=1, pattern="reluctant to do")
    meaning = WordLocalization(word_id=1, learner_language="de", core_meaning="widerwillig")
    assert content_status(None, None, 0) is ContentStatus.NONE
    assert content_status(WordContent(word_id=1), None, 0) is ContentStatus.NONE
    assert content_status(None, meaning, 0, "de") is ContentStatus.PARTIAL
    assert content_status(None, None, 1) is ContentStatus.PARTIAL
    assert content_status(usage, meaning, 1, "de") is ContentStatus.PARTIAL
    assert content_status(usage, meaning, 2, "de") is ContentStatus.COMPLETE
    # A German explanation completes nothing for a Spanish speaker...
    assert content_status(usage, None, 2, "es") is ContentStatus.PARTIAL
    # ...and a learner who chose no language is taught from the definitions.
    assert content_status(usage, None, 2) is ContentStatus.COMPLETE


def test_context_marks_and_blanks_the_word() -> None:
    context = WordContext(word_id=1, text="The room was {{ cramped }} and hot.")
    assert context.target == "cramped"
    assert context.plain == "The room was cramped and hot."
    assert context.blanked() == "The room was _____ and hot."


def test_abstract_encodings() -> None:
    assert EncodingType.CONTRAST.is_abstract and EncodingType.RELATION.is_abstract
    assert not EncodingType.IMAGE.is_abstract


# -- repositories ----------------------------------------------------------------


def test_two_learner_languages_share_one_word(database: Database, words: dict[str, int]) -> None:
    """English → Spanish and English → German: one word, one card, two explanations."""
    repo = ContentRepository(database)
    word_id = words["commute"]
    repo.save_content(WordContent(word_id=word_id, pattern="commute to/from sth",
                                  collocations=("commute to work", "a long commute")))
    (context_id,) = repo.add_contexts(
        [WordContext(word_id=word_id, text="I {{commute}} to the city every day.")]
    )
    repo.save_localization(WordLocalization(
        word_id=word_id, learner_language="es", core_meaning="ir y volver del trabajo",
    ))
    repo.save_localization(WordLocalization(
        word_id=word_id, learner_language="de", core_meaning="pendeln",
        encoding_type=EncodingType.ACTION,
    ))
    repo.save_translation(context_id, "es", "Voy y vuelvo de la ciudad cada día.")
    repo.save_translation(context_id, "de", "Ich pendle jeden Tag in die Stadt.")

    spanish = repo.teaching(word_id, "es")
    german = repo.teaching(word_id, "de")
    assert spanish.core_meaning == "ir y volver del trabajo"
    assert german.core_meaning == "pendeln"
    assert german.localization.encoding_type is EncodingType.ACTION
    assert spanish.translation(spanish.contexts[0]) == "Voy y vuelvo de la ciudad cada día."
    assert german.translation(german.contexts[0]) == "Ich pendle jeden Tag in die Stadt."
    # The target-language part is the same for both: stored once.
    assert spanish.content == german.content and spanish.contexts == german.contexts
    assert set(repo.localizations(word_id)) == {"de", "es"}
    assert repo.learner_languages() == ["de", "es"]

    def count(table: str) -> int:
        return database.connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {'id' if table == 'words' else 'word_id'} = ?",
            (word_id,),
        ).fetchone()[0]

    assert count("words") == count("word_content") == count("word_contexts") == 1
    # A third language with nothing stored gets only what is shared.
    french = repo.teaching(word_id, "fr")
    assert french.core_meaning is None and french.content == spanish.content


def test_content_round_trip(database: Database, words: dict[str, int]) -> None:
    repo = ContentRepository(database)
    word_id = words["reluctant"]
    assert repo.teaching(word_id, "de").status is ContentStatus.NONE

    repo.save_content(WordContent(
        word_id=word_id, collocations=("reluctant to admit", "a reluctant hero"),
        source="batch_001",
    ))
    repo.save_localization(WordLocalization(
        word_id=word_id, learner_language="de", core_meaning="widerwillig",
        encoding_type=EncodingType.CONTRAST,
    ))
    repo.add_contexts([
        WordContext(word_id=word_id, text="He was {{reluctant}} to go."),
        WordContext(word_id=word_id, text="A {{reluctant}} yes."),
    ])
    teaching = repo.teaching(word_id, "de")
    assert teaching.content.collocations == ("reluctant to admit", "a reluctant hero")
    assert teaching.localization.encoding_type is EncodingType.CONTRAST
    assert teaching.content.updated_at and teaching.localization.updated_at
    assert teaching.status is ContentStatus.COMPLETE
    statuses = repo.statuses(words.values(), "de")
    assert statuses[word_id] is ContentStatus.COMPLETE
    assert statuses[words["apple"]] is ContentStatus.NONE
    # Saving a localization again counts its version up.
    repo.save_localization(replace(teaching.localization, core_meaning="zögerlich"))
    assert repo.localization(word_id, "de").content_version == 2


def test_attempts_are_kept_when_undone(database: Database, words: dict[str, int]) -> None:
    repo = AttemptRepository(database)
    at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    first = repo.add(LearningAttempt(
        word_id=words["cramped"], at=at, on_day="2026-09-25", phase=Phase.INTRODUCTION,
        role=Role.RETRIEVAL, task=Task.MEANING_TO_WORD, success=True,
        effort=Effort.NORMAL, depth=Depth.SHORT,
    ))
    repo.add(LearningAttempt(
        word_id=words["cramped"], at=at, on_day="2026-09-25", phase=Phase.REVIEW,
        role=Role.PRIMARY, task=Task.WORD_TO_MEANING, success=False,
    ))
    assert [a.task for a in repo.for_word(words["cramped"])] == [
        Task.MEANING_TO_WORD, Task.WORD_TO_MEANING,
    ]
    assert repo.mark_undone([first], at) == 1
    assert len(repo.for_word(words["cramped"])) == 1
    kept = repo.for_word(words["cramped"], include_undone=True)
    assert len(kept) == 2 and kept[0].undone_at == at
    assert kept[0].depth is Depth.SHORT and kept[0].level == 2


# -- the enrichment file ------------------------------------------------------------


def test_a_batch_asks_for_each_learner_language_and_names_no_language_itself(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    path = service.export_batch(
        [words["reluctant"], words["apple"]], tmp_path / "out.json", "batch_001",
        learner_languages=["de", "Spanish"],
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == FORMAT and document["schema_version"] == 2
    assert document["learner_languages"] == ["de", "es"]
    assert "German, Spanish" in document["instructions"]
    first = document["words"][0]
    assert first["word"] == "reluctant" and first["target_language"] == "en"
    assert set(first["localizations"]) == {"de", "es"}
    assert "core_meaning" in first["needs"]["de"] and "contexts" in first["needs"]["target"]
    assert "_tr" not in path.read_text(encoding="utf-8")


def test_import_fills_each_part_where_it_belongs(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    word_id = words["reluctant"]
    path = _file(tmp_path, [_entry(
        word_id, "reluctant", pattern="reluctant to do sth", depth_hint="Deep",
        localizations={
            "de": {"core_meaning": "widerwillig", "encoding_type": "contrast"},
            "es": {"core_meaning": "reacio"},
        },
    )])
    preview = service.preview_import(path)
    assert not preview.rejected
    assert preview.learner_languages == ["de", "es"]
    assert preview.fill_count == 5 and preview.context_count == 2
    result = service.apply_import(preview)
    assert result.words == 1 and result.contexts_added == 2 and result.translations_added == 1

    german = service.teaching(word_id, "de")
    assert german.content.pattern == "reluctant to do sth"
    assert german.localization.encoding_type is EncodingType.CONTRAST
    assert german.localization.source == "batch_001"
    assert german.translation(german.contexts[0]) == "x"
    assert service.teaching(word_id, "es").core_meaning == "reacio"
    assert german.status is ContentStatus.COMPLETE
    assert service.needing_content([word_id, words["apple"]], "de") == [words["apple"]]


def test_a_second_language_is_added_later_without_touching_the_first(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    word_id = words["reluctant"]
    service.apply_import(service.preview_import(_file(tmp_path, [_entry(
        word_id, "reluctant", pattern="reluctant to do sth",
        localizations={"tr": {"core_meaning": "meaning-in-tr"}},
    )])))
    later = service.preview_import(_file(tmp_path, [_entry(
        word_id, "reluctant", pattern="reluctant to do sth",
        localizations={"de": {"core_meaning": "widerwillig"}},
    )], "batch_002"))
    assert later.conflict_count == 0 and later.fill_count == 1
    service.apply_import(later)
    assert service.teaching(word_id, "tr").core_meaning == "meaning-in-tr"
    assert service.teaching(word_id, "de").core_meaning == "widerwillig"
    assert len(ContentRepository(database).contexts(word_id)) == 2


def test_import_never_overwrites_without_being_told(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    word_id = words["reluctant"]
    service.apply_import(service.preview_import(_file(
        tmp_path, [_entry(word_id, "reluctant", localizations={"de": {"core_meaning": "A"}})]
    )))
    second = service.preview_import(_file(
        tmp_path, [_entry(word_id, "reluctant", localizations={"de": {"core_meaning": "B"}})],
        "batch_002",
    ))
    assert second.conflict_count == 1
    # The same contexts again are recognised, not added twice.
    assert second.context_count == 0 and second.plans[0].duplicate_contexts == 2

    service.apply_import(second)
    assert service.teaching(word_id, "de").core_meaning == "A"
    service.apply_import(second, replace_fields=[(word_id, "de.core_meaning")])
    assert service.teaching(word_id, "de").core_meaning == "B"


def test_a_schema_1_file_is_read_as_turkish_content(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    """Schema 1 kept its one learner language in the shared block."""
    service = ContentService(database)
    word_id = words["reluctant"]
    old = {
        "word_id": word_id, "word": "reluctant",
        "content": {"core_meaning_tr": "meaning-in-tr", "pattern": "reluctant to do sth",
                    "encoding_type": "CONTRAST"},
        "contexts": [{"text": "She was {{reluctant}} to leave.",
                      "translation_tr": "sentence-in-tr"}],
    }
    service.apply_import(service.preview_import(_file(tmp_path, [old], schema_version=1)))
    teaching = service.teaching(word_id, "tr")
    assert teaching.core_meaning == "meaning-in-tr"
    assert teaching.localization.encoding_type is EncodingType.CONTRAST
    assert teaching.content.pattern == "reluctant to do sth"
    assert teaching.translation(teaching.contexts[0]) == "sentence-in-tr"


def test_import_refuses_an_id_that_names_another_word_or_language(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    german = _entry(words["cramped"], "cramped")
    german["target_language"] = "de"
    preview = service.preview_import(_file(tmp_path, [
        _entry(words["apple"], "reluctant"),
        _entry(99_999, "ghost"),
        _entry(words["cramped"], "cramped", pattern="so cramped that"),
        _entry(words["cramped"], "cramped"),
        german,
    ]))
    assert len(preview.rejected) == 4
    assert [plan.word for plan in preview.plans] == ["cramped"]
    service.apply_import(preview)
    assert service.teaching(words["apple"]).content is None


def test_bad_values_become_warnings_not_data(
    database: Database, words: dict[str, int], tmp_path: Path
) -> None:
    service = ContentService(database)
    item = _entry(words["cramped"], "cramped",
                  localizations={"de": {"encoding_type": "SMELL", "core_meaning": "eng"},
                                 "not a language!": {"core_meaning": "?"}})
    item["contexts"].append({"text": "No marker here."})
    preview = service.preview_import(_file(tmp_path, [item]))
    plan = preview.plans[0]
    assert "de.encoding_type" not in plan.fills and "de.core_meaning" in plan.fills
    assert len(plan.new_contexts) == 2
    assert len(plan.warnings) == 3


def test_other_files_are_not_content(tmp_path: Path, database: Database) -> None:
    path = tmp_path / "list.json"
    path.write_text(json.dumps({"words": ["apple"]}), encoding="utf-8")
    with pytest.raises(InvalidFileError):
        ContentService(database).preview_import(path)


def test_a_content_file_is_not_imported_as_a_word_list(tmp_path: Path) -> None:
    path = _file(tmp_path, [_entry(1, "reluctant")])
    with pytest.raises(InvalidFileError, match="content file"):
        JsonParser().parse(JsonDocument.open(path))
