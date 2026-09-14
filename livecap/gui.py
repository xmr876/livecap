"""Double-click launcher: pick a device, press start, get subtitles.

Everything here works from *system audio* (WASAPI loopback), so the user just
plays the Japanese stream in their browser - no links, no cookies.

When the LLM translator is selected the panel also exposes its API settings
(provider preset, endpoint, model, key) and stores them in ``settings.json``.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

from .config import Config
from .main import Pipeline
from .overlay import Bridge, create_overlay, install_shortcuts
from .paths import app_root, models_dir, resolve_model
from .settings import load as load_settings, save as save_settings

log = logging.getLogger("livecap.gui")

ASR_CHOICES = [
    ("large-v3（最准）", "Systran/faster-whisper-large-v3"),
    ("large-v3-turbo（更快）", "deepdml/faster-whisper-large-v3-turbo-ct2"),
]
MT_CHOICES = [
    ("本地 NLLB（离线，默认）", "ct2"),
    ("大模型 API（质量最好，需填 Key）", "llm"),
    ("不翻译（只看日文）", "none"),
]
# label -> (base url, default model); empty means "fill in yourself".
# Model names follow the current DeepSeek docs: deepseek-flash / deepseek-v4-pro.
LLM_PROVIDERS = [
    ("DeepSeek · deepseek-flash（推荐）", "https://api.deepseek.com", "deepseek-flash"),
    ("DeepSeek · deepseek-v4-pro", "https://api.deepseek.com", "deepseek-v4-pro"),
    ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("自定义 / 其它兼容接口", "", ""),
]
LLM_MODELS = ["deepseek-flash", "deepseek-v4-pro", "gpt-4o-mini", "gpt-4o", "qwen3:4b"]
THINKING_CHOICES = [
    ("关闭（推荐，字幕更快）", "disabled"),
    ("开启（更准但慢几秒）", "enabled"),
    ("自动（DeepSeek 默认关闭）", "auto"),
]


def make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#1f6feb"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Microsoft YaHei UI", 30, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "字")
    painter.end()
    return QIcon(pixmap)


def loopback_devices() -> list[str]:
    import soundcard as sc

    return [m.name for m in sc.all_microphones(include_loopback=True) if m.isloopback]


def default_device() -> str:
    try:
        import soundcard as sc

        return sc.default_speaker().name
    except Exception:  # noqa: BLE001
        return ""


def write_log_file() -> None:
    """Keep a log next to the exe: the first thing to look at when it misbehaves."""
    try:
        handler = logging.FileHandler(app_root() / "livecap.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
    except Exception:  # noqa: BLE001
        pass


class Launcher(QWidget):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.pipeline: Pipeline | None = None
        self.overlay = None
        self.bridge: Bridge | None = None
        self.cfg: Config | None = None
        self.settings = load_settings()

        self.setWindowTitle("日文直播实时字幕")
        self.setWindowIcon(make_icon())
        self.setMinimumWidth(470)

        title = QLabel("日文直播实时字幕")
        title.setStyleSheet("font-size:20px; font-weight:700;")
        hint = QLabel(
            "在浏览器里正常播放日文直播，字幕会自动浮在屏幕底部。\n"
            "不需要填链接、不需要 cookie —— 直接抓电脑正在播放的声音。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555;")

        self.device_box = QComboBox()
        self.asr_box = QComboBox()
        self.asr_box.addItems([label for label, _ in ASR_CHOICES])
        self.mt_box = QComboBox()
        self.mt_box.addItems([label for label, _ in MT_CHOICES])
        self.mt_box.currentIndexChanged.connect(self._sync_llm_visibility)
        self.font_box = QSpinBox()
        self.font_box.setRange(16, 48)
        self.font_box.setValue(27)
        self.font_box.setSuffix(" px")

        form = QFormLayout()
        form.addRow("输出设备", self.device_box)
        form.addRow("识别模型", self.asr_box)
        form.addRow("中文翻译", self.mt_box)
        form.addRow("中文字号", self.font_box)

        # ---------------------------------------------------------- LLM settings
        self.llm_group = QGroupBox("大模型翻译设置（选「大模型 API」时生效）")
        self.provider_box = QComboBox()
        self.provider_box.addItems([label for label, _, _ in LLM_PROVIDERS])
        self.provider_box.currentIndexChanged.connect(self._provider_changed)
        self.base_edit = QLineEdit()
        self.base_edit.setPlaceholderText("https://api.deepseek.com")
        self.model_edit = QComboBox()
        self.model_edit.setEditable(True)
        self.model_edit.addItems(LLM_MODELS)
        self.model_edit.setCurrentText("deepseek-flash")
        self.thinking_box = QComboBox()
        self.thinking_box.addItems([label for label, _ in THINKING_CHOICES])
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._test_llm)
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        self.test_status.setStyleSheet("color:#666; font-size:11px;")

        llm_form = QGridLayout()
        llm_form.addWidget(QLabel("服务商"), 0, 0)
        llm_form.addWidget(self.provider_box, 0, 1, 1, 2)
        llm_form.addWidget(QLabel("接口地址"), 1, 0)
        llm_form.addWidget(self.base_edit, 1, 1, 1, 2)
        llm_form.addWidget(QLabel("模型"), 2, 0)
        llm_form.addWidget(self.model_edit, 2, 1, 1, 2)
        llm_form.addWidget(QLabel("思考模式"), 3, 0)
        llm_form.addWidget(self.thinking_box, 3, 1, 1, 2)
        llm_form.addWidget(QLabel("API Key"), 4, 0)
        llm_form.addWidget(self.key_edit, 4, 1)
        llm_form.addWidget(self.test_button, 4, 2)
        llm_form.addWidget(self.test_status, 5, 0, 1, 3)
        llm_form.setColumnStretch(1, 1)
        self.llm_group.setLayout(llm_form)

        self.start_button = QPushButton("开始字幕")
        self.start_button.setStyleSheet(
            "font-size:16px; font-weight:700; padding:10px; background:#1f6feb; color:white;"
            "border-radius:8px;"
        )
        self.start_button.clicked.connect(self.start)

        refresh = QPushButton("刷新设备")
        refresh.clicked.connect(self.reload_devices)
        self.model_button = QPushButton("检查 / 下载模型")
        self.model_button.clicked.connect(self._check_or_download_models)
        quit_button = QPushButton("退出")
        quit_button.clicked.connect(self.app.quit)

        row = QHBoxLayout()
        row.addWidget(refresh)
        row.addWidget(self.model_button)
        row.addStretch(1)
        row.addWidget(quit_button)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#ddd;")

        self.status = QLabel("就绪")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#1f6feb;")

        shortcut_hint = QLabel(
            "字幕条：黑色半透明底、白字；左键拖动 · Ctrl+Shift+H 隐藏 · Ctrl+Shift+T 鼠标穿透 · "
            "Ctrl+Shift+O/I 字号 · Ctrl+Shift+C 清空 · Ctrl+Q 退出"
        )
        shortcut_hint.setWordWrap(True)
        shortcut_hint.setStyleSheet("color:#888; font-size:11px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.llm_group)
        layout.addWidget(self.start_button)
        layout.addWidget(line)
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addWidget(shortcut_hint)

        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("日文直播实时字幕")
        self.tray.setContextMenu(self._build_tray_menu())
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

        self._load_settings_into_widgets()
        self.reload_devices()
        self._sync_llm_visibility()

    # --------------------------------------------------------------- settings
    def _load_settings_into_widgets(self) -> None:
        data = self.settings
        index = self.provider_box.findText(data.get("llm_provider", ""))
        self.provider_box.setCurrentIndex(index if index >= 0 else 0)
        self.base_edit.setText(data.get("llm_base_url", ""))
        self.model_edit.setCurrentText(data.get("llm_model", "deepseek-flash"))
        self.key_edit.setText(data.get("llm_api_key", ""))
        for position, (_, value) in enumerate(THINKING_CHOICES):
            if value == data.get("llm_thinking", "disabled"):
                self.thinking_box.setCurrentIndex(position)
                break

        for combo, choices, key in ((self.asr_box, ASR_CHOICES, "asr_model"),
                                    (self.mt_box, MT_CHOICES, "translator")):
            wanted = data.get(key, "")
            for position, (_, value) in enumerate(choices):
                if value == wanted:
                    combo.setCurrentIndex(position)
                    break
        try:
            self.font_box.setValue(int(data.get("font_zh", 27)))
        except (TypeError, ValueError):
            pass

    def _collect_settings(self) -> dict:
        # start from the file so keys owned by other parts (e.g. the dragged
        # subtitle-bar position) are preserved
        data = load_settings()
        data.update({
            "llm_provider": self.provider_box.currentText(),
            "llm_base_url": self.base_edit.text().strip(),
            "llm_model": self.model_edit.currentText().strip(),
            "llm_thinking": THINKING_CHOICES[self.thinking_box.currentIndex()][1],
            "llm_api_key": self.key_edit.text().strip(),
            "asr_model": ASR_CHOICES[self.asr_box.currentIndex()][1],
            "translator": MT_CHOICES[self.mt_box.currentIndex()][1],
            "loopback_device": self.device_box.currentText(),
            "font_zh": int(self.font_box.value()),
        })
        return data

    # --------------------------------------------------------------- tray menu
    def _build_tray_menu(self):
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        self.act_toggle = QAction("隐藏字幕条", self)
        self.act_toggle.triggered.connect(self.toggle_overlay)
        self.act_panel = QAction("显示设置面板", self)
        self.act_panel.triggered.connect(self.show_panel)
        self.act_stop = QAction("停止字幕", self)
        self.act_stop.triggered.connect(self.stop)
        self.act_quit = QAction("退出", self)
        self.act_quit.triggered.connect(self.app.quit)
        for action in (self.act_toggle, self.act_panel, self.act_stop):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        return menu

    def _tray_clicked(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.show_panel()

    # ------------------------------------------------------------------ devices
    def reload_devices(self) -> None:
        self.device_box.clear()
        try:
            devices = loopback_devices()
        except Exception as exc:  # noqa: BLE001
            devices = []
            self.status.setText(f"无法枚举音频设备：{exc}")
        if not devices:
            self.device_box.addItem("（没找到可采集的输出设备）")
            self.start_button.setEnabled(False)
            return
        self.device_box.addItems(devices)
        wanted = self.settings.get("loopback_device") or default_device()
        for index, name in enumerate(devices):
            if name == wanted:
                self.device_box.setCurrentIndex(index)
                break
        self.start_button.setEnabled(True)

    # --------------------------------------------------------------- llm panel
    @property
    def _translator_kind(self) -> str:
        return MT_CHOICES[self.mt_box.currentIndex()][1]

    def _sync_llm_visibility(self) -> None:
        self.llm_group.setVisible(self._translator_kind == "llm")
        self.adjustSize()

    def _provider_changed(self) -> None:
        label = self.provider_box.currentText()
        for name, base, model in LLM_PROVIDERS:
            if name == label and base:
                self.base_edit.setText(base)
                self.model_edit.setCurrentText(model)
                break

    def _test_llm(self) -> None:
        from .translate import llm_probe

        base = self.base_edit.text().strip()
        model = self.model_edit.currentText().strip()
        key = self.key_edit.text().strip()
        thinking = THINKING_CHOICES[self.thinking_box.currentIndex()][1]
        if not base or not model:
            self.test_status.setText("请先填写接口地址和模型名")
            return

        self.test_button.setEnabled(False)
        self.test_status.setText("测试中…")

        def work():
            try:
                message = f"连接成功 ✓ 返回：{llm_probe(base, model, key, thinking)}"
            except Exception as exc:  # noqa: BLE001
                message = f"失败：{exc}"
            QTimer.singleShot(0, lambda: self._finish_test(message))

        threading.Thread(target=work, name="llm-test", daemon=True).start()

    def _finish_test(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_status.setText(message)

    # ------------------------------------------------------------------- models
    def _check_or_download_models(self) -> None:
        from .models import describe, missing

        report = describe()
        self.status.setText(report.replace("\n", " · "))
        if not missing():
            QMessageBox.information(self, "模型已就绪", report)
            return
        answer = QMessageBox.question(
            self, "需要下载模型",
            f"{report}\n\n现在下载吗？约 4GB，视网速可能要十几分钟。\n"
            f"中途断了没关系，下次点这个按钮会接着下。",
        )
        if answer == QMessageBox.Yes:
            self._download_models()

    def _download_models(self) -> None:
        from .models import download_all, folder_size
        from .paths import models_dir

        self.model_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.status.setText("正在下载模型…")

        timer = QTimer(self)

        def tick() -> None:
            size = folder_size(models_dir()) / (1024 ** 3)
            self.status.setText(f"正在下载模型… 已下载 {size:.2f} GB（总计约 4GB）")

        timer.timeout.connect(tick)
        timer.start(1000)

        def work() -> None:
            try:
                download_all(note=lambda message: log.info("models: %s", message))
                QTimer.singleShot(0, lambda: self._finish_download(True, "模型下载完成 ✓", timer))
            except Exception as exc:  # noqa: BLE001
                message = f"下载失败：{exc}"
                QTimer.singleShot(0, lambda: self._finish_download(False, message, timer))

        threading.Thread(target=work, name="model-download", daemon=True).start()

    def _finish_download(self, ok: bool, message: str, timer: QTimer) -> None:
        from .models import describe

        timer.stop()
        self.model_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.status.setText(message)
        if ok:
            QMessageBox.information(self, "模型", f"{message}\n\n{describe()}")
        else:
            QMessageBox.warning(self, "模型下载失败",
                                f"{message}\n\n稍后再点「检查 / 下载模型」可以续传（已下载的部分会保留）。")

    # ------------------------------------------------------------------- checks
    def _preflight(self) -> str | None:
        """Return an error message when something essential is missing."""
        asr_ref = ASR_CHOICES[self.asr_box.currentIndex()][1]
        if not Path(resolve_model(asr_ref)).exists():
            return (f"识别模型还没下载：{asr_ref}\n\n"
                    f"请在项目目录里运行：\n"
                    f".venv\\Scripts\\python.exe scripts\\prefetch_models.py")

        kind = self._translator_kind
        if kind == "ct2":
            model_dir = models_dir() / "nllb-200-600M-ct2"
            if not (model_dir / "tokenizer.json").exists():
                return (f"翻译模型还没转换：{model_dir}\n\n"
                        f"请运行：\n"
                        f".venv\\Scripts\\python.exe scripts\\convert_mt_model.py")
        if kind == "llm":
            if not self.base_edit.text().strip() or not self.model_edit.currentText().strip():
                return "请填写大模型翻译的接口地址和模型名。"
            if not self.key_edit.text().strip() and not Config().llm_api_key:
                return ("大模型翻译需要 API Key：在「大模型翻译设置」里填好并点「测试连接」，\n"
                        "或者把「中文翻译」改回本地 NLLB（离线、免费）。")
        return None

    # -------------------------------------------------------------------- start
    def start(self) -> None:
        problem = self._preflight()
        if problem:
            QMessageBox.warning(self, "还不能开始", problem)
            return

        data = self._collect_settings()
        save_settings(data)
        self.settings = data

        cfg = Config()
        cfg.source = "loopback"
        cfg.loopback_device = self.device_box.currentText()
        cfg.asr_model = data["asr_model"]
        cfg.translator = data["translator"]
        cfg.font_zh = int(data["font_zh"])
        cfg.font_ja = max(12, int(round(cfg.font_zh * 0.85)))
        cfg.llm_base_url = data["llm_base_url"] or cfg.llm_base_url
        cfg.llm_model = data["llm_model"] or cfg.llm_model
        cfg.llm_thinking = data["llm_thinking"]
        cfg.llm_api_key_value = data["llm_api_key"]
        self.cfg = cfg

        if self.bridge is None:
            self.bridge = Bridge()
            self.overlay = create_overlay(self.app, cfg, self.bridge)
            install_shortcuts(self.app, cfg, self.overlay)
            self.bridge.status.connect(self._on_status)
            self.bridge.finished.connect(self._on_finished)
        else:
            self.overlay.cfg = cfg
            self.overlay._size_zh = cfg.font_zh
            self.overlay._size_ja = cfg.font_ja
            self.overlay._apply_fonts()
            self.overlay.show()

        self.pipeline = Pipeline(cfg, self.bridge)
        self.pipeline.start_async()

        def watch():
            self.pipeline._stop.wait()  # noqa: SLF001 - react when capture dies
            time.sleep(0.8)
            try:
                self.bridge.finished.emit()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=watch, name="watcher", daemon=True).start()

        self.hide()
        self.tray.showMessage("日文直播实时字幕", "已开始，在浏览器里播放日文直播即可。",
                              make_icon(), 3000)
        self.status.setText(f"正在监听：{cfg.loopback_device}")

    def _on_status(self, text: str) -> None:
        self.status.setText(text)
        self.tray.setToolTip(f"日文直播实时字幕\n{text}")

    def _on_finished(self) -> None:
        if self.pipeline is not None and self.pipeline._stop.is_set():  # noqa: SLF001
            self.status.setText("音频采集已停止")
        self.show_panel()

    # --------------------------------------------------------------------- stop
    def stop(self) -> None:
        if self.pipeline is not None:
            self.pipeline.stop()
            self.pipeline = None
        if self.overlay is not None:
            self.overlay.hide()
        self.status.setText("已停止")
        self.show_panel()

    def show_panel(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_overlay(self) -> None:
        if self.overlay is None:
            return
        visible = not self.overlay.isVisible()
        self.overlay.setVisible(visible)
        self.act_toggle.setText("隐藏字幕条" if visible else "显示字幕条")

    def closeEvent(self, event):
        """Closing the window keeps the app in the tray."""
        event.ignore()
        self.hide()
        self.tray.showMessage("日文直播实时字幕", "已最小化到托盘，右键图标可以退出。",
                              make_icon(), 3000)


def main(argv: list[str] | None = None) -> int:
    import os

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    write_log_file()

    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setQuitOnLastWindowClosed(False)   # lives in the tray
    window = Launcher(app)
    window.show()

    # test hooks: start the pipeline immediately and/or quit after N seconds
    if os.environ.get("LIVECAP_GUI_AUTOSTART"):
        QTimer.singleShot(500, window.start)
    autoclose = os.environ.get("LIVECAP_GUI_AUTOCLOSE")
    if autoclose:
        QTimer.singleShot(int(float(autoclose) * 1000), app.quit)

    try:
        return app.exec()
    finally:
        if window.pipeline is not None:
            window.pipeline.stop()
