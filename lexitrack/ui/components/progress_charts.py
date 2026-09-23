"""The Progress page's three charts, drawn in the app's own palette.

Each answers one question and nothing else:

* :class:`PipelineBar` - where are my words right now? One stacked bar from
  "not answered yet" to "Known", with the count on each part.
* :class:`TimelineChart` - how fast am I going? Words introduced and words
  learned, both as running totals; the gap between the lines is what is still
  being learned.
* :class:`CalibrationChart` - does the schedule fit me? For each band of
  predicted recall, how often the word was actually remembered.

They are painted rather than built on QtCharts so they share the colours,
type and corner radii of the rest of the app in both themes.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ...services.progress import Calibration, DayTotals, PipelineStage
from ..theme import current_palette


def _font(widget: QWidget, size: int, bold: bool = False) -> QFont:
    font = QFont(widget.font())
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _short(day: str) -> str:
    value = date.fromisoformat(day)
    return f"{value.day} {value.strftime('%b')}"


class PipelineBar(QWidget):
    """Where the studied words are, as one bar with a legend underneath."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stages: list[PipelineStage] = []
        # The bar and one line of legend; five stages fit on a line at the
        # page's narrowest width.
        self.setMinimumHeight(52)

    def set_stages(self, stages: list[PipelineStage]) -> None:
        self._stages = stages
        self.setAccessibleName(
            "; ".join(f"{stage.label}: {stage.count}" for stage in stages)
        )
        self.update()

    def _colours(self) -> list[QColor]:
        """Grey, then warm while fragile, the accent as it grows, green once Known."""
        p = current_palette()
        growing = QColor(p.accent)
        growing.setAlpha(110)
        return [
            QColor(p.border_strong),
            QColor(p.unknown),
            growing,
            QColor(p.accent),
            QColor(p.known),
        ]

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(stage.count for stage in self._stages)
        bar = QRectF(0, 4, self.width(), 14)
        track = QPainterPath()
        track.addRoundedRect(bar, 7, 7)
        painter.fillPath(track, QColor(palette.surface_sunken))
        colours = self._colours()
        if total:
            painter.setClipPath(track)
            x = 0.0
            for stage, colour in zip(self._stages, colours, strict=False):
                width = bar.width() * stage.count / total
                painter.fillRect(QRectF(x, bar.top(), width, bar.height()), colour)
                x += width
            painter.setClipping(False)

        # the legend: a swatch, a label and a count for each stage
        painter.setFont(_font(self, 12))
        x = 0.0
        y = bar.bottom() + 16
        for stage, colour in zip(self._stages, colours, strict=False):
            label = f"{stage.label}  "
            count = f"{stage.count:,}"
            metrics = painter.fontMetrics()
            needed = 14 + metrics.horizontalAdvance(label) + metrics.horizontalAdvance(count) + 22
            if x + needed > self.width() and x > 0:
                x = 0.0
                y += 22
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(QRectF(x, y - 5, 10, 10), 3, 3)
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(QPointF(x + 14, y + 4), label)
            painter.setPen(QColor(palette.text))
            painter.setFont(_font(self, 12, bold=True))
            painter.drawText(QPointF(x + 14 + metrics.horizontalAdvance(label), y + 4), count)
            painter.setFont(_font(self, 12))
            x += needed
        painter.end()


