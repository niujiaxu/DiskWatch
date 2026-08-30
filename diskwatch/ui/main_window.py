"""统一主窗口：概览、文件活动和设置入口。"""

from __future__ import annotations

import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent, QPainter, QPaintEvent, QPen
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
from .style import (
    app_icon,
    apply_window_icon,
    enable_titlebar,
    panel_qss,
    theme_tokens,
)


class _WindowControlButton(QPushButton):
    """与应用主题一致的极简窗口控制按钮。"""

    def __init__(self, kind: str, parent=None) -> None:
        object_name = "titleBarClose" if kind == "close" else "titleBarButton"
        super().__init__("", parent, objectName=object_name)
        self.kind = kind
        self.setFixedSize(42, 36)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        t = theme_tokens()
        color = t.color("text_dim")
        if self.kind == "close" and self.underMouse():
            color = Qt.white
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(color, 1.45))
        cx = self.width() / 2
        cy = self.height() / 2
        if self.kind == "minimize":
            painter.drawLine(QPoint(int(cx - 6), int(cy + 3)), QPoint(int(cx + 6), int(cy + 3)))
        elif self.kind == "maximize":
            window = self.window()
            if window.isMaximized():
                painter.drawRoundedRect(QRectF(cx - 4, cy - 5, 9, 9), 0.8, 0.8)
                painter.drawRoundedRect(QRectF(cx - 6, cy - 3, 9, 9), 0.8, 0.8)
            else:
                painter.drawRoundedRect(QRectF(cx - 5, cy - 5, 10, 10), 0.8, 0.8)
        else:
            painter.drawLine(QPoint(int(cx - 5), int(cy - 5)), QPoint(int(cx + 5), int(cy + 5)))
            painter.drawLine(QPoint(int(cx + 5), int(cy - 5)), QPoint(int(cx - 5), int(cy + 5)))
        painter.end()


class _TitleBar(QFrame):
    """无装饰窗口的标题栏，保留系统拖动与最大化行为。"""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window, objectName="appTitleBar")
        self._window = window
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 6, 4)
        layout.setSpacing(8)

        icon = QLabel(objectName="titleBarIcon")
        icon.setFixedSize(22, 22)
        icon.setPixmap(app_icon().pixmap(20, 20))
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title = QLabel(window.windowTitle(), objectName="titleBarTitle")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(icon)
        layout.addWidget(self.title)
        layout.addStretch(1)

        self.btn_minimize = _WindowControlButton("minimize", self)
        self.btn_maximize = _WindowControlButton("maximize", self)
        self.btn_close = _WindowControlButton("close", self)
        self.btn_minimize.setToolTip(tr("最小化"))
        self.btn_maximize.setToolTip(tr("最大化"))
        self.btn_close.setToolTip(tr("关闭"))
        self.btn_minimize.setAccessibleName(tr("最小化"))
        self.btn_maximize.setAccessibleName(tr("最大化"))
        self.btn_close.setAccessibleName(tr("关闭"))
        self.btn_minimize.clicked.connect(window.showMinimized)
        self.btn_maximize.clicked.connect(window.toggle_maximized)
        self.btn_close.clicked.connect(window.close)
        layout.addWidget(self.btn_minimize)
        layout.addWidget(self.btn_maximize)
        layout.addWidget(self.btn_close)
        window.windowTitleChanged.connect(self.title.setText)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._window.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def refresh_state(self) -> None:
        self.btn_maximize.setToolTip(
            tr("还原") if self._window.isMaximized() else tr("最大化")
        )
        self.btn_maximize.setAccessibleName(self.btn_maximize.toolTip())
        for button in (self.btn_minimize, self.btn_maximize, self.btn_close):
            button.update()


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
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowTitle(tr("DiskWatch · 磁盘空间"))
        self.resize(1180, 780)
        self.setMinimumSize(900, 620)
        apply_window_icon(self)

        root = QWidget(objectName="mainRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.title_bar = _TitleBar(self)
        root_layout.addWidget(self.title_bar)

        body = QWidget(objectName="panelRoot")
        root_layout.addWidget(body, 1)
        layout = QHBoxLayout(body)
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

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.title_bar.refresh_state()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.refresh_state()

    def nativeEvent(self, event_type, message):
        """为无边框窗口恢复 Windows 四边和四角的原生缩放命中区。"""
        if sys.platform == "win32" and not self.isMaximized():
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST
                    pos = self.mapFromGlobal(QCursor.pos())
                    edge = 7
                    left = pos.x() < edge
                    right = pos.x() >= self.width() - edge
                    top = pos.y() < edge
                    bottom = pos.y() >= self.height() - edge
                    hit = {
                        (True, False, True, False): 13,   # HTTOPLEFT
                        (False, True, True, False): 14,   # HTTOPRIGHT
                        (True, False, False, True): 16,   # HTBOTTOMLEFT
                        (False, True, False, True): 17,   # HTBOTTOMRIGHT
                    }.get((left, right, top, bottom))
                    if hit is None:
                        if left:
                            hit = 10  # HTLEFT
                        elif right:
                            hit = 11  # HTRIGHT
                        elif top:
                            hit = 12  # HTTOP
                        elif bottom:
                            hit = 15  # HTBOTTOM
                    if hit is not None:
                        return True, hit
            except (AttributeError, TypeError, ValueError):
                pass
        return super().nativeEvent(event_type, message)

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
QWidget#mainRoot {{ background: {t.window}; border: 1px solid {t.border}; }}
QFrame#appTitleBar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
QLabel#titleBarTitle {{ color: {t.text_dim}; font-size: 12px; font-weight: 500; }}
QPushButton#titleBarButton, QPushButton#titleBarClose {{
    background: transparent; border: none; border-radius: 8px; padding: 0;
}}
QPushButton#titleBarButton:hover {{ background: {t.button}; }}
QPushButton#titleBarClose:hover {{ background: {t.danger}; }}
QPushButton#titleBarButton:focus, QPushButton#titleBarClose:focus {{ border: none; }}
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
        self.title_bar.refresh_state()
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
