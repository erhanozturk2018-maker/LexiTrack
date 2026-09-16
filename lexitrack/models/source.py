"""The document a vocabulary item came from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Source:
    """A document that has been imported into the vocabulary database.

    A source is identified by ``key``, a stable slug derived from the document
    (for example ``oxford3000``). Re-importing the same document reuses the
    same source row instead of creating a second one.
    """

    key: str
    name: str
    parser_type: str
    file_path: str | None = None
    id: int | None = None
    created_at: datetime | None = None
    word_count: int = 0
