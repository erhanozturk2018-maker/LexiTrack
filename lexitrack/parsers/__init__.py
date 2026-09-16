"""Parser layer: one parser per document format, all producing ``WordEntry``."""

from .base import DocumentParser, ListMetadata, ParserInfo, ProgressCallback
from .document import Document
from .generic import GenericTextParser
from .json_document import SUPPORTED_EXTENSIONS, AnyDocument, JsonDocument, open_document
from .json_parser import JsonParser
from .oxford import OxfordParser
from .registry import AUTO, ParserRegistry, default_parsers

__all__ = [
    "AUTO",
    "SUPPORTED_EXTENSIONS",
    "AnyDocument",
    "Document",
    "DocumentParser",
    "GenericTextParser",
    "JsonDocument",
    "JsonParser",
    "ListMetadata",
    "OxfordParser",
    "ParserInfo",
    "ParserRegistry",
    "ProgressCallback",
    "default_parsers",
    "open_document",
]
