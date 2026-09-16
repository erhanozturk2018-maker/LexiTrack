"""The contract every parser implements.

A parser belongs to a *document*, not to a word. It knows how one kind of PDF
is laid out and turns it into ``WordEntry`` objects; everything downstream of
that is format-agnostic. Adding support for a new PDF therefore means adding a
parser, never changing the vocabulary engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from ..models.word_entry import WordEntry
from .document import Document

#: Called with ``(current, total)`` while a long document is parsed.
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class ParserInfo:
    """Description of a parser, used by the registry and the import dialog."""

    key: str
    name: str
    description: str


class DocumentParser(ABC):
    """Base class for all parsers."""

    #: Stable identifier stored in ``sources.parser_type``.
    key: str = "base"
    #: Name shown in the import dialog.
    name: str = "Parser"
    #: One sentence describing what this parser is for.
    description: str = ""
    #: Higher priority parsers are offered a document first.
    priority: int = 0

    @abstractmethod
    def can_parse(self, document: Document) -> bool:
        """Return ``True`` if this parser recognises ``document``'s format."""

    @abstractmethod
    def parse(
        self, document: Document, progress: ProgressCallback | None = None
    ) -> list[WordEntry]:
        """Extract vocabulary from ``document``.

        Implementations report progress through ``progress`` when given, and
        return entries in document order with ``normalized_word`` already set.
        """

    def source_key(self, document: Document) -> str:
        """Return the stable key identifying ``document`` as a source.

        Two imports that produce the same key are treated as the same document,
        so the key must not depend on where the file happens to live. The
        default derives it from the parser and the document name.
        """
        slug = "".join(
            character if character.isalnum() else "-" for character in document.name.lower()
        )
        slug = "-".join(part for part in slug.split("-") if part)
        return f"{self.key}:{slug or document.path.stem.lower()}"

    def source_name(self, document: Document) -> str:
        """Return the display name for ``document`` as a source."""
        return document.name

    @property
    def info(self) -> ParserInfo:
        return ParserInfo(key=self.key, name=self.name, description=self.description)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} key={self.key!r}>"
