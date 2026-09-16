"""Parser for the official "Oxford 3000/5000 by CEFR level" PDFs.

The rules below were derived from the real documents published by Oxford
University Press (``The_Oxford_3000_by_CEFR_level.pdf``, 12 pages, and
``The_Oxford_5000_by_CEFR_level.pdf``, 8 pages), not from guesswork. What the
extracted text actually looks like::

    © Oxford University Press
    1 / 12
    The Oxford 3000™ by CEFR level
    The Oxford 3000 is the list of the 3000 most important words ...
    A1
    a, an indefinite article
    about prep., adv.
    ...
    A2
    ...

So: a CEFR level is a *section heading* that applies to every entry beneath it
until the next heading, and an entry is a single line of ``word`` followed by a
comma- or slash-separated list of part-of-speech abbreviations.

Four quirks in the real files drive most of the code here:

``bank (money) n.``
    A parenthesised sense disambiguator. The sense is kept as metadata; the
    vocabulary identity is just ``bank``.
``light (from the`` / ``sun/a lamp) n.``
    Long disambiguators wrap onto the next line, so lines with unbalanced
    brackets are joined before being parsed.
``can1 modal v.`` and ``can2 modal v.``
    Homograph numbering, rendered as a superscript digit. The digit is dropped,
    which is what makes both senses collapse into one word the user is asked
    about once.
``a, an indefinite article`` and ``ice cream n.``
    Entries covering more than one form, separated by a comma, and multi-word
    entries joined by a non-breaking space.

Neither PDF contains definitions or example sentences, so ``WordEntry``'s
``definition`` and ``example`` stay ``None`` for this source. That is a
property of the document, not a gap in the parser.
"""

from __future__ import annotations

import logging
import re

from ..models.word_entry import WordEntry
from ..normalization.word_normalizer import display_form, normalize_word
from .base import DocumentParser, ProgressCallback
from .document import Document

log = logging.getLogger(__name__)

#: Part-of-speech abbreviations used by the Oxford lists, mapped to the full
#: names LexiTrack shows in the UI and in exports.
POS_EXPANSIONS: dict[str, str] = {
    "n.": "noun",
    "v.": "verb",
    "adj.": "adjective",
    "adv.": "adverb",
    "prep.": "preposition",
    "pron.": "pronoun",
    "det.": "determiner",
    "conj.": "conjunction",
    "exclam.": "exclamation",
    "number": "number",
    "modal v.": "modal verb",
    "auxiliary v.": "auxiliary verb",
    "indefinite article": "indefinite article",
    "definite article": "definite article",
    "infinitive marker": "infinitive marker",
}

#: Recognised CEFR section headings, in the order the documents use them.
CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")

_LEVEL_HEADING_RE = re.compile(r"^(A1|A2|B1|B2|C1|C2)$")

# The part-of-speech tail of an entry line: one or more abbreviations joined by
# commas or slashes. Longest alternatives first so "modal v." wins over "v.".
_POS_TOKEN = (
    r"(?:modal\s+v\.|auxiliary\s+v\.|indefinite\s+article|definite\s+article"
    r"|infinitive\s+marker|number|n\.|v\.|adj\.|adv\.|prep\.|pron\.|det\.|conj\.|exclam\.)"
)
_POS_TAIL_RE = re.compile(
    rf"(?P<word>.+?)\s+(?P<pos>{_POS_TOKEN}(?:\s*[,/]\s*{_POS_TOKEN})*)\s*$"
)

#: Superscript homograph numbering, e.g. the ``1`` in ``can1``.
_HOMOGRAPH_RE = re.compile(r"(?<=[^\W\d_])[0-9]$")
#: A parenthesised sense disambiguator, e.g. ``(money)`` in ``bank (money)``.
_SENSE_RE = re.compile(r"\(([^)]*)\)")

#: Lines that are page furniture rather than vocabulary.
_NOISE_RE = re.compile(
    r"^\s*(?:\d+\s*/\s*\d+"           # "1 / 12" page numbers
    r"|.{0,3}\s*Oxford University Press.*"  # copyright line
    r"|The Oxford \d+.*"              # document title
    r"|The Oxford \d+ is .*"          # blurb, first line
    r"|\d+, it includes .*"           # blurb, wrapped second line
    r")\s*$",
    re.IGNORECASE,
)

#: Minimum share of candidate lines that must look like entries for the
#: structural fallback in :meth:`OxfordParser.can_parse` to accept a document.
_STRUCTURAL_THRESHOLD = 0.6


