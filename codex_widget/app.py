from __future__ import annotations

import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication

from .config import CONFIG_DIR, AppConfig
from .ui import CodexWidget


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(CONFIG_DIR / 'app.lock'))
    if not lock.tryLock(100):
        return 0

    config = AppConfig.load()
    widget = CodexWidget(config)
    widget.show()
    try:
        return app.exec()
    finally:
        lock.unlock()
