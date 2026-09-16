"""Application entry point.

Run with ``lexitrack`` after installing, or ``python -m lexitrack``.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .core import paths
from .core.errors import LexiTrackError
from .core.logging_config import configure_logging
from .services.vocabulary_service import VocabularyService
from .ui.main_window import MainWindow
from .ui.theme import ThemeManager

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Start LexiTrack and return the process exit code."""
    paths.ensure_data_dirs()
    configure_logging()
    log.info("Starting LexiTrack %s (data folder: %s)", __version__, paths.data_dir())

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("LexiTrack")
    app.setApplicationDisplayName("LexiTrack")
    app.setOrganizationName("LexiTrack")
    app.setApplicationVersion(__version__)
    # Qt's own Fusion style is the same on every platform, which is what makes
    # one stylesheet able to produce the same result everywhere.
    app.setStyle("Fusion")

    theme = ThemeManager(app)
    theme.apply()

    try:
        service = VocabularyService()
        window = MainWindow(service, theme)
    except LexiTrackError as exc:
        log.exception("LexiTrack could not start")
        QMessageBox.critical(None, "LexiTrack could not start", exc.user_message)
        return 1

    window.show()
    window.raise_()
    window.activateWindow()

    backup = service.database.migration_backup
    if backup is not None:
        window.show_migration_notice(backup.name)
    return app.exec()


def _enable_high_dpi() -> None:
    """Keep text crisp on scaled Windows displays."""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


if __name__ == "__main__":  # pragma: no cover
    _enable_high_dpi()
    sys.exit(main())