class TimelineChart(QWidget):
    """Introduced and learned, as running totals by day."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: list[DayTotals] = []
        self.setMinimumHeight(190)

    def set_days(self, days: list[DayTotals]) -> None:
        self._days = days
        if days:
            last = days[-1]
            self.setAccessibleName(
                f"Since {days[0].day}: {last.introduced} words introduced, "
                f"{last.learned} learned"
            )
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(_font(self, 11))
        faint = QColor(palette.text_faint)
        left, right, top, bottom = 40.0, 20.0, 26.0, 22.0
        area = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)

        if len(self._days) < 2:
            painter.setPen(faint)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "The chart starts after your second day of study.",
            )
            painter.end()
            return

        ceiling = max(max(day.introduced for day in self._days), 1)
        count = len(self._days)

        def point(index: int, value: int) -> QPointF:
            x = area.left() + area.width() * index / (count - 1)
            y = area.bottom() - area.height() * value / ceiling
            return QPointF(x, y)

        # grid: zero and the top, labelled
        painter.setPen(QPen(QColor(palette.border), 1))
        for value in (0, ceiling):
            y = point(0, value).y()
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(faint)
            painter.drawText(
                QRectF(0, y - 8, left - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:,}",
            )
            painter.setPen(QPen(QColor(palette.border), 1))

        introduced = [point(i, d.introduced) for i, d in enumerate(self._days)]
        learned = [point(i, d.learned) for i, d in enumerate(self._days)]

        # the gap: words introduced and not yet learned
        gap = QPainterPath()
        gap.moveTo(introduced[0])
        for p in introduced[1:]:
            gap.lineTo(p)
        for p in reversed(learned):
            gap.lineTo(p)
        gap.closeSubpath()
        fill = QColor(palette.accent)
        fill.setAlpha(28)
        painter.fillPath(gap, fill)

        for points, colour in ((introduced, palette.accent), (learned, palette.known)):
            path = QPainterPath()
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
            painter.setPen(QPen(QColor(colour), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        # first and last day under the axis
        painter.setPen(faint)
        painter.drawText(
            QRectF(area.left() - 30, area.bottom() + 4, 60, 16),
            Qt.AlignmentFlag.AlignHCenter,
            _short(self._days[0].day),
        )
        painter.drawText(
            QRectF(area.right() - 40, area.bottom() + 4, 60, 16),
            Qt.AlignmentFlag.AlignHCenter,
            _short(self._days[-1].day),
        )

        # legend, top left
        last = self._days[-1]
        x = area.left()
        for label, value, colour in (
            ("Introduced", last.introduced, palette.accent),
            ("Learned", last.learned, palette.known),
        ):
            painter.setPen(QPen(QColor(colour), 3))
            painter.drawLine(QPointF(x, 10), QPointF(x + 14, 10))
            painter.setPen(QColor(palette.text_muted))
            text = f"{label} {value:,}"
            painter.drawText(QPointF(x + 20, 14), text)
            x += 20 + painter.fontMetrics().horizontalAdvance(text) + 24
        painter.end()


class CalibrationChart(QWidget):
    """For each band of predicted recall: predicted against actual.

    Two bars per band, the forecast in the accent colour and what happened in
    green, with the number of answers under the band so a bar built on five
    answers is not mistaken for one built on five hundred.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._calibration: Calibration | None = None
        self.setMinimumHeight(170)

    def set_calibration(self, calibration: Calibration) -> None:
        self._calibration = calibration
        self.setAccessibleName(
            "; ".join(
                f"{b.label}: predicted {round(b.predicted * 100)}%, "
                f"remembered {round(b.actual * 100)}%, {b.count} answers"
                for b in calibration.bins
            )
        )
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(_font(self, 11))
        faint = QColor(palette.text_faint)
        calibration = self._calibration
        if calibration is None or not calibration.bins:
            painter.setPen(faint)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "This needs a word answered twice: it starts on your third day.",
            )
            painter.end()
            return

        left, right, top, bottom = 40.0, 12.0, 26.0, 38.0
        area = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        painter.setPen(QPen(QColor(palette.border), 1))
        for value in (0, 50, 100):
            y = area.bottom() - area.height() * value / 100
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(faint)
            painter.drawText(
                QRectF(0, y - 8, left - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value}%",
            )
            painter.setPen(QPen(QColor(palette.border), 1))

        slot = area.width() / len(calibration.bins)
        bar = min(26.0, slot / 4)
        for index, band in enumerate(calibration.bins):
            centre = area.left() + slot * (index + 0.5)
            for offset, value, colour in (
                (-bar - 2, band.predicted, palette.accent),
                (2, band.actual, palette.known),
            ):
                height = area.height() * value
                rect = QRectF(centre + offset, area.bottom() - height, bar, height)
                path = QPainterPath()
                path.addRoundedRect(rect, 3, 3)
                painter.fillPath(path, QColor(colour))
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(
                QRectF(centre - slot / 2, area.bottom() + 4, slot, 14),
                Qt.AlignmentFlag.AlignHCenter,
                band.label,
            )
            painter.setPen(faint)
            painter.drawText(
                QRectF(centre - slot / 2, area.bottom() + 18, slot, 14),
                Qt.AlignmentFlag.AlignHCenter,
                f"{band.count:,} answers",
            )

        x = area.left()
        for label, colour in (("Predicted", palette.accent), ("You remembered", palette.known)):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(QRectF(x, 5, 10, 10), 3, 3)
            painter.setPen(QColor(palette.text_muted))
            painter.drawText(QPointF(x + 14, 14), label)
            x += 14 + painter.fontMetrics().horizontalAdvance(label) + 20
        painter.end()
