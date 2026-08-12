"""配色、样式表与程序化生成的图标（不依赖任何图片资源）。

偏亮的科技冷蓝：表面带一点蓝调，强调色用清晰蓝 + 浅青辅色（同冷色相），
文字提亮，避免发闷的灰紫。
"""

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
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QProxyStyle, QStyle, QWidget

# ---------- 色板 ----------

# 卡片渐变：略提亮，带冷蓝底色
BG_TOP = QColor(46, 56, 82, 232)
BG_BOTTOM = QColor(30, 38, 58, 238)
BORDER = QColor(140, 180, 255, 36)

# 科技蓝主色 + 同冷色相浅青辅色（环/渐变用）
ACCENT = QColor(86, 152, 255)
ACCENT_2 = QColor(112, 210, 236)
ACCENT_HOVER = QColor(112, 170, 255)

TEXT = "#e8eef8"
TEXT_DIM = "#a8b6cc"
SURFACE = "#161e2e"
SURFACE_2 = "#1e2840"
FIELD = "#28344c"
BASE = "#182234"
BUTTON = "#2c3850"
BUTTON_HOVER = "#3a4864"

# 状态点：青绿 / 柔橙，亮度跟上整体
OK = QColor(72, 204, 178)
WARN = QColor(232, 148, 118)

# 详情树：与正文同一阶梯
DIM_FG = QColor("#96a4bc")
GROUP_FG = QColor("#d0daf0")

_AR, _AG, _AB = ACCENT.red(), ACCENT.green(), ACCENT.blue()
_SEL = f"rgba({_AR},{_AG},{_AB},0.32)"

