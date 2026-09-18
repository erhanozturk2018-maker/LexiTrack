"""Build ``app.ico`` from ``app.svg``.

Windows shortcuts, the taskbar and Explorer want an ``.ico`` file, not an SVG.
An ``.ico`` is a small directory of images at several sizes; Windows picks the
one closest to what it is drawing (16 px in a menu, 256 px in a large-icon
view). Each size here is rendered from the SVG and stored as PNG, which every
Windows version since Vista reads inside an ``.ico``.

Run it again whenever ``app.svg`` changes::

    python tools/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ICONS = Path(__file__).resolve().parents[1] / "lexitrack" / "ui" / "theme" / "icons"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def build_ico(svg: Path, target: Path, sizes: tuple[int, ...] = SIZES) -> Path:
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"{svg} is not a valid SVG")
    images = [(size, render_png(renderer, size)) for size in sizes]

    # ICONDIR, then one ICONDIRENTRY per image, then the PNG data.
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    target.write_bytes(header + entries + blobs)
    return target


def main() -> int:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    target = build_ico(ICONS / "app.svg", ICONS / "app.ico")
    print(f"Wrote {target} ({target.stat().st_size:,} bytes, sizes {', '.join(map(str, SIZES))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
