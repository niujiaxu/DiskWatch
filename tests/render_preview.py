"""把界面离屏渲染成 PNG，用于 README 预览（不截屏、不干扰运行中的实例）。

用法：
    .venv\\Scripts\\python.exe tests\\render_preview.py            # 全部
    .venv\\Scripts\\python.exe tests\\render_preview.py settings   # 只渲染设置页
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QTabWidget

from diskwatch.config import Config, DB_PATH
from diskwatch.storage import Storage
from diskwatch.ui.ball import MiniBall
from diskwatch.ui.panel import DetailPanel
from diskwatch.ui.settings import SettingsDialog
from diskwatch.ui.style import apply_dark_theme
from diskwatch.ui.widget import FloatingWidget
from diskwatch.watcher import FileMonitor

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"
# 略偏冷的浅灰，衬托深色科技蓝界面
_CANVAS = QColor("#c5cad6")


def save(widget, name: str, margin: int = 24) -> None:
    pm = widget.grab()
    # grab() 返回物理像素图并自带 devicePixelRatio，而 QPainter 按逻辑坐标绘制。
    # 画布必须按物理尺寸开、按同一 DPR 标记，否则高分屏下会留出大片空白。
    dpr = pm.devicePixelRatio()
    canvas = QPixmap(
        int(pm.width() + 2 * margin * dpr), int(pm.height() + 2 * margin * dpr)
    )
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(_CANVAS)
    p = QPainter(canvas)
    p.drawPixmap(margin, margin, pm)
    p.end()
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{name}.png"
    canvas.save(str(out))
    print(f"saved {out.name}  ({pm.width()}x{pm.height()} @ {dpr:g}x)")


def main() -> int:
    targets = sys.argv[1:] or ["widget", "ball", "panel", "settings"]

    app = QApplication(sys.argv[:1])
    apply_dark_theme(app)
    config = Config()
    storage = Storage(DB_PATH)
    monitor = FileMonitor(config, storage)  # 不 start()，只用于显示「监控 N 个位置」
    monitor._roots = ["C:\\"]

    jobs: list[tuple] = []

    if "widget" in targets:
        w = FloatingWidget(storage, monitor, config)
        w.refresh()
        w.adjustSize()
        w.show()
        jobs.append((lambda: None, w, "widget-preview"))

    if "ball" in targets:
        b = MiniBall(storage, monitor, config)
        b.refresh(initial=True)
        b.show()
        jobs.append((lambda: None, b, "ball-preview"))

    if "panel" in targets:
        p = DetailPanel(storage)
        p.reload()
        # 展示「按应用分组」树状效果
        if hasattr(p, "chk_group"):
            p.chk_group.setChecked(True)
        p.resize(1000, 620)
        p.show()
        jobs.append((lambda: None, p, "panel-preview"))

    if "settings" in targets:
        s = SettingsDialog(config, storage)
        s.resize(640, 580)
        s.show()
        tabs = s.findChild(QTabWidget)
        # README 用的主图：第一页
        jobs.append((lambda: tabs.setCurrentIndex(0), s, "settings-preview"))
        for i in range(tabs.count()):
            name = f"settings-{i}-{tabs.tabText(i)}"
            jobs.append((lambda idx=i: tabs.setCurrentIndex(idx), s, name))

    def grab_all() -> None:
        for prepare, widget, name in jobs:
            prepare()
            app.processEvents()
            if name == "panel-preview":
                app.processEvents()
            save(widget, name)
        storage.close()
        app.quit()

    # 详情面板有异步查库，多给一点时间填表
    delay = 1200 if "panel" in targets else 800
    QTimer.singleShot(delay, grab_all)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