WIDGET_QSS = f"""
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel#title  {{ color: {TEXT_DIM}; font-size: 11px; letter-spacing: 1px; }}
QLabel#count  {{ color: {TEXT}; font-size: 34px; font-weight: 600; }}
QLabel#unit   {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#sub    {{ color: {TEXT_DIM}; font-size: 11px; }}
QLabel#fname  {{ color: {TEXT}; font-size: 11px; }}
QLabel#fmeta  {{ color: {TEXT_DIM}; font-size: 10px; }}
QLabel#dot    {{ color: {OK.name()}; font-size: 14px; }}

QPushButton#tool {{
    color: {TEXT_DIM};
    background: rgba(255,255,255,0.05);
    border: none; border-radius: 6px;
    padding: 4px 10px; font-size: 11px;
}}
QPushButton#tool:hover {{ background: rgba(255,255,255,0.10); color: {TEXT}; }}
QPushButton#close {{
    color: {TEXT_DIM}; background: transparent; border: none;
    border-radius: 6px; font-size: 16px; font-weight: 600;
    padding: 0px; min-width: 28px; min-height: 28px;
}}
QPushButton#close:hover {{
    color: {TEXT}; background: rgba(255,255,255,0.10);
}}

QScrollArea#recentScroll {{
    background: transparent; border: none;
}}
QWidget#recentHost {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 6px; margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.16); border-radius: 3px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.26); }}
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
QLabel#banner {{
    color: #f5c97b; font-size: 12px;
    background: rgba(232, 148, 118, 0.12);
    border: 1px solid rgba(232, 148, 118, 0.35);
    border-radius: 6px; padding: 6px 10px;
}}
QFrame#card {{
    background: {SURFACE_2}; border: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px;
}}
QLineEdit, QPushButton#dayPicker {{
    background: {FIELD}; color: {TEXT};
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px; padding: 5px 8px; min-height: 20px;
}}
QPushButton#dayPicker {{
    text-align: left; padding-right: 22px;
}}
QPushButton#dayPicker:hover {{ background: {BUTTON}; }}
QListWidget#dayPickerPopup {{
    background: {FIELD}; color: {TEXT};
    selection-background-color: {ACCENT.name()};
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px; outline: none;
}}
QListWidget#dayPickerPopup::item {{
    padding: 6px 10px;
}}
QListWidget#dayPickerPopup::item:selected {{
    background: {ACCENT.name()};
}}
QPushButton {{
    background: {BUTTON}; color: {TEXT};
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ background: {BUTTON_HOVER}; }}
QPushButton#primary {{
    background: {ACCENT.name()}; border: none; color: {TEXT};
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER.name()}; }}
QTableWidget, QTableView, QTreeView {{
    background: {BASE}; alternate-background-color: {SURFACE_2};
    color: {TEXT}; gridline-color: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.05); border-radius: 8px;
    selection-background-color: {_SEL};
}}
QHeaderView::section {{
    background: {FIELD}; color: {TEXT_DIM};
    border: none; border-bottom: 1px solid rgba(255,255,255,0.06);
    padding: 6px; font-weight: 500;
}}
QTableWidget::item, QTableView::item, QTreeView::item {{ padding: 4px 6px; }}
QTreeView::branch {{
    background: transparent;
}}
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    border-image: none;
    image: none;
}}
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {{
    border-image: none;
    image: none;
}}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.14); border-radius: 4px; min-height: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
QCheckBox, QSpinBox, QPlainTextEdit, QListWidget {{ color: {TEXT}; }}
QCheckBox {{ spacing: 7px; padding: 2px 0px; }}
QPlainTextEdit, QListWidget {{
    background: {BASE}; border: 1px solid rgba(255,255,255,0.06);
    border-radius: 6px; padding: 4px;
}}
QListWidget::item {{ padding: 3px 4px; }}
QListWidget::item:selected {{ background: {_SEL}; }}
QSpinBox {{
    background: {FIELD}; border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px; padding: 4px 6px;
}}
QTabWidget::pane {{
    background: {SURFACE_2};
    border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 7px 16px; border: none;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT.name()}; }}
QToolTip {{
    background: {FIELD}; color: {TEXT};
    border: 1px solid rgba(255,255,255,0.12); padding: 4px 6px;
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

    def eventFilter(self, obj, event) -> bool:
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


class _CheckStyle(QProxyStyle):
    """深色主题下的勾选框：空心圆角框 + 勾号（不用填充块）。

    样式表没法在 indicator 里画勾号（会被 ACCENT 整块填充吞掉），
    所以 indicator 的绘制交给这里：未选=空心框，选中=蓝色框 + 白色勾号。
    """

    _BORDER = QColor(255, 255, 255, 56)     # rgba(255,255,255,0.22)
    _BORDER_HOVER = QColor(255, 255, 255, 97)
    _CHECK = QColor(255, 255, 255, 235)

    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option,
        painter,
        widget=None,
    ) -> None:
        if element != QStyle.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect
        box = QRectF(r.x() + 0.5, r.y() + 0.5, r.width() - 1, r.height() - 1)
        hovered = bool(option.state & QStyle.State_MouseOver)
        checked = bool(option.state & QStyle.State_On)

        border = ACCENT if checked else (
            self._BORDER_HOVER if hovered else self._BORDER
        )
        painter.setPen(QPen(border, 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box, 4, 4)

        if checked:
            pen = QPen(self._CHECK, 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            x, y = r.x(), r.y()
            w, h = r.width(), r.height()
            path.moveTo(x + w * 0.24, y + h * 0.54)
            path.lineTo(x + w * 0.44, y + h * 0.72)
            path.lineTo(x + w * 0.78, y + h * 0.32)
            painter.drawPath(path)
        painter.restore()

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric == QStyle.PM_IndicatorWidth:
            return 16
        if metric == QStyle.PM_IndicatorHeight:
            return 16
        return super().pixelMetric(metric, option, widget)


def apply_dark_theme(app) -> None:
    """统一深色调色板。

    仅靠样式表不够：QMessageBox、QSpinBox 按钮等控件的部分绘制走调色板，
    不设的话在浅色系统主题下会出现浅字压浅底。
    """
    app.setStyle("Fusion")
    # 勾选框由代理样式绘制（QSS 无法在 indicator 里画勾号）
    app.setStyle(_CheckStyle(app.style()))
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
    pal.setColor(QPalette.Base, QColor(BASE))
    pal.setColor(QPalette.AlternateBase, QColor(SURFACE_2))
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, QColor(BUTTON))
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.ToolTipBase, QColor(FIELD))
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    pal.setColor(QPalette.Highlight, ACCENT)
    pal.setColor(QPalette.HighlightedText, text)
    pal.setColor(QPalette.Link, ACCENT_2)
    disabled = QColor("#6e7588")
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
    disc = QColor(TEXT)
    disc.setAlpha(230)
    p.setBrush(disc)
    r = size * 0.30
    c = size / 2
    p.drawEllipse(QPointF(c, c), r, r)
    p.setBrush(QColor(SURFACE))
    p.drawEllipse(QPointF(c, c), r * 0.30, r * 0.30)

    p.setBrush(OK)
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
