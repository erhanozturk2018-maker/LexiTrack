"""Database and repository behaviour."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lexitrack.database.connection import Database
from lexitrack.models.source import Source
from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.repositories.source_repository import SourceRepository
from lexitrack.repositories.state_repository import StateRepository
from lexitrack.repositories.word_repository import WordRepository

from .conftest import entry


@pytest.fixture
def repos(database: Database):
    return (
        WordRepository(database),
        SourceRepository(database),
        StateRepository(database),
    )


def make_source(sources: SourceRepository, key: str = "src", name: str = "Source") -> int:
    stored = sources.upsert(Source(key=key, name=name, parser_type="generic"))
    assert stored.id is not None
    return stored.id


# -- schema -----------------------------------------------------------------


def test_database_file_and_schema_are_created_on_first_use(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "vocabulary.db"
    assert not path.exists()

    db = Database(path)
    db.connect()
    try:
        tables = {
            row["name"]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        db.close()

    assert path.exists()
    assert {"sources", "words", "word_sources", "user_word_state"} <= tables


def test_connecting_twice_does_not_destroy_data(tmp_path: Path) -> None:
    path = tmp_path / "vocabulary.db"
    first = Database(path)
    WordRepository(first).add_entries([entry("alpha")], make_source(SourceRepository(first)))
    first.close()

    second = Database(path)
    try:
        assert WordRepository(second).count() == 1
    finally:
        second.close()


def test_normalized_word_is_unique(database: Database) -> None:
    database.connection.execute(
        "INSERT INTO words (normalized_word, display_word) VALUES ('alpha', 'Alpha')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.connection.execute(
            "INSERT INTO words (normalized_word, display_word) VALUES ('alpha', 'ALPHA')"
        )


def test_foreign_keys_are_enforced(database: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        database.connection.execute(
            "INSERT INTO user_word_state (word_id, status) VALUES (999, 'known')"
        )


def test_a_failed_transaction_is_rolled_back(database: Database) -> None:
    words = WordRepository(database)
    source_id = make_source(SourceRepository(database))
    words.add_entries([entry("kept")], source_id)

    with pytest.raises(RuntimeError):
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO words (normalized_word, display_word) VALUES ('gone', 'gone')"
            )
            raise RuntimeError("boom")

    assert words.find("gone") is None
    assert words.find("kept") is not None


# -- sources ----------------------------------------------------------------


def test_reimporting_a_source_reuses_its_row(database: Database) -> None:
    sources = SourceRepository(database)
    first = sources.upsert(Source(key="oxford3000", name="Oxford 3000", parser_type="oxford"))
    second = sources.upsert(Source(key="oxford3000", name="Oxford 3000", parser_type="oxford"))

    assert first.id == second.id
    assert sources.count() == 1


def test_source_file_path_is_refreshed_on_reimport(database: Database) -> None:
    sources = SourceRepository(database)
    sources.upsert(Source(key="s", name="S", parser_type="oxford", file_path="/old/a.pdf"))
    sources.upsert(Source(key="s", name="S", parser_type="oxford", file_path="/new/a.pdf"))

    assert sources.get_by_key("s").file_path == "/new/a.pdf"


# -- words ------------------------------------------------------------------


def test_inserting_words_creates_rows_and_review_state(database: Database) -> None:
    words, sources, state = (
        WordRepository(database),
        SourceRepository(database),
        StateRepository(database),
    )
    result = words.add_entries([entry("alpha"), entry("beta")], make_source(sources))

    assert result.new_words == 2
    assert words.count() == 2
    assert state.progress().total == 2
    assert words.find("alpha").status is ReviewStatus.NOT_REVIEWED


def test_duplicate_words_in_one_source_do_not_duplicate_rows(database: Database) -> None:
    words = WordRepository(database)
    source_id = make_source(SourceRepository(database))

    words.add_entries([entry("alpha")], source_id)
    result = words.add_entries([entry("alpha")], source_id)

    assert words.count() == 1
    assert result.new_words == 0
    assert result.existing_words == 1
    assert result.new_links == 0


def test_the_same_word_from_two_sources_is_one_vocabulary_item(database: Database) -> None:
    """Oxford 3000 and Oxford 5000 both contain 'ability'; the user sees it once."""
    words, sources = WordRepository(database), SourceRepository(database)
    first = make_source(sources, "oxford3000", "Oxford 3000")
    second = make_source(sources, "oxford5000", "Oxford 5000")

    words.add_entries([entry("ability", cefr_level="A2")], first)
    result = words.add_entries([entry("ability", cefr_level="B1")], second)

    assert words.count() == 1
    assert result.new_words == 0
    assert result.existing_words == 1
    assert result.new_links == 1

    stored = words.find("ability")
    assert set(stored.sources) == {"Oxford 3000", "Oxford 5000"}
    # The first source's metadata is not overwritten by the second.
    assert stored.cefr_level == "A2"


def test_case_variants_across_sources_are_one_item(database: Database) -> None:
    words, sources = WordRepository(database), SourceRepository(database)
    words.add_entries([entry("Ability")], make_source(sources, "a", "A"))
    words.add_entries([entry("ABILITY")], make_source(sources, "b", "B"))

    assert words.count() == 1
    assert words.find("ability").word == "Ability"


def test_metadata_is_filled_in_but_never_overwritten(database: Database) -> None:
    words, sources = WordRepository(database), SourceRepository(database)
    source_id = make_source(sources)

    words.add_entries([entry("set", part_of_speech="noun")], source_id)
    words.add_entries([entry("set", cefr_level="A1")], source_id)

    stored = words.find("set")
    assert stored.part_of_speech == "noun"
    assert stored.cefr_level == "A1"


def test_existing_identities_reports_what_is_already_stored(database: Database) -> None:
    words = WordRepository(database)
    words.add_entries([entry("alpha"), entry("beta")], make_source(SourceRepository(database)))

    assert words.existing_identities(["alpha", "gamma"]) == {"alpha"}
    assert words.existing_identities([]) == set()


# -- review state -----------------------------------------------------------


def test_marking_known_and_unknown_is_recorded(database: Database) -> None:
    words, state = WordRepository(database), StateRepository(database)
    words.add_entries([entry("alpha"), entry("beta")], make_source(SourceRepository(database)))

    state.set_status(words.find("alpha").id, ReviewStatus.KNOWN)
    state.set_status(words.find("beta").id, ReviewStatus.UNKNOWN)

    assert words.find("alpha").status is ReviewStatus.KNOWN
    assert words.find("beta").status is ReviewStatus.UNKNOWN
    progress = state.progress()
    assert (progress.known, progress.unknown, progress.remaining) == (1, 1, 0)


def test_a_review_timestamp_is_stored(database: Database) -> None:
    words, state = WordRepository(database), StateRepository(database)
    words.add_entries([entry("alpha")], make_source(SourceRepository(database)))
    word_id = words.find("alpha").id

    state.set_status(word_id, ReviewStatus.KNOWN)
    assert state.get(word_id).reviewed_at is not None

    state.set_status(word_id, ReviewStatus.NOT_REVIEWED)
    assert state.get(word_id).reviewed_at is None


def test_review_state_survives_a_second_import(database: Database) -> None:
    words, sources, state = (
        WordRepository(database),
        SourceRepository(database),
        StateRepository(database),
    )
    source_id = make_source(sources)
    words.add_entries([entry("alpha")], source_id)
    state.set_status(words.find("alpha").id, ReviewStatus.KNOWN)

    words.add_entries([entry("alpha")], source_id)

    assert words.find("alpha").status is ReviewStatus.KNOWN


def test_progress_counts_words_without_a_state_row_as_unreviewed(database: Database) -> None:
    words, state = WordRepository(database), StateRepository(database)
    words.add_entries([entry("alpha")], make_source(SourceRepository(database)))
    database.connection.execute("DELETE FROM user_word_state")

    progress = state.progress()
    assert (progress.total, progress.reviewed, progress.remaining) == (1, 0, 1)


def test_reset_returns_every_word_to_the_queue(database: Database) -> None:
    words, state = WordRepository(database), StateRepository(database)
    words.add_entries(
        [entry("alpha"), entry("beta")], make_source(SourceRepository(database))
    )
    state.set_status(words.find("alpha").id, ReviewStatus.KNOWN)

    state.reset_all()

    progress = state.progress()
    assert (progress.known, progress.unknown, progress.remaining) == (0, 0, 2)


# -- queue ------------------------------------------------------------------


def test_next_unreviewed_follows_insertion_order(database: Database) -> None:
    words = WordRepository(database)
    words.add_entries(
        [entry("first"), entry("second"), entry("third")],
        make_source(SourceRepository(database)),
    )
    assert words.next_unreviewed().normalized_word == "first"


def test_reviewed_words_are_skipped(database: Database) -> None:
    words, state = WordRepository(database), StateRepository(database)
    words.add_entries(
        [entry("first"), entry("second")], make_source(SourceRepository(database))
    )

    state.set_status(words.find("first").id, ReviewStatus.KNOWN)
    assert words.next_unreviewed().normalized_word == "second"

    state.set_status(words.find("second").id, ReviewStatus.UNKNOWN)
    assert words.next_unreviewed() is None


def test_listing_by_status_is_alphabetical(database: Database) -> None:
    words, state = WordRepository(database), StateRepository(database)
    words.add_entries(
        [entry("zebra"), entry("apple"), entry("mango")],
        make_source(SourceRepository(database)),
    )
    for word in ("zebra", "apple"):
        state.set_status(words.find(word).id, ReviewStatus.UNKNOWN)

    listed = words.list_by_status(ReviewStatus.UNKNOWN)
    assert [w.normalized_word for w in listed] == ["apple", "zebra"]


# -- threads ------------------------------------------------------------------


def test_a_second_thread_does_not_join_a_transaction_in_progress(database: Database) -> None:
    """The Telegram thread's answer must not ride on the UI's transaction.

    Without the lock, the nesting counter is shared: a transaction opened on
    another thread while one is in progress thinks it is nested, skips its own
    BEGIN/COMMIT, and is rolled back with the outer one.
    """
    import threading

    started = threading.Event()
    other_done = threading.Event()

    def other_thread() -> None:
        started.wait(5)
        with database.transaction() as conn:
            conn.execute("INSERT INTO words (normalized_word, display_word) VALUES ('b', 'b')")
        other_done.set()

    worker = threading.Thread(target=other_thread)
    worker.start()
    try:
        with database.transaction() as conn:
            conn.execute("INSERT INTO words (normalized_word, display_word) VALUES ('a', 'a')")
            started.set()
            # The other thread must wait for us rather than join us.
            assert not other_done.wait(0.3)
            raise RuntimeError("roll back the first transaction")
    except RuntimeError:
        pass
    worker.join(5)
    assert other_done.is_set()
    words = {
        row["normalized_word"]
        for row in database.connection.execute("SELECT normalized_word FROM words")
    }
    assert words == {"b"}, "the other thread's write survived our rollback"
