"""Theme management: applying a palette and remembering the choice."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .palette import DARK, LIGHT, METRICS, PALETTES, Palette, ThemeName, get_palette
from .stylesheet import build_stylesheet

log = logging.getLogger(__name__)

_ORGANISATION = "LexiTrack"
_APPLICATION = "LexiTrack"
_SETTINGS_KEY = "appearance/theme"


class ThemeManager(QObject):
    """Applies a theme to the application and persists the user's choice.

    The selection is stored with :class:`QSettings` (the registry on Windows,
    a config file elsewhere), so the application reopens in the theme it was
    closed in.
    """

    #: Emitted after a new theme has been applied.
    theme_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings(_ORGANISATION, _APPLICATION)
        self._name = self._load_saved_theme()

    # -- state -------------------------------------------------------------

    @property
    def current(self) -> ThemeName:
        return self._name

    @property
    def palette(self) -> Palette:
        return get_palette(self._name)

    def _load_saved_theme(self) -> ThemeName:
        stored = self._settings.value(_SETTINGS_KEY, ThemeName.LIGHT.value)
        try:
            return ThemeName(str(stored))
        except ValueError:
            return ThemeName.LIGHT

    # -- applying ----------------------------------------------------------

    def apply(self, name: ThemeName | str | None = None) -> None:
        """Apply ``name`` (or the current theme) to the running application."""
        if name is not None:
            self._name = ThemeName(str(name))

        app = QApplication.instance()
        if app is None:  # pragma: no cover - only in headless unit tests
            return

        palette = self.palette
        app.setStyleSheet(build_stylesheet(palette))
        _apply_qpalette(app, palette)

        self._settings.setValue(_SETTINGS_KEY, self._name.value)
        self._settings.sync()
        log.info("Applied %s theme", self._name.value)
        self.theme_changed.emit(self._name.value)

    def toggle(self) -> ThemeName:
        """Switch between light and dark and return the new theme."""
        self.apply(ThemeName.DARK if self._name is ThemeName.LIGHT else ThemeName.LIGHT)
        return self._name


def _apply_qpalette(app: QApplication, palette: Palette) -> None:
    """Align Qt's own palette with the stylesheet.

    The stylesheet covers widgets, but native pieces Qt draws itself — text
    selection, the window frame's own colours, disabled text in some styles —
    read from ``QPalette``. Setting both stops dark mode from showing light
    fragments.
    """
    qt_palette = QPalette()
    window = QColor(palette.background)
    base = QColor(palette.surface)
    text = QColor(palette.text)
    muted = QColor(palette.text_faint)
    accent = QColor(palette.accent)

    qt_palette.setColor(QPalette.ColorRole.Window, window)
    qt_palette.setColor(QPalette.ColorRole.WindowText, text)
    qt_palette.setColor(QPalette.ColorRole.Base, base)
    qt_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(palette.surface_sunken))
    qt_palette.setColor(QPalette.ColorRole.Text, text)
    qt_palette.setColor(QPalette.ColorRole.Button, base)
    qt_palette.setColor(QPalette.ColorRole.ButtonText, text)
    qt_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette.surface_raised))
    qt_palette.setColor(QPalette.ColorRole.ToolTipText, text)
    qt_palette.setColor(QPalette.ColorRole.Highlight, accent)
    qt_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(palette.text_on_accent))
    qt_palette.setColor(QPalette.ColorRole.PlaceholderText, muted)
    qt_palette.setColor(QPalette.ColorRole.Link, accent)

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        qt_palette.setColor(QPalette.ColorGroup.Disabled, role, muted)

    app.setPalette(qt_palette)


__all__ = [
    "DARK",
    "LIGHT",
    "METRICS",
    "PALETTES",
    "Palette",
    "ThemeManager",
    "ThemeName",
    "build_stylesheet",
    "get_palette",
]
