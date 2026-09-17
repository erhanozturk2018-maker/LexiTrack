"""A short, non-modal message with an optional Undo.

Used after actions that are easy to reverse (copying or moving words between
lists) and to report results (an export). Rather than a dialog that must be
dismissed, the result appears at the bottom of the page for a few seconds. Undo
— the button or Ctrl+Z while the message is showing — reverses it; a result
can carry another action instead, such as Open Folder.

The toast floats over its parent instead of sitting in the layout, so showing
it never shifts the table underneath.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

#: How long a message stays, in milliseconds.
DURATION_MS = 6000
_BOTTOM_MARGIN = 24


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
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(DURATION_MS)

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
            if not widget.isHidden():
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
