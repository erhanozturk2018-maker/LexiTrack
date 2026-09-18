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
from PySide6.QtWidgets import QApplication, QStyle  # noqa: E402

from lexitrack.models.user_word_state import Progress, ReviewStatus  # noqa: E402
from lexitrack.models.vocabulary_list import VocabularyList  # noqa: E402
from lexitrack.repositories.word_repository import StoredWord  # noqa: E402
from lexitrack.services.review_session import ReviewItem  # noqa: E402
from lexitrack.services.vocabulary_service import VocabularyService  # noqa: E402
from lexitrack.ui.components.cards import ListCard, ModeSwitch, SegmentedProgress  # noqa: E402
from lexitrack.ui.components.status import STATUS_ROLE  # noqa: E402
from lexitrack.ui.components.vocabulary_table import (  # noqa: E402
    WORD_ID_ROLE,
    Column,
    VocabularyTable,
)
from lexitrack.ui.dialogs import AddWordDialog, ListDialog  # noqa: E402
from lexitrack.ui.import_dialog import ImportDialog  # noqa: E402
from lexitrack.ui.main_window import HOME, REVIEW, STUDY, UNKNOWN, MainWindow  # noqa: E402
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


@pytest.mark.parametrize("key, expected", [(Qt.Key.Key_K, True), (Qt.Key.Key_U, False)])
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
    for name in ("chevron-down", "check"):
        for theme_name in ("light", "dark"):
            assert (icons / f"{name}-{theme_name}.svg").exists()


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


def test_order_numbers_are_never_cut_off(qtbot, theme) -> None:
    """Regression: a fixed 56px column showed "1,013" but elided "1,020" to "1,0…"."""
    theme.apply(ThemeName.LIGHT)
    widget = VocabularyTable()
    qtbot.addWidget(widget)
    widget.set_words([
        word(id=i, word=f"w{i}", normalized_word=f"w{i}") for i in range(1, 4954)
    ])
    widget.resize(1200, 600)
    widget.show()
    qtbot.waitExposed(widget)

    from lexitrack.ui.components.vocabulary_table import order_cell_margin

    metrics = widget.view.fontMetrics()
    frame = widget.view.style().pixelMetric(
        QStyle.PixelMetric.PM_FocusFrameHMargin, None, widget.view
    ) + 1
    # What the cell really has for text: stylesheet padding and style margin.
    available = widget.view.columnWidth(Column.ORDER) - 2 * 10 - 2 * frame
    assert order_cell_margin(widget.view) >= 2 * 10 + 2 * frame
    for label in ("1,020", "4,953", "8,888"):
        assert metrics.horizontalAdvance(label) <= available, label



def test_arrow_keys_navigate_and_never_answer(qtbot) -> None:
    """Regression for the 0.2.0 behaviour where \u2190 and \u2192 marked the word."""
    widget = ReviewWidget()
    qtbot.addWidget(widget)
    answered: list[bool] = []
    back: list[bool] = []
    forward: list[bool] = []
    widget.answered.connect(answered.append)
    widget.back_requested.connect(lambda: back.append(True))
    widget.forward_requested.connect(lambda: forward.append(True))

    # On an unanswered word: \u2190 goes back, \u2192 does nothing.
    widget.show_item(item(), can_go_back=True)
    qtbot.keyClick(widget, Qt.Key.Key_Right)
    qtbot.keyClick(widget, Qt.Key.Key_Left)
    assert (answered, back, forward) == ([], [True], [])
    assert widget._next_button.isHidden()

    # On a word you went back to: \u2192 moves forward.
    widget.show_item(item(steps_back=1, status=ReviewStatus.KNOWN), can_go_back=True)
    assert not widget._next_button.isHidden()
    qtbot.keyClick(widget, Qt.Key.Key_Right)
    assert (answered, forward) == ([], [True])


