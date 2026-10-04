"""A short, non-modal message with an optional Undo, and a ring counting it down.

Used after actions that are easy to reverse (copying or moving words between
lists) and to report results (an export). Rather than a dialog that must be
dismissed, the result appears at the bottom of the page for a few seconds. Undo
— the button or Ctrl+Z while the message is showing — reverses it; a result
can carry another action instead, such as Open Folder.

The toast floats over its parent instead of sitting in the layout, so showing
it never shifts the table underneath. With Undo, a ring beside the button
counts down the seconds left to press it (as on the phone).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from ..theme import current_palette

#: How long a message stays, in milliseconds.
DURATION_MS = 6000
#: How long Undo is offered: the ring's five seconds, as on the phone.
UNDO_MS = 5000
_BOTTOM_MARGIN = 24


class CountdownRing(QWidget):
    """A ring that empties over ``duration`` ms, the seconds left inside it."""

    SIZE = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(self.SIZE, self.SIZE))
        self._duration = UNDO_MS
        self._clock = QElapsedTimer()
        self._tick = QTimer(self)
        self._tick.setInterval(100)
        self._tick.timeout.connect(self.update)

    def start(self, duration: int) -> None:
        self._duration = max(duration, 1)
        self._clock.start()
        self._tick.start()
        self.update()

    def stop(self) -> None:
        self._tick.stop()

    @property
    def left(self) -> float:
        """The part of the time left, 1 → 0."""
        if not self._clock.isValid():
            return 1.0
        return max(0.0, 1.0 - self._clock.elapsed() / self._duration)

    @property
    def seconds(self) -> int:
        return int(-(-self.left * self._duration // 1000))  # rounded up

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        accent = QColor(palette.accent)
        faint = QColor(palette.accent)
        faint.setAlphaF(0.18)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(2, 2, self.SIZE - 4, self.SIZE - 4)
        painter.setPen(QPen(faint, 2.2))
        painter.drawEllipse(rect)
        pen = QPen(accent, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 90 * 16, int(360 * 16 * self.left))
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self.seconds))
        painter.end()


class Toast(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._undo: Callable[[], None] | None = None
        self._action: Callable[[], None] | None = None
        #: Floating widgets the toast must sit above, such as a selection bar.
        self._avoid: list[QWidget] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 10, 10)
        layout.setSpacing(12)
        self.message = QLabel()
        self.message.setObjectName("ToastText")
        layout.addWidget(self.message)
        self.ring = CountdownRing()
        self.ring.hide()
        layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setObjectName("ToastAction")
        self.undo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_button.setToolTip("Undo (Ctrl+Z)")
        self.undo_button.clicked.connect(self._on_button)
        layout.addWidget(self.undo_button)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

        self._shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), parent)
        self._shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut.activated.connect(self.undo)
        self._shortcut.setEnabled(False)

        parent.installEventFilter(self)
        self.hide()

    def show_message(
        self,
        text: str,
        undo: Callable[[], None] | None = None,
        action: tuple[str, Callable[[], None]] | None = None,
    ) -> None:
        """Show ``text``, with Undo, or with another ``(label, callback)`` action."""
        self.message.setText(text)
        self._undo = undo
        self._action = action[1] if action and undo is None else None
        if undo is not None:
            self.undo_button.setText("Undo")
            self.undo_button.setToolTip("Undo (Ctrl+Z)")
        elif action is not None:
            self.undo_button.setText(action[0])
            self.undo_button.setToolTip("")
        self.undo_button.setVisible(undo is not None or action is not None)
        self._shortcut.setEnabled(undo is not None)
        duration = UNDO_MS if undo is not None else DURATION_MS
        self.ring.setVisible(undo is not None)
        if undo is not None:
            self.ring.start(duration)
        else:
            self.ring.stop()
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(duration)

    def avoid(self, widget: QWidget) -> None:
        """Keep clear of ``widget`` (a sibling floating over the same page)."""
        self._avoid.append(widget)

    def _on_button(self) -> None:
        if self._undo is not None:
            self.undo()
            return
        action, self._action = self._action, None
        self.dismiss()
        if action is not None:
            action()

    def undo(self) -> None:
        action, self._undo = self._undo, None
        self.dismiss()
        if action is not None:
            action()

    def dismiss(self) -> None:
        self._timer.stop()
        self.ring.stop()
        self._shortcut.setEnabled(False)
        self.hide()

    @property
    def can_undo(self) -> bool:
        return self._undo is not None and not self.isHidden()

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        bottom = parent.height() - _BOTTOM_MARGIN
        for widget in self._avoid:
            # Visible, not merely not hidden: a page in the back of a stack is
            # hidden as a whole while its children are not.
            if widget.isVisible():
                top = widget.mapTo(parent, widget.rect().topLeft()).y()
                bottom = min(bottom, top - 8)
        self.move(
            max((parent.width() - self.width()) // 2, 0),
            max(bottom - self.height(), 0),
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False


def notify(widget: QWidget, text: str, action: tuple[str, Callable[[], None]] | None = None):
    """Show ``text`` in a toast over ``widget``'s window, creating it once."""
    window = widget.window()
    host = window.centralWidget() if hasattr(window, "centralWidget") else window
    host = host or window
    toast = next(
        (
            child
            for child in host.findChildren(Toast, options=Qt.FindChildOption.FindDirectChildrenOnly)
            if child.property("window_toast")
        ),
        None,
    )
    if toast is None:
        toast = Toast(host)
        toast.setProperty("window_toast", True)
    toast.show_message(text, action=action)
    return toast
