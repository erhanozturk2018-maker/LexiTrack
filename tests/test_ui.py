"""UI behaviour, driven through real widgets.

These tests build actual Qt widgets and drive them through their signals and
key events, so they catch what only shows up once a widget exists: a signal
wired to the wrong slot, a screen that does not switch, a shortcut that does
nothing, a table that filters the wrong rows.

Every test runs in its own settings scope, so nothing here touches the real
LexiTrack settings of the person running the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lexitrack.models.user_word_state import Progress, ReviewStatus  # noqa: E402
from lexitrack.models.vocabulary_list import VocabularyList  # noqa: E402
from lexitrack.repositories.word_repository import StoredWord  # noqa: E402
from lexitrack.services.review_session import ReviewItem  # noqa: E402
from lexitrack.services.vocabulary_service import VocabularyService  # noqa: E402
from lexitrack.ui.components.cards import ListCard, ModeSwitch, SegmentedProgress  # noqa: E402
from lexitrack.ui.components.status import STATUS_ROLE  # noqa: E402
from lexitrack.ui.components.vocabulary_table import Column, VocabularyTable  # noqa: E402
from lexitrack.ui.dialogs import AddWordDialog, ListDialog  # noqa: E402
from lexitrack.ui.import_dialog import ImportDialog  # noqa: E402
from lexitrack.ui.main_window import HOME, REVIEW, UNKNOWN, MainWindow  # noqa: E402
from lexitrack.ui.progress_widget import StatsBar  # noqa: E402
from lexitrack.ui.review_widget import ReviewWidget  # noqa: E402
from lexitrack.ui.theme import ThemeManager, ThemeName, build_stylesheet  # noqa: E402
from lexitrack.ui.theme.palette import DARK, LIGHT  # noqa: E402

SIMPLE_PDF = "alpha beta gamma delta epsilon"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture(autouse=True)
def isolated_settings(qapp):
    qapp.setOrganizationName("LexiTrackTest")
    qapp.setApplicationName("LexiTrackTest")
    QSettings().clear()
    yield
    QSettings().clear()


@pytest.fixture
def theme(qapp):
    return ThemeManager()


@pytest.fixture
def loaded(service: VocabularyService, make_pdf) -> VocabularyService:
    """One list, 'sample', with five words."""
    service.import_document(make_pdf([SIMPLE_PDF]))
    return service


@pytest.fixture
def window(qapp, theme, loaded):
    win = MainWindow(loaded, theme)
    yield win
    win.close()


def word(**kwargs) -> StoredWord:
    defaults = dict(
        id=1, word="abandon", normalized_word="abandon", part_of_speech="verb",
        cefr_level="B2", sources=("Oxford 3000",),
    )
    return StoredWord(**{**defaults, **kwargs})


def item(steps_back: int = 0, **kwargs) -> ReviewItem:
    return ReviewItem(word(**kwargs), steps_back, Progress(total=3000, known=120, unknown=12))


# -- flashcard widget ------------------------------------------------------


def test_the_word_metadata_and_provenance_are_displayed(qapp) -> None:
    widget = ReviewWidget()
    widget.show_item(item(), can_go_back=False)

    assert widget._word_label.text() == "abandon"
    assert widget._meta_label.text() == "verb  ·  B2"
    assert widget._source_label.text() == "Source: Oxford 3000"
    assert widget._position_label.text() == "133 / 3,000"


def test_absent_metadata_hides_its_row(qapp) -> None:
    widget = ReviewWidget()
    widget.show_item(item(part_of_speech=None, cefr_level=None, sources=()), False)
    assert widget._meta_label.isHidden()
    assert widget._definition_label.isHidden()
    assert widget._source_label.isHidden()


def test_a_live_word_hides_its_status_and_an_earlier_word_shows_it(qapp) -> None:
    widget = ReviewWidget()
    widget.show_item(item(), False)
    assert widget._status_badge.isHidden()
    assert widget._history_banner.isHidden()

    widget.show_item(item(steps_back=2, status=ReviewStatus.UNKNOWN), True)
    assert not widget._status_badge.isHidden()
    assert widget._status_badge.text().endswith("Unknown")
    assert "2 words back" in widget._history_banner.text()
    assert not widget._reset_button.isHidden()


@pytest.mark.parametrize(
    "key, expected",
    [
        (Qt.Key.Key_K, True),
        (Qt.Key.Key_Left, True),
        (Qt.Key.Key_U, False),
        (Qt.Key.Key_Right, False),
    ],
)
def test_answer_keys(qtbot, key, expected) -> None:
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    widget.show_item(item(), False)
    with qtbot.waitSignal(widget.answered) as signal:
        qtbot.keyClick(widget, key)
    assert signal.args == [expected]


def test_enter_and_space_ask_to_repeat(qtbot) -> None:
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    widget.show_item(item(), False)
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        with qtbot.waitSignal(widget.repeat_requested):
            qtbot.keyClick(widget, key)


def test_backspace_only_works_when_there_is_somewhere_to_go(qtbot) -> None:
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    fired: list[bool] = []
    widget.back_requested.connect(lambda: fired.append(True))

    widget.show_item(item(), can_go_back=False)
    qtbot.keyClick(widget, Qt.Key.Key_Backspace)
    assert fired == []

    widget.show_item(item(), can_go_back=True)
    qtbot.keyClick(widget, Qt.Key.Key_Backspace)
    assert fired == [True]


def test_reset_key_only_applies_to_a_word_with_a_status(qtbot) -> None:
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    fired: list[bool] = []
    widget.reset_requested.connect(lambda: fired.append(True))

    widget.show_item(item(), False)
    qtbot.keyClick(widget, Qt.Key.Key_R)
    assert fired == []

    widget.show_item(item(steps_back=1, status=ReviewStatus.KNOWN), True)
    qtbot.keyClick(widget, Qt.Key.Key_R)
    assert fired == [True]


def test_a_double_click_answers_once(qapp) -> None:
    widget = ReviewWidget()
    widget.show_item(item(), False)
    received: list[bool] = []
    widget.answered.connect(received.append)
    widget._known_button.click()
    widget._known_button.click()
    assert received == [True]


# -- components ------------------------------------------------------------


def test_stats_bar_shows_every_counter(qapp) -> None:
    stats = StatsBar()
    stats.update_progress(Progress(total=3000, known=120, unknown=12))
    assert stats._known._value.text() == "120"
    assert stats._unknown._value.text() == "12"
    assert stats._remaining._value.text() == "2,868"
    assert stats._total._value.text() == "3,000"


def test_list_card_describes_its_list(qapp) -> None:
    card = ListCard(
        VocabularyList(id=4, name="German A1", language="de",
                       progress=Progress(total=40, known=9, unknown=5))
    )
    assert card.name_label.text() == "German A1"
    assert card.language_tag.text() == "DE"
    assert card.meta_label.text() == "40 words · 9 known · 5 unknown"
    assert card.percent_label.text() == "35%"


def test_segmented_progress_explains_itself(qapp) -> None:
    bar = SegmentedProgress()
    bar.set_progress(Progress(total=28, known=0, unknown=28))
    assert bar.toolTip() == "0 known · 28 unknown · 0 not reviewed"


def test_mode_switch_reports_changes(qtbot) -> None:
    switch = ModeSwitch()
    qtbot.addWidget(switch)
    with qtbot.waitSignal(switch.mode_changed) as signal:
        switch.buttons[ModeSwitch.LIST].click()
    assert signal.args == [ModeSwitch.LIST]
    assert switch.mode == ModeSwitch.LIST


# -- vocabulary table --------------------------------------------------------


@pytest.fixture
def table(qtbot) -> VocabularyTable:
    widget = VocabularyTable()
    qtbot.addWidget(widget)
    widget.set_words([
        word(id=1, word="abandon", normalized_word="abandon", cefr_level="B2",
             status=ReviewStatus.KNOWN, definition="to leave"),
        word(id=2, word="ability", normalized_word="ability", cefr_level="A2",
             status=ReviewStatus.UNKNOWN),
        word(id=3, word="able", normalized_word="able", cefr_level="A2"),
        word(id=4, word="absorb", normalized_word="absorb", cefr_level="C1",
             status=ReviewStatus.UNKNOWN),
    ])
    return widget


def visible_words(widget: VocabularyTable) -> list[str]:
    return [
        widget.proxy.index(row, Column.WORD).data() for row in range(widget.proxy.rowCount())
    ]


def test_table_starts_in_list_order_and_counts_words(table) -> None:
    assert visible_words(table) == ["abandon", "ability", "able", "absorb"]
    assert table.count_label.text() == "4 words"


def test_search_matches_words_and_definitions(table) -> None:
    table.search.setText("abl")
    assert visible_words(table) == ["able"]
    table.search.setText("AB")
    assert visible_words(table) == ["abandon", "ability", "able", "absorb"]
    table.search.setText("LEAVE")
    assert visible_words(table) == ["abandon"]
    assert table.count_label.text() == "1 of 4 words"


def test_status_filter(table) -> None:
    table.status_filter.setCurrentIndex(table.status_filter.findData("unknown"))
    assert visible_words(table) == ["ability", "absorb"]
    table.status_filter.setCurrentIndex(0)
    assert len(visible_words(table)) == 4


def test_sorting_by_cefr_uses_level_order(table) -> None:
    table.view.sortByColumn(Column.CEFR, Qt.SortOrder.AscendingOrder)
    assert visible_words(table) == ["ability", "able", "abandon", "absorb"]
    table.view.sortByColumn(Column.ORDER, Qt.SortOrder.AscendingOrder)
    assert visible_words(table) == ["abandon", "ability", "able", "absorb"]


def test_sorting_by_status_puts_what_needs_attention_first(table) -> None:
    table.view.sortByColumn(Column.STATUS, Qt.SortOrder.AscendingOrder)
    assert visible_words(table) == ["ability", "absorb", "able", "abandon"]


def test_status_cells_carry_the_status_for_painting(table) -> None:
    assert table.proxy.index(0, Column.STATUS).data(STATUS_ROLE) == "known"


def test_selection_shows_the_selection_bar(table) -> None:
    assert table.selection_bar.isHidden()
    table.select_ids([2, 4])
    assert not table.selection_bar.isHidden()
    assert table.selection_count.text() == "2 selected"
    assert table.selected_ids() == [2, 4]
    table.clear_selection()
    assert table.selection_bar.isHidden()


def test_select_all_selects_only_visible_rows(table) -> None:
    table.search.setText("ab")
    table.status_filter.setCurrentIndex(table.status_filter.findData("unknown"))
    table.select_all()
    assert table.selected_ids() == [2, 4]


@pytest.mark.parametrize(
    "key, status",
    [(Qt.Key.Key_K, ReviewStatus.KNOWN), (Qt.Key.Key_U, ReviewStatus.UNKNOWN),
     (Qt.Key.Key_R, ReviewStatus.NOT_REVIEWED)],
)
def test_status_keys_apply_to_the_selection(qtbot, table, key, status) -> None:
    table.select_ids([1, 3])
    with qtbot.waitSignal(table.status_requested) as signal:
        qtbot.keyClick(table.view, key)
    assert signal.args[0] == [1, 3]
    assert ReviewStatus(signal.args[1]) is status


def test_toolbar_buttons_apply_to_the_selection(qtbot, table) -> None:
    table.select_ids([2])
    with qtbot.waitSignal(table.status_requested) as signal:
        table.mark_known_button.click()
    assert signal.args[0] == [2]


def test_refreshing_rows_keeps_the_selection(table) -> None:
    table.select_ids([2])
    table.refresh_words([word(id=2, word="ability", normalized_word="ability",
                              status=ReviewStatus.KNOWN)])
    assert table.selected_ids() == [2]
    assert table.proxy.index(1, Column.STATUS).data(STATUS_ROLE) == "known"


# -- main window -----------------------------------------------------------


def test_an_empty_database_opens_on_the_welcome_screen(qapp, theme, service) -> None:
    win = MainWindow(service, theme)
    try:
        assert win.current_page == HOME
        assert win.home._stack.currentWidget() is win.home.welcome
        win.show_page(REVIEW)  # nothing to review: stays home
        assert win.current_page == HOME
    finally:
        win.close()


def test_a_fresh_list_opens_home_with_it_ready_to_continue(window) -> None:
    assert window.current_page == HOME
    assert window.home.current_name.text() == "sample"
    assert window.home.continue_button.text().startswith("Continue")


def test_continue_opens_flashcards_for_the_current_list(window) -> None:
    window.home.continue_button.click()
    assert window.current_page == REVIEW
    assert window.review.list_button.text().startswith("sample")
    assert window.review.flashcard._word_label.text() == "alpha"


def test_answering_and_going_back_through_the_window(window, loaded) -> None:
    window.open_review(mode=ModeSwitch.FLASHCARD)
    card = window.review.flashcard

    card._known_button.click()
    card._unknown_button.click()
    assert card._word_label.text() == "gamma"

    card.back_requested.emit()
    assert card._word_label.text() == "beta"
    assert card._status_badge.text().endswith("Unknown")
    card.back_requested.emit()
    assert card._word_label.text() == "alpha"

    statuses = {w.normalized_word: w.status for w in loaded.list_words(window.review.list_id)}
    assert statuses["alpha"] is ReviewStatus.KNOWN
    assert statuses["beta"] is ReviewStatus.UNKNOWN
    assert window.review.stats._known._value.text() == "1"


def test_switching_mode_keeps_the_list_and_the_history(window) -> None:
    window.open_review(mode=ModeSwitch.FLASHCARD)
    list_id = window.review.list_id
    session = window.review.session
    window.review.flashcard._known_button.click()

    window.review.set_mode(ModeSwitch.LIST)
    assert window.review.list_id == list_id
    assert window.review.modes.currentIndex() == 1
    assert window.review.table.model.rowCount() == 5

    window.review.set_mode(ModeSwitch.FLASHCARD)
    assert window.review.session is session
    assert window.review.session.can_go_back


def test_bulk_status_change_in_list_mode(window, loaded) -> None:
    window.open_review(mode=ModeSwitch.LIST)
    table = window.review.table
    ids = [w.id for w in table.model.words[:3]]
    table.select_ids(ids)

    table.mark_known_button.click()

    assert all(loaded.get_word(i).status is ReviewStatus.KNOWN for i in ids)
    assert window.review.stats._known._value.text() == "3"
    assert table.selected_ids() == ids


def test_finishing_a_list_and_stepping_back_into_it(window) -> None:
    window.open_review(mode=ModeSwitch.FLASHCARD)
    for _ in range(5):
        window.review.flashcard._known_button.click()
    assert window.review.flashcard_stack.currentWidget() is window.review.finished

    window.review.finished.back_requested.emit()
    assert window.review.flashcard_stack.currentWidget() is window.review.flashcard
    assert window.review.flashcard._word_label.text() == "epsilon"


def test_unknown_manager_lists_and_clears_unknown_words(window, loaded) -> None:
    words = loaded.list_words(loaded.lists()[0].id)
    loaded.set_status([words[0].id, words[1].id], ReviewStatus.UNKNOWN)

    window.show_page(UNKNOWN)
    table = window.unknown.table
    assert table.model.rowCount() == 2
    assert "2 words" in window.unknown.subtitle.text()

    table.select_ids([words[0].id])
    table.mark_known_button.click()

    assert table.model.rowCount() == 1
    assert loaded.get_word(words[0].id).status is ReviewStatus.KNOWN
    assert loaded.get_word(words[0].id) is not None


def test_unknown_manager_can_filter_by_list(window, loaded) -> None:
    other = loaded.create_list("Other")
    extra, _ = loaded.add_word(other.id, "zebra")
    loaded.set_status([extra.id], ReviewStatus.UNKNOWN)
    first = loaded.list_words(loaded.lists()[1].id)[0]
    loaded.set_status([first.id], ReviewStatus.UNKNOWN)

    window.show_page(UNKNOWN)
    assert window.unknown.table.model.rowCount() == 2
    combo = window.unknown.list_filter
    combo.setCurrentIndex(combo.findData(other.id))
    assert [w.normalized_word for w in window.unknown.table.model.words] == ["zebra"]


def test_the_current_list_and_mode_are_remembered(qapp, theme, loaded) -> None:
    second = loaded.create_list("Second")
    loaded.add_word(second.id, "word")
    first = MainWindow(loaded, theme)
    first.open_review(second.id, ModeSwitch.LIST)
    first.close()

    again = MainWindow(VocabularyService(loaded.database), theme)
    try:
        again.show_page(REVIEW)
        assert again.review.list_id == second.id
        assert again.review.mode == ModeSwitch.LIST
    finally:
        again.close()


def test_resuming_mid_list_opens_straight_into_review(qapp, theme, loaded) -> None:
    list_id = loaded.lists()[0].id
    loaded.mark_known(loaded.get_next_word(list_id).id)
    win = MainWindow(loaded, theme)
    try:
        assert win.current_page == REVIEW
        assert win.review.flashcard._word_label.text() == "beta"
    finally:
        win.close()


def test_a_deleted_current_list_falls_back_to_another(window, loaded) -> None:
    other = loaded.create_list("Other")
    loaded.add_word(other.id, "word")
    window.open_review(loaded.lists()[1].id)  # "sample"
    loaded.delete_list(window.review.list_id)

    window._on_data_changed()

    assert window.current_page == REVIEW
    assert window.review.list_id == other.id
    assert window.review.list_button.text().startswith("Other")


def test_the_theme_button_names_the_theme_it_switches_to(window) -> None:
    window._theme.apply(ThemeName.LIGHT)
    window._update_theme_labels()
    assert window.theme_button.text() == "Dark"
    window.toggle_theme()
    assert window._theme.current is ThemeName.DARK
    assert window.theme_button.text() == "Light"


# -- dialogs ---------------------------------------------------------------


def test_list_dialog_creates_a_list(qapp, service) -> None:
    dialog = ListDialog(service)
    dialog.name_field.setText("German A1")
    dialog.language_field.setCurrentIndex(dialog.language_field.findData("de"))
    dialog.description_field.setPlainText("Basics")
    dialog._save()

    assert dialog.result_list is not None
    assert (dialog.result_list.name, dialog.result_list.language) == ("German A1", "de")


def test_list_dialog_explains_a_taken_name_and_keeps_the_input(qapp, service) -> None:
    service.create_list("German A1")
    dialog = ListDialog(service)
    dialog.name_field.setText("german a1")
    dialog._save()

    assert dialog.result_list is None
    assert not dialog.error.isHidden()
    assert "already exists" in dialog.error.text()
    assert dialog.name_field.text() == "german a1"


def test_add_word_dialog_adds_and_stays_open(qapp, service) -> None:
    target = service.create_list("German", "de")
    dialog = AddWordDialog(service, target)
    dialog.word_field.setText("Haus")
    dialog.definition_field.setText("house")
    dialog._add()

    assert "Added" in dialog.message.text()
    assert dialog.word_field.text() == ""
    (stored,) = service.list_words(target.id)
    assert (stored.word, stored.definition, stored.language) == ("Haus", "house", "de")

    dialog.word_field.setText("123")
    dialog._add()
    assert "cannot be added" in dialog.message.text()


# -- import dialog -----------------------------------------------------------


def test_importing_two_files_into_new_and_existing_lists(qtbot, service, tmp_path: Path) -> None:
    existing = service.create_list("My Difficult Words")
    german = tmp_path / "german_a1.json"
    german.write_text(
        json.dumps({"name": "German A1", "language": "de", "words": ["Haus", "gehen"]}),
        encoding="utf-8",
    )
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    dialog = ImportDialog(service, initial_paths=[german, broken])
    qtbot.addWidget(dialog)
    dialog._read()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == ImportDialog._PREVIEW, timeout=10_000)

    good, bad = dialog._panels
    assert bad.outcome.preview is None and not bad.included
    assert good.new_list.isChecked() and good.new_name.text() == "German A1"
    assert "2 new" in good.detail.text()

    # Also send the words to an existing list.
    for row in range(good.targets.count()):
        entry = good.targets.item(row)
        if entry.data(Qt.ItemDataRole.UserRole) == existing.id:
            entry.setCheckState(Qt.CheckState.Checked)
    assert dialog.primary_button.isEnabled()

    dialog._commit()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == ImportDialog._DONE, timeout=10_000)

    assert dialog.imported_anything
    assert {lst.name: lst.progress.total for lst in service.lists()} == {
        "German A1": 2,
        "My Difficult Words": 2,
    }
    assert "broken.json" in dialog.summary.text()


def test_import_dialog_blocks_a_language_clash(qtbot, service, tmp_path: Path) -> None:
    english = service.create_list("Oxford 3000", "en")
    german = tmp_path / "german.json"
    german.write_text(json.dumps({"language": "de", "words": ["Haus"]}), encoding="utf-8")

    dialog = ImportDialog(service, target_list_id=english.id, initial_paths=[german])
    qtbot.addWidget(dialog)
    dialog._read()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == ImportDialog._PREVIEW, timeout=10_000)

    (panel,) = dialog._panels
    assert panel.problem() is not None and "German" in panel.problem()
    assert not dialog.primary_button.isEnabled()


# -- themes ----------------------------------------------------------------


def test_both_themes_produce_complete_and_different_stylesheets() -> None:
    light, dark = build_stylesheet(LIGHT), build_stylesheet(DARK)
    for palette, sheet in ((LIGHT, light), (DARK, dark)):
        assert palette.background in sheet
        assert palette.known in sheet and palette.unknown in sheet
        assert "#StatusBadge" in sheet and "QTableView" in sheet
        assert f"chevron-down-{palette.name.value}.svg" in sheet
    assert light != dark


def test_theme_icons_exist() -> None:
    icons = Path(__file__).resolve().parents[1] / "lexitrack" / "ui" / "theme" / "icons"
    assert (icons / "chevron-down-light.svg").exists()
    assert (icons / "chevron-down-dark.svg").exists()


def test_toggling_switches_between_the_two_themes(theme) -> None:
    theme.apply(ThemeName.LIGHT)
    assert theme.toggle() is ThemeName.DARK
    assert theme.toggle() is ThemeName.LIGHT


def test_the_chosen_theme_is_remembered(qapp, theme) -> None:
    theme.apply(ThemeName.DARK)
    assert ThemeManager().current is ThemeName.DARK
    assert DARK.background in qapp.styleSheet()
    theme.apply(ThemeName.LIGHT)
    assert ThemeManager().current is ThemeName.LIGHT


# -- flows that open native dialogs ----------------------------------------


@pytest.fixture
def no_blocking_dialogs(monkeypatch):
    """Answer message boxes instead of blocking, and record what they said."""
    from PySide6.QtWidgets import QMessageBox

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2]))
    )
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2]))
    )
    return shown


def test_export_dialog_writes_the_chosen_scope_and_format(
    qapp, loaded, tmp_path, monkeypatch, no_blocking_dialogs
) -> None:
    from PySide6.QtWidgets import QFileDialog

    from lexitrack.services.export_service import ExportFormat
    from lexitrack.ui.export_dialog import ExportDialog, ExportScope

    list_id = loaded.lists()[0].id
    words = loaded.list_words(list_id)
    loaded.set_status([words[0].id], ReviewStatus.UNKNOWN)
    target = tmp_path / "out.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    everything = ExportScope(
        "All words", lambda: loaded.export_content_for_list(list_id), ExportFormat.JSON
    )
    unknown = ExportScope(
        "Unknown", lambda: loaded.export_content_for_unknown(list_id), ExportFormat.PDF
    )
    dialog = ExportDialog(loaded, [everything, unknown])
    assert dialog.selected_format() is ExportFormat.JSON
    dialog._scope_group.button(1).setChecked(True)
    assert dialog.selected_format() is ExportFormat.PDF
    dialog._format_buttons[ExportFormat.JSON].setChecked(True)
    dialog._export()

    assert dialog.written == target
    assert json.loads(target.read_text(encoding="utf-8"))["words"] == [words[0].word]


def test_export_dialog_refuses_an_empty_scope(qapp, loaded) -> None:
    from lexitrack.ui.export_dialog import ExportDialog, ExportScope

    scope = ExportScope("Unknown", lambda: loaded.export_content_for_unknown())
    dialog = ExportDialog(loaded, [scope])
    dialog._export()
    assert dialog.written is None
    assert not dialog.error.isHidden()


def test_deleting_a_list_asks_first_and_says_what_is_lost(
    window, loaded, monkeypatch
) -> None:
    import lexitrack.ui.list_actions as list_actions

    asked: list[str] = []
    monkeypatch.setattr(
        list_actions, "confirm", lambda _p, _t, text, _a: asked.append(text) or True
    )
    list_id = loaded.lists()[0].id

    assert window.actions.delete_list(list_id)

    assert "5 words are only in this list" in asked[0]
    assert loaded.lists() == []
    assert window.current_page == HOME


def test_declining_the_delete_keeps_the_list(window, loaded, monkeypatch) -> None:
    import lexitrack.ui.list_actions as list_actions

    monkeypatch.setattr(list_actions, "confirm", lambda *a: False)
    assert not window.actions.delete_list(loaded.lists()[0].id)
    assert len(loaded.lists()) == 1


def test_word_dialog_changes_status_explicitly(qapp, loaded) -> None:
    from lexitrack.ui.dialogs import WordDialog

    target = loaded.list_words(loaded.lists()[0].id)[0]
    dialog = WordDialog(loaded, target)
    assert not dialog._buttons[ReviewStatus.NOT_REVIEWED].isEnabled()

    dialog._buttons[ReviewStatus.UNKNOWN].click()

    assert loaded.get_word(target.id).status is ReviewStatus.UNKNOWN
    assert dialog.badge.text().endswith("Unknown")


def test_adding_selected_words_to_another_list(
    window, loaded, monkeypatch, no_blocking_dialogs
) -> None:
    import lexitrack.ui.review_page as review_page

    other = loaded.create_list("My Difficult Words")

    class Chooser:
        def __init__(self, *args, **kwargs):
            self.chosen = loaded.get_list(other.id)

        def exec(self):
            return True

    monkeypatch.setattr(review_page, "ChooseListDialog", Chooser)
    window.open_review(loaded.lists()[1].id, ModeSwitch.LIST)
    ids = [w.id for w in window.review.table.model.words[:2]]
    window.review.table.select_ids(ids)

    window.review.table.add_to_list_requested.emit(ids)

    assert loaded.get_progress(other.id).total == 2
    assert "Added 2 words" in no_blocking_dialogs[-1]
