"""Always-on-top bilingual subtitle bar (PySide6).

Layout: one single rounded box holding two stacked lines - Chinese on top and
Japanese right below it, slightly smaller so the eye lands on the translation
first. Each language stays on exactly one line; long sentences shrink instead of
wrapping, so the bar never grows into a tall block.

Exposed pieces
--------------
* ``Bridge``            - QObject with ja/zh/status/clear/finished signals; the
                          pipeline emits into it from worker threads.
* ``SubtitleOverlay``   - the bar itself (frameless, translucent, draggable).
* ``create_overlay``    - build + show a bar wired to a bridge.
* ``run_overlay``       - CLI mode: app + bar + event loop.

Shortcuts (application wide, so they work while the browser has focus)
---------------------------------------------------------------------
Ctrl+Shift+H  show / hide      Ctrl+Shift+T  click-through on / off
Ctrl+Shift+O  bigger text      Ctrl+Shift+I  smaller text
Ctrl+Shift+C  clear            Ctrl+Shift+R  back to bottom centre
Ctrl+Q        quit
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget,
)

log = logging.getLogger("livecap.overlay")

PADDING_H = 20
PADDING_V = 10
LINE_SPACING = 2
JA_RATIO = 0.85          # Japanese is "slightly smaller" than Chinese
MIN_FONT_RATIO = 0.6     # never shrink a line below this share of its base size
BACKGROUND_COLOR = (0, 0, 0, 205)   # black, semi transparent (streaming-caption look)
TEXT_COLOR = "#FFFFFF"
CORNER_RADIUS = 10
SHADOW_BLUR = 0          # 0 = off: the background box already provides contrast


class Bridge(QObject):
    """Thread-safe hand-off from the pipeline threads to the GUI thread."""

    ja = Signal(str)
    zh = Signal(str)
    status = Signal(str)
    clear = Signal()
    finished = Signal()


class SubtitleOverlay(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("livecap")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(cfg.opacity)
        self.setMinimumSize(1, 1)      # the layout must never clamp our geometry
        # NOTE: a stylesheet background on a frameless translucent *window* is not
        # painted reliably, so the box is drawn in paintEvent() instead.

        self._drag_from = None
        self._size_zh = cfg.font_zh
        self._size_ja = cfg.font_ja
        self._status_text = ""      # kept for the tray tooltip / logs only
        # once the user drags the bar we never move it again - only resize in
        # place - otherwise every new sentence would snap it back to the default
        self._user_position: tuple[int, int] | None = self._stored_position()
        if self._user_position is not None:
            log.info("using the saved subtitle bar position %s", self._user_position)

        self.zh_label = QLabel("")
        self.ja_label = QLabel("")
        for label in (self.zh_label, self.ja_label):
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(False)          # one line each, shrink instead
            label.setTextInteractionFlags(Qt.NoTextInteraction)
            label.setAttribute(Qt.WA_TranslucentBackground, True)

        self.zh_label.setStyleSheet(f"color:{TEXT_COLOR}; background:transparent;")
        self.ja_label.setStyleSheet(f"color:{TEXT_COLOR}; background:transparent;")
        for label in (self.zh_label, self.ja_label):
            self._add_shadow(label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PADDING_H, PADDING_V, PADDING_H, PADDING_V)
        layout.setSpacing(LINE_SPACING)
        layout.addWidget(self.zh_label)
        layout.addWidget(self.ja_label)

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self.on_clear)

        self._apply_fonts()
        self._place()

    # ------------------------------------------------------------- appearance
    @staticmethod
    def _add_shadow(label: QLabel) -> None:
        """Optional dark halo behind the glyphs (off while the box is dark)."""
        if SHADOW_BLUR <= 0:
            return
        effect = QGraphicsDropShadowEffect(label)
        effect.setBlurRadius(SHADOW_BLUR)
        effect.setOffset(0, 1)
        effect.setColor(QColor(0, 0, 0, 235))
        label.setGraphicsEffect(effect)

    @staticmethod
    def _font(pixel_size: int, family: str, bold: bool) -> QFont:
        font = QFont(family)
        font.setPixelSize(max(8, pixel_size))
        font.setBold(bold)
        font.setStyleStrategy(QFont.PreferAntialias)
        return font

    def _fit_font(self, label: QLabel, text: str, base_size: int,
                  family: str, bold: bool) -> None:
        """Keep the line on one line by shrinking it, never by wrapping."""
        limit = max(240, int(self._screen_width() * self.cfg.max_width_fraction)
                    - 2 * PADDING_H)
        size = base_size
        font = self._font(size, family, bold)
        while size > int(base_size * MIN_FONT_RATIO):
            if QFontMetrics(font).horizontalAdvance(text) <= limit:
                break
            size -= 1
            font = self._font(size, family, bold)
        label.setFont(font)

    def _screen_width(self) -> int:
        screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry().width() if screen else 1920

    def _apply_fonts(self) -> None:
        self._fit_font(self.zh_label, self.zh_label.text(), self._size_zh,
                       "Microsoft YaHei UI", True)
        self._fit_font(self.ja_label, self.ja_label.text(), self._size_ja,
                       "Yu Gothic UI", False)
        self._refresh_geometry()

    def _refresh_geometry(self) -> None:
        """Resize the box, and only reposition it while the user has not moved it.

        Sizes are computed from font metrics instead of sizeHint(): with a
        fractional device pixel ratio the hint-based layout left a lot of dead
        space under the text.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()

        fm_zh = QFontMetrics(self.zh_label.font())
        fm_ja = QFontMetrics(self.ja_label.font())
        text_w = max(fm_zh.horizontalAdvance(self.zh_label.text()),
                     fm_ja.horizontalAdvance(self.ja_label.text()), 120)
        full_w = int(geo.width() * self.cfg.max_width_fraction)
        width = full_w if self.cfg.fixed_width else min(full_w, text_w + 2 * PADDING_H)
        height = (2 * PADDING_V + LINE_SPACING + fm_zh.height() + fm_ja.height())

        if self._user_position is None:
            x = geo.x() + (geo.width() - width) // 2
            y = geo.y() + geo.height() - height - 60
        else:  # keep the dragged position, just pull it back on screen if needed
            x, y = self._user_position
            x = max(geo.x(), min(x, geo.x() + geo.width() - width))
            y = max(geo.y(), min(y, geo.y() + geo.height() - height))
            self._user_position = (x, y)

        self.zh_label.setFixedHeight(fm_zh.height())
        self.ja_label.setFixedHeight(fm_ja.height())
        self.setGeometry(x, y, width, height)

    def _place(self) -> None:
        self._refresh_geometry()

    # ------------------------------------------------------------------ slots
    def on_ja(self, text: str):
        self.ja_label.setText(text)
        if self.cfg.font_ja <= 0:                      # derive from the Chinese size
            self._size_ja = max(12, int(self._size_zh * JA_RATIO))
        self._apply_fonts()
        self._idle.start(int(self.cfg.clear_after * 1000))

    def on_zh(self, text: str):
        self.zh_label.setText(text)
        self._apply_fonts()
        self._idle.start(int(self.cfg.clear_after * 1000))

    def on_status(self, text: str):
        self._status_text = text          # intentionally not drawn in the bar

    def on_clear(self):
        self.zh_label.clear()
        self.ja_label.clear()
        self._refresh_geometry()

    def bigger(self):
        self._size_zh += 2
        self._size_ja = max(12, int(self._size_zh * JA_RATIO))
        self._apply_fonts()

    def smaller(self):
        self._size_zh = max(12, self._size_zh - 2)
        self._size_ja = max(10, int(self._size_zh * JA_RATIO))
        self._apply_fonts()

    def toggle_click_through(self) -> bool:
        on = not bool(self.windowFlags() & Qt.WindowTransparentForInput)
        self.setWindowFlag(Qt.WindowTransparentForInput, on)
        self.show()
        log.info("click-through: %s", on)
        return on

    # ----------------------------------------------------------------- events
    def paintEvent(self, event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(*BACKGROUND_COLOR))
        painter.drawRoundedRect(self.rect(), CORNER_RADIUS, CORNER_RADIUS)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_from is not None:
            self._drag_from = None
            self._remember_position()

    # ------------------------------------------------------------ remembered pos
    @staticmethod
    def _stored_position() -> tuple[int, int] | None:
        try:
            from .settings import load

            data = load()
            x, y = data.get("bar_x"), data.get("bar_y")
            if isinstance(x, int) and isinstance(y, int):
                return x, y
        except Exception:  # noqa: BLE001 - a missing settings file is fine
            pass
        return None

    def _remember_position(self) -> None:
        self._user_position = (self.x(), self.y())
        log.info("subtitle bar moved to %s", self._user_position)
        try:
            from .settings import load, save

            data = load()
            data["bar_x"], data["bar_y"] = self._user_position
            save(data)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not store the bar position: %s", exc)

    def reset_position(self) -> None:
        """Drop the dragged position and go back to the bottom centre."""
        self._user_position = None
        self._refresh_geometry()


