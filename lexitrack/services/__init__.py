"""Application services. The UI talks to this layer and nothing below it.

The names below are resolved on first use rather than at import, so that
importing one service — the learning engine, the review flow — does not load
the import and export pipeline and its PDF libraries with it. A client with
no PDF support (a phone, the Telegram bot on its own) can use the learning
services alone.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

_EXPORTS = {
    "ExportContent": ".export_service",
    "ExportFormat": ".export_service",
    "ExportService": ".export_service",
    "ImportPreview": ".import_service",
    "ImportRequest": ".import_service",
    "ImportService": ".import_service",
    "ImportTarget": ".import_service",
    "NewList": ".import_service",
    "ProgressReporter": ".import_service",
    "ReviewItem": ".review_session",
    "ReviewSession": ".review_session",
    "VocabularyService": ".vocabulary_service",
}

__all__ = [
    "ExportContent",
    "ExportFormat",
    "ExportService",
    "ImportPreview",
    "ImportRequest",
    "ImportService",
    "ImportTarget",
    "NewList",
    "ProgressReporter",
    "ReviewItem",
    "ReviewSession",
    "VocabularyService",
]

if TYPE_CHECKING:
    from .export_service import ExportContent, ExportFormat, ExportService
    from .import_service import (
        ImportPreview,
        ImportRequest,
        ImportService,
        ImportTarget,
        NewList,
        ProgressReporter,
    )
    from .review_session import ReviewItem, ReviewSession
    from .vocabulary_service import VocabularyService


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)
