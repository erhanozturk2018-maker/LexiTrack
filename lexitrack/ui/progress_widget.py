"""Progress display: the bar in the app bar and the counters along the bottom.

Progress is split in two on purpose. The thin bar sits at the top, where it is
visible but silent — a session's shape at a glance. The counters sit at the
bottom, out of the path between the word and the buttons, because looking at
them is an occasional act and answering is a constant one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models.user_word_state import Progress
from .theme.palette import METRICS


class ProgressBarWidget(QWidget):
    """A thin determinate bar with a ``132 / 3000`` caption beside it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        m = METRICS

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(m.space_3)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setMinimumWidth(160)
        self._bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bar.setRange(0, 100)

        self._caption = QLabel()
        self._caption.setObjectName("AppBarStatus")

        layout.addWidget(self._bar, 1)
        layout.addWidget(self._caption, 0)

    def update_progress(self, progress: Progress) -> None:
        self._bar.setValue(int(progress.percent_complete))
        self._bar.setToolTip(f"{progress.percent_complete:.0f}% reviewed")
        self._caption.setText(f"{progress.reviewed:,} / {progress.total:,}")


class StatsBar(QWidget):
    """The Known / Unknown / Remaining counters along the bottom of the window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatsBar")
        # A plain QWidget does not paint a stylesheet background unless it is
        # told to; without this the bar loses its surface colour and its rule.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        m = METRICS

        layout = QHBoxLayout(self)
        layout.setContentsMargins(m.space_5, m.space_3, m.space_5, m.space_3)
        layout.setSpacing(0)

        self._known = _Stat("KNOWN", tone="known")
        self._unknown = _Stat("UNKNOWN", tone="unknown")
        self._remaining = _Stat("REMAINING")
        self._total = _Stat("TOTAL")

        layout.addStretch(1)
        for index, stat in enumerate((self._known, self._unknown, self._remaining, self._total)):
            if index:
                layout.addWidget(_divider())
            layout.addWidget(stat)
        layout.addStretch(1)

    def update_progress(self, progress: Progress) -> None:
        self._known.set_value(progress.known)
        self._unknown.set_value(progress.unknown)
        self._remaining.set_value(progress.remaining)
        self._total.set_value(progress.total)


class _Stat(QWidget):
    """One labelled number."""

    def __init__(self, label: str, tone: str | None = None) -> None:
        super().__init__()
        # Deliberately *not* styled-background: the stat shows the bar's
        # surface through instead of painting a second rectangle on top.
        m = METRICS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(m.space_5, 0, m.space_5, 0)
        layout.setSpacing(1)

        self._value = QLabel("0")
        self._value.setObjectName("StatValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if tone:
            self._value.setProperty("tone", tone)

        caption = QLabel(label)
        caption.setObjectName("StatLabel")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._value)
        layout.addWidget(caption)

    def set_value(self, value: int) -> None:
        self._value.setText(f"{value:,}")


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("StatDivider")
    line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    line.setFixedWidth(1)
    line.setFixedHeight(28)
    return line