def test_arrows_move_through_the_session_without_changing_status(window, loaded) -> None:
    window.open_review(mode=ModeSwitch.FLASHCARD)
    card = window.review.flashcard
    card._known_button.click()   # alpha
    card._unknown_button.click()  # beta

    card.back_requested.emit()
    card.back_requested.emit()
    assert card._word_label.text() == "alpha"
    card.forward_requested.emit()
    assert card._word_label.text() == "beta"
    card.forward_requested.emit()
    assert card._word_label.text() == "gamma"

    statuses = {w.normalized_word: w.status for w in loaded.list_words(window.review.list_id)}
    assert (statuses["alpha"], statuses["beta"], statuses["gamma"]) == (
        ReviewStatus.KNOWN, ReviewStatus.UNKNOWN, ReviewStatus.NOT_REVIEWED,
    )


# -- copying and moving words between lists ----------------------------------


@pytest.fixture
def two_lists(window, loaded):
    """'sample' (five words, open in List mode) and an empty 'Difficult' list."""
    sample = next(lst for lst in loaded.lists() if lst.name == "sample")
    difficult = loaded.create_list("Difficult")
    window.open_review(sample.id, ModeSwitch.LIST)
    table = window.review.table
    ids = [w.id for w in table.model.words[:2]]
    table.select_ids(ids)
    return window, loaded, sample, difficult, ids


def test_copy_to_adds_words_and_keeps_them_here(two_lists) -> None:
    window, service, sample, difficult, ids = two_lists

    window.review.table.copy_requested.emit(ids, difficult.id)

    assert service.get_progress(difficult.id).total == 2
    assert service.get_progress(sample.id).total == 5
    assert "Copied 2 words" in window.review.toast.message.text()
    assert window.review.toast.can_undo


def test_move_to_adds_there_and_removes_here_without_deleting(two_lists) -> None:
    window, service, sample, difficult, ids = two_lists
    service.mark_known(ids[0])

    window.review.table.move_requested.emit(ids, difficult.id)

    assert service.get_progress(sample.id).total == 3
    assert service.get_progress(difficult.id).total == 2
    assert service.get_word(ids[0]).status is ReviewStatus.KNOWN
    assert window.review.table.model.rowCount() == 3
    assert "Moved 2 words" in window.review.toast.message.text()


def test_undo_reverses_a_move(two_lists) -> None:
    window, service, sample, difficult, ids = two_lists
    window.review.table.move_requested.emit(ids, difficult.id)

    window.review.toast.undo()

    assert service.get_progress(sample.id).total == 5
    assert service.get_progress(difficult.id).total == 0
    assert window.review.toast.isHidden()


def test_undo_of_a_copy_leaves_words_that_were_already_there(two_lists) -> None:
    window, service, sample, difficult, ids = two_lists
    service.add_words_to_list(difficult.id, [ids[0]])

    window.review.table.copy_requested.emit(ids, difficult.id)
    assert "1 were already there" in window.review.toast.message.text()
    window.review.toast.undo()

    assert [w.id for w in service.list_words(difficult.id)] == [ids[0]]


def test_lists_in_another_language_are_not_offered(two_lists) -> None:
    window, service, sample, difficult, ids = two_lists
    service.create_list("German", "de")
    offered = [name for _id, name in window.review._transfer_targets(ids)]
    assert offered == ["Difficult"]


def test_selection_bar_menus_list_targets_and_new_list(two_lists) -> None:
    window, *_ = two_lists
    table = window.review.table
    menu = table.copy_button.menu()
    menu.aboutToShow.emit()
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels == ["Difficult", "New List\u2026"]


def test_right_click_menu_offers_every_selection_action(two_lists) -> None:
    window, *_ = two_lists
    labels = [a.text() for a in window.review.table.build_context_menu().actions()]
    for expected in ("Mark Known", "Mark Unknown", "Reset to Not Reviewed", "Copy to",
                     "Move to", "Remove from This List\u2026", "Export\u2026"):
        assert expected in labels


