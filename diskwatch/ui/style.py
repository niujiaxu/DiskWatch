"""配色、样式表与程序化生成的图标（不依赖任何图片资源）。"""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

BG_TOP = QColor(30, 32, 44, 235)
BG_BOTTOM = QColor(20, 21, 30, 238)
BORDER = QColor(255, 255, 255, 28)
ACCENT = QColor(124, 108, 255)
ACCENT_2 = QColor(88, 191, 255)
TEXT = "#eceef6"
TEXT_DIM = "#aab0c2"
SURFACE = "#16171f"
SURFACE_2 = "#1e202b"
FIELD = "#23252f"

WIDGET_QSS = f"""
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel#title  {{ color: {TEXT_DIM}; font-size: 11px; letter-spacing: 1px; }}
QLabel#count  {{ color: {TEXT}; font-size: 34px; font-weight: 700; }}
QLabel#unit   {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#sub    {{ color: {TEXT_DIM}; font-size: 11px; }}
QLabel#fname  {{ color: {TEXT}; font-size: 11px; }}
QLabel#fmeta  {{ color: {TEXT_DIM}; font-size: 10px; }}
QLabel#dot    {{ color: #57d9a3; font-size: 14px; }}

QPushButton#tool {{
    color: {TEXT_DIM};
    background: rgba(255,255,255,0.06);
    border: none; border-radius: 6px;
    padding: 4px 10px; font-size: 11px;
}}
QPushButton#tool:hover {{ background: rgba(255,255,255,0.14); color: {TEXT}; }}
QPushButton#close {{
    color: {TEXT_DIM}; background: transparent; border: none;
    border-radius: 6px; font-size: 16px; font-weight: 600;
    padding: 0px; min-width: 28px; min-height: 28px;
}}
QPushButton#close:hover {{
    color: {TEXT}; background: rgba(255,255,255,0.12);
}}

QScrollArea#recentScroll {{
    background: transparent; border: none;
}}
QWidget#recentHost {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.22); border-radius: 3px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.35); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0; border: none; background: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""

PANEL_QSS = f"""
/* 对话框默认是浅色底，而这里的文字是给深色底配的，
   所以必须把窗口自身也刷成深色，否则浅字压浅底完全看不见。 */
