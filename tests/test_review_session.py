"""Flashcard review: multi-step Backspace navigation versus learning status."""

from __future__ import annotations

import pytest

from lexitrack.models.user_word_state import ReviewStatus
from lexitrack.services.vocabulary_service import VocabularyService

WORDS = ["alpha", "bravo", "charlie", "delta", "echo"]


@pytest.fixture
def session_setup(service: VocabularyService):
    lst = service.create_list("Phonetic", "en")
    ids = {}
    for text in WORDS:
        word, _ = service.add_word(lst.id, text)
        ids[text] = word.id
    return service, lst, ids, service.start_review(lst.id)


def status(service: VocabularyService, word_id: int) -> ReviewStatus:
    return service.get_word(word_id).status


def test_a_session_starts_at_the_first_unreviewed_word(session_setup) -> None:
    _, _, _, session = session_setup
    item = session.current()
    assert item.word.normalized_word == "alpha"
    assert not item.is_history
    assert not session.can_go_back


def test_answering_records_status_and_advances(session_setup) -> None:
    service, _, ids, session = session_setup
    item = session.answer(True)
    assert item.word.normalized_word == "bravo"
    assert status(service, ids["alpha"]) is ReviewStatus.KNOWN


def test_backspace_walks_back_through_every_answer(session_setup) -> None:
    """Four answers, then Backspace visits them newest first: D, C, B, A."""
    _, _, _, session = session_setup
    for known in (True, False, True, False):  # alpha..delta answered, echo live
        session.answer(known)
    assert session.current().word.normalized_word == "echo"

    visited = []
    while session.can_go_back:
        visited.append(session.back().word.normalized_word)

    assert visited == ["delta", "charlie", "bravo", "alpha"]


def test_backspace_does_not_change_learning_status(session_setup) -> None:
    service, _, ids, session = session_setup
    session.answer(True)   # alpha known
    session.answer(False)  # bravo unknown
    session.answer(True)   # charlie known

    item = session.back()
    assert item.word.normalized_word == "charlie"
    assert item.word.status is ReviewStatus.KNOWN

    item = session.back()
    assert item.word.normalized_word == "bravo"
    assert item.word.status is ReviewStatus.UNKNOWN
    assert item.steps_back == 2

    assert status(service, ids["alpha"]) is ReviewStatus.KNOWN
    assert status(service, ids["bravo"]) is ReviewStatus.UNKNOWN
    assert status(service, ids["charlie"]) is ReviewStatus.KNOWN
    assert service.get_progress().reviewed == 3


def test_backspace_at_the_start_is_a_harmless_no_op(session_setup) -> None:
    _, _, _, session = session_setup
    assert session.back().word.normalized_word == "alpha"
    assert session.history_length == 0


def test_forward_navigation_after_going_back_keeps_statuses(session_setup) -> None:
    service, _, ids, session = session_setup
    session.answer(True)
    session.answer(False)
    session.back()
    session.back()

    assert session.forward().word.normalized_word == "bravo"
    assert session.forward().word.normalized_word == "charlie"
    assert session.is_live
    assert status(service, ids["alpha"]) is ReviewStatus.KNOWN
    assert status(service, ids["bravo"]) is ReviewStatus.UNKNOWN


def test_answering_a_word_you_went_back_to_changes_it_explicitly(session_setup) -> None:
    service, _, ids, session = session_setup
    session.answer(True)   # alpha known
    session.answer(False)  # bravo unknown
    session.back()         # bravo

    item = session.answer(True)  # change bravo to known, move forward

    assert status(service, ids["bravo"]) is ReviewStatus.KNOWN
    assert item.word.normalized_word == "charlie"
    assert session.is_live


def test_changing_an_old_answer_keeps_history_intact(session_setup) -> None:
    _, _, _, session = session_setup
    session.answer(True)
    session.answer(True)
    session.back()
    session.back()
    session.answer(False)  # alpha changed; now looking at bravo in history

    assert session.current().word.normalized_word == "bravo"
    assert session.back().word.normalized_word == "alpha"
    assert session.history_length == 2


def test_explicit_reset_returns_a_word_to_not_reviewed(session_setup) -> None:
    service, _, ids, session = session_setup
    session.answer(False)
    session.back()

    item = session.reset_current()

    assert item.word.normalized_word == "alpha"
    assert item.word.status is ReviewStatus.NOT_REVIEWED
    assert status(service, ids["alpha"]) is ReviewStatus.NOT_REVIEWED


def test_a_reset_word_comes_round_again_and_is_not_duplicated_in_history(
    session_setup,
) -> None:
    _, _, _, session = session_setup
    session.answer(True)   # alpha
    session.answer(True)   # bravo
    session.back()
    session.back()
    session.reset_current()          # alpha -> not reviewed
    session.forward()
    session.forward()                # live again

    assert session.current().word.normalized_word == "alpha"  # earliest unreviewed
    session.answer(False)
    assert session.history_length == 2
    visited = []
    while session.can_go_back:
        visited.append(session.back().word.normalized_word)
    assert visited == ["alpha", "bravo"]


def test_enter_repeats_the_last_answer_on_a_live_word(session_setup) -> None:
    service, _, ids, session = session_setup
    session.answer(False)
    session.repeat_last_answer()
    assert status(service, ids["bravo"]) is ReviewStatus.UNKNOWN


def test_enter_moves_forward_without_changing_an_old_word(session_setup) -> None:
    service, _, ids, session = session_setup
    session.answer(False)
    session.answer(True)
    session.back()
    session.back()

    item = session.repeat_last_answer()

    assert item.word.normalized_word == "bravo"
    assert status(service, ids["alpha"]) is ReviewStatus.UNKNOWN


def test_enter_does_nothing_before_any_answer(session_setup) -> None:
    service, _, _, session = session_setup
    session.repeat_last_answer()
    assert service.get_progress().reviewed == 0


def test_finishing_the_list_ends_the_session(session_setup) -> None:
    _, _, _, session = session_setup
    for _ in WORDS:
        session.answer(True)
    assert session.current() is None
    assert session.back().word.normalized_word == "echo"


def test_a_session_reviews_only_its_own_list(service: VocabularyService) -> None:
    first = service.create_list("First", "en")
    second = service.create_list("Second", "en")
    service.add_word(first.id, "one")
    service.add_word(second.id, "two")

    session = service.start_review(second.id)
    assert session.current().word.normalized_word == "two"
    session.answer(True)
    assert session.current() is None
    assert service.get_next_word(first.id).normalized_word == "one"


def test_progress_reported_during_review_is_for_the_list(session_setup) -> None:
    _, _, _, session = session_setup
    session.answer(True)
    item = session.answer(False)
    assert (item.progress.total, item.progress.known, item.progress.unknown) == (5, 1, 1)
    assert item.position == 3


def test_a_word_deleted_mid_session_drops_out_of_history(session_setup) -> None:
    service, lst, ids, session = session_setup
    session.answer(True)
    session.answer(True)
    service.remove_words_from_list(lst.id, [ids["alpha"]])

    assert session.back().word.normalized_word == "bravo"
    assert session.back().word.normalized_word == "bravo"
    assert not session.can_go_back


def test_statuses_changed_elsewhere_show_when_navigating_back(session_setup) -> None:
    """History stores words, not snapshots: the card shows the current status."""
    service, _, ids, session = session_setup
    session.answer(True)
    service.set_status([ids["alpha"]], ReviewStatus.UNKNOWN)
    assert session.back().word.status is ReviewStatus.UNKNOWN
