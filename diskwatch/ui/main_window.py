"""统一主窗口：概览、文件活动和设置入口。"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QPoint,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
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

# ---- 无边框窗口的边缘缩放（Windows）----
_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_SETCURSOR = 0x0020
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_RESIZE_EDGE = 7  # 命中带宽度（逻辑像素）
# HT* 命中码 -> IDC_* 光标资源号
_HIT_CURSOR = {
    10: 32644,  # HTLEFT        -> IDC_SIZEWE
    11: 32644,  # HTRIGHT       -> IDC_SIZEWE
    12: 32645,  # HTTOP         -> IDC_SIZENS
    15: 32645,  # HTBOTTOM      -> IDC_SIZENS
    13: 32642,  # HTTOPLEFT     -> IDC_SIZENWSE
    17: 32642,  # HTBOTTOMRIGHT -> IDC_SIZENWSE
    14: 32643,  # HTTOPRIGHT    -> IDC_SIZENESW
    16: 32643,  # HTBOTTOMLEFT  -> IDC_SIZENESW
}


class _EdgeCursorFilter(QAbstractNativeEventFilter):
    """在 Qt 之前接管主窗口的边缘缩放消息（WM_NCHITTEST / WM_SETCURSOR）。

    两个坑都在这里规避：

    - Qt 按“鼠标下控件”的光标处理 WM_SETCURSOR，会把手型/箭头覆盖到边缘，
      导致缩放光标时有时无；
    - Qt 记录的窗口几何可能与原生窗口不一致（原生缩放后），依赖
      QCursor.pos()+mapFromGlobal 判断边缘会失效，表现为“有箭头但拖不动”。

    因此位置全部按原生消息计算（lParam / GetCursorPos + GetWindowRect），
    不依赖 Qt 的几何和光标状态。
    """

    def __init__(self, hwnd: int = 0) -> None:
        super().__init__()
        self._hwnd = int(hwnd)

    def set_hwnd(self, hwnd: int) -> None:
        """由 Qt 侧（showEvent / 句柄变化）更新。

        绝不能在 nativeEventFilter 里调 winId() 等 Qt API：原生消息回调中
        重入 Qt 会导致进程崩溃（STATUS_FATAL_USER_CALLBACK_EXCEPTION）。
        """
        self._hwnd = int(hwnd)

    @staticmethod
    def _hit(hwnd: int, x: int, y: int) -> int | None:
        """按屏幕坐标（物理像素）判断窗口边缘/四角，返回 HT* 命中码。"""
        user32 = ctypes.windll.user32
        if not hwnd or user32.IsZoomed(wintypes.HWND(hwnd)):
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        # SM_CXSIZEFRAME + SM_CXPADDEDBORDER：系统标准可缩放边框厚度
        margin = user32.GetSystemMetrics(32) + user32.GetSystemMetrics(92)
        if margin <= 0:
            margin = 8
        left = x < rect.left + margin
        right = x >= rect.right - margin
        top = y < rect.top + margin
        bottom = y >= rect.bottom - margin
        if left and top:
            return 13  # HTTOPLEFT
        if right and top:
            return 14  # HTTOPRIGHT
        if left and bottom:
            return 16  # HTBOTTOMLEFT
        if right and bottom:
            return 17  # HTBOTTOMRIGHT
        if left:
            return 10  # HTLEFT
        if right:
            return 11  # HTRIGHT
        if top:
            return 12  # HTTOP
        if bottom:
            return 15  # HTBOTTOM
        return None

    @staticmethod
    def _ensure_thickframe(hwnd: int) -> None:
        """边缘自愈：某些路径会让 Qt 丢掉 WS_THICKFRAME，丢了就拖不动。"""
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
        if style & _WS_THICKFRAME:
            return
        user32.SetWindowLongW(hwnd, _GWL_STYLE, style | _WS_THICKFRAME)
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
        )

    def nativeEventFilter(self, event_type, message):
        try:
            name = (
                event_type.decode(errors="ignore")
                if isinstance(event_type, (bytes, bytearray))
                else str(event_type)
            )
            if "windows_generic_MSG" not in name and "windows_dispatcher_MSG" not in name:
                return False, 0
            hwnd = self._hwnd
            if not hwnd:
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            if int(msg.hWnd or 0) != hwnd:
                return False, 0
            user32 = ctypes.windll.user32
            if msg.message == _WM_NCHITTEST:
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                hit = self._hit(hwnd, x, y)
                if hit is not None:
                    return True, hit
            elif msg.message == _WM_SETCURSOR:
                hit = msg.lParam & 0xFFFF
                cursor_id = _HIT_CURSOR.get(hit)
                if cursor_id is None:
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    cursor_id = _HIT_CURSOR.get(self._hit(hwnd, pt.x, pt.y))
                if cursor_id is not None:
                    user32.SetCursor(user32.LoadCursorW(None, cursor_id))
                    return True, 0
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        return False, 0


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
        # 边缘缩放光标：抢在 Qt 的光标逻辑之前接管 WM_SETCURSOR，
        # 否则鼠标压在图表/按钮等带光标的子控件上时会被改回箭头。
        self._edge_filter: _EdgeCursorFilter | None = None
        self._style_timer: QTimer | None = None
        app = QApplication.instance()
        if app is not None:
            self._edge_filter = _EdgeCursorFilter()
            app.installNativeEventFilter(self._edge_filter)
        self.resize(1180, 780)
        # 下限放低：各页面用滚动区/按比例布局兜底，窗口可以自由拖小。
        self.setMinimumSize(760, 500)
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
        # 已最大化的窗口保持最大化，只在最小化时恢复。无条件
        # showNormal() 会让页面切换与窗口尺寸变化在同一时刻竞争布局。
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
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
        if event.type() == QEvent.WinIdChange:
            self._enable_native_resize()
            self._sync_edge_filter()
        elif event.type() == QEvent.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.refresh_state()

    def _sync_edge_filter(self) -> None:
        """把当前原生句柄告诉过滤器（过滤器内部不能回调 Qt）。"""
        if self._edge_filter is not None:
            try:
                self._edge_filter.set_hwnd(int(self.winId()))
            except (RuntimeError, TypeError, ValueError):
                self._edge_filter.set_hwnd(0)

    def _enable_native_resize(self) -> None:
        """给无边框窗口补上 WS_THICKFRAME，Windows 才会真正进入缩放循环。

        WM_NCHITTEST 返回 HT* 只让系统知道“这里是缩放区”（光标也随之改变），
        但按下拖拽时 DefWindowProc 需要窗口带可调整边框样式才会启动缩放。
        样式加上后客户区会被系统内缩，因此 nativeEvent 里用 WM_NCCALCSIZE
        把客户区仍保持为整个窗口，视觉上与之前完全一致。
        """
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
        except (TypeError, ValueError):
            return
        if not hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
            if style & _WS_THICKFRAME:
                return
            user32.SetWindowLongW(hwnd, _GWL_STYLE, style | _WS_THICKFRAME)
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
            )
        except (AttributeError, OSError):
            pass

    def _resize_hit(self, pos: QPoint | None = None) -> int | None:
        """鼠标是否落在窗口边缘/角上；是则返回 HT* 命中码，否则 None。

        pos 为窗口内坐标，默认取当前光标位置（供测试传入固定点）。
        """
        if self.isMaximized():
            return None
        if pos is None:
            pos = self.mapFromGlobal(QCursor.pos())
        e = _RESIZE_EDGE
        left = pos.x() < e
        right = pos.x() >= self.width() - e
        top = pos.y() < e
        bottom = pos.y() >= self.height() - e
        if left and top:
            return 13  # HTTOPLEFT
        if right and top:
            return 14  # HTTOPRIGHT
        if left and bottom:
            return 16  # HTBOTTOMLEFT
        if right and bottom:
            return 17  # HTBOTTOMRIGHT
        if left:
            return 10  # HTLEFT
        if right:
            return 11  # HTRIGHT
        if top:
            return 12  # HTTOP
        if bottom:
            return 15  # HTBOTTOM
        return None

    def nativeEvent(self, event_type, message):
        """无边框窗口：恢复四边/四角的原生缩放命中区、缩放光标和客户区。

        - WM_NCCALCSIZE：客户区保持为整个窗口（WS_THICKFRAME 不产生原生边框）
        - WM_NCHITTEST：边缘/四角返回 HT* 命中码
        - WM_SETCURSOR：Qt 会把光标改回箭头，这里按命中码显式设置缩放光标
        """
        if sys.platform == "win32":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == _WM_NCCALCSIZE:
                    if msg.wParam:
                        # 返回 0：客户区 = 窗口矩形（去掉原生边框，外观不变）
                        return True, 0
                elif msg.message == _WM_NCHITTEST:
                    hit = self._resize_hit()
                    if hit is not None:
                        return True, hit
                elif msg.message == _WM_SETCURSOR:
                    # 命中码在 lParam 低字（Windows 依据我们的 WM_NCHITTEST 得到）
                    hit = msg.lParam & 0xFFFF
                    cursor_id = _HIT_CURSOR.get(hit)
                    if cursor_id is not None and not self.isMaximized():
                        user32 = ctypes.windll.user32
                        user32.SetCursor(user32.LoadCursorW(None, cursor_id))
                        return True, 1
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
        self._enable_native_resize()
        self._sync_edge_filter()
        if self._style_timer is None:
            # 兜底自愈：个别路径下 Qt 会重置窗口样式（丢掉 WS_THICKFRAME），
            # 那时边缘就没有缩放命中区。低频检查并补回。
            self._style_timer = QTimer(self)
            self._style_timer.timeout.connect(self._enable_native_resize)
            self._style_timer.start(2000)

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
