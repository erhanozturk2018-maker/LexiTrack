"""Render the three design directions explored for LexiTrack 0.2.

Each direction is composed from the application's real components (the
vocabulary table, list cards, flashcard widget and generated stylesheet) and
filled from a real database, so the screenshots show what the app can
actually draw rather than an idealised drawing of it.

The layouts here are static: nothing is wired to behave. They exist to compare
directions side by side before one is chosen.

Usage::

    python tools/design_mockups.py            # writes docs/design/*.png

The Oxford PDFs are used when present in ``pdfs/``; without them the Oxford
lists are replaced by the example JSON lists alone.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LEXITRACK_DATA_DIR", tempfile.mkdtemp(prefix="lexitrack-mockups-"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lexitrack.database.connection import Database  # noqa: E402
from lexitrack.models.user_word_state import ReviewStatus  # noqa: E402
from lexitrack.services.vocabulary_service import VocabularyService  # noqa: E402
from lexitrack.ui.components.cards import (  # noqa: E402
    ListCard,
    ModeSwitch,
    SegmentedProgress,
    StatTile,
)
from lexitrack.ui.components.status import StatusBadge  # noqa: E402
from lexitrack.ui.components.vocabulary_table import Column, VocabularyTable  # noqa: E402
from lexitrack.ui.progress_widget import StatsBar  # noqa: E402
from lexitrack.ui.review_widget import ReviewWidget  # noqa: E402
from lexitrack.ui.theme import ThemeManager, ThemeName  # noqa: E402

OUT = ROOT / "docs" / "design"

#: Design B's sidebar is not part of the application, so its styles live here.
SIDEBAR_STYLES = """
#Sidebar {{ background-color: {surface}; border-right: 1px solid {border}; }}
QPushButton#SidebarItem {{
    background: transparent; border: none; border-radius: 6px; color: {muted};
    text-align: left; padding: 8px 12px; font-weight: 500;
}}
QPushButton#SidebarItem:hover {{ background-color: {sunken}; color: {text}; }}
QPushButton#SidebarItem:checked {{ background-color: {accent_soft}; color: {text}; }}
"""


def apply_theme(app: QApplication, theme: ThemeManager, name: ThemeName) -> None:
    theme.apply(name)
    p = theme.palette
    app.setStyleSheet(
        app.styleSheet()
        + SIDEBAR_STYLES.format(
            surface=p.surface, border=p.border, muted=p.text_muted, sunken=p.surface_sunken,
            text=p.text, accent_soft=p.accent_soft,
        )
    )
SIZE = (1180, 760)


# -- sample data -------------------------------------------------------------


def build_sample(service: VocabularyService) -> dict[str, int]:
    pdfs = ROOT / "pdfs"
    for name in ("The_Oxford_3000_by_CEFR_level.pdf", "The_Oxford_5000_by_CEFR_level.pdf"):
        if (pdfs / name).exists():
            service.import_document(pdfs / name)
    for name in ("german_a1.json", "ielts_vocabulary.json"):
        service.import_document(ROOT / "examples" / name)

    lists = {lst.name: lst.id for lst in service.lists()}
    difficult = service.create_list("My Difficult Words", description="Words I keep forgetting")
    lists["My Difficult Words"] = difficult.id

    def review(list_name: str, count: int, unknown_every: int) -> None:
        if list_name not in lists:
            return
        words = service.list_words(lists[list_name])[:count]
        known = [w.id for i, w in enumerate(words) if i % unknown_every]
        unknown = [w.id for i, w in enumerate(words) if not i % unknown_every]
        service.set_status(known, ReviewStatus.KNOWN)
        service.set_status(unknown, ReviewStatus.UNKNOWN)

    review("Oxford 3000", 964, 7)
    review("Oxford 5000", 410, 4)
    review("German A1", 14, 3)
    review("IELTS Vocabulary", 11, 3)

    hard = [w.id for w in service.list_unknown_words()][:24]
    german_unknown = [w.id for w in service.list_unknown_words(lists["German A1"])]
    service.add_words_to_list(difficult.id, hard + german_unknown)

    # Spread review times over the last few days so "recent" looks like use.
    connection = service.database.connection
    rows = connection.execute(
        "SELECT word_id FROM user_word_state WHERE status != 'not_reviewed' ORDER BY word_id DESC"
    ).fetchall()
    now = datetime.now()
    for offset, (word_id,) in enumerate(rows):
        stamp = (now - timedelta(minutes=offset * 7 + 3)).isoformat(timespec="seconds")
        connection.execute(
            "UPDATE user_word_state SET reviewed_at = ? WHERE word_id = ?", (stamp, word_id)
        )
    return lists


# -- shared pieces -----------------------------------------------------------


def label(text: str, name: str | None = None) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text: str, variant: str | None = None, small: bool = False) -> QPushButton:
    widget = QPushButton(text)
    if variant:
        widget.setProperty("variant", variant)
    if small:
        widget.setProperty("size", "small")
    return widget


def styled(widget: QWidget, name: str) -> QWidget:
    widget.setObjectName(name)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


def flashcard(service: VocabularyService, list_id: int) -> ReviewWidget:
    widget = ReviewWidget()
    session = service.start_review(list_id)
    widget.show_item(session.current(), can_go_back=True)
    return widget


def list_table(service: VocabularyService, list_id: int, select: int = 0) -> VocabularyTable:
    table = VocabularyTable()
    table.set_words(service.list_words(list_id))
    if select:
        ids = [w.id for w in table.model.words[7:7 + select]]
        table.select_ids(ids)
    return table


def unknown_table(service: VocabularyService, lists: dict[str, int]) -> VocabularyTable:
    table = VocabularyTable(
        columns=(Column.WORD, Column.STATUS, Column.PART_OF_SPEECH, Column.CEFR, Column.LISTS),
        allow_remove=False,
    )
    selector = QComboBox()
    selector.addItem("All lists")
    for name in sorted(lists, key=str.casefold):
        selector.addItem(name)
    table.extra_filters.addWidget(selector)
    table.set_words(service.list_unknown_words())
    table.select_ids([w.id for w in table.model.words[2:4]])
    return table


def page(margins: tuple[int, int, int, int] = (32, 24, 32, 24)) -> tuple[QWidget, QVBoxLayout]:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(16)
    return widget, layout


def continue_panel(service: VocabularyService, list_id: int, compact: bool = False) -> QFrame:
    lst = service.get_list(list_id)
    panel = styled(QFrame(), "Panel")
    layout = QHBoxLayout(panel)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(24)

    text = QVBoxLayout()
    text.setSpacing(6)
    text.addWidget(label("CONTINUE LEARNING", "SectionTitle"))
    name_row = QHBoxLayout()
    name_row.addWidget(label(lst.name, "ContextName" if compact else "PageTitle"))
    tag = label(lst.language.upper(), "LanguageTag")
    name_row.addWidget(tag, 0, Qt.AlignmentFlag.AlignVCenter)
    name_row.addStretch(1)
    text.addLayout(name_row)
    p = lst.progress
    text.addWidget(
        label(f"{p.reviewed:,} of {p.total:,} reviewed · {p.remaining:,} remaining · "
              f"{p.unknown:,} to learn", "Muted")
    )
    bar = SegmentedProgress()
    bar.set_progress(p)
    text.addWidget(bar)
    layout.addLayout(text, 1)

    actions = QVBoxLayout()
    actions.setSpacing(8)
    go = button("Continue  →", "primary")
    go.setMinimumWidth(170)
    go.setMinimumHeight(40)
    actions.addWidget(go)
    mode = ModeSwitch()
    actions.addWidget(mode, 0, Qt.AlignmentFlag.AlignHCenter)
    layout.addLayout(actions)
    return panel


def stats_row(service: VocabularyService) -> QHBoxLayout:
    p = service.get_progress()
    row = QHBoxLayout()
    row.setSpacing(12)
    for text, value, tone in (
        ("Total words", p.total, None),
        ("Known", p.known, "known"),
        ("Unknown", p.unknown, "unknown"),
        ("Not reviewed", p.remaining, None),
    ):
        tile = StatTile(text, tone)
        tile.set_value(value)
        row.addWidget(tile)
    return row


def list_grid(service: VocabularyService, current_id: int, columns: int = 2) -> QGridLayout:
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(12)
    for index, lst in enumerate(service.lists()):
        card = ListCard(lst)
        card.set_current(lst.id == current_id)
        grid.addWidget(card, index // columns, index % columns)
    return grid


def scrolled(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidget(content)
    return area


# -- Design A: conservative evolution ---------------------------------------


def a_app_bar(active: str) -> QFrame:
    bar = styled(QFrame(), "AppBar")
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(24, 10, 24, 0)
    layout.setSpacing(8)
    layout.addWidget(label("LexiTrack", "AppTitle"))
    layout.addSpacing(24)
    for name in ("Home", "Lists", "Review", "Unknown Words"):
        tab = QPushButton(name)
        tab.setObjectName("NavTab")
        tab.setCheckable(True)
        tab.setChecked(name == active)
        layout.addWidget(tab, 0, Qt.AlignmentFlag.AlignBottom)
    layout.addStretch(1)
    for text, variant in (("Import", None), ("Dark", "ghost")):
        b = button(text, variant, small=True)
        layout.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
    bar.setMinimumHeight(56)
    return bar


def a_context_strip(service: VocabularyService, list_id: int, mode: str) -> QFrame:
    lst = service.get_list(list_id)
    strip = QFrame()
    layout = QHBoxLayout(strip)
    layout.setContentsMargins(32, 16, 32, 0)
    layout.setSpacing(12)
    column = QVBoxLayout()
    column.setSpacing(0)
    column.addWidget(label("REVIEWING", "ContextLabel"))
    column.addWidget(label(lst.name, "ContextName"))
    layout.addLayout(column)
    layout.addWidget(label(lst.language.upper(), "LanguageTag"), 0, Qt.AlignmentFlag.AlignBottom)
    layout.addWidget(button("Change list", "ghost", small=True), 0, Qt.AlignmentFlag.AlignBottom)
    layout.addStretch(1)
    switch = ModeSwitch()
    switch.set_mode(mode)
    layout.addWidget(switch, 0, Qt.AlignmentFlag.AlignBottom)
    return strip


def design_a(service: VocabularyService, lists: dict[str, int], current: int) -> dict[str, QWidget]:
    screens: dict[str, QWidget] = {}

    # Home
    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    outer.addWidget(a_app_bar("Home"))
    body, layout = page((48, 28, 48, 28))
    layout.addWidget(continue_panel(service, current))
    layout.addLayout(stats_row(service))
    header = QHBoxLayout()
    header.addWidget(label("YOUR LISTS", "SectionTitle"))
    header.addStretch(1)
    header.addWidget(button("New List", small=True))
    header.addWidget(button("Import…", small=True))
    layout.addSpacing(4)
    layout.addLayout(header)
    layout.addLayout(list_grid(service, current, columns=3))
    layout.addStretch(1)
    outer.addWidget(scrolled(body), 1)
    screens["home"] = root

    # Flashcard / List
    for mode, key in ((ModeSwitch.FLASHCARD, "flashcard"), (ModeSwitch.LIST, "list")):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(a_app_bar("Review"))
        outer.addWidget(a_context_strip(service, current, mode))
        if key == "flashcard":
            outer.addWidget(flashcard(service, current), 1)
        else:
            holder, holder_layout = page((32, 16, 32, 16))
            holder_layout.addWidget(list_table(service, current, select=3))
            outer.addWidget(holder, 1)
        stats = StatsBar()
        stats.update_progress(service.get_progress(current))
        outer.addWidget(stats)
        screens[key] = root

    # Unknown
    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    outer.addWidget(a_app_bar("Unknown Words"))
    body, layout = page((32, 20, 32, 20))
    header = QHBoxLayout()
    titles = QVBoxLayout()
    titles.setSpacing(2)
    titles.addWidget(label("Unknown Words", "PageTitle"))
    count = service.unknown_count()
    subtitle = f"{count:,} words you marked as unknown, across all lists"
    titles.addWidget(label(subtitle, "PageSubtitle"))
    header.addLayout(titles)
    header.addStretch(1)
    header.addWidget(button("Review Unknown"), 0, Qt.AlignmentFlag.AlignBottom)
    header.addWidget(button("Export…"), 0, Qt.AlignmentFlag.AlignBottom)
    layout.addLayout(header)
    layout.addWidget(unknown_table(service, lists), 1)
    outer.addWidget(body, 1)
    screens["unknown"] = root
    return screens


# -- Design B: modern dashboard ----------------------------------------------


def b_sidebar(service: VocabularyService, active: str, current: int) -> QFrame:
    bar = styled(QFrame(), "Sidebar")
    bar.setFixedWidth(232)
    layout = QVBoxLayout(bar)
    layout.setContentsMargins(14, 18, 14, 14)
    layout.setSpacing(2)
    title = label("LexiTrack", "AppTitle")
    title.setContentsMargins(12, 0, 0, 12)
    layout.addWidget(title)
    for name in ("Home", "Review", "Unknown Words"):
        item = QPushButton(name)
        item.setObjectName("SidebarItem")
        item.setCheckable(True)
        item.setChecked(name == active)
        layout.addWidget(item)
    layout.addSpacing(18)
    lists_title = label("LISTS", "SectionTitle")
    lists_title.setContentsMargins(12, 0, 0, 6)
    layout.addWidget(lists_title)
    for lst in service.lists():
        item = QPushButton(f"{lst.name}")
        item.setObjectName("SidebarItem")
        item.setCheckable(True)
        item.setChecked(active == "Review" and lst.id == current)
        row = QHBoxLayout(item)
        row.setContentsMargins(0, 0, 10, 0)
        row.addStretch(1)
        row.addWidget(label(f"{int(lst.progress.percent_complete)}%", "Faint"))
        layout.addWidget(item)
    layout.addStretch(1)
    layout.addWidget(button("+  New List", "ghost", small=True))
    layout.addWidget(button("Import…", small=True))
    layout.addSpacing(6)
    layout.addWidget(button("Dark mode", "ghost", small=True))
    return bar


def b_frame(service: VocabularyService, active: str, current: int, content: QWidget) -> QWidget:
    root = QWidget()
    layout = QHBoxLayout(root)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(b_sidebar(service, active, current))
    layout.addWidget(content, 1)
    return root


def b_review_header(service: VocabularyService, list_id: int, mode: str) -> QHBoxLayout:
    lst = service.get_list(list_id)
    p = lst.progress
    row = QHBoxLayout()
    titles = QVBoxLayout()
    titles.setSpacing(2)
    titles.addWidget(label(lst.name, "PageTitle"))
    titles.addWidget(
        label(f"{p.known:,} known · {p.unknown:,} unknown · {p.remaining:,} remaining",
              "PageSubtitle")
    )
    row.addLayout(titles)
    row.addStretch(1)
    switch = ModeSwitch()
    switch.set_mode(mode)
    row.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def recent_panel(service: VocabularyService) -> QFrame:
    panel = styled(QFrame(), "Panel")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    layout.addWidget(label("RECENT ACTIVITY", "SectionTitle"))
    words = sorted(
        service.list_known_words() + service.list_unknown_words(),
        key=lambda w: w.reviewed_at or "",
        reverse=True,
    )[:6]
    for word in words:
        row = QHBoxLayout()
        row.addWidget(label(word.word))
        row.addWidget(label(word.lists[0] if word.lists else "", "Faint"))
        row.addStretch(1)
        badge = StatusBadge(word.status)
        row.addWidget(badge)
        layout.addLayout(row)
    layout.addStretch(1)
    return panel


def design_b(service: VocabularyService, lists: dict[str, int], current: int) -> dict[str, QWidget]:
    screens: dict[str, QWidget] = {}

    body, layout = page((32, 26, 32, 26))
    layout.addWidget(label("Home", "PageTitle"))
    layout.addLayout(stats_row(service))
    middle = QHBoxLayout()
    middle.setSpacing(12)
    left = QVBoxLayout()
    left.setSpacing(12)
    left.addWidget(continue_panel(service, current, compact=True))
    quick = styled(QFrame(), "Panel")
    q = QHBoxLayout(quick)
    q.setContentsMargins(20, 14, 20, 14)
    q.addWidget(label("QUICK ACTIONS", "SectionTitle"))
    q.addStretch(1)
    for text in ("Import…", "New List", "Review Unknown", "Manage Vocabulary"):
        q.addWidget(button(text, small=True))
    left.addWidget(quick)
    lists_panel = styled(QFrame(), "Panel")
    lp = QVBoxLayout(lists_panel)
    lp.setContentsMargins(20, 16, 20, 16)
    lp.setSpacing(10)
    lp.addWidget(label("LISTS", "SectionTitle"))
    for lst in service.lists():
        row = QHBoxLayout()
        name = label(lst.name)
        name.setMinimumWidth(170)
        row.addWidget(name)
        bar = SegmentedProgress()
        bar.set_progress(lst.progress)
        row.addWidget(bar, 1)
        pct = label(f"{int(lst.progress.percent_complete)}%", "Faint")
        pct.setMinimumWidth(36)
        pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(pct)
        lp.addLayout(row)
    left.addWidget(lists_panel)
    middle.addLayout(left, 3)
    middle.addWidget(recent_panel(service), 2)
    layout.addLayout(middle)
    layout.addStretch(1)
    screens["home"] = b_frame(service, "Home", current, body)

    for mode, key in ((ModeSwitch.FLASHCARD, "flashcard"), (ModeSwitch.LIST, "list")):
        body, layout = page((32, 22, 32, 16))
        layout.addLayout(b_review_header(service, current, mode))
        if key == "flashcard":
            card = flashcard(service, current)
            layout.addWidget(card, 1)
        else:
            layout.addWidget(list_table(service, current, select=3), 1)
        screens[key] = b_frame(service, "Review", current, body)

    body, layout = page((32, 22, 32, 20))
    header = QHBoxLayout()
    titles = QVBoxLayout()
    titles.addWidget(label("Unknown Words", "PageTitle"))
    titles.addWidget(label(f"{service.unknown_count():,} words across all lists", "PageSubtitle"))
    header.addLayout(titles)
    header.addStretch(1)
    header.addWidget(button("Review Unknown"), 0, Qt.AlignmentFlag.AlignBottom)
    header.addWidget(button("Export…"), 0, Qt.AlignmentFlag.AlignBottom)
    layout.addLayout(header)
    layout.addWidget(unknown_table(service, lists), 1)
    screens["unknown"] = b_frame(service, "Unknown Words", current, body)
    return screens


# -- Design C: minimal learning workspace ------------------------------------


def c_top_bar(service: VocabularyService, current: int, mode: str | None, unknown_active=False):
    bar = styled(QFrame(), "AppBar")
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(20, 10, 20, 10)
    layout.setSpacing(10)
    selector = QComboBox()
    selector.setMinimumWidth(230)
    for lst in service.lists():
        selector.addItem(f"{lst.name}   ·   {int(lst.progress.percent_complete)}%", lst.id)
    selector.setCurrentIndex(selector.findData(current))
    layout.addWidget(selector)
    layout.addStretch(1)
    switch = ModeSwitch()
    if mode:
        switch.set_mode(mode)
    else:
        for b in switch.buttons.values():
            b.setAutoExclusive(False)
            b.setChecked(False)
    layout.addWidget(switch)
    layout.addStretch(1)
    unknown = button(
        f"?  Unknown  {service.unknown_count():,}",
        None if unknown_active else "ghost",
        small=True,
    )
    layout.addWidget(unknown)
    layout.addWidget(button("Import", "ghost", small=True))
    layout.addWidget(button("⋯", "ghost", small=True))
    return bar


def c_progress_line(service: VocabularyService, list_id: int) -> QWidget:
    p = service.get_progress(list_id)
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(24, 8, 24, 0)
    layout.setSpacing(12)
    bar = SegmentedProgress(height=4)
    bar.set_progress(p)
    layout.addWidget(bar, 1)
    layout.addWidget(
        label(f"✓ {p.known:,}   ? {p.unknown:,}   – {p.remaining:,} left", "Faint")
    )
    return widget


def design_c(service: VocabularyService, lists: dict[str, int], current: int) -> dict[str, QWidget]:
    screens: dict[str, QWidget] = {}

    # Home: the workspace with the list drawer open.
    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    outer.addWidget(c_top_bar(service, current, ModeSwitch.FLASHCARD))
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    drawer = styled(QFrame(), "Sidebar")
    drawer.setFixedWidth(340)
    d = QVBoxLayout(drawer)
    d.setContentsMargins(18, 18, 18, 18)
    d.setSpacing(10)
    d.addWidget(label("Your lists", "ContextName"))
    p = service.get_progress()
    d.addWidget(
        label(f"{p.total:,} words · {p.known:,} known · {p.unknown:,} unknown", "Faint")
    )
    for lst in service.lists():
        card = ListCard(lst)
        card.set_current(lst.id == current)
        d.addWidget(card)
    d.addStretch(1)
    actions = QHBoxLayout()
    actions.addWidget(button("+ New List", small=True))
    actions.addWidget(button("Import…", small=True))
    d.addLayout(actions)
    row.addWidget(drawer)
    work = QVBoxLayout()
    work.setContentsMargins(0, 0, 0, 0)
    work.addWidget(c_progress_line(service, current))
    work.addWidget(flashcard(service, current), 1)
    row.addLayout(work, 1)
    outer.addLayout(row, 1)
    screens["home"] = root

    for mode, key in ((ModeSwitch.FLASHCARD, "flashcard"), (ModeSwitch.LIST, "list")):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(c_top_bar(service, current, mode))
        outer.addWidget(c_progress_line(service, current))
        if key == "flashcard":
            outer.addWidget(flashcard(service, current), 1)
        else:
            holder, holder_layout = page((24, 12, 24, 16))
            holder_layout.addWidget(list_table(service, current, select=3))
            outer.addWidget(holder, 1)
        screens[key] = root

    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    outer.addWidget(c_top_bar(service, current, None, unknown_active=True))
    holder, holder_layout = page((24, 16, 24, 16))
    holder_layout.addWidget(unknown_table(service, lists), 1)
    outer.addWidget(holder, 1)
    screens["unknown"] = root
    return screens


# -- main --------------------------------------------------------------------


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setOrganizationName("LexiTrackMockups")
    app.setApplicationName("LexiTrackMockups")
    theme = ThemeManager(app)

    data_dir = Path(os.environ["LEXITRACK_DATA_DIR"])
    service = VocabularyService(Database(data_dir / "mockups.db"))
    lists = build_sample(service)
    current = lists.get("Oxford 3000") or lists["German A1"]

    OUT.mkdir(parents=True, exist_ok=True)
    builders = {"a": design_a, "b": design_b, "c": design_c}
    for theme_name in (ThemeName.LIGHT, ThemeName.DARK):
        apply_theme(app, theme, theme_name)
        for key, build in builders.items():
            for screen, widget in build(service, lists, current).items():
                if theme_name is ThemeName.DARK and screen not in ("home", "flashcard"):
                    widget.deleteLater()
                    continue
                widget.resize(*SIZE)
                widget.show()
                app.processEvents()
                suffix = "" if theme_name is ThemeName.LIGHT else "-dark"
                target = OUT / f"design-{key}-{screen}{suffix}.png"
                widget.grab().save(str(target))
                widget.close()
                widget.deleteLater()
                print("wrote", target.relative_to(ROOT))
    service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
