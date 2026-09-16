"""Regenerate the README screenshots from the running application.

Uses the same sample data as ``design_mockups.py`` (the Oxford PDFs when they
are in ``pdfs/``, plus the lists in ``examples/``), in a temporary data folder
and a separate settings scope, so a developer's own vocabulary and settings are
never touched or shown.

Usage::

    python tools/screenshots.py        # writes docs/screenshots/*.png
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["LEXITRACK_DATA_DIR"] = tempfile.mkdtemp(prefix="lexitrack-screenshots-")

import pymupdf  # noqa: E402
from design_mockups import build_sample  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lexitrack.database.connection import Database  # noqa: E402
from lexitrack.services.vocabulary_service import VocabularyService  # noqa: E402
from lexitrack.ui.components.cards import ModeSwitch  # noqa: E402
from lexitrack.ui.import_dialog import ImportDialog  # noqa: E402
from lexitrack.ui.main_window import HOME, UNKNOWN, MainWindow  # noqa: E402
from lexitrack.ui.theme import ThemeManager, ThemeName  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
SIZE = (1180, 780)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setOrganizationName("LexiTrackScreenshots")
    app.setApplicationName("LexiTrackScreenshots")
    QSettings().clear()

    theme = ThemeManager(app)
    theme.apply(ThemeName.LIGHT)
    data = Path(os.environ["LEXITRACK_DATA_DIR"])
    service = VocabularyService(Database(data / "vocabulary.db"))
    lists = build_sample(service)
    current = lists.get("Oxford 3000") or lists["German A1"]

    OUT.mkdir(parents=True, exist_ok=True)
    window = MainWindow(service, theme)
    window.resize(*SIZE)
    window.show()

    def grab(widget, name: str) -> None:
        app.processEvents()
        widget.grab().save(str(OUT / f"{name}.png"))
        print("wrote", f"docs/screenshots/{name}.png")

    def flashcard_with_history() -> None:
        window.open_review(current, ModeSwitch.FLASHCARD)
        review = window.review
        review.flashcard.answered.emit(True)
        review.flashcard.answered.emit(False)
        review.flashcard.back_requested.emit()

    for name in (ThemeName.LIGHT, ThemeName.DARK):
        theme.apply(name)
        window._update_theme_labels()
        suffix = "" if name is ThemeName.LIGHT else "-dark"

        window.show_page(HOME)
        grab(window, f"home{suffix}")

        flashcard_with_history()
        grab(window, f"flashcard{suffix}")

        if name is ThemeName.LIGHT:
            window.review.set_mode(ModeSwitch.LIST)
            table = window.review.table
            table.select_ids([w.id for w in table.model.words[7:10]])
            grab(window, "list-mode")

            window.show_page(UNKNOWN)
            unknown = window.unknown.table
            unknown.select_ids([w.id for w in unknown.model.words[2:4]])
            grab(window, "unknown-words")

            dialog = ImportDialog(
                service,
                parent=window,
                initial_paths=[ROOT / "examples" / "simple_list.json"],
            )
            dialog.resize(660, 640)
            dialog.show()
            dialog._read()
            while dialog.pages.currentIndex() != ImportDialog._PREVIEW:
                app.processEvents()
            grab(dialog, "import-preview")
            dialog.close()

    pdf = service.export(
        service.export_content_for_unknown(current), data / "unknown.pdf", "pdf"
    )
    document = pymupdf.open(pdf)
    document[0].get_pixmap(dpi=110).save(str(OUT / "export-pdf.png"))
    document.close()
    print("wrote docs/screenshots/export-pdf.png")

    window.close()
    QSettings().clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
