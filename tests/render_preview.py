"""用合成数据离屏渲染当前 UI，不读取真实用户配置或数据库。"""

from __future__ import annotations

import copy
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from diskwatch.config import DEFAULTS
from diskwatch.storage import Storage, make_record
from diskwatch.ui.activity import ActivityPanel
from diskwatch.ui.dashboard import DashboardPanel
from diskwatch.ui.main_window import MainWindow
from diskwatch.ui.settings import SettingsDialog
from diskwatch.ui.style import install_theme
from diskwatch.ui.widget import FloatingWidget

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"


class PreviewConfig:
    def __init__(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)
        self.data["widget_pos"] = None

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def update(self, values) -> None:
        self.data.update(values)

    def save(self) -> None:
        pass

    def save_soon(self) -> None:
        pass


class PreviewMonitor:
    roots: ClassVar[list[str]] = ["C:\\"]
    errors: ClassVar[list[str]] = []

    @staticmethod
    def stats() -> tuple[int, int, int]:
        return 42, 0, 3

    @staticmethod
    def diagnostics() -> dict[str, int]:
        return {
            "seen": 18420,
            "passed": 18420,
            "processed": 17360,
            "coalesced": 1057,
            "dropped": 0,
            "queued": 3,
            "pending_modifications": 2,
        }


def seed(storage: Storage) -> None:
    now = time.time()
    rows = [
        (r"C:\Users\Alex\Downloads\archive.zip", 2_400_000_000, "download"),
        (r"C:\Users\Alex\AppData\Local\Browser\Cache\data.bin", 780_000_000, "cache"),
        (r"C:\Code\project\build\bundle.bin", 420_000_000, "development"),
        (r"C:\Windows\SoftwareDistribution\update.cab", 310_000_000, "system"),
        (r"C:\Users\Alex\Documents\report.docx", 18_000_000, "user"),
    ]
    for index, (path, size, category) in enumerate(rows):
        storage.add_files(
            [make_record(path, size, now - index * 900, category)],
            occurred_at=now - index * 900,
        )
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    storage.record_disk_space(
        [(yesterday, "C:", 180_000_000_000, 512_000_000_000)],
        sampled_at=now - 86400,
    )
    storage.record_disk_space(
        [(today, "C:", 175_500_000_000, 512_000_000_000)],
        sampled_at=now,
    )


def save(widget, name: str, canvas_color: str) -> None:
    QApplication.processEvents()
    pixmap = widget.grab()
    margin = 24
    ratio = pixmap.devicePixelRatio()
    canvas = QPixmap(
        int(pixmap.width() + 2 * margin * ratio),
        int(pixmap.height() + 2 * margin * ratio),
    )
    canvas.setDevicePixelRatio(ratio)
    canvas.fill(QColor(canvas_color))
    painter = QPainter(canvas)
    painter.drawPixmap(margin, margin, pixmap)
    painter.end()
    OUT_DIR.mkdir(exist_ok=True)
    output = OUT_DIR / f"{name}.png"
    canvas.save(str(output))
    print(f"saved {output.name}")


def main() -> int:
    app = QApplication(sys.argv[:1])
    config = PreviewConfig()
    monitor = PreviewMonitor()
    temp = tempfile.TemporaryDirectory(prefix="diskwatch-preview-")
    storage = Storage(Path(temp.name) / "preview.db")
    seed(storage)

    dashboard = DashboardPanel(storage)
    activity = ActivityPanel(storage)
    settings = SettingsDialog(config, storage, monitor=monitor, embedded=True)
    window = MainWindow(dashboard, activity, settings)
    window.resize(1180, 780)
    widget = FloatingWidget(storage, monitor, config)
    widget.adjustSize()
    for surface in (window, widget):
        surface.setAttribute(Qt.WA_DontShowOnScreen, True)

    def render() -> None:
        install_theme(app, "dark")
        window.show_overview()
        app.processEvents()
        dashboard.reload()
        QTimer.singleShot(700, render_dark)

    def render_dark() -> None:
        app.processEvents()
        save(window, "main-dark-preview", "#0b0d10")
        widget.show()
        app.processEvents()
        save(widget, "widget-preview", "#dfe3e9")
        widget.hide()

        install_theme(app, "light")
        window.show_activity()
        activity.reload()
        app.processEvents()
        save(window, "main-light-preview", "#e9edf2")
        window.show_settings()
        settings.nav.setCurrentRow(2)
        app.processEvents()
        save(window, "settings-preview", "#e9edf2")

        window.close()
        dashboard.wait_for_idle()
        storage.close()
        temp.cleanup()
        app.quit()

    QTimer.singleShot(0, render)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
