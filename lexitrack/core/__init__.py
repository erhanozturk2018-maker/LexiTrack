"""Cross-cutting utilities: paths, logging and the exception hierarchy."""

from .errors import (
    DocumentError,
    EmptyDocumentError,
    ExportError,
    ImageOnlyDocumentError,
    LexiTrackError,
    NoParserError,
    NoWordsFoundError,
    ParserError,
    StorageError,
    UnsupportedDocumentError,
)
from .logging_config import configure_logging

__all__ = [
    "DocumentError",
    "EmptyDocumentError",
    "ExportError",
    "ImageOnlyDocumentError",
    "LexiTrackError",
    "NoParserError",
    "NoWordsFoundError",
    "ParserError",
    "StorageError",
    "UnsupportedDocumentError",
    "configure_logging",
]