def test_c_and_m_keys_open_the_picker(qtbot, two_lists, monkeypatch) -> None:
    import lexitrack.ui.word_transfer as word_transfer

    window, service, sample, difficult, ids = two_lists
    asked: list[str] = []

    def fake_pick(title, lists, anchor, at=None, allow_new=True, current_id=None):
        asked.append(title)
        return difficult.id

    monkeypatch.setattr(word_transfer.ListPicker, "pick", staticmethod(fake_pick))
    qtbot.keyClick(window.review.table.view, Qt.Key.Key_M)

    assert asked == ["Move 2 to"]
    assert service.get_progress(difficult.id).total == 2
    assert service.get_progress(sample.id).total == 3


def test_list_picker_filters_and_is_keyboard_driven(qapp, loaded) -> None:
    from lexitrack.ui.word_transfer import NEW_LIST_ID, ListPicker

    loaded.create_list("German A1", "de")
    loaded.create_list("IELTS Vocabulary", "en")
    picker = ListPicker("Copy to", loaded.lists())

    picker.filter.setText("ielts")
    visible = [picker.items.item(r).text() for r in picker._visible_rows()]
    assert visible[0].startswith("IELTS Vocabulary")
    assert visible[-1].endswith("New List\u2026")

    picker.move_selection(1)
    picker._accept_current()
    assert picker.chosen_id == NEW_LIST_ID


def test_the_lists_column_shows_other_lists_only(two_lists) -> None:
    window, service, sample, difficult, ids = two_lists
    window.review.table.copy_requested.emit(ids[:1], difficult.id)
    table = window.review.table
    row = next(r for r in range(table.proxy.rowCount())
               if table.proxy.index(r, Column.WORD).data(WORD_ID_ROLE) == ids[0])
    assert table.proxy.index(row, Column.LISTS).data() == "Difficult"
    assert table.proxy.headerData(Column.LISTS, Qt.Orientation.Horizontal) == "ALSO IN"
    other = next(r for r in range(table.proxy.rowCount()) if r != row)
    assert table.proxy.index(other, Column.LISTS).data() == "\u2014"


def test_unknown_words_can_be_copied_but_not_moved(window, loaded) -> None:
    difficult = loaded.create_list("Difficult")
    first = loaded.list_words(next(lst.id for lst in loaded.lists() if lst.name == "sample"))[0]
    loaded.mark_unknown(first.id)
    window.show_page(UNKNOWN)
    table = window.unknown.table
    assert table.move_button.isHidden()

    table.select_ids([first.id])
    table.copy_requested.emit([first.id], difficult.id)

    assert loaded.get_progress(difficult.id).total == 1
    assert table.model.rowCount() == 1  # still unknown, still listed


# -- keyboard navigation ---------------------------------------------------


def test_arrow_keys_move_between_home_cards(qtbot, window, loaded) -> None:
    """Arrows move focus across the 3-column grid; Up from the top row returns to Continue.

    ``focusWidget()`` is checked rather than ``hasFocus()``: the latter is false
    whenever the test window is not the active window, which it may not be.
    """
    for name in ("Beta", "Gamma", "Delta"):
        created = loaded.create_list(name)
        loaded.add_word(created.id, "word")
    window.show_page(HOME)
    window.show()
    qtbot.waitExposed(window)
    cards = list(window.home._cards.values())  # Beta, Delta, Gamma / sample

    def focused():
        return window.focusWidget()

    cards[0].setFocus()
    qtbot.keyClick(cards[0], Qt.Key.Key_Right)
    assert focused() is cards[1]
    qtbot.keyClick(cards[1], Qt.Key.Key_Down)  # nothing below Delta
    assert focused() is cards[1]
    qtbot.keyClick(cards[1], Qt.Key.Key_Left)
    qtbot.keyClick(cards[0], Qt.Key.Key_Down)
    assert focused() is cards[3]
    qtbot.keyClick(cards[3], Qt.Key.Key_Up)
    assert focused() is cards[0]
    qtbot.keyClick(cards[0], Qt.Key.Key_Up)
    assert focused() is window.home.continue_button
    qtbot.keyClick(window.home.continue_button, Qt.Key.Key_Down)
    assert isinstance(focused(), ListCard)

