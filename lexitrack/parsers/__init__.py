"""Parser layer: one parser per document format, all producing ``WordEntry``."""

from .base import DocumentParser, ParserInfo, ProgressCallback
from .document import Document, TextLine, TextSpan
from .generic import GenericTextParser
from .oxford import OxfordParser
from .registry import AUTO, ParserRegistry, default_parsers

__all__ = [
    "AUTO",
    "Document",
    "DocumentParser",
    "GenericTextParser",
    "OxfordParser",
    "ParserInfo",
    "ParserRegistry",
    "ProgressCallback",
    "TextLine",
    "TextSpan",
    "default_parsers",
]
