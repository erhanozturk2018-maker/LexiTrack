"""Application level exceptions.

Every exception carries a message that is safe to show to a non-technical user
in the UI. Technical detail belongs in the log, not in the dialog.
"""

from __future__ import annotations


class LexiTrackError(Exception):
    """Base class for all errors raised by LexiTrack."""

    #: Message shown to the user when nothing more specific is available.
    default_message = "Something went wrong."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)

    @property
    def user_message(self) -> str:
        return str(self)


class DocumentError(LexiTrackError):
    """The document could not be opened or read."""

    default_message = "This document could not be opened."


class UnsupportedDocumentError(DocumentError):
    """The document is a PDF, but its content cannot be used."""

    default_message = "This PDF is not supported."


class EmptyDocumentError(UnsupportedDocumentError):
    default_message = "This PDF contains no pages."


class ImageOnlyDocumentError(UnsupportedDocumentError):
    default_message = (
        "This PDF contains only scanned images, so no text could be extracted. "
        "LexiTrack does not support OCR yet."
    )


class ParserError(LexiTrackError):
    """A parser failed while processing a document."""

    default_message = "The document could not be processed."


class NoParserError(ParserError):
    default_message = "No parser was able to read this document."


class NoWordsFoundError(ParserError):
    default_message = "No words could be extracted from this document."


class StorageError(LexiTrackError):
    """The database could not be read or written."""

    default_message = "The vocabulary database could not be accessed."


class InvalidFileError(DocumentError):
    """A structured file (JSON) was readable but did not have the expected shape."""

    default_message = "This file is not in a format LexiTrack can import."


class ListError(LexiTrackError):
    """A list operation was not allowed."""

    default_message = "The list could not be changed."


class ListNotFoundError(ListError):
    default_message = "That list no longer exists."


class DuplicateListError(ListError):
    default_message = "A list with that name already exists."


class LanguageMismatchError(ListError):
    default_message = "Those words are in a different language from the list."


class ExportError(LexiTrackError):
    """An export could not be produced."""

    default_message = "The export could not be created."
