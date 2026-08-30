"""统一主窗口：概览、文件活动和设置入口。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .style import apply_window_icon, enable_titlebar, panel_qss, theme_tokens


class MainWindow(QMainWindow):
    """承载原有稳定页面的统一壳，逐步迁移而不打断功能。"""

    settings_requested = Signal()
    scan_cancel_requested = Signal()

    def __init__(
        self,
        dashboard: QWidget,
        activity: QWidget,
        settings: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.dashboard = dashboard
        self.activity = activity
        self.setWindowTitle(tr("DiskWatch · 磁盘空间"))
        self.resize(1180, 780)
        self.setMinimumSize(900, 620)
        apply_window_icon(self)

        root = QWidget(objectName="panelRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QFrame(objectName="mainSidebar")
        self.sidebar.setFixedWidth(176)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(18, 22, 14, 18)
        side.setSpacing(6)
        brand = QLabel("DiskWatch", objectName="brand")
        subtitle = QLabel(tr("磁盘空间去向"), objectName="dim")
        side.addWidget(brand)
        side.addWidget(subtitle)
        side.addSpacing(22)

        self.btn_overview = self._nav_button(tr("概览"), 0)
        self.btn_activity = self._nav_button(tr("文件活动"), 1)
        side.addWidget(self.btn_overview)
        side.addWidget(self.btn_activity)
        side.addStretch(1)
        if settings is None:
            self.btn_settings = QPushButton(tr("设置"), objectName="navButton")
            self.btn_settings.clicked.connect(self.settings_requested.emit)
        else:
            self.btn_settings = self._nav_button(tr("设置"), 2)
        side.addWidget(self.btn_settings)
        layout.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages, 1)
        self.settings = settings
        for page in (dashboard, activity, settings):
            if page is None:
                continue
            page.setParent(self.pages)
            page.setWindowFlags(Qt.Widget)
            page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.pages.addWidget(page)

        self.scan_strip = QFrame(objectName="scanStrip")
        scan_layout = QHBoxLayout(self.scan_strip)
        scan_layout.setContentsMargins(18, 9, 18, 9)
        scan_layout.setSpacing(10)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setFixedWidth(120)
        self.scan_progress.setTextVisible(False)
        self.scan_label = QLabel(tr("正在恢复离线文件变化…"), objectName="dim")
        self.scan_cancel = QPushButton(tr("取消补扫"))
        self.scan_cancel.clicked.connect(self._request_scan_cancel)
        scan_layout.addWidget(self.scan_progress)
        scan_layout.addWidget(self.scan_label, 1)
        scan_layout.addWidget(self.scan_cancel)
        self.scan_strip.hide()
        self._scan_cancelling = False
        content_layout.addWidget(self.scan_strip)
        layout.addWidget(content, 1)

        self._nav_buttons = (
            (self.btn_overview, self.btn_activity, self.btn_settings)
            if settings is not None
            else (self.btn_overview, self.btn_activity)
        )
        self.select_page(0)
        self.apply_theme()

    def _nav_button(self, text: str, index: int) -> QPushButton:
        button = QPushButton(text, objectName="navButton")
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, i=index: self.select_page(i))
        return button

    def select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(getattr(self, "_nav_buttons", ())):
            button.setChecked(i == index)

    def show_overview(self) -> None:
        self.select_page(0)
        self._show_front()

    def show_activity(self) -> None:
        self.select_page(1)
        self._show_front()

    def show_settings(self) -> None:
        if self.settings is None:
            self.settings_requested.emit()
            return
        self.select_page(2)
        self._show_front()

    def _show_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_scan_progress(self, directories: int, files: int, path: str = "") -> None:
        self.scan_strip.show()
        if not self._scan_cancelling:
            self.scan_cancel.show()
            self.scan_cancel.setEnabled(True)
            self.scan_cancel.setText(tr("取消补扫"))
        self.scan_label.setText(
            tr(
                "正在补扫：{directories} 个目录 · {files} 个文件",
                directories=f"{directories:,}",
                files=f"{files:,}",
            )
        )
        self.scan_label.setToolTip(path)

    def finish_scan(self, added: int, cancelled: bool) -> None:
        self._scan_cancelling = False
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(1)
        text = (
            tr("补扫已取消，已补回 {count} 条", count=f"{added:,}")
            if cancelled
            else tr("补扫完成，已补回 {count} 条", count=f"{added:,}")
        )
        self.scan_label.setText(text)
        self.scan_label.setToolTip("")
        self.scan_cancel.hide()
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3500, self.scan_strip.hide)

    def _request_scan_cancel(self) -> None:
        self._scan_cancelling = True
        self.scan_cancel.setEnabled(False)
        self.scan_cancel.setText(tr("正在取消…"))
        self.scan_cancel_requested.emit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_window_icon(self)
        enable_titlebar(self)

    def apply_theme(self) -> None:
        t = theme_tokens()
        nav_qss = f"""
QFrame#mainSidebar {{ background: {t.surface}; border-right: 1px solid {t.border}; }}
QLabel#brand {{ color: {t.text}; font-size: 18px; font-weight: 650; }}
QPushButton#navButton {{
    color: {t.text_dim}; background: transparent; border: none;
    border-radius: 9px; padding: 9px 12px; text-align: left;
}}
QPushButton#navButton:hover {{ color: {t.text}; background: {t.button}; }}
QPushButton#navButton:checked {{ color: {t.accent}; background: {t.accent_soft}; }}
QFrame#scanStrip {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
QProgressBar {{ background: {t.field}; border: none; border-radius: 3px; height: 6px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
"""
        self.setStyleSheet(panel_qss() + nav_qss)
        for page in (self.dashboard, self.activity, self.settings):
            if page is None:
                continue
            hook = getattr(page, "apply_theme", None)
            if callable(hook):
                hook()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("DiskWatch · 磁盘空间"))
        self.btn_overview.setText(tr("概览"))
        self.btn_activity.setText(tr("文件活动"))
        self.btn_settings.setText(tr("设置"))
        self.scan_cancel.setText(tr("取消补扫"))
