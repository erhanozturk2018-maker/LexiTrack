"""Small shared widgets."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QLabel, QLayout, QSizePolicy, QWidget

from .theme.palette import METRICS


class WrappedLabel(QLabel):
    """A word-wrapped label that reports the height its text actually needs.

    Qt computes a wrapping label's size hint as if the text had unlimited
    width. In a vertical layout the label is then given too little height and
    the text is clipped, or overlaps whatever sits above it — and adding an
    alignment flag makes it worse, because the layout stops consulting
    ``heightForWidth`` entirely. Pinning the width and recomputing the minimum
    height on every text change avoids both problems.
    """

    def __init__(
        self,
        text: str = "",
        width: int = 460,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._refresh_height()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setText(text)
        self._refresh_height()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt naming
        """Recompute the height whenever the text's metrics could have changed.

        The label is built before the stylesheet is applied, so its first
        measurement uses the default font. When the theme sets a larger one the
        text needs more lines than the height reserved for it, and the last
        line is clipped — unless the height is measured again here.
        """
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            QEvent.Type.ApplicationFontChange,
        ):
            self._refresh_height()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._refresh_height()

    def _refresh_height(self) -> None:
        self.setMinimumHeight(self.heightForWidth(self.width()))


class PageColumn(QObject):
    """Keeps a page's content in a centred column no wider than the page width.

    The page keeps its own margins on a normal window; on a wide one the side
    margins grow instead of the content. Scroll bars stay at the window edge,
    which wrapping the page in a narrower widget would not allow.
    """

    def __init__(
        self,
        widget: QWidget,
        layout: QLayout,
        max_width: int = METRICS.page_max_width,
    ) -> None:
        super().__init__(widget)
        self._layout = layout
        self._max_width = max_width
        margins = layout.contentsMargins()
        self._side = margins.left()
        self._top = margins.top()
        self._bottom = margins.bottom()
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize and isinstance(watched, QWidget):
            spare = watched.width() - 2 * self._side - self._max_width
            side = self._side + max(0, spare // 2)
            self._layout.setContentsMargins(side, self._top, side, self._bottom)
        return False
