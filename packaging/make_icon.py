"""Generate app.ico (a single PNG-compressed 256x256 entry) without Pillow."""
from __future__ import annotations

import struct
import sys
from pathlib import Path


def render_png(size: int = 256) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])   # noqa: F841 - needed for QPixmap
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#1f6feb"))
    painter.setPen(Qt.NoPen)
    radius = size // 8
    painter.drawRoundedRect(2, 2, size - 4, size - 4, radius, radius)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Microsoft YaHei UI", int(size * 0.55), QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "字")
    painter.end()

    buffer = QBuffer(QByteArray())
    buffer.open(QBuffer.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "packaging/app.ico")
    png = render_png(256)
    header = struct.pack("<HHH", 0, 1, 1)                      # ICONDIR: reserved, type, count
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)  # 0 => 256px
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + entry + png)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
