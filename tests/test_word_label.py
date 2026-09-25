"""The headword fits its card (ui/components/word_label.py).

"departures and arrivals board" once wrapped at full size into a label one
line tall, and both lines were cut off.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication

from lexitrack.ui.components.word_label import MAX_LINES, SIZES, WordLabel
from lexitrack.ui.theme import ThemeManager, ThemeName


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    ThemeManager(app).apply(ThemeName.LIGHT)
    return app


def _label(qtbot, text: str, width: int) -> WordLabel:
    label = WordLabel()
    qtbot.addWidget(label)
    label.resize(width, 80)
    label.setText(text)
    label.show()
    QApplication.processEvents()
    return label


def _lines(label: WordLabel) -> int:
    metrics = QFontMetrics(label.font())
    return round((label.minimumHeight() - 4) / metrics.lineSpacing())


def test_a_short_word_keeps_the_full_size(qapp, qtbot) -> None:
    label = _label(qtbot, "piece", 700)
    assert label.pixel_size == SIZES[0]
    assert _lines(label) == 1


def test_a_phrase_takes_two_lines_and_the_height_they_need(qapp, qtbot) -> None:
    label = _label(qtbot, "departures and arrivals board", 700)
    assert _lines(label) <= MAX_LINES
    assert label.height() >= label.minimumHeight()


def test_a_long_word_shrinks_rather_than_running_past_the_card(qapp, qtbot) -> None:
    label = _label(qtbot, "internationalisation", 420)
    assert label.pixel_size < SIZES[0]
    metrics = QFontMetrics(label.font())
    assert metrics.horizontalAdvance("internationalisation") <= label.contentsRect().width()
