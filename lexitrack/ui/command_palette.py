"""Ctrl+K: one box that reaches everything.

Type part of a command, a list's name or a word. Commands explain what they
do in a line under their name and show their shortcut on the right, so the
palette doubles as the place where shortcuts are learnt. Lists open for
review; words open in List mode with their details beside the table.

The commands come from the main window, which also builds the Keyboard
Shortcuts window from the same entries, so the two can never disagree.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QModelIndex, QObject, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..repositories.word_repository import StoredWord
from .theme import current_palette

#: Words shown for a query; the palette is for jumping, not browsing.
MAX_WORDS = 8
PALETTE_WIDTH = 640

_KIND_ROLE = Qt.ItemDataRole.UserRole + 41
_DESCRIPTION_ROLE = Qt.ItemDataRole.UserRole + 42
_SHORTCUT_ROLE = Qt.ItemDataRole.UserRole + 43
_HEADER, _ENTRY = "header", "entry"


@dataclass(frozen=True, slots=True)
class Command:
    """Something the user can do, with what it does and how to reach it."""

    title: str
    description: str
    run: Callable[[], object]
    shortcut: str | None = None
    keywords: str = ""

    def matches(self, query: str) -> bool:
        haystack = f"{self.title} {self.description} {self.keywords}".casefold()
        return all(part in haystack for part in query.split())


class CommandPalette(QDialog):
    """A popup search over commands, lists and words."""

    def __init__(
        self,
        commands: Sequence[Command],
        lists: Sequence[tuple[str, str, Callable[[], object]]],
        words: Callable[[], Sequence[StoredWord]],
        open_word: Callable[[StoredWord], object],
        parent: QWidget,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("CommandPalette")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._commands = list(commands)
        self._lists = list(lists)
        self._words_source = words
        self._words: list[StoredWord] | None = None
        self._open_word = open_word
        self._actions: dict[int, Callable[[], object]] = {}
        self.setFixedWidth(PALETTE_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("PaletteSearch")
        self.search.setPlaceholderText("Search commands, lists and words")
        self.search.textChanged.connect(self._refresh)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.setObjectName("PaletteResults")
        self.results.setItemDelegate(_ResultDelegate(self.results))
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemActivated.connect(self._run_item)
        self.results.itemClicked.connect(self._run_item)
        layout.addWidget(self.results)

        footer = QLabel("↑ ↓ move   ·   Enter run   ·   Esc close   ·   F1 all shortcuts")
        footer.setObjectName("Faint")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)
        self._refresh("")

    # -- showing -----------------------------------------------------------

    def popup(self) -> None:
        window = self.parentWidget().window()
        top_left = window.mapToGlobal(window.rect().topLeft())
        self.adjustSize()
        self.move(top_left.x() + (window.width() - self.width()) // 2, top_left.y() + 90)
        self.show()
        self.search.setFocus()

    # -- results -----------------------------------------------------------

    def _refresh(self, text: str) -> None:
        query = text.strip().casefold()
        self.results.clear()
        self._actions.clear()

        commands = [c for c in self._commands if not query or c.matches(query)]
        if commands:
            self._header("COMMANDS")
            for command in commands:
                self._entry(command.title, command.description, command.shortcut, command.run)

        lists = [entry for entry in self._lists if not query or query in entry[0].casefold()]
        if lists and (query or len(self._lists) <= 6):
            self._header("LISTS")
            for name, detail, run in lists:
                self._entry(name, detail, None, run)

        if len(query) >= 2:
            words = self._matching_words(query)
            if words:
                self._header("WORDS")
                for word in words:
                    meta = " · ".join(
                        p for p in (word.part_of_speech, word.cefr_level, word.list_label) if p
                    )
                    self._entry(word.word, meta, None, lambda w=word: self._open_word(w))

        if not self._actions:
            empty = QListWidgetItem("Nothing matches")
            empty.setData(_KIND_ROLE, _HEADER)
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(empty)
        else:
            self._select_next(-1, 1)
        rows = min(self.results.count(), 12)
        height = sum(self.results.sizeHintForRow(r) for r in range(rows)) + 8
        self.results.setFixedHeight(max(height, 60))
        self.adjustSize()

    def _matching_words(self, query: str) -> list[StoredWord]:
        if self._words is None:
            self._words = list(self._words_source())
        starts = [w for w in self._words if w.normalized_word.startswith(query)]
        if len(starts) < MAX_WORDS:
            chosen = {w.id for w in starts}
            starts += [
                w for w in self._words
                if w.id not in chosen and query in w.normalized_word
            ][: MAX_WORDS - len(starts)]
        return sorted(starts[:MAX_WORDS], key=lambda w: (len(w.normalized_word), w.normalized_word))

    def _header(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setData(_KIND_ROLE, _HEADER)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.results.addItem(item)

    def _entry(
        self, title: str, description: str, shortcut: str | None, run: Callable[[], object]
    ) -> None:
        item = QListWidgetItem(title)
        item.setData(_KIND_ROLE, _ENTRY)
        item.setData(_DESCRIPTION_ROLE, description)
        item.setData(_SHORTCUT_ROLE, shortcut or "")
        item.setToolTip(description)
        self.results.addItem(item)
        self._actions[self.results.row(item)] = run

    # -- keyboard ----------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._select_next(self.results.currentRow(), 1)
                return True
            if key == Qt.Key.Key_Up:
                self._select_next(self.results.currentRow(), -1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.results.currentItem()
                if item is not None:
                    self._run_item(item)
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)

    def _select_next(self, row: int, step: int) -> None:
        count = self.results.count()
        candidate = row + step
        while 0 <= candidate < count:
            if candidate in self._actions:
                self.results.setCurrentRow(candidate)
                return
            candidate += step

    def _run_item(self, item: QListWidgetItem) -> None:
        run = self._actions.get(self.results.row(item))
        if run is None:
            return
        self.accept()
        run()


class _ResultDelegate(QStyledItemDelegate):
    """Title and description on the left, shortcut on the right."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        if index.data(_KIND_ROLE) == _HEADER:
            return QSize(option.rect.width(), 30)
        return QSize(option.rect.width(), 50 if index.data(_DESCRIPTION_ROLE) else 34)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        p = current_palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 1, -4, -1)
        base = QFont(option.font)

        if index.data(_KIND_ROLE) == _HEADER:
            font = QFont(base)
            font.setPointSizeF(max(base.pointSizeF() * 0.78, 7))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(p.text_faint))
            painter.drawText(
                rect.adjusted(10, 8, 0, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                index.data(Qt.ItemDataRole.DisplayRole),
            )
            painter.restore()
            return

        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p.accent_soft))
            painter.drawRoundedRect(rect, 8, 8)

        shortcut = index.data(_SHORTCUT_ROLE) or ""
        right = rect.right() - 12
        if shortcut:
            small = QFont(base)
            small.setPointSizeF(base.pointSizeF() * 0.85)
            painter.setFont(small)
            width = painter.fontMetrics().horizontalAdvance(shortcut) + 16
            chip = QRect(right - width, rect.center().y() - 11, width, 22)
            painter.setPen(QColor(p.border_strong))
            painter.setBrush(QColor(p.surface_sunken))
            painter.drawRoundedRect(chip, 5, 5)
            painter.setPen(QColor(p.text_muted))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, shortcut)
            right = chip.left() - 12

        description = index.data(_DESCRIPTION_ROLE) or ""
        title_font = QFont(base)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(p.text))
        text_rect = QRect(rect.left() + 12, rect.top(), right - rect.left() - 12, rect.height())
        if description:
            top = QRect(text_rect.left(), rect.top() + 6, text_rect.width(), 20)
            painter.drawText(
                top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(
                    index.data(Qt.ItemDataRole.DisplayRole), Qt.TextElideMode.ElideRight,
                    top.width(),
                ),
            )
            small = QFont(base)
            small.setPointSizeF(base.pointSizeF() * 0.86)
            painter.setFont(small)
            painter.setPen(QColor(p.text_muted))
            bottom = QRect(text_rect.left(), rect.top() + 26, text_rect.width(), 18)
            painter.drawText(
                bottom, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(description, Qt.TextElideMode.ElideRight,
                                                 bottom.width()),
            )
        else:
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                index.data(Qt.ItemDataRole.DisplayRole),
            )
        painter.restore()
