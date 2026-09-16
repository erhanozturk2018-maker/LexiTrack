"""Small display components: list cards, stat tiles and the mode switch."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...models.language import UNDETERMINED
from ...models.user_word_state import Progress
from ...models.vocabulary_list import VocabularyList
from ..theme import current_palette
from ..theme.palette import METRICS


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class SegmentedProgress(QWidget):
    """A thin bar split into known and unknown portions.

    A single "reviewed" bar makes a list of 28 words all marked unknown look
    100% done. Splitting it shows what the percentage is made of: green for
    known, amber for still to learn, track for not yet reviewed.
    """

    def __init__(self, height: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = Progress()
        self.setFixedHeight(height)
        self.setMinimumWidth(60)

    def set_progress(self, progress: Progress) -> None:
        self._progress = progress
        self.setToolTip(
            f"{progress.known:,} known · {progress.unknown:,} unknown · "
            f"{progress.remaining:,} not reviewed"
        )
        self.setAccessibleName(self.toolTip())
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self.height())

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        painter.fillPath(track, QColor(palette.surface_sunken))

        total = self._progress.total
        if total:
            painter.setClipPath(track)
            known_width = rect.width() * self._progress.known / total
            unknown_width = rect.width() * self._progress.unknown / total
            painter.fillRect(
                QRectF(rect.left(), rect.top(), known_width, rect.height()), QColor(palette.known)
            )
            painter.fillRect(
                QRectF(rect.left() + known_width, rect.top(), unknown_width, rect.height()),
                QColor(palette.unknown),
            )
        painter.end()


class ListCard(QFrame):
    """One list: name, language, size and progress. Clicking selects it."""

    clicked = Signal(int)
    activated = Signal(int)
    #: An arrow key was pressed on the card: ``(columns, rows)`` to move by.
    navigate = Signal(int, int)

    def __init__(self, vocabulary_list: VocabularyList, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ListCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_id = vocabulary_list.id
        m = METRICS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_4, m.space_3, m.space_4, m.space_3)
        layout.setSpacing(m.space_2)

        top = QHBoxLayout()
        top.setSpacing(m.space_2)
        self.name_label = QLabel()
        self.name_label.setObjectName("ListCardName")
        top.addWidget(self.name_label, 1)
        self.language_tag = QLabel()
        self.language_tag.setObjectName("LanguageTag")
        top.addWidget(self.language_tag, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        self.meta_label = QLabel()
        self.meta_label.setObjectName("ListCardMeta")
        layout.addWidget(self.meta_label)

        bottom = QHBoxLayout()
        bottom.setSpacing(m.space_3)
        self.progress_bar = SegmentedProgress()
        bottom.addWidget(self.progress_bar, 1)
        self.percent_label = QLabel()
        self.percent_label.setObjectName("ListCardPercent")
        self.percent_label.setMinimumWidth(38)
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom.addWidget(self.percent_label)
        layout.addLayout(bottom)

        self.update_list(vocabulary_list)

    def update_list(self, vocabulary_list: VocabularyList) -> None:
        progress = vocabulary_list.progress
        self.name_label.setText(vocabulary_list.name)
        if vocabulary_list.language == UNDETERMINED:
            self.language_tag.setVisible(False)
        else:
            self.language_tag.setVisible(True)
            self.language_tag.setText(vocabulary_list.language.upper())
            self.language_tag.setToolTip(vocabulary_list.language_name)
        noun = "word" if progress.total == 1 else "words"
        self.meta_label.setText(
            f"{progress.total:,} {noun} · {progress.known:,} known · "
            f"{progress.unknown:,} unknown"
        )
        percent = int(progress.percent_complete)
        self.progress_bar.set_progress(progress)
        self.percent_label.setText(f"{percent}%")
        self.percent_label.setToolTip(f"{percent}% reviewed")
        self.setAccessibleName(
            f"{vocabulary_list.name}, {progress.total} words, {percent} percent reviewed"
        )
        self.setToolTip(vocabulary_list.description or "")

    def set_current(self, current: bool) -> None:
        self.setProperty("current", "true" if current else "false")
        repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.list_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.activated.emit(self.list_id)
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.list_id)
            return
        arrows = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        if key in arrows:
            self.navigate.emit(*arrows[key])
            return
        if key == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift+F10 is the Windows keyboard equivalent of a right-click.
            self.customContextMenuRequested.emit(self.rect().center())
            return
        super().keyPressEvent(event)


class StatTile(QFrame):
    """A number with a label under it."""

    def __init__(self, label: str, tone: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StatTile")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        m = METRICS
        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_4, m.space_3, m.space_4, m.space_3)
        layout.setSpacing(2)
        self.value_label = QLabel("0")
        self.value_label.setObjectName("StatTileValue")
        if tone:
            self.value_label.setProperty("tone", tone)
        caption = QLabel(label)
        caption.setObjectName("StatTileLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(caption)

    def set_value(self, value: int) -> None:
        self.value_label.setText(f"{value:,}")


class ModeSwitch(QFrame):
    """A two-segment control: Flashcard | List."""

    FLASHCARD = "flashcard"
    LIST = "list"

    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModeSwitch")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for mode, text, shortcut in (
            (self.FLASHCARD, "Flashcard", "Ctrl+1"),
            (self.LIST, "List", "Ctrl+2"),
        ):
            button = QPushButton(text)
            button.setObjectName("ModeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{text} mode ({shortcut})")
            button.clicked.connect(lambda _checked=False, m=mode: self._select(m))
            self._group.addButton(button)
            self.buttons[mode] = button
            layout.addWidget(button)
        self.buttons[self.FLASHCARD].setChecked(True)

    @property
    def mode(self) -> str:
        return self.LIST if self.buttons[self.LIST].isChecked() else self.FLASHCARD

    def set_mode(self, mode: str) -> None:
        self.buttons[mode].setChecked(True)

    def _select(self, mode: str) -> None:
        self.buttons[mode].setChecked(True)
        self.mode_changed.emit(mode)
