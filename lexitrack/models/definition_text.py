"""How a definition is laid out (mobile lib/domain/text/definition_text.dart).

One sense is a line of text. Several are numbered, one per line::

    1. (noun) the top part of your legs when you are sitting
    2. (verb) (of water) to touch something gently with small waves
    Note: …

A line that starts with "Note:" is a note about the word — a spelling
variant, irregular forms, register — and not part of its meaning. In a
definition "~" stands for the word itself, as in a printed dictionary:
"(~ up)" under "lap" reads "lap up". Older definitions, and the learner's
own, may list senses between semicolons instead; both are understood.
"""

from __future__ import annotations

import re

from .context import clean_context

_NOTE = re.compile(r"^\s*note\s*:", re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*\d+\.\s")


def sense_text(definition: str) -> str:
    """The definition without its notes: what a question shows, since a note
    such as "British spelling: harbour" would give the word away."""
    lines = definition.split("\n")
    if not any(_NOTE.match(line) for line in lines):
        return definition.strip()
    return "\n".join(line for line in lines if not _NOTE.match(line)).strip()


def sense_count(definition: str) -> int:
    """How many senses a definition lists: its numbered lines, or else the
    parts between semicolons."""
    text = sense_text(definition)
    numbered = sum(1 for line in text.split("\n") if _NUMBERED.match(line))
    if numbered:
        return numbered
    return max(1, sum(1 for part in text.split(";") if part.strip()))


def one_line_definition(definition: str) -> str:
    """The definition on one line, for a table row or a copied line: its
    senses one after another ("1. … 2. …"), without notes."""
    return " ".join(line.strip() for line in sense_text(definition).split("\n") if line.strip())


def clean_definition(text: str | None) -> str:
    """A definition as stored: each line cleaned as a context is (spacing
    made single, ends trimmed), empty lines dropped, the line breaks kept, so
    numbered senses stay one per line."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(clean for clean in (clean_context(line) for line in lines) if clean)
