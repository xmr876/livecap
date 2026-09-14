"""Render the subtitle bar with sample text into a PNG, so the layout can be
checked without watching the screen.

    .venv\\Scripts\\python.exe scripts\\preview_overlay.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QPainter, QPixmap   # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

from livecap.config import Config                     # noqa: E402
from livecap.overlay import SubtitleOverlay           # noqa: E402

SAMPLES = [
    ("明日の午後は東京でも雨が降るそうです", "明天下午东京也会下雨"),
    ("総務省が発表したデータによると、去年の訪日外国人は二千五百万人を超えました。",
     "总务省数据显示，去年访日外国人超过2500万人次，创下历史新高"),
    ("なるほど", "原来如此"),
]


def main() -> int:
    app = QApplication(sys.argv)
    cfg = Config()
    overlay = SubtitleOverlay(cfg)
    overlay.show()
    app.processEvents()

    out_dir = ROOT / "testdata"
    out_dir.mkdir(exist_ok=True)

    for index, (ja, zh) in enumerate(SAMPLES, start=1):
        overlay.on_zh(zh)
        overlay.on_ja(ja)
        for _ in range(5):          # let the resize actually reach the window
            app.processEvents()
        time.sleep(0.05)
        app.processEvents()
        layout = overlay.layout()
        print(f"--- sample {index}")
        print(f"    widget size={overlay.size().width()}x{overlay.size().height()} "
              f"sizeHint={overlay.sizeHint().width()}x{overlay.sizeHint().height()} "
              f"minHint={overlay.minimumSizeHint().width()}x{overlay.minimumSizeHint().height()} "
              f"layoutHint={layout.sizeHint().width()}x{layout.sizeHint().height()} "
              f"layoutMin={layout.minimumSize().width()}x{layout.minimumSize().height()}")
        for name, label in (("zh", overlay.zh_label), ("ja", overlay.ja_label)):
            print(f"    {name}: hint={label.sizeHint().width()}x{label.sizeHint().height()} "
                  f"minHint={label.minimumSizeHint().width()}x{label.minimumSizeHint().height()} "
                  f"geom={label.geometry().width()}x{label.geometry().height()} "
                  f"font={label.font().pixelSize()}px")
        # render() at 1:1: grab() is unreliable here (translucent window on a
        # 2.5x screen paints at logical scale into a DPR-scaled pixmap).
        # The backdrop simulates a bright video frame so the box is visible.
        pixmap = QPixmap(overlay.size())
        pixmap.fill(QColor("#b9b9b9"))
        overlay.render(pixmap)
        canvas = pixmap
        path = out_dir / f"overlay_preview_{index}.png"
        canvas.save(str(path))

        # measure where the text actually landed (near-white pixels: the text is
        # #FFFFFF while the fake video frame behind is #b9b9b9)
        image = canvas.toImage()
        rows = []
        cols = []
        for y in range(image.height()):
            for x in range(0, image.width(), 2):
                color = image.pixelColor(x, y)
                if color.red() > 230 and color.green() > 230 and color.blue() > 230:
                    rows.append(y)
                    cols.append(x)
        if rows:
            print(f"    ink: x {min(cols)}..{max(cols)}  y {min(rows)}..{max(rows)}  "
                  f"(box {canvas.width()}x{canvas.height()}, "
                  f"dpr {overlay.devicePixelRatioF():.2f})")
        # prove the background box is actually painted: sample a spot away from
        # the glyphs. #b9b9b9 backdrop + black@205 alpha should land near #24.
        corner = image.pixelColor(24, 6)
        print(f"    box pixel at (24,6): rgb({corner.red()},{corner.green()},{corner.blue()})"
              f"  alpha={corner.alpha()}")
        print(f"    saved {path.name}")

    check_drag_persistence(overlay)
    return 0


def check_drag_persistence(overlay) -> None:
    """A dragged bar must stay where the user put it when new text arrives."""
    overlay.move(300, 200)
    overlay._remember_position()          # what mouseReleaseEvent() does
    overlay.on_zh("拖动之后的新一句字幕")
    overlay.on_ja("ドラッグしたあとの新しい文です")
    position = (overlay.x(), overlay.y())
    assert position == (300, 200), f"bar jumped back to {position} after new text"
    print(f"drag persistence OK - still at {position} after new subtitles")

    # clean up the position written by the test
    from livecap.settings import load as load_settings, save as save_settings

    data = load_settings()
    data["bar_x"] = data["bar_y"] = None
    save_settings(data)


if __name__ == "__main__":
    raise SystemExit(main())
