"""Choosing the parser for a document.

The registry keeps parser selection a *document-level* decision made in one
place. Adding ``CambridgeParser`` later means registering it here; no other
module changes.
"""

from __future__ import annotations

import logging

from ..core.errors import NoParserError
from .base import DocumentParser, ParserInfo
from .document import Document
from .generic import GenericTextParser
from .oxford import OxfordParser

log = logging.getLogger(__name__)

#: Parser key meaning "let the registry decide".
AUTO = "auto"


class ParserRegistry:
    """Holds the available parsers and picks one for a given document."""

    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        self._parsers: list[DocumentParser] = []
        for parser in parsers if parsers is not None else default_parsers():
            self.register(parser)

    def register(self, parser: DocumentParser) -> None:
        """Add ``parser``, keeping the list ordered by descending priority."""
        self._parsers.append(parser)
        self._parsers.sort(key=lambda p: -p.priority)

    @property
    def parsers(self) -> list[DocumentParser]:
        return list(self._parsers)

    def describe(self) -> list[ParserInfo]:
        """Return parser descriptions for the import dialog."""
        return [parser.info for parser in self._parsers]

    def get(self, key: str) -> DocumentParser | None:
        """Return the parser registered under ``key``."""
        for parser in self._parsers:
            if parser.key == key:
                return parser
        return None

    def select(self, document: Document, preferred_key: str = AUTO) -> DocumentParser:
        """Return the parser to use for ``document``.

        ``preferred_key`` lets the user override detection from the import
        dialog, which matters when a document is an Oxford list in all but
        name. ``AUTO`` asks each parser in priority order whether it recognises
        the document.

        Raises:
            NoParserError: no parser accepted the document.
        """
        if preferred_key and preferred_key != AUTO:
            parser = self.get(preferred_key)
            if parser is None:
                raise NoParserError(f"There is no parser called '{preferred_key}'.")
            log.info("Using parser %s for %s (chosen by user)", parser.key, document.path.name)
            return parser

        for parser in self._parsers:
            try:
                if parser.can_parse(document):
                    log.info(
                        "Using parser %s for %s (detected)", parser.key, document.path.name
                    )
                    return parser
            except Exception:
                # A broken detector must not stop the remaining parsers from
                # being offered the document.
                log.exception("Detection failed in parser %s; skipping it", parser.key)

        raise NoParserError(
            f"No parser was able to read {document.path.name}. Try choosing one manually."
        )


def default_parsers() -> list[DocumentParser]:
    """Return the parsers shipped with LexiTrack, most specific first."""
    return [OxfordParser(), GenericTextParser()]
