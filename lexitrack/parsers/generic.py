"""Fallback parser for ordinary text-based PDFs.

This parser exists so that *any* readable PDF produces something useful, not so
that every PDF produces something perfect. It tokenises the text and keeps
what looks like a word; it does not try to identify headwords, infer parts of
speech or guess at meaning. Predictability matters more here than cleverness —
a user who imports a novel should get the words in that novel, with no
surprises about which ones were quietly discarded.
"""

from __future__ import annotations

import logging
import re

from ..models.word_entry import WordEntry
from ..normalization.word_normalizer import display_form, normalize_word
from .base import DocumentParser, ProgressCallback
from .document import Document

log = logging.getLogger(__name__)

#: A candidate token: letters, optionally joined by internal apostrophes or
#: hyphens. Digits and punctuation are never part of a match, which is what
#: keeps "42", "3.5" and "--" out of the vocabulary.
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)

#: A hyphen at the end of a line, used to split a word across two lines.
_LINE_BREAK_HYPHEN_RE = re.compile(r"(?<=[^\W\d_])[-‐­]\s*\n\s*(?=[^\W\d_])")

#: Tokens shorter than this are skipped: single letters are almost always
#: list markers, initials or extraction noise rather than vocabulary.
MIN_WORD_LENGTH = 2


class GenericTextParser(DocumentParser):
    """Extracts every distinct word from a text-based PDF."""

    key = "generic"
    name = "Generic text"
    description = (
        "Any text-based PDF. Extracts every distinct word, without part of "
        "speech, level or definition."
    )
    #: Lowest priority: this parser accepts everything, so it must be asked last.
    priority = -100

    def can_parse(self, document: Document) -> bool:
        """Accept any document that has extractable text.

        ``Document.open`` has already rejected image-only PDFs, so anything
        that reaches a parser has text worth tokenising.
        """
        return document.page_count > 0

    def parse(
        self, document: Document, progress: ProgressCallback | None = None
    ) -> list[WordEntry]:
        source_key = self.source_key(document)
        entries: list[WordEntry] = []
        # Runtime deduplication: the same word appears constantly in prose, and
        # checking a set is far cheaper than building millions of dataclasses.
        seen_words: set[str] = set()
        total_pages = document.page_count

        for page_number, page_text in document.iter_page_text():
            for surface, normalized in _iter_words(page_text):
                if normalized in seen_words:
                    continue
                seen_words.add(normalized)
                entries.append(
                    WordEntry(
                        word=surface,
                        normalized_word=normalized,
                        source_id=source_key,
                    )
                )

            if progress is not None:
                progress(page_number + 1, total_pages)

        log.info(
            "Generic parser extracted %d distinct words from %s",
            len(entries),
            document.path.name,
        )
        return entries


def _iter_words(text: str):
    """Yield ``(display_form, normalized_word)`` for every word in ``text``."""
    # Repair words split across a line break before tokenising, so that
    # "vocab-\nulary" becomes one word rather than two fragments.
    text = _LINE_BREAK_HYPHEN_RE.sub("", text)

    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        normalized = normalize_word(token)
        if len(normalized) < MIN_WORD_LENGTH:
            continue
        yield display_form(token), normalized