def create_overlay(app: QApplication, cfg, bridge: Bridge) -> SubtitleOverlay:
    """Create, wire and show the subtitle bar."""
    overlay = SubtitleOverlay(cfg)
    bridge.ja.connect(overlay.on_ja)
    bridge.zh.connect(overlay.on_zh)
    bridge.status.connect(overlay.on_status)
    bridge.clear.connect(overlay.on_clear)
    overlay.show()
    return overlay


def install_shortcuts(app: QApplication, cfg, overlay: SubtitleOverlay) -> None:
    def bind(seq, fn):
        shortcut = QShortcut(QKeySequence(seq), overlay)
        shortcut.setContext(Qt.ApplicationShortcut)  # works while the browser has focus
        shortcut.activated.connect(fn)

    bind("Ctrl+Shift+H", lambda: overlay.setVisible(not overlay.isVisible()))
    bind("Ctrl+Shift+T", overlay.toggle_click_through)
    bind("Ctrl+Shift+O", overlay.bigger)
    bind("Ctrl+Shift+I", overlay.smaller)
    bind("Ctrl+Shift+C", overlay.on_clear)
    bind("Ctrl+Shift+R", overlay.reset_position)
    bind("Ctrl+Q", app.quit)


def run_overlay(cfg, worker_start):
    """CLI mode: the subtitle bar is the whole UI."""
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    overlay = create_overlay(app, cfg, bridge)
    install_shortcuts(app, cfg, overlay)
    bridge.finished.connect(app.quit)   # stream over -> close
    worker_start(bridge)
    return app.exec()