def test_enter_on_a_home_card_opens_it(qtbot, window, loaded) -> None:
    window.show_page(HOME)
    card = next(iter(window.home._cards.values()))
    qtbot.keyClick(card, Qt.Key.Key_Return)
    assert window.current_page == REVIEW
    assert window.review.list_id == card.list_id


def test_ctrl_tab_cycles_pages(window) -> None:
    window.show_page(HOME)
    window.cycle_page(1)
    assert window.current_page == REVIEW
    window.cycle_page(1)
    assert window.current_page == UNKNOWN
    window.cycle_page(1)
    assert window.current_page == STUDY, "cycling wraps round to the first tab"
    window.cycle_page(1)
    assert window.current_page == HOME
    window.cycle_page(-1)
    assert window.current_page == STUDY


def test_ctrl_l_switches_list_from_the_keyboard(window, loaded, monkeypatch) -> None:
    import lexitrack.ui.review_page as review_page

    other = loaded.create_list("Other")
    loaded.add_word(other.id, "zebra")
    monkeypatch.setattr(
        review_page.ListPicker, "pick", staticmethod(lambda *a, **k: other.id)
    )
    window.show_page(HOME)

    window.switch_list()

    assert window.current_page == REVIEW
    assert window.review.list_id == other.id


# -- export preview ----------------------------------------------------------


def test_export_orders_words_alphabetically_or_by_cefr(
    qapp, service, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QFileDialog

    from lexitrack.services.export_service import ExportFormat
    from lexitrack.ui.export_dialog import ExportDialog, ExportOrder, ExportScope

    target = service.create_list("Levels", "en")
    for text, level in (("zebra", "A1"), ("apple", "B2"), ("mango", "A1")):
        service.add_word(target.id, text, cefr_level=level)
    out = tmp_path / "levels.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), ""))
    )
    scope = ExportScope("All", lambda: service.export_content_for_list(target.id))

    def exported(order: ExportOrder) -> list[str]:
        dialog = ExportDialog(service, [scope])
        dialog._format_buttons[ExportFormat.JSON].click()
        dialog._order_buttons[order].setChecked(True)
        dialog._export()
        words = json.loads(out.read_text(encoding="utf-8"))["words"]
        return [w["word"] if isinstance(w, dict) else w for w in words]

    assert exported(ExportOrder.LIST) == ["zebra", "apple", "mango"]
    assert exported(ExportOrder.ALPHABETICAL) == ["apple", "mango", "zebra"]
    assert exported(ExportOrder.CEFR) == ["mango", "zebra", "apple"]


def test_export_preview_shows_the_file_before_saving(qtbot, loaded) -> None:
    from lexitrack.services.export_service import ExportFormat
    from lexitrack.ui.export_dialog import ExportDialog, ExportScope

    list_id = loaded.lists()[0].id
    scope = ExportScope("All", lambda: loaded.export_content_for_list(list_id))
    dialog = ExportDialog(loaded, [scope])
    qtbot.addWidget(dialog)

    dialog._render_preview()
    assert dialog.selected_format() is ExportFormat.PDF
    assert not dialog.page_view.pixmap().isNull()
    assert dialog.summary.text().startswith("5 words")

    dialog._format_buttons[ExportFormat.CSV].click()
    dialog._render_preview()
    assert dialog.text_view.toPlainText().startswith("Word,")
    assert dialog.written is None


def test_export_remembers_format_and_order(qapp, loaded, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QFileDialog

    from lexitrack.services.export_service import ExportFormat
    from lexitrack.ui.export_dialog import ExportDialog, ExportOrder, ExportScope

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "out.csv"), "")),
    )
    list_id = loaded.lists()[0].id
    scope = ExportScope("All", lambda: loaded.export_content_for_list(list_id))
    first = ExportDialog(loaded, [scope])
    first._format_buttons[ExportFormat.CSV].click()
    first._order_buttons[ExportOrder.CEFR].setChecked(True)
    first._export()

    second = ExportDialog(loaded, [scope])
    assert second.selected_format() is ExportFormat.CSV
    assert second.selected_order() is ExportOrder.CEFR


