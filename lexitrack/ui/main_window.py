"""The application window: navigation, menus and the current-list context.

Three destinations, reached from tabs in the app bar:

* **Home** — continue learning, overview, your lists.
* **Review** — the current list, as flashcards or as a table.
* **Unknown Words** — every unknown word, across lists.

The window owns only what is shared between them: which list is current and
which review mode was last used (both remembered between runs), the theme, and
the menus. Each page reads what it needs from the service when shown, so no
page can show stale numbers after another page changed something.

The pages do not know how they are navigated to. Changing the navigation
(say, to a sidebar) means changing this file, not the pages.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..services.vocabulary_service import VocabularyService
from .components.cards import ModeSwitch
from .dialogs import confirm
from .export_dialog import ExportDialog, ExportScope
from .home_page import HomePage
from .list_actions import ListActions
from .review_page import ReviewPage
from .theme import ThemeManager, ThemeName
from .theme.palette import METRICS
from .unknown_page import UnknownPage

log = logging.getLogger(__name__)

HOME, REVIEW, UNKNOWN = "home", "review", "unknown"

_SETTINGS_LIST = "review/list_id"
_SETTINGS_MODE = "review/mode"

SHORTCUTS_TEXT = """
<h3>Keyboard shortcuts</h3>
<p><b>Flashcards</b></p>
<table cellspacing="6">
<tr><td><b>K</b> or <b>←</b></td><td>I Know</td></tr>
<tr><td><b>U</b> or <b>→</b></td><td>I Don't Know</td></tr>
<tr><td><b>Enter</b> / <b>Space</b></td><td>Repeat your last answer. On an earlier word:
move forward without changing it.</td></tr>
<tr><td><b>Backspace</b></td><td>Step back to the previous word.
Its status is not changed.</td></tr>
<tr><td><b>R</b></td><td>Reset the word on screen to Not Reviewed</td></tr>
</table>
<p><b>List mode and Unknown Words</b></p>
<table cellspacing="6">
<tr><td><b>↑ ↓</b>, <b>Shift</b>+arrows</td><td>Move and extend the selection</td></tr>
<tr><td><b>Ctrl+A</b></td><td>Select all visible words</td></tr>
<tr><td><b>K</b> / <b>U</b> / <b>R</b></td>
<td>Mark the selection Known / Unknown / Not Reviewed</td></tr>
<tr><td><b>Enter</b></td><td>Open word details</td></tr>
<tr><td><b>Delete</b></td><td>Remove the selection from this list (asks first)</td></tr>
<tr><td><b>Ctrl+F</b></td><td>Search</td></tr>
<tr><td><b>Esc</b></td><td>Clear the selection</td></tr>
</table>
<p><b>Everywhere</b></p>
<table cellspacing="6">
<tr><td><b>Alt+H</b> / <b>Alt+R</b> / <b>Alt+U</b></td><td>Home / Review / Unknown Words</td></tr>
<tr><td><b>Ctrl+1</b> / <b>Ctrl+2</b></td><td>Flashcard / List mode</td></tr>
<tr><td><b>Ctrl+O</b></td><td>Import</td></tr>
<tr><td><b>Ctrl+N</b></td><td>New list</td></tr>
<tr><td><b>Ctrl+E</b></td><td>Export</td></tr>
<tr><td><b>Ctrl+T</b></td><td>Switch between light and dark</td></tr>
</table>
"""


class MainWindow(QMainWindow):
    def __init__(
        self,
        service: VocabularyService,
        theme: ThemeManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._theme = theme
        self._settings = QSettings()
        self._current_list_id: int | None = self._load_int(_SETTINGS_LIST)
        self._mode = str(self._settings.value(_SETTINGS_MODE, ModeSwitch.FLASHCARD))
        if self._mode not in (ModeSwitch.FLASHCARD, ModeSwitch.LIST):
            self._mode = ModeSwitch.FLASHCARD

        self.setWindowTitle("LexiTrack")
        self.resize(1080, 760)
        self.setMinimumSize(760, 600)

        self.actions = ListActions(service, self)
        self.actions.changed.connect(self._on_data_changed)
        self.actions.focus_list.connect(self._focus_list)
        self.actions.deleted.connect(self._on_list_deleted)

        self._build_menu()
        self._build_body()
        self._ensure_current_list()
        self.show_page(REVIEW if self._has_resumable_review() else HOME)

    # -- construction ------------------------------------------------------

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action(file_menu, "&Import…", QKeySequence.StandardKey.Open,
                     lambda: self.actions.import_into(None))
        self._action(file_menu, "&New List…", QKeySequence.StandardKey.New,
                     self.actions.create_list)
        file_menu.addSeparator()
        self._action(file_menu, "&Export…", "Ctrl+E", self.export)
        file_menu.addSeparator()
        self._action(file_menu, "Reset All Progress…", None, self.reset_progress)
        self._action(file_menu, "Open &Data Folder", None, self._open_data_folder)
        file_menu.addSeparator()
        self._action(file_menu, "&Quit", QKeySequence.StandardKey.Quit, self.close)

        view_menu = bar.addMenu("&View")
        self._action(view_menu, "&Home", None, lambda: self.show_page(HOME))
        self._action(view_menu, "&Review", None, lambda: self.show_page(REVIEW))
        self._action(view_menu, "&Unknown Words", None, lambda: self.show_page(UNKNOWN))
        view_menu.addSeparator()
        self._action(view_menu, "&Flashcard Mode", "Ctrl+1",
                     lambda: self.open_review(mode=ModeSwitch.FLASHCARD))
        self._action(view_menu, "&List Mode", "Ctrl+2",
                     lambda: self.open_review(mode=ModeSwitch.LIST))
        view_menu.addSeparator()
        self._theme_action = self._action(view_menu, "", "Ctrl+T", self.toggle_theme)

        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "&Keyboard Shortcuts", QKeySequence.StandardKey.HelpContents,
                     self._show_shortcuts)
        self._action(help_menu, "&About LexiTrack", None, self._show_about)

    def _action(self, menu, text, shortcut, slot) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_body(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_app_bar())

        self.pages = QStackedWidget()
        self.home = HomePage(self._service, self.actions)
        self.home.open_list.connect(lambda list_id, mode: self.open_review(list_id, mode))
        self.home.current_changed.connect(self._set_current_list)
        self.home.show_unknown.connect(lambda: self.show_page(UNKNOWN))

        self.review = ReviewPage(self._service, self.actions)
        self.review.list_missing.connect(self._on_list_missing)
        self.review.context_changed.connect(self._on_review_context)

        self.unknown = UnknownPage(self._service)

        self._page_widgets = {HOME: self.home, REVIEW: self.review, UNKNOWN: self.unknown}
        for widget in self._page_widgets.values():
            self.pages.addWidget(widget)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)

    def _build_app_bar(self) -> QWidget:
        m = METRICS
        bar = QFrame()
        bar.setObjectName("AppBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(m.space_5, m.space_2, m.space_5, 0)
        layout.setSpacing(m.space_2)

        title = QLabel("LexiTrack")
        title.setObjectName("AppTitle")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addSpacing(m.space_5)

        self._tabs = QButtonGroup(self)
        self._tabs.setExclusive(True)
        self.tab_buttons: dict[str, QPushButton] = {}
        # Explicit Alt shortcuts rather than "&" mnemonics, which the Fusion
        # style underlines permanently.
        for key, text, shortcut in (
            (HOME, "Home", "Alt+H"),
            (REVIEW, "Review", "Alt+R"),
            (UNKNOWN, "Unknown Words", "Alt+U"),
        ):
            tab = QPushButton(text)
            tab.setShortcut(QKeySequence(shortcut))
            tab.setToolTip(f"{text} ({shortcut})")
            tab.setObjectName("NavTab")
            tab.setCheckable(True)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            self._tabs.addButton(tab)
            self.tab_buttons[key] = tab
            layout.addWidget(tab, 0, Qt.AlignmentFlag.AlignBottom)

        layout.addStretch(1)
        import_button = QPushButton("Import")
        import_button.setProperty("size", "small")
        import_button.setToolTip("Import PDF or JSON files (Ctrl+O)")
        import_button.clicked.connect(lambda: self.actions.import_into(None))
        layout.addWidget(import_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.theme_button = QPushButton()
        self.theme_button.setProperty("variant", "ghost")
        self.theme_button.setProperty("size", "small")
        self.theme_button.setToolTip("Switch between Light and Dark (Ctrl+T)")
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._update_theme_labels()
        bar.setMinimumHeight(54)
        return bar

    # -- navigation --------------------------------------------------------

    @property
    def current_page(self) -> str:
        widget = self.pages.currentWidget()
        return next(key for key, page in self._page_widgets.items() if page is widget)

    def show_page(self, page: str) -> None:
        if page == REVIEW:
            self._ensure_current_list()
            if self._current_list_id is None:
                page = HOME
        self.tab_buttons[page].setChecked(True)
        self.pages.setCurrentWidget(self._page_widgets[page])
        if page == HOME:
            self.home.refresh(self._current_list_id, self._mode)
        elif page == REVIEW:
            self.review.set_list(self._current_list_id, self._mode)
        else:
            self.unknown.refresh()

    def open_review(self, list_id: int | None = None, mode: str | None = None) -> None:
        if list_id is not None:
            self._set_current_list(list_id)
        if mode is not None:
            self._set_mode(mode)
        self.show_page(REVIEW)

    # -- context -----------------------------------------------------------

    def _set_current_list(self, list_id: int) -> None:
        self._current_list_id = list_id
        self._settings.setValue(_SETTINGS_LIST, list_id)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._settings.setValue(_SETTINGS_MODE, mode)

    def _on_review_context(self, list_id: int, mode: str) -> None:
        self._set_current_list(list_id)
        self._set_mode(mode)

    def _ensure_current_list(self) -> None:
        lists = self._service.lists()
        ids = {lst.id for lst in lists}
        if self._current_list_id not in ids:
            self._current_list_id = None
            if lists:
                # Prefer a list with something left to review.
                pending = [lst for lst in lists if lst.progress.remaining]
                self._set_current_list((pending or lists)[0].id)

    def _has_resumable_review(self) -> bool:
        if self._current_list_id is None:
            return False
        current = self._service.get_list(self._current_list_id)
        if current is None:
            return False
        return current.progress.reviewed > 0 and current.progress.remaining > 0

    def _focus_list(self, list_id: int) -> None:
        self._set_current_list(list_id)

    def _on_list_deleted(self, list_id: int) -> None:
        if self._current_list_id == list_id:
            self._current_list_id = None
            self._settings.remove(_SETTINGS_LIST)

    def _on_list_missing(self) -> None:
        self._current_list_id = None
        self._ensure_current_list()
        self.show_page(HOME)

    def _on_data_changed(self) -> None:
        """Something was written; redraw the page on screen from the database."""
        self._ensure_current_list()
        page = self.current_page
        if page == REVIEW and self._current_list_id is not None:
            if self.review.list_id != self._current_list_id:
                self.review.set_list(self._current_list_id, self._mode)
            else:
                self.review.refresh(reload_table=True)
        else:
            self.show_page(page)

    # -- commands ----------------------------------------------------------

    def export(self) -> None:
        """Export from wherever the user is, with scopes that fit."""
        page = self.current_page
        if page == UNKNOWN:
            self.unknown.export(self.unknown.table.selected_ids())
            return
        if self._current_list_id is not None and page in (HOME, REVIEW):
            selected = (
                self.review.table.selected_ids()
                if page == REVIEW and self.review.mode == ModeSwitch.LIST
                else None
            )
            self.actions.export_list(self._current_list_id, selected)
            return
        scopes = [
            ExportScope(
                f"All unknown words ({self._service.unknown_count():,})",
                lambda: self._service.export_content_for_unknown(None),
            )
        ]
        ExportDialog(self._service, scopes, parent=self).exec()

    def reset_progress(self) -> None:
        progress = self._service.get_progress()
        if progress.reviewed == 0:
            QMessageBox.information(self, "Nothing to reset", "No word has been reviewed yet.")
            return
        text = (
            f"This marks all {progress.total:,} words in every list as not reviewed, clearing "
            f"{progress.known:,} known and {progress.unknown:,} unknown answers.\n\n"
            "Your lists and words are kept. This cannot be undone."
        )
        if not confirm(self, "Reset all progress?", text, "Reset Everything"):
            return
        self._service.reset_progress()
        self.review.session = None
        self._on_data_changed()

    def toggle_theme(self) -> None:
        self._theme.toggle()
        self._update_theme_labels()
        # Painted components (table pills, progress bars) read the palette.
        self.update()
        for widget in self.findChildren(QWidget):
            widget.update()

    def _update_theme_labels(self) -> None:
        going_dark = self._theme.current is ThemeName.LIGHT
        self.theme_button.setText("Dark" if going_dark else "Light")
        if hasattr(self, "_theme_action"):
            self._theme_action.setText(
                "Switch to &Dark Mode" if going_dark else "Switch to &Light Mode"
            )

    def show_migration_notice(self, backup_name: str) -> None:
        QMessageBox.information(
            self,
            "Vocabulary upgraded",
            "LexiTrack upgraded your vocabulary to support lists. Your words and "
            "everything you have reviewed were kept, and each document you imported "
            "is now a list.\n\n"
            f"A copy of your previous data was saved as {backup_name} in the data folder.",
        )

    # -- misc --------------------------------------------------------------

    def _open_data_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.ensure_data_dirs())))

    def _show_shortcuts(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard Shortcuts")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(SHORTCUTS_TEXT)
        box.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About LexiTrack",
            "<h3>LexiTrack</h3>"
            "<p>Vocabulary Learning &amp; Review</p>"
            "<p>Import word lists from PDF and JSON, organise them into lists, and "
            "review them as flashcards or in a table. Everything is stored locally.</p>"
            f"<p style='color:gray'>Data folder: {paths.data_dir()}</p>",
        )

    def _load_int(self, key: str) -> int | None:
        value = self._settings.value(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._service.close()
        super().closeEvent(event)
