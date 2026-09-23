"""The application window: navigation, menus and the current-list context.

Four destinations, reached from tabs in the app bar:

* **Study** — today's new words and today's reviews, from the study plan. It
  comes first because it is the only page that says what to do *now*; the
  others are places to look things up or to work freely.
* **Home** — continue learning, overview, your lists.
* **Review** — the current list, as flashcards or as a table. Free study: no
  schedule, no consequences, any list, any time.
* **Unknown Words** — every unknown word, across lists.

The window owns only what is shared between them: which list is current and
which review mode was last used (both remembered between runs), the theme, and
the menus. Each page reads what it needs from the service when shown, so no
page can show stale numbers after another page changed something.

The pages do not know how they are navigated to. Changing the navigation
(say, to a sidebar) means changing this file, not the pages.

Everything the app can do is declared once, in :meth:`MainWindow.commands`:
the Ctrl+K palette lists those commands, the "⋯" menu in the app bar offers
them, and the Keyboard Shortcuts window is built from them. There is no
separate menu bar to keep in step.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import paths
from ..repositories.word_repository import StoredWord
from ..services.learning_service import LearningService
from ..services.progress import ProgressService
from ..services.vocabulary_service import VocabularyService
from .command_palette import Command, CommandPalette
from .components.cards import ModeSwitch
from .components.toast import Toast
from .components.word_history import WordHistoryDialog
from .dialogs import confirm
from .export_dialog import ExportDialog, ExportScope
from .help_dialog import HelpDialog
from .home_page import HomePage
from .list_actions import ListActions
from .progress_page import ProgressPage
from .review_page import ReviewPage
from .settings_dialog import SettingsDialog
from .shortcuts_dialog import FLASHCARD_KEYS, HOME_KEYS, STUDY_KEYS, TABLE_KEYS, ShortcutsDialog
from .study_page import StudyPage
from .study_plan_dialog import StudyPlanDialog
from .telegram_controller import TelegramController
from .theme import ThemeManager, ThemeName
from .theme.palette import METRICS
from .tray import Tray, app_icon
from .unknown_page import UnknownPage

log = logging.getLogger(__name__)

STUDY, PROGRESS, HOME, REVIEW, UNKNOWN = "study", "progress", "home", "review", "unknown"
#: Tab order, and the order Ctrl+Tab cycles through.
PAGE_ORDER = (STUDY, PROGRESS, HOME, REVIEW, UNKNOWN)

_SETTINGS_LIST = "review/list_id"
_SETTINGS_MODE = "review/mode"

_ICONS = Path(__file__).with_name("theme") / "icons"


class MainWindow(QMainWindow):
    def __init__(
        self,
        service: VocabularyService,
        theme: ThemeManager,
        engine: LearningService | None = None,
        telegram: TelegramController | None = None,
        use_tray: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._theme = theme
        # One engine for the whole window, and later for the Telegram thread:
        # both must read the same settings and the same clock.
        self._engine = engine or LearningService(service.database)
        self.telegram = telegram or TelegramController(service.database, parent=self)
        self.telegram.activity.connect(self._on_bot_activity)
        self._quitting = False
        self._told_about_tray = False
        self.tray: Tray | None = None
        if use_tray and Tray.available():
            self.tray = Tray(self._engine, self.telegram, self)
            self.tray.open_requested.connect(self.bring_forward)
            self.tray.settings_requested.connect(self._settings_from_tray)
            self.tray.quit_requested.connect(self.quit)
            self.tray.show()
        self.setWindowIcon(app_icon())
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

        self._build_actions()
        self._build_body()
        for keys, step in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1), ("Ctrl+Backtab", -1)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self.cycle_page(s))
        self._ensure_current_list()
        self._engine.close_stale_sessions()
        self.show_page(self._opening_page())
        self.telegram.apply()

    # -- construction ------------------------------------------------------

    def commands(self) -> list[Command]:
        """Everything the user can do from anywhere, in the order it is offered."""
        going_dark = self._theme.current is ThemeName.LIGHT
        return [
            Command("Go to Study", "Today's new words and today's reviews",
                    lambda: self.show_page(STUDY), "Alt+S", "today plan srs due"),
            Command("Go to Progress", "What you have learned here, and every answer",
                    lambda: self.show_page(PROGRESS), "Alt+P",
                    "statistics history learned answers log calibration"),
            Command("Go to Home", "Continue learning, see your progress and your lists",
                    lambda: self.show_page(HOME), "Alt+H", "start overview"),
            Command("Go to Review", "Review the current list as flashcards or a table",
                    lambda: self.show_page(REVIEW), "Alt+R", "study flashcards"),
            Command("Go to Unknown Words", "Every word you marked unknown, across all lists",
                    lambda: self.show_page(UNKNOWN), "Alt+U", "difficult"),
            Command("Switch List\u2026", "Choose which list to review", self.switch_list,
                    "Ctrl+L", "change list open"),
            Command("Flashcard Mode", "Review the current list one word at a time",
                    lambda: self.open_review(mode=ModeSwitch.FLASHCARD), "Ctrl+1", "cards"),
            Command("List Mode", "See the current list as a table you can search and filter",
                    lambda: self.open_review(mode=ModeSwitch.LIST), "Ctrl+2", "table"),
            Command("Import\u2026", "Add words from PDF or JSON files",
                    lambda: self.actions.import_into(None), "Ctrl+O", "open pdf json add"),
            Command("New List\u2026", "Create an empty list and add words to it",
                    self.actions.create_list, "Ctrl+N", "create"),
            Command("Export\u2026", "Save words as PDF, CSV or JSON, with a preview first",
                    self.export, "Ctrl+E", "save pdf csv json print"),
            Command("Study Plan\u2026", "Choose which lists you are working through",
                    self.manage_plan, "Ctrl+P", "plan lists schedule"),
            Command("Settings\u2026", "Daily counts, the day boundary, theme and data",
                    self.open_settings, "Ctrl+,", "preferences options configure"),
            Command("Switch to Dark Mode" if going_dark else "Switch to Light Mode",
                    "Change between the light and dark themes", self.toggle_theme, "Ctrl+T",
                    "theme appearance"),
            Command("How LexiTrack Works", "The tabs, your day, the four answers, what is recorded",
                    self.show_help, "Shift+F1", "help guide tutorial explain learn"),
            Command("Keyboard Shortcuts", "Every key LexiTrack understands, in one place",
                    self.show_shortcuts, "F1", "keys help"),
            Command("Open Data Folder", "Where your vocabulary and exports are stored",
                    self._open_data_folder, None, "files location"),
            Command("Reset All Progress\u2026", "Mark every word in every list as not reviewed",
                    self.reset_progress, None, "clear start over"),
            Command("About LexiTrack", "Version and where your data lives", self._show_about),
        ]

    def _build_actions(self) -> None:
        """Window-wide shortcuts. The tabs carry their own Alt+S / P / H / R / U."""
        self._shortcut_actions: list[QAction] = []
        for keys, slot in (
            ("Ctrl+K", self.open_palette),
            ("Ctrl+L", self.switch_list),
            ("Ctrl+1", lambda: self.open_review(mode=ModeSwitch.FLASHCARD)),
            ("Ctrl+2", lambda: self.open_review(mode=ModeSwitch.LIST)),
            ("Ctrl+O", lambda: self.actions.import_into(None)),
            ("Ctrl+N", self.actions.create_list),
            ("Ctrl+E", self.export),
            ("Ctrl+P", self.manage_plan),
            ("Ctrl+,", self.open_settings),
            ("Ctrl+T", self.toggle_theme),
            ("F1", self.show_shortcuts),
            ("Shift+F1", self.show_help),
            ("Ctrl+Q", self.quit),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(keys))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(slot)
            self.addAction(action)
            self._shortcut_actions.append(action)

    def _fill_app_menu(self) -> None:
        """The "⋯" menu: the commands, grouped, with their shortcuts."""
        menu = self.app_menu
        menu.clear()
        groups = (
            ("Import\u2026", "New List\u2026", "Export\u2026"),
            ("Study Plan\u2026", "Switch List\u2026", "Flashcard Mode", "List Mode"),
            ("Settings\u2026", "Switch to Dark Mode", "Switch to Light Mode",
             "How LexiTrack Works", "Keyboard Shortcuts"),
            ("Open Data Folder", "Reset All Progress\u2026", "About LexiTrack"),
        )
        by_title = {command.title: command for command in self.commands()}
        search = menu.addAction("Search Commands\u2026", self.open_palette)
        search.setShortcut(QKeySequence("Ctrl+K"))
        for group in groups:
            menu.addSeparator()
            for title in group:
                command = by_title.get(title)
                if command is None:
                    continue
                action = menu.addAction(command.title, command.run)
                action.setToolTip(command.description)
                if command.shortcut:
                    action.setShortcut(QKeySequence(command.shortcut))
                    # Shown in the menu; the window-wide action does the work.
                    action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        menu.addSeparator()
        quit_action = menu.addAction("Quit", self.quit)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)

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

        # One reader of the learning record for every place that shows it:
        # the details panels, the Study page's words and the Progress page.
        self._progress = ProgressService(self._service.database, self._engine)
        for table in (self.review.table, self.unknown.table):
            table.panel.set_history_source(self._progress.journey)

        self.study = StudyPage(self._engine)
        self.study.history_opener = self.open_word_history
        self.progress = ProgressPage(self._progress, self._engine)
        self.progress.history_opener = self.open_word_history
        self.progress.settings_requested.connect(self.open_settings)
        self.study.manage_plan.connect(self.manage_plan)
        self.study.show_review.connect(lambda: self.show_page(REVIEW))
        self.study.help_requested.connect(self.show_help)
        self.study.data_changed.connect(self._on_data_changed)
        self.study.notify.connect(self._toast)
        self.study.notify_undo.connect(
            lambda text, undo: self._toast_widget.show_message(text, undo=undo)
        )
        self.study.export_requested.connect(self.export_study)

        self._page_widgets = {
            STUDY: self.study,
            PROGRESS: self.progress,
            HOME: self.home,
            REVIEW: self.review,
            UNKNOWN: self.unknown,
        }
        for widget in self._page_widgets.values():
            self.pages.addWidget(widget)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        # One toast for the window, so a message from the Study page appears in
        # the same place as one from a list action.
        self._toast_widget = Toast(central)

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
            (STUDY, "Study", "Alt+S"),
            (PROGRESS, "Progress", "Alt+P"),
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
        self.palette_button = QPushButton("Search or run a command")
        self.palette_button.setObjectName("CommandButton")
        self.palette_button.setToolTip("Search commands, lists and words (Ctrl+K)")
        self.palette_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.palette_button.setIconSize(QSize(15, 15))
        self.palette_button.clicked.connect(self.open_palette)
        layout.addWidget(self.palette_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addSpacing(m.space_2)

        import_button = QPushButton("Import")
        import_button.setProperty("size", "small")
        import_button.setToolTip("Import PDF or JSON files (Ctrl+O)")
        import_button.clicked.connect(lambda: self.actions.import_into(None))
        layout.addWidget(import_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.theme_button = QPushButton()
        self.theme_button.setObjectName("IconButton")
        self.theme_button.setIconSize(QSize(18, 18))
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.menu_button = QPushButton()
        self.menu_button.setObjectName("IconButton")
        self.menu_button.setIconSize(QSize(18, 18))
        self.menu_button.setToolTip("More")
        self.menu_button.setAccessibleName("More")
        self.menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.app_menu = QMenu(self.menu_button)
        self.app_menu.aboutToShow.connect(self._fill_app_menu)
        self.menu_button.setMenu(self.app_menu)
        layout.addWidget(self.menu_button, 0, Qt.AlignmentFlag.AlignVCenter)
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
        if page == STUDY:
            self.study.refresh()
            self.study.setFocus()
        elif page == PROGRESS:
            self.progress.refresh()
        elif page == HOME:
            self.home.refresh(self._current_list_id, self._mode)
        elif page == REVIEW:
            self.review.set_list(self._current_list_id, self._mode)
        else:
            self.unknown.refresh()

    def cycle_page(self, step: int) -> None:
        """Ctrl+Tab / Ctrl+Shift+Tab: next or previous page, wrapping around."""
        order = list(PAGE_ORDER)
        self.show_page(order[(order.index(self.current_page) + step) % len(order)])

    def _opening_page(self) -> str:
        """Where the window opens: whatever has work waiting.

        Study first if the plan has something due or new words to confirm —
        that is the whole point of the page. Otherwise the old behaviour,
        because a user with no plan should not land on an empty screen.
        """
        plan = self._engine.daily_plan()
        if plan.has_plan and plan.has_work:
            return STUDY
        return REVIEW if self._has_resumable_review() else HOME

    def switch_list(self) -> None:
        """Ctrl+L: open Review and choose a list from the keyboard."""
        if not self._service.lists():
            return
        if self.current_page != REVIEW:
            self.show_page(REVIEW)
        self.review.pick_list()

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
        if page == STUDY:
            self.study.refresh()
            return
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
        if page == STUDY:
            self.export_study()
            return
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

    def manage_plan(self) -> None:
        """Ctrl+P: create or change the study plan, then show Study."""
        dialog = StudyPlanDialog(self._engine, self._service, parent=self)
        dialog.exec()
        if dialog.changed:
            self._engine.refresh_settings()
            self.show_page(STUDY)
            self._toast("Study plan saved.")

    def open_settings(self) -> None:
        """Ctrl+, : the settings window.

        Its changes reach the engine through the service rather than through
        this window, so the page behind only needs redrawing.
        """
        dialog = SettingsDialog(
            self._engine, self._service, self._theme, telegram=self.telegram, parent=self
        )
        dialog.changed.connect(self._on_data_changed)
        dialog.exec()
        # The bot switch is saved with the rest; this starts or stops the
        # thread to match it.
        self.telegram.apply()

    def bring_forward(self) -> None:
        """Show the window from the tray or a second launch, on top."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.current_page == STUDY:
            self.study.refresh()

    def quit(self) -> None:
        """Quit for real: the window, the bot and the process."""
        self._quitting = True
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _settings_from_tray(self) -> None:
        self.bring_forward()
        self.open_settings()

    def _on_bot_activity(self) -> None:
        """An answer or a confirmation arrived from the phone."""
        if self.current_page == STUDY and not self.study.in_session:
            self.study.refresh()

    def _toast(self, message: str) -> None:
        """A short message at the bottom of the window."""
        self._toast_widget.show_message(message)

    def open_word_history(self, word_id: int) -> None:
        journey = self._progress.journey(word_id)
        if journey is not None:
            WordHistoryDialog(journey, parent=self).exec()

    def export_study(self) -> None:
        """Export from the Study page: today's words, and the hard ones.

        The same dialog as every other export, with a preview, so a day's
        words can be printed or carried to a phone as a PDF.
        """
        scopes: list[ExportScope] = []
        new_ids = [word.id for word in self._engine.daily_plan().new_words]
        if new_ids:
            scopes.append(
                ExportScope(
                    f"Today's new words ({len(new_ids)})",
                    lambda: self._service.export_content_for_selection(new_ids),
                )
            )
        hard_ids = [item.word.id for item in self._engine.struggling_words()]
        if hard_ids:
            scopes.append(
                ExportScope(
                    f"Words you find hard ({len(hard_ids)})",
                    lambda: self._service.export_content_for_selection(hard_ids),
                )
            )
        if not scopes:
            self._toast("There is nothing to export from Study today.")
            return
        ExportDialog(self._service, scopes, parent=self).exec()

    def reset_progress(self) -> None:
        progress = self._service.get_progress()
        if progress.reviewed == 0:
            QMessageBox.information(self, "Nothing to reset", "No word has been reviewed yet.")
            return
        text = (
            f"This marks all {progress.total:,} words in every list as not reviewed, clearing "
            f"{progress.known:,} known and {progress.unknown:,} unknown answers.\n\n"
            "The study schedule and review history are cleared too, so the study "
            "plan starts again from day one. Your lists, words and definitions are "
            "kept, and yesterday's backup is in the data folder. This cannot be undone."
        )
        if not confirm(self, "Reset all progress?", text, "Reset everything"):
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
        tone = self._theme.current.value
        label = "Switch to dark mode" if going_dark else "Switch to light mode"
        icon = "moon" if going_dark else "sun"
        self.theme_button.setIcon(QIcon(str(_ICONS / f"{icon}-{tone}.svg")))
        self.theme_button.setAccessibleName(label)
        self.theme_button.setToolTip(f"{label} (Ctrl+T)")
        self.menu_button.setIcon(QIcon(str(_ICONS / f"more-{tone}.svg")))
        self.palette_button.setIcon(QIcon(str(_ICONS / f"search-{tone}.svg")))

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

    def shortcut_sections(self):
        everywhere = [
            ("Ctrl+K", "Search commands, lists and words"),
            ("Ctrl+Tab / Ctrl+Shift+Tab", "Next / previous page"),
        ]
        everywhere += [
            (c.shortcut, c.title.rstrip("\u2026")) for c in self.commands() if c.shortcut
        ]
        everywhere.append(("Ctrl+Q", "Quit"))
        return [
            STUDY_KEYS,
            FLASHCARD_KEYS,
            TABLE_KEYS,
            HOME_KEYS,
            ("Everywhere", everywhere),
        ]

    def show_help(self) -> None:
        HelpDialog(parent=self).exec()

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self.shortcut_sections(), parent=self).exec()

    def open_palette(self) -> CommandPalette:
        lists = []
        for lst in self._service.lists():
            percent = int(lst.progress.percent_complete)
            detail = f"{lst.progress.total:,} words \u00b7 {percent}% reviewed"
            lists.append((lst.name, detail, lambda i=lst.id: self.open_review(i)))
        palette = CommandPalette(
            self.commands(), lists, self._all_words, self.reveal_word, parent=self
        )
        palette.popup()
        return palette

    def _all_words(self) -> list[StoredWord]:
        seen: dict[int, StoredWord] = {}
        for lst in self._service.lists():
            for word in self._service.list_words(lst.id):
                seen.setdefault(word.id, word)
        return list(seen.values())

    def reveal_word(self, word: StoredWord) -> None:
        """Open ``word`` in List mode, selected, with its details showing."""
        ids = {lst.name: lst.id for lst in self._service.lists()}
        current = self._service.get_list(self._current_list_id) if self._current_list_id else None
        if current is not None and current.name in word.lists:
            list_id = current.id
        else:
            list_id = next((ids[name] for name in word.lists if name in ids), None)
        if list_id is None:
            return
        self.open_review(list_id, ModeSwitch.LIST)
        table = self.review.table
        table.search.clear()
        table.status_filter.setCurrentIndex(0)
        for chip in table.level_chips.values():
            chip.setChecked(False)
        table.select_ids([word.id])
        table.show_details(True)
        table.view.setFocus()

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
        """Close hides to the tray while the bot is on; otherwise it quits.

        With the bot off there is nothing to keep running, and an app that
        lingers in the tray for no reason is an app people learn to kill.
        """
        if not self._quitting and self.tray is not None and self.telegram.enabled:
            event.ignore()
            self.hide()
            if not self._told_about_tray:
                self._told_about_tray = True
                self.tray.message(
                    "LexiTrack is still running",
                    "The Telegram bot keeps working. Quit from the tray icon to stop it.",
                )
            return
        # Stop polling before the database closes under the bot thread.
        self.telegram.shutdown()
        if self.tray is not None:
            self.tray.hide()
        self._service.close()
        super().closeEvent(event)
