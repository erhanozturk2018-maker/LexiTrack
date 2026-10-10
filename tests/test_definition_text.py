"""How a definition is laid out (models/definition_text.py); the phone's
test/domain/definition_text_test.dart checks the same cases."""

from __future__ import annotations

from lexitrack.models.definition_text import (
    clean_definition,
    one_line_definition,
    sense_count,
    sense_text,
)

LAP = (
    "1. (noun) the top part of your legs when you are sitting\n"
    "2. (verb) (of water) to touch something gently with small waves\n"
    "3. (phrasal verb) (~ up) to enjoy something very much\n"
    "Note: Informal in the third meaning."
)


def test_notes_are_left_out_of_what_a_question_shows() -> None:
    assert "Note" not in sense_text(LAP)
    assert len(sense_text(LAP).split("\n")) == 3
    noted = "a room under a house\nNote: British spelling: cellar."
    assert sense_text(noted) == "a room under a house"
    assert sense_text("to stop trying") == "to stop trying"
    assert sense_text("  note: only a note") == ""


def test_senses_are_counted_by_numbered_lines_or_else_by_semicolons() -> None:
    assert sense_count(LAP) == 3
    assert sense_count("to stop trying") == 1
    assert sense_count("to stop trying; to surrender") == 2
    assert sense_count("1. one; with a semicolon\n2. two") == 2
    assert sense_count("") == 1


def test_one_line_for_a_list_row() -> None:
    assert one_line_definition(LAP) == (
        "1. (noun) the top part of your legs when you are sitting "
        "2. (verb) (of water) to touch something gently with small waves "
        "3. (phrasal verb) (~ up) to enjoy something very much"
    )
    assert one_line_definition("a farm building") == "a farm building"
    assert one_line_definition("1. (noun) a farm building") == "(noun) a farm building"


def test_a_definition_is_cleaned_line_by_line_and_keeps_its_line_breaks() -> None:
    assert clean_definition("  1.  one\r\n\n 2. two  \rNote:  x ") == "1. one\n2. two\nNote: x"
    assert clean_definition(LAP) == LAP
    assert clean_definition(None) == ""
