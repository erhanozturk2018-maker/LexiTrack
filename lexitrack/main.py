"""Application entry point.

Run with ``lexitrack`` after installing, or ``python -m lexitrack``.

``--minimized``
    Start in the tray without showing the window. This is what Start with
    Windows uses, so signing in does not throw a window in your face.
``--headless``
    No window at all: just the Telegram bot, until Ctrl+C. For a machine
    where nobody looks at the screen. Same process model, same database.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .core import paths
from .core.errors import LexiTrackError
from .core.logging_config import configure_logging
from .services.learning_service import LearningService
from .services.vocabulary_service import VocabularyService
from .ui.main_window import MainWindow
from .ui.single_instance import InstanceServer, notify_running_instance
from .ui.theme import ThemeManager
from .ui.tray import Tray

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Start LexiTrack and return the process exit code."""
    args = list(argv if argv is not None else sys.argv)
    paths.ensure_data_dirs()
    configure_logging()
    log.info("Starting LexiTrack %s (data folder: %s)", __version__, paths.data_dir())

    if "--headless" in args:
        return run_headless()

    app = QApplication(args)
    app.setApplicationName("LexiTrack")
    app.setApplicationDisplayName("LexiTrack")
    app.setOrganizationName("LexiTrack")
    app.setApplicationVersion(__version__)
    # Qt's own Fusion style is the same on every platform, which is what makes
    # one stylesheet able to produce the same result everywhere.
    app.setStyle("Fusion")

    # A second launch shows the first one and leaves: two instances would
    # mean two Telegram pollers on one token and two writers on one database.
    if notify_running_instance():
        log.info("LexiTrack is already running; asked it to come forward")
        return 0
    instance = InstanceServer(parent=app)
    instance.listen()

    theme = ThemeManager(app)
    theme.apply()

    use_tray = Tray.available()
    # With a tray, closing the last window must not end the process: the
    # window hides while the bot works, and Quit in the tray menu ends it.
    app.setQuitOnLastWindowClosed(not use_tray)

    try:
        service = VocabularyService()
        # One engine for the window; the Telegram thread builds its own over
        # the same database, because settings and clock objects are per client.
        engine = LearningService(service.database)
        window = MainWindow(service, theme, engine, use_tray=use_tray)
    except LexiTrackError as exc:
        log.exception("LexiTrack could not start")
        QMessageBox.critical(None, "LexiTrack could not start", exc.user_message)
        return 1
    instance.show_requested.connect(window.bring_forward)

    minimized = "--minimized" in args and window.tray is not None
    if not minimized:
        window.show()
        window.raise_()
        window.activateWindow()

    backup = service.database.migration_backup
    if backup is not None:
        window.bring_forward()
        window.show_migration_notice(backup.name)
    return app.exec()


def run_headless() -> int:
    """The Telegram bot and nothing else, until interrupted."""
    from PySide6.QtCore import QCoreApplication

    from .database.connection import Database
    from .telegram.config import load_config
    from .telegram.runtime import TelegramRuntime

    # The same single-instance rule as the window: a headless bot next to a
    # running app would be two pollers on one token.
    _core = QCoreApplication.instance() or QCoreApplication([])
    if notify_running_instance():
        print("LexiTrack is already running; not starting a second bot.", file=sys.stderr)
        return 1
    instance = InstanceServer()
    instance.listen()

    config = load_config()
    if not config.has_token:
        print("No Telegram token. Put LEXITRACK_TELEGRAM_TOKEN in .env first.", file=sys.stderr)
        return 2
    database = Database()
    database.connect()
    stopped = threading.Event()
    runtime = TelegramRuntime(
        database,
        config,
        on_state=lambda state, detail: print(f"Telegram: {state.label} {detail}".strip()),
    )
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    runtime.start()
    print("LexiTrack is running headless. Press Ctrl+C to stop.")
    try:
        while not stopped.wait(1.0):
            if not runtime.running:
                break
    finally:
        runtime.stop()
        database.close()
        instance.close()
    return 0


def _enable_high_dpi() -> None:
    """Keep text crisp on scaled Windows displays."""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


if __name__ == "__main__":  # pragma: no cover
    _enable_high_dpi()
    sys.exit(main())