def test_export_reports_the_result_in_a_toast(window, loaded, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QFileDialog

    from lexitrack.ui.components.toast import Toast
    from lexitrack.ui.export_dialog import ExportDialog, ExportScope

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "out.pdf"), "")),
    )
    list_id = loaded.lists()[0].id
    scope = ExportScope("All", lambda: loaded.export_content_for_list(list_id))
    ExportDialog(loaded, [scope], parent=window)._export()

    toasts = [t for t in window.findChildren(Toast) if t.property("window_toast")]
    assert toasts and "Exported 5 words" in toasts[0].message.text()
    assert toasts[0].undo_button.text() == "Open Folder"


# -- floating selection bar, level chips, details panel ----------------------


def test_the_selection_bar_floats_instead_of_pushing_the_rows(qtbot, table) -> None:
    table.resize(1200, 500)
    table.show()
    qtbot.waitExposed(table)
    top_before = table.view.mapTo(table, table.view.rect().topLeft()).y()

    table.select_ids([1])

    assert table.selection_bar.parentWidget() is table
    assert table.view.mapTo(table, table.view.rect().topLeft()).y() == top_before
    assert table._bar_space.height() > 0
    bar = table.selection_bar.geometry()
    assert table.table_frame.geometry().contains(bar)
    table.clear_selection()
    assert table._bar_space.height() == 0


def test_level_chips_filter_by_cefr(table) -> None:
    assert list(table.level_chips) == ["A2", "B2", "C1"]
    table.level_chips["A2"].setChecked(True)
    assert visible_words(table) == ["ability", "able"]
    table.level_chips["C1"].setChecked(True)
    assert visible_words(table) == ["ability", "able", "absorb"]
    table.level_chips["A2"].setChecked(False)
    table.level_chips["C1"].setChecked(False)
    assert len(visible_words(table)) == 4


def test_details_panel_follows_the_current_row(qtbot, table) -> None:
    table.show_details(True)
    table.select_ids([1])
    assert table.panel.title.text() == "abandon"
    assert table.panel.definition.text() == "to leave"
    assert not table.panel.buttons[ReviewStatus.KNOWN].isEnabled()

    requested: list = []
    table.status_requested.connect(lambda ids, status: requested.append((ids, status)))
    table.panel.buttons[ReviewStatus.UNKNOWN].click()
    assert requested == [([1], ReviewStatus.UNKNOWN)]

    table.select_ids([3])
    assert table.panel.title.text() == "able"
    assert table.panel.definition.text() == "No definition yet"


def test_enter_opens_the_details_panel(qtbot, table) -> None:
    table.show_details(False)
    table.select_ids([2])
    table.view.setCurrentIndex(table.proxy.index(1, Column.WORD))
    qtbot.keyClick(table.view, Qt.Key.Key_Return)
    assert table.details_button.isChecked()
    assert not table.panel.isHidden()


def test_details_panel_changes_status_through_the_page(window, loaded) -> None:
    list_id = loaded.lists()[0].id
    window.open_review(list_id, ModeSwitch.LIST)
    table = window.review.table
    target = table.model.words[0]
    table.select_ids([target.id])

    table.panel.buttons[ReviewStatus.UNKNOWN].click()

    assert loaded.get_word(target.id).status is ReviewStatus.UNKNOWN
    assert table.panel.badge.text().endswith("Unknown")


def test_unknown_words_has_no_status_column_or_unknown_action(window) -> None:
    table = window.unknown.table
    assert table.view.isColumnHidden(Column.STATUS)
    assert table.mark_unknown_button.isHidden()
    assert table.panel.buttons[ReviewStatus.UNKNOWN].isHidden()
    assert table.reset_button.text().endswith("Reset")


# -- app bar, command palette, shortcuts -------------------------------------


def test_there_is_no_separate_menu_bar(window) -> None:
    from PySide6.QtWidgets import QMenuBar

    assert window.findChild(QMenuBar) is None
    window._fill_app_menu()
    titles = [a.text() for a in window.app_menu.actions() if a.text()]
    assert "Export…" in titles and "Keyboard Shortcuts" in titles and "Quit" in titles


