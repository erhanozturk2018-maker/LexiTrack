"""Shared fixtures.

Every test runs against its own temporary database and its own data directory,
so nothing here can touch the user's real vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest

from lexitrack.database.connection import Database
from lexitrack.models.word_entry import WordEntry
from lexitrack.normalization.word_normalizer import normalize_word
from lexitrack.services.vocabulary_service import VocabularyService

#: The two real Oxford PDFs, when the developer has them locally. They are not
#: committed (they are Oxford University Press material), so the tests that use
#: them skip rather than fail on a clean clone.
PDF_DIR = Path(__file__).resolve().parents[1] / "pdfs"
OXFORD_3000 = PDF_DIR / "The_Oxford_3000_by_CEFR_level.pdf"
OXFORD_5000 = PDF_DIR / "The_Oxford_5000_by_CEFR_level.pdf"

requires_oxford_3000 = pytest.mark.skipif(
    not OXFORD_3000.exists(),
    reason=f"{OXFORD_3000.name} is not present in pdfs/",
)
requires_oxford_5000 = pytest.mark.skipif(
    not OXFORD_5000.exists(),
    reason=f"{OXFORD_5000.name} is not present in pdfs/",
)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point LexiTrack's data directory at a fresh temporary folder."""
    monkeypatch.setenv("LEXITRACK_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    """An empty database in a real file, so persistence can be tested."""
    db = Database(tmp_path / "test.db")
    db.connect()
    yield db
    db.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A path for a database that a test wants to open more than once."""
    return tmp_path / "resume.db"


@pytest.fixture
def service(database: Database) -> Iterator[VocabularyService]:
    """A vocabulary service backed by the temporary database."""
    svc = VocabularyService(database)
    yield svc


def entry(word: str, **kwargs: object) -> WordEntry:
    """Build a ``WordEntry`` for ``word`` with its normalized form filled in."""
    return WordEntry(word=word, normalized_word=normalize_word(word), **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def make_pdf(tmp_path: Path):
    """Return a factory that writes a real text PDF and returns its path.

    Building actual PDFs keeps the parser tests honest: they exercise PyMuPDF
    text extraction rather than a hand-made stand-in for it.
    """

    def factory(pages: list[str], name: str = "sample.pdf", fontsize: int = 11) -> Path:
        path = tmp_path / name
        document = pymupdf.open()
        for text in pages:
            page = document.new_page()
            page.insert_textbox(
                pymupdf.Rect(50, 50, 545, 790),
                text,
                fontsize=fontsize,
                fontname="helv",
            )
        document.save(path)
        document.close()
        return path

    return factory


@pytest.fixture
def image_only_pdf(tmp_path: Path) -> Path:
    """A PDF with pages but no extractable text, standing in for a scan."""
    path = tmp_path / "scanned.pdf"
    document = pymupdf.open()
    document.new_page()
    document.new_page()
    document.save(path)
    document.close()
    return path
