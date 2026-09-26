"""Regenerate the README screenshots from the running application.

Uses the same sample data as ``design_mockups.py`` (the Oxford PDFs when they
are in ``pdfs/``, plus the lists in ``examples/``), in a temporary data folder
and a separate settings scope, so a developer's own vocabulary and settings are
never touched or shown. The Telegram controller is given an empty
configuration, so a real token in ``.env`` is never read, and no page that
prints a file path is captured, so no user name appears in an image.

The Study screenshots run the sample plan forward a week on a frozen clock,
so they show real reviews, a real week ahead and real hard words, and the
Progress page and a word's history show that week's real record.

Usage::

    python tools/screenshots.py        # writes docs/screenshots/*.png
    python tools/screenshots.py --content words.json

``--content`` imports a contexts file (the format of docs/formats/contexts.md)
into the sample first, so the cards show real definitions and contexts; the
file only adds to the sample's words and is never copied anywhere. Without it,
a word with no definition gets a stand-in.
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

from dataclasses import replace  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import pymupdf  # noqa: E402
from design_mockups import build_sample  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from lexitrack.core.clock import FrozenClock  # noqa: E402
from lexitrack.database.connection import Database  # noqa: E402
from lexitrack.models.attempt import Effort, LearningAttempt, Phase, Role, Task  # noqa: E402
from lexitrack.models.settings import Setting  # noqa: E402
from lexitrack.models.srs import Rating  # noqa: E402
from lexitrack.services.content_service import ContentService  # noqa: E402
from lexitrack.services.learning_service import LearningService  # noqa: E402
from lexitrack.services.vocabulary_service import VocabularyService  # noqa: E402
from lexitrack.telegram.config import TelegramConfig  # noqa: E402
from lexitrack.ui.components.cards import ModeSwitch  # noqa: E402
from lexitrack.ui.components.word_history import WordHistoryDialog  # noqa: E402
from lexitrack.ui.export_dialog import (  # noqa: E402
    ExportDialog,
    ExportOrder,
    ExportScope,
    order_words,
)
from lexitrack.ui.import_dialog import ImportDialog  # noqa: E402
from lexitrack.ui.main_window import HOME, PROGRESS, STUDY, UNKNOWN, MainWindow  # noqa: E402
from lexitrack.ui.settings_dialog import SettingsDialog  # noqa: E402
from lexitrack.ui.study_plan_dialog import StudyPlanDialog  # noqa: E402
from lexitrack.ui.telegram_controller import TelegramController  # noqa: E402
from lexitrack.ui.theme import ThemeManager, ThemeName, current_palette  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
SIZE = (1180, 780)


def main(argv: list[str]) -> int:
    content_file = Path(argv[argv.index("--content") + 1]) if "--content" in argv else None
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
    # Building the sample marks statuses on today's wall clock, days after the
    # frozen week the Study screenshots run through; left in, every word's
    # history would show a stray "Marked Unknown by hand" in the middle.
    service.database.connection.execute("DELETE FROM word_status_events")
    if content_file is not None:
        content = ContentService(service.database)
        result = content.apply_import(content.preview_import(content_file))
        print(f"imported {result.contexts_added} contexts for {result.words} sample words")
    current = lists.get("Oxford 3000") or lists["German A1"]

    OUT.mkdir(parents=True, exist_ok=True)
    clock = FrozenClock(datetime(2026, 9, 17, 5, 0, tzinfo=UTC))
    engine = LearningService(service.database, clock)
    telegram = TelegramController(service.database, config=TelegramConfig())
    window = MainWindow(service, theme, engine, telegram=telegram)
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

            window.show_page(HOME)
            palette = window.open_palette()
            palette.search.setText("ex")
            app.processEvents()
            shot = window.grab()
            painter = QPainter(shot)
            painter.drawPixmap(palette.geometry().topLeft() - window.geometry().topLeft(),
                               palette.grab())
            painter.end()
            shot.save(str(OUT / "command-palette.png"))
            print("wrote docs/screenshots/command-palette.png")
            palette.close()

            export = ExportDialog(
                service,
                [ExportScope("Unknown words in this list",
                             lambda: service.export_content_for_unknown(current))],
                parent=window,
            )
            export._order_buttons[ExportOrder.CEFR].setChecked(True)
            export.resize(980, 680)
            export.show()
            app.processEvents()
            export._render_preview()
            grab(export, "export-preview")
            export.reject()

    study_screens(app, window, engine, clock, current, theme, grab)

    content = service.export_content_for_unknown(current)
    content = replace(
        content, words=order_words(content.words, ExportOrder.CEFR), group_by_level=True
    )
    pdf = service.export(content, data / "unknown.pdf", "pdf")
    document = pymupdf.open(pdf)
    document[0].get_pixmap(dpi=110).save(str(OUT / "export-pdf.png"))
    document.close()
    print("wrote docs/screenshots/export-pdf.png")

    window.close()
    QSettings().clear()
    return 0


#: Definitions for sample words that have none, for the review card screenshot.
SAMPLE_DEFINITIONS = {
    "for": "intended to be given to or used by someone",
    "banana": "a long curved fruit with a yellow skin",
    "accident": "something bad that happens by chance",
    "carrot": "a long orange vegetable that grows under the ground",
    "horse": "a large animal that people ride",
    "ticket": "a piece of paper that lets you travel or enter a place",
    "scientist": "a person who studies the natural world",
    "dad": "an informal word for father",
    "play": "to do things for fun, as children do",
}


#: Sentences for a few of the sample's words, so a session shows both tasks.
SAMPLE_CONTEXTS = {
    "ticket": ["I bought a ticket for the train to Leeds.",
               "You need a ticket to get into the museum."],
    "scientist": ["The scientist tested the water every morning."],
    "play": ["The children play in the park after school."],
}


def study_screens(app, window, engine, clock, list_id, theme, grab) -> None:
    """Study, a review card, the plan window and Settings, from a week of use."""
    # Every word needs a definition to be asked; the sample's own where it
    # has one, a stand-in otherwise, and contexts for a few.
    service = window._service
    for word in service.list_words(list_id):
        if not word.definition:
            service.set_definition(
                word.id,
                SAMPLE_DEFINITIONS.get(word.normalized_word, f"the meaning of {word.word}"),
            )
        for sentence in SAMPLE_CONTEXTS.get(word.normalized_word, []):
            service.add_context(word.id, sentence)
    engine.create_plan("Oxford 3000", list_ids=[list_id])
    engine.save_settings(
        {Setting.NEW_WORDS_PER_DAY: 25, Setting.LEECH_CONSECUTIVE: 2}
    )
    engine.introduce()
    for _ in range(8):
        clock.advance_to_day_start(1)
        clock.advance(hours=4)
        engine.introduce()
        for index, item in enumerate(engine.review_queue()):
            missed = index % 6 == 0
            task = Task.CONTEXT_TO_DEFINITION if index % 4 == 1 else Task.DEFINITION_TO_WORD
            rating = Rating.AGAIN if missed else Rating.GOOD
            attempt = LearningAttempt(
                word_id=item.word.id, at=clock.now_utc(), on_day=clock.today(),
                phase=Phase.REVIEW, role=Role.PRIMARY, task=task, correct=not missed,
                effort=None if missed else Effort.of(rating),
            )
            engine.review(item.word.id, rating, task=task, correct=not missed,
                          attempts=[attempt])
    clock.advance_to_day_start(1)
    clock.advance(hours=4)

    for name in (ThemeName.LIGHT, ThemeName.DARK):
        theme.apply(name)
        window._update_theme_labels()
        suffix = "" if name is ThemeName.LIGHT else "-dark"
        window.resize(1180, 1060)
        window.show_page(STUDY)
        grab(window, f"study{suffix}")
        window.resize(*SIZE)
        window.study.start_session()
        grab(window, f"study-session{suffix}")
        window.study.end_session()

    theme.apply(ThemeName.LIGHT)
    window._update_theme_labels()
    plan = StudyPlanDialog(engine, window._service, parent=window)
    plan.resize(640, 600)
    plan.show()
    app.processEvents()
    grab(plan, "study-plan")
    plan.reject()

    settings = SettingsDialog(engine, window._service, theme, parent=window)
    settings.resize(820, 720)
    settings.show()
    app.processEvents()
    grab(settings, "settings")
    settings.reject()

    # Progress, the whole page rather than the window's view of it.
    window.resize(1180, 1400)
    window.show_page(PROGRESS)
    app.processEvents()
    body = window.progress.findChild(QScrollArea).widget()
    body.resize(window.progress.width(), body.sizeHint().height())
    app.processEvents()
    # The page is transparent over the window; alone it would come out on black.
    shot = QPixmap(body.size())
    shot.fill(QColor(current_palette().background))
    body.render(shot)
    shot.save(str(OUT / "progress.png"))
    print("wrote docs/screenshots/progress.png")
    window.resize(*SIZE)

    # The history of the word with the most to show.
    rows = window._progress.words()
    busiest = max(rows, key=lambda row: (row.answers, row.agains))
    history = WordHistoryDialog(window._progress.journey(busiest.word.id), parent=window)
    history.resize(700, 860)
    history.show()
    app.processEvents()
    grab(history, "word-history")
    history.reject()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