def test_the_theme_button_says_which_theme_it_switches_to(window) -> None:
    window._theme.apply(ThemeName.LIGHT)
    window._update_theme_labels()
    assert window.theme_button.accessibleName() == "Switch to dark mode"
    window.toggle_theme()
    assert window._theme.current is ThemeName.DARK
    assert window.theme_button.accessibleName() == "Switch to light mode"


def test_command_palette_runs_a_command(window, monkeypatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(window, "show_shortcuts", lambda: ran.append("shortcuts"))
    palette = window.open_palette()
    palette.search.setText("keyboard")
    item = palette.results.currentItem()
    assert item.text() == "Keyboard Shortcuts"
    palette._run_item(item)
    assert ran == ["shortcuts"]


def test_command_palette_finds_a_word_and_opens_it(window, loaded) -> None:
    window.show_page(HOME)
    palette = window.open_palette()
    palette.search.setText("gamm")
    item = palette.results.currentItem()
    assert item.text() == "gamma"
    palette._run_item(item)

    assert window.current_page == REVIEW
    assert window.review.mode == ModeSwitch.LIST
    table = window.review.table
    assert [table.model.word_at(i).word for i in range(table.model.rowCount())
            if table.model.word_at(i).id in table.selected_ids()] == ["gamma"]
    assert table.panel.title.text() == "gamma"


def test_shortcuts_window_fits_the_screen_and_filters(qtbot, window) -> None:
    from lexitrack.ui.shortcuts_dialog import ShortcutsDialog

    dialog = ShortcutsDialog(window.shortcut_sections(), parent=window)
    qtbot.addWidget(dialog)
    available = dialog.screen().availableGeometry().height()
    assert dialog.height() <= available * 0.8 + 1

    dialog.filter.setText("export")
    visible = [text.text() for _caps, text, _h in dialog._rows if not text.isHidden()]
    assert visible == ["Export"]
    keys = {keys for _title, entries in window.shortcut_sections() for keys, _ in entries}
    assert {"Ctrl+K", "Ctrl+E", "F1", "K"} <= keys


def test_the_flashcard_prints_no_shortcut_line(qapp) -> None:
    widget = ReviewWidget()
    assert not hasattr(widget, "_hint_label")
    assert widget._known_button.text() == "I Know"


def test_the_unknown_tile_opens_unknown_words(qtbot, window) -> None:
    window.show_page(HOME)
    qtbot.mouseClick(window.home.unknown_tile, Qt.MouseButton.LeftButton)
    assert window.current_page == UNKNOWN


def test_notes_show_in_the_panel_and_on_the_card(qtbot, table) -> None:
    table.model.set_words([word(id=9, word="crisps", normalized_word="crisps",
                                note="UK; chips in US")])
    table.select_ids([9])
    assert not table.panel.note.isHidden()
    assert table.panel.note.text() == "UK; chips in US"
    table.search.setText("chips in us")
    assert visible_words(table) == ["crisps"]

    card = ReviewWidget()
    qtbot.addWidget(card)
    card.show_item(item(note="UK; chips in US"), can_go_back=False)
    assert card._note_label.text() == "UK; chips in US"
    card.show_item(item(), can_go_back=False)
    assert card._note_label.isHidden()


def test_cefr_order_asks_the_pdf_for_level_headings(qapp, loaded) -> None:
    from lexitrack.ui.export_dialog import ExportDialog, ExportOrder, ExportScope

    list_id = loaded.lists()[0].id
    scope = ExportScope("All", lambda: loaded.export_content_for_list(list_id))
    dialog = ExportDialog(loaded, [scope])
    dialog._order_buttons[ExportOrder.CEFR].setChecked(True)
    assert dialog._content().group_by_level
    dialog._order_buttons[ExportOrder.ALPHABETICAL].setChecked(True)
    assert not dialog._content().group_by_level