class OxfordParser(DocumentParser):
    """Extracts levelled vocabulary from the Oxford 3000 and Oxford 5000 PDFs."""

    key = "oxford"
    name = "Oxford word list"
    description = (
        "The official Oxford 3000 and Oxford 5000 'by CEFR level' PDFs. "
        "Extracts each word with its part of speech and CEFR level."
    )
    priority = 100

    # -- detection ---------------------------------------------------------

    def can_parse(self, document: Document) -> bool:
        """Recognise an Oxford word list.

        Detection is by title first, because it is unambiguous, and by
        structure second, so a re-exported or renamed copy of the same list is
        still handled correctly.
        """
        head = document.text(max_pages=1)
        if _detect_list_size(head) is not None:
            return True
        return self._looks_structurally_like_oxford(document)

    @staticmethod
    def _looks_structurally_like_oxford(document: Document) -> bool:
        """Accept documents shaped like a levelled word list even without a title."""
        candidates = 0
        matches = 0
        has_level_heading = False

        for raw_line in document.text(max_pages=2).splitlines():
            line = _clean_line(raw_line)
            if not line or _NOISE_RE.match(line):
                continue
            if _LEVEL_HEADING_RE.match(line):
                has_level_heading = True
                continue
            candidates += 1
            if _POS_TAIL_RE.match(line):
                matches += 1

        if not has_level_heading or candidates < 20:
            return False
        return matches / candidates >= _STRUCTURAL_THRESHOLD

    # -- source identity ---------------------------------------------------

    def source_key(self, document: Document) -> str:
        size = _detect_list_size(document.text(max_pages=1))
        return f"oxford{size}" if size else super().source_key(document)

    def source_name(self, document: Document) -> str:
        size = _detect_list_size(document.text(max_pages=1))
        return f"Oxford {size}" if size else document.name

    # -- parsing -----------------------------------------------------------

    def parse(
        self, document: Document, progress: ProgressCallback | None = None
    ) -> list[WordEntry]:
        source_key = self.source_key(document)
        entries: list[WordEntry] = []
        current_level: str | None = None
        total_pages = document.page_count

        for page_number, page_text in document.iter_page_text():
            for line in _join_wrapped_lines(page_text.splitlines()):
                if _NOISE_RE.match(line):
                    continue

                heading = _LEVEL_HEADING_RE.match(line)
                if heading:
                    current_level = heading.group(1)
                    continue

                entries.extend(_parse_entry_line(line, source_key, current_level))

            if progress is not None:
                progress(page_number + 1, total_pages)

        log.info(
            "Oxford parser extracted %d entries from %s across levels %s",
            len(entries),
            document.path.name,
            sorted({e.cefr_level for e in entries if e.cefr_level}),
        )
        return entries


# -- helpers ---------------------------------------------------------------


def _detect_list_size(text: str) -> str | None:
    """Return ``"3000"`` or ``"5000"`` when ``text`` names an Oxford list."""
    match = re.search(r"The\s+Oxford\s+(3000|5000)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _clean_line(raw: str) -> str:
    """Normalise the whitespace and stray control characters of one PDF line."""
    line = raw.replace(" ", " ").replace("\x08", "").replace("\t", " ")
    return re.sub(r"\s+", " ", line).strip()


def _join_wrapped_lines(raw_lines: list[str]) -> list[str]:
    """Rejoin entries that the PDF split across two lines.

    Long sense disambiguators wrap, producing pairs such as ``light (from the``
    followed by ``sun/a lamp) n.``. A line is treated as incomplete when it has
    an unclosed bracket or ends on a separator, and is glued to the next one.
    """
    joined: list[str] = []
    pending: str | None = None

    for raw in raw_lines:
        line = _clean_line(raw)
        if not line:
            continue

        if pending is not None:
            line = f"{pending} {line}"
            pending = None

        if _is_incomplete(line):
            pending = line
            continue

        joined.append(line)

    if pending is not None:
        joined.append(pending)
    return joined


def _is_incomplete(line: str) -> bool:
    """True when ``line`` is the first half of a wrapped entry."""
    if _NOISE_RE.match(line) or _LEVEL_HEADING_RE.match(line):
        return False
    if line.count("(") > line.count(")"):
        return True
    return line.endswith(("/", ","))


def _parse_entry_line(line: str, source_key: str, level: str | None) -> list[WordEntry]:
    """Turn one entry line into ``WordEntry`` objects.

    Returns more than one entry when the line covers several forms, as in
    ``a, an indefinite article``. Returns an empty list when the line is not an
    entry, which keeps stray text out of the vocabulary.
    """
    match = _POS_TAIL_RE.match(line)
    if not match:
        return []

    part_of_speech = _expand_pos(match.group("pos"))
    word_field = match.group("word").strip()

    sense = None
    sense_match = _SENSE_RE.search(word_field)
    if sense_match:
        sense = re.sub(r"\s+", " ", sense_match.group(1)).strip() or None
        word_field = _SENSE_RE.sub("", word_field).strip()

    entries: list[WordEntry] = []
    for form in (part.strip() for part in word_field.split(",")):
        if not form:
            continue
        surface = _HOMOGRAPH_RE.sub("", form).strip()
        normalized = normalize_word(surface)
        if not normalized:
            continue

        metadata: dict[str, str] = {}
        if sense:
            metadata["sense"] = sense
        if form != surface:
            metadata["homograph"] = form

        entries.append(
            WordEntry(
                word=display_form(surface),
                normalized_word=normalized,
                source_id=source_key,
                part_of_speech=part_of_speech,
                cefr_level=level,
                metadata=metadata,
            )
        )
    return entries


def _expand_pos(raw: str) -> str | None:
    """Expand ``"n., v."`` into ``"noun, verb"``.

    Unknown abbreviations are passed through rather than dropped, so a future
    edition that adds one degrades gracefully instead of losing information.
    """
    parts = [part.strip() for part in re.split(r"[,/]", raw) if part.strip()]
    expanded: list[str] = []
    for part in parts:
        collapsed = re.sub(r"\s+", " ", part)
        name = POS_EXPANSIONS.get(collapsed, collapsed)
        if name not in expanded:
            expanded.append(name)
    return ", ".join(expanded) or None