QWidget#panelRoot, QDialog, QMessageBox {{ background: {SURFACE}; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel#h1 {{ font-size: 17px; font-weight: 600; }}
QLabel#dim {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#statValue {{ font-size: 20px; font-weight: 600; }}
QFrame#card {{
    background: #1e202b; border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
}}
QComboBox, QLineEdit {{
    background: #23252f; color: {TEXT};
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 6px; padding: 5px 8px; min-height: 20px;
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: #23252f; color: {TEXT};
    selection-background-color: {ACCENT.name()};
    border: 1px solid rgba(255,255,255,0.10);
}}
QPushButton {{
    background: #2a2d3a; color: {TEXT};
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ background: #343849; }}
QPushButton#primary {{ background: {ACCENT.name()}; border: none; color: white; }}
QPushButton#primary:hover {{ background: #8b7cff; }}
QTableWidget {{
    background: #1a1c25; alternate-background-color: #1e202b;
    color: {TEXT}; gridline-color: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;
    selection-background-color: rgba(124,108,255,0.35);
}}
QHeaderView::section {{
    background: #23252f; color: {TEXT_DIM};
    border: none; border-bottom: 1px solid rgba(255,255,255,0.08);
    padding: 6px; font-weight: 500;
}}
QTableWidget::item {{ padding: 4px 6px; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.18); border-radius: 4px; min-height: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
QCheckBox, QSpinBox, QPlainTextEdit, QListWidget {{ color: {TEXT}; }}
QCheckBox {{ spacing: 7px; padding: 2px 0px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid rgba(255,255,255,0.28); background: {FIELD};
}}
QCheckBox::indicator:hover {{ border-color: rgba(255,255,255,0.5); }}
QCheckBox::indicator:checked {{
    background: {ACCENT.name()}; border-color: {ACCENT.name()};
}}
QPlainTextEdit, QListWidget {{
    background: #1a1c25; border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px; padding: 4px;
}}
QListWidget::item {{ padding: 3px 4px; }}
QListWidget::item:selected {{ background: rgba(124,108,255,0.35); }}
QSpinBox {{
    background: {FIELD}; border: 1px solid rgba(255,255,255,0.10);
    border-radius: 6px; padding: 4px 6px;
}}
QTabWidget::pane {{
    background: {SURFACE_2};
    border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 7px 16px; border: none;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT.name()}; }}
QToolTip {{
    background: {FIELD}; color: {TEXT};
    border: 1px solid rgba(255,255,255,0.15); padding: 4px 6px;
}}
"""


def enable_dark_titlebar(widget: QWidget) -> None:
    """让 Windows 原生标题栏跟深色主题走，去掉刺眼的白条。

    Win10 1903+ / Win11 通过 DWMWA_USE_IMMERSIVE_DARK_MODE 生效。
    """
    if sys.platform != "win32" or widget is None:
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return
    if not hwnd:
        return
    value = ctypes.c_int(1)
    dwm = ctypes.windll.dwmapi
    # 20 = 新常量；19 = 旧预览版常量。两个都试，兼容不同系统版本。
    for attr in (20, 19):
        try:
            dwm.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass


class _DarkTitleBarFilter(QObject):
    """顶层窗口一显示就刷深色标题栏（含设置、详情、消息框）。"""

    _instance: "_DarkTitleBarFilter | None" = None

    @classmethod
    def instance(cls) -> "_DarkTitleBarFilter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Show, QEvent.WinIdChange) and isinstance(obj, QWidget):
            if obj.isWindow() and not obj.windowFlags() & Qt.FramelessWindowHint:
                enable_dark_titlebar(obj)
        return False


def prefer_ui_font(app) -> None:
    """优先选用带中文的系统字体，避免默认西文字体把汉字渲成方框。"""
    available = set(QFontDatabase.families())
    for name in (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Segoe UI",
        "PingFang SC",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "SimHei",
    ):
        if name in available:
            font = QFont(name, 10)
            app.setFont(font)
            return


def apply_dark_theme(app) -> None:
    """统一深色调色板。

    仅靠样式表不够：QMessageBox、QSpinBox 按钮等控件的部分绘制走调色板，
    不设的话在浅色系统主题下会出现浅字压浅底。
    """
    app.setStyle("Fusion")
    prefer_ui_font(app)
    # Qt 6.5+：告诉系统本应用偏好深色，部分原生控件/标题栏会跟着变
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass

    pal = QPalette()
    text = QColor(TEXT)
    pal.setColor(QPalette.Window, QColor(SURFACE))
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, QColor("#1a1c25"))
    pal.setColor(QPalette.AlternateBase, QColor(SURFACE_2))
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, QColor("#2a2d3a"))
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.ToolTipBase, QColor(FIELD))
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    pal.setColor(QPalette.Highlight, ACCENT)
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, ACCENT_2)
    disabled = QColor("#767b8c")
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, disabled)
    app.setPalette(pal)

    filt = _DarkTitleBarFilter.instance()
    app.installEventFilter(filt)
    # 已经创建的顶层窗口也补刷一次
    for w in app.topLevelWidgets():
        if w.isWindow():
            enable_dark_titlebar(w)


_ICON_CACHE: QIcon | None = None


def set_app_user_model_id(app_id: str = "DiskWatch.Desktop") -> None:
    """让 Windows 任务栏用我们的窗口图标，而不是 python.exe 自带图标。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _paint_app_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    pad = max(1, size // 32)
    radius = size * 0.28
    grad = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    grad.setColorAt(0.0, ACCENT)
    grad.setColorAt(1.0, ACCENT_2)

    path = QPainterPath()
    path.addRoundedRect(
        QRectF(pad, pad, size - 2 * pad, size - 2 * pad), radius, radius
    )
    p.fillPath(path, grad)

    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 235))
    r = size * 0.30
    c = size / 2
    p.drawEllipse(QPointF(c, c), r, r)
    p.setBrush(QColor(40, 42, 60))
    p.drawEllipse(QPointF(c, c), r * 0.30, r * 0.30)

    p.setBrush(QColor(87, 217, 163))
    p.drawEllipse(QPointF(size * 0.76, size * 0.76), size * 0.11, size * 0.11)
    p.end()
    return pm


def app_icon(size: int = 64) -> QIcon:
    """多尺寸程序图标（任务栏 / 标题栏 / 托盘），不依赖外部 .ico 文件。"""
    global _ICON_CACHE
    if _ICON_CACHE is not None:
        return _ICON_CACHE
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_paint_app_pixmap(s))
    _ICON_CACHE = icon
    return icon


def apply_window_icon(widget: QWidget) -> None:
    """给顶层窗口挂上应用图标（改 windowFlags 之后要再调一次）。"""
    widget.setWindowIcon(app_icon())


def mono_font(size: int = 10) -> QFont:
    f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(size)
    return f
