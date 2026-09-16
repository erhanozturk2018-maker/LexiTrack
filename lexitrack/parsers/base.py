"""The contract every parser implements.

A parser belongs to a *document*, not to a word. It knows how one kind of file
is laid out and turns it into ``WordEntry`` objects; everything downstream of
that is format-agnostic. Adding support for a new format therefore means adding
a parser, never changing the vocabulary engine.

Besides words, a parser can say what a document *is*: a suggested list name,
its language and a description. These are suggestions for the import preview,
where the user has the final say, and the parser only offers what the document
actually states — it never invents a source or a language.
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
class ListMetadata:
    """What a document says about itself, as a suggestion for the target list."""

    name: str
    #: ``None`` when the document does not state a language.
    language: str | None = None
    description: str | None = None


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
    #: Document classes this parser can read. The registry never offers a
    #: parser a document of another type.
    document_types: tuple[type, ...] = (Document,)
    #: Language every document this parser reads is in, when that is fixed.
    language: str | None = None

    def accepts_type(self, document: object) -> bool:
        return isinstance(document, self.document_types)

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

    def list_metadata(self, document: Document) -> ListMetadata:
        """Suggest a name, language and description for the list to import into."""
        return ListMetadata(name=self.source_name(document), language=self.language)

    @property
    def info(self) -> ParserInfo:
        return ParserInfo(key=self.key, name=self.name, description=self.description)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} key={self.key!r}>"
