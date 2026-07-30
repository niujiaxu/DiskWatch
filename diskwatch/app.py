"""应用装配：托盘、悬浮组件、详情面板、监控线程的生命周期管理。"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSharedMemory, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, APP_TITLE, VERSION
from .config import Config, DB_PATH, apply_paths, paths
from .storage import Storage, human_size, today_str
from .ui.ball import MiniBall
from .ui.panel import DetailPanel
from .ui.settings import SettingsDialog
from .ui.style import app_icon, apply_dark_theme, set_app_user_model_id
from .ui.widget import FloatingWidget
from .watcher import FileMonitor

PURGE_INTERVAL_MS = 60 * 60 * 1000  # 每小时清一次过期数据


class DiskWatchApp:
    def __init__(self, qt_app: QApplication, instance_lock: QSharedMemory | None = None) -> None:
        self.qt_app = qt_app
        self._instance_lock = instance_lock
        self.config = Config()
        self.storage = Storage(Path(str(DB_PATH)))
        self.monitor = FileMonitor(self.config, self.storage)

        self.widget = FloatingWidget(self.storage, self.monitor, self.config)
        self.ball = MiniBall(self.storage, self.monitor, self.config)
        self.panel = DetailPanel(self.storage)
        self.tray = QSystemTrayIcon(app_icon(), qt_app)

        self._wire()
        self._build_tray()

        self.monitor.start()
        self._purge()

        self._purge_timer = QTimer(qt_app)
        self._purge_timer.timeout.connect(self._purge)
        self._purge_timer.start(PURGE_INTERVAL_MS)

        self._tip_signature: tuple | None = None
        self._tip_timer = QTimer(qt_app)
        self._tip_timer.timeout.connect(self._update_tooltip)
        self._tip_timer.start(5000)
        self._update_tooltip()

        self.widget.hide()
        self.ball.hide()
        if not (
            self.config.get("start_minimized")
            or not self.config.get("widget_visible", True)
        ):
            self._show_surface()

        if self.monitor.errors:
            self.tray.showMessage(
                APP_TITLE,
                "部分位置监控失败：\n" + "\n".join(self.monitor.errors[:3]),
                QSystemTrayIcon.Warning,
                5000,
            )

    # ---------- 装配 ----------

    def _wire(self) -> None:
        for surface in (self.widget, self.ball):
            surface.open_panel.connect(self.show_panel)
            surface.open_settings.connect(self.show_settings)
            surface.request_quit.connect(self.quit)
        self.widget.hidden_by_user.connect(self._sync_tray_actions)
        self.widget.collapse_requested.connect(self.collapse)
        self.ball.expand_requested.connect(self.expand)

    def _build_tray(self) -> None:
        menu = QMenu()
        self.act_widget = menu.addAction("显示悬浮组件")
        self.act_widget.setCheckable(True)
        self.act_widget.triggered.connect(self._toggle_widget)
        self.act_ball = menu.addAction("迷你球模式")
        self.act_ball.setCheckable(True)
        self.act_ball.triggered.connect(
            lambda checked: self.collapse() if checked else self.expand()
        )
        menu.addAction("详情面板…", self.show_panel)
        menu.addSeparator()
        menu.addAction("设置…", self.show_settings)
        menu.addAction("重新开始监控", self._restart_monitor)
        menu.addAction("重启", self._restart_app)
        menu.addSeparator()
        menu.addAction(f"关于 {APP_NAME} {VERSION}", self._about)
        menu.addAction("退出", self.quit)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip(APP_TITLE)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._sync_tray_actions()

    # ---------- 动作 ----------

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._toggle_widget(not self._surface_visible())
        elif reason == QSystemTrayIcon.DoubleClick:
            self.show_panel()

    def _collapsed(self) -> bool:
        return bool(self.config.get("collapsed", False))

    def _surface_visible(self) -> bool:
        return self.widget.isVisible() or self.ball.isVisible()

    def _show_surface(self) -> None:
        """按当前模式显示卡片或迷你球，另一个确保隐藏。"""
        if self._collapsed():
            self.widget.hide()
            self.ball.refresh()
            self.ball.show()
            self.ball.raise_()
        else:
            self.ball.hide()
            self.widget.refresh()
            self.widget.show()
            self.widget.raise_()
        self.config.set("widget_visible", True)
        self.config.save_soon()
        self._sync_tray_actions()

    def collapse(self) -> None:
        if self._collapsed() and self.ball.isVisible():
            return
        was_visible = self.widget.isVisible()
        self.config.set("collapsed", True)
        if was_visible:
            # 让球出现在卡片原来的位置附近，收起时视觉上有连续感
            self.ball.place_near(self.widget.frameGeometry())
        self._show_surface()

    def expand(self) -> None:
        self.config.set("collapsed", False)
        self._show_surface()

    def _toggle_widget(self, checked: bool) -> None:
        if checked:
            self._show_surface()
        else:
            self.widget.hide()
            self.ball.hide()
            self.config.set("widget_visible", False)
            self.config.save_soon()
        self._sync_tray_actions()

    def _sync_tray_actions(self) -> None:
        self.act_widget.setChecked(self._surface_visible())
        self.act_ball.setChecked(self._collapsed())

    def show_panel(self) -> None:
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        # 卡片也是置顶窗，再抬一次详情，避免挡在表上
        self.panel.raise_()

    def show_settings(self) -> None:
        dlg = SettingsDialog(self.config, self.storage, self.panel)
        if not dlg.exec():
            return
        self.config.update(dlg.result_values())
        self.config.save()
        self.widget.apply_appearance()
        self.ball.apply_appearance()
        self._restart_monitor()
        self.panel.reload(keep_day=True)

        pending = dlg.pending_paths()
        if pending:
            cfg_path, db_path = pending
            try:
                # 必须先关掉数据库连接，否则 Windows 上拷贝/替换会失败
                self.monitor.stop()
                self.storage.close()
                apply_paths(Path(cfg_path), Path(db_path), migrate=True)
                QMessageBox.information(
                    self.panel,
                    "位置已更新",
                    f"配置：{paths.config}\n数据库：{paths.db}\n\n程序将重启以加载新位置。",
                )
            except OSError as exc:
                QMessageBox.warning(self.panel, "更改位置失败", str(exc))
                # 尽力恢复原库连接
                self.storage = Storage(Path(str(DB_PATH)))
                self.monitor = FileMonitor(self.config, self.storage)
                self.monitor.start()
                return
            self._relaunch()

    def _restart_app(self) -> None:
        """整程序重启（托盘菜单「重启」）。"""
        self.config.save()
        self._relaunch()

    def _relaunch(self) -> None:
        """重新拉起自身，然后退出当前实例。兼容源码运行与便携版 exe。"""
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        exe = Path(sys.executable)
        if exe.name.lower() in ("python.exe", "pythonw.exe"):
            pythonw = exe.with_name("pythonw.exe")
            runner = pythonw if pythonw.exists() else exe
            entry = Path(__file__).resolve().parent.parent / "run.pyw"
            args = [str(runner), str(entry)]
            cwd = str(entry.parent)
        else:
            args = [str(exe)]
            cwd = str(exe.parent)

        # 先放开单实例锁，否则新进程会以为已经在运行
        if self._instance_lock is not None:
            try:
                self._instance_lock.detach()
            except Exception:
                pass
            self._instance_lock = None

        try:
            subprocess.Popen(args, cwd=cwd, creationflags=creation)
        except OSError as exc:
            QMessageBox.warning(
                self.panel,
                "自动重启失败",
                f"请手动重新运行程序。\n{exc}",
            )
            return

        try:
            self.monitor.stop()
        except Exception:
            pass
        try:
            self.storage.close()
        except Exception:
            pass
        self.tray.hide()
        self.qt_app.quit()

    def _restart_monitor(self) -> None:
        self.monitor.restart()
        self.widget.refresh()
        self.ball.refresh()

    def _purge(self) -> None:
        days = int(self.config.get("retention_days", 90))
        if days > 0:
            storage = self.storage

            def _run() -> None:
                try:
                    storage.purge_older_than(days)
                except Exception:
                    pass

            threading.Thread(target=_run, name="dw-purge", daemon=True).start()

    def _update_tooltip(self) -> None:
        count, size = self.storage.day_stats(today_str())
        if (count, size) == self._tip_signature:
            return
        self._tip_signature = (count, size)
        self.tray.setToolTip(
            f"{APP_TITLE}\n今日新增 {count:,} 个文件 · {human_size(size)}"
        )

    def _about(self) -> None:
        QMessageBox.information(
            self.panel,
            f"关于 {APP_NAME}",
            f"{APP_TITLE} v{VERSION}\n\n"
            "实时记录硬盘上每天新增了哪些文件。\n"
            f"数据库：{paths.db}\n"
            f"配置：{paths.config}\n\n"
            "左键点击托盘图标可显示/隐藏悬浮组件，双击打开详情面板。\n"
            "点卡片上的「－」收成迷你球，单击球可再展开。",
        )

    def quit(self) -> None:
        self.config.save()
        self.monitor.stop()
        self.storage.close()
        self.tray.hide()
        self.qt_app.quit()


def main() -> int:
    # 必须在创建 QApplication 之前：否则任务栏仍显示 python.exe 图标
    set_app_user_model_id(f"{APP_NAME}.Desktop")

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setQuitOnLastWindowClosed(False)
    icon = app_icon()
    qt_app.setWindowIcon(icon)
    apply_dark_theme(qt_app)

    # 单实例：重复启动时直接退出，避免两个监控互相打架
    lock = QSharedMemory(f"{APP_NAME}-single-instance")
    if not lock.create(1):
        QMessageBox.information(None, APP_TITLE, f"{APP_NAME} 已经在运行了（见系统托盘）。")
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_TITLE, "当前系统没有可用的托盘区，无法运行。")
        return 1

    app = DiskWatchApp(qt_app, instance_lock=lock)
    try:
        return qt_app.exec()
    finally:
        app.monitor.stop()
        if app._instance_lock is not None:
            lock.detach()
