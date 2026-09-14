"""Frozen GUI entry point (windowed, no console)."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _crash_log(exc: BaseException) -> Path | None:
    try:
        target = Path(sys.executable).resolve().parent / "livecap_crash.log"
        target.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        return target
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    try:
        from livecap.gui import main as gui_main

        return gui_main()
    except BaseException as exc:  # noqa: BLE001 - frozen apps die silently otherwise
        path = _crash_log(exc)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication([])
            QMessageBox.critical(
                None, "日文直播实时字幕 - 启动失败",
                f"{type(exc).__name__}: {exc}\n\n详细堆栈已写入：\n{path}",
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
