"""DiskWatch 的主题令牌、动态样式与程序化图标。"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
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
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QWidget


@dataclass(frozen=True)
class ThemeTokens:
    """UI 只依赖语义角色，不直接判断深浅主题。"""

    name: str
    dark: bool
    window: str
    surface: str
    surface_raised: str
    field: str
    base: str
    button: str
    button_hover: str
    text: str
    text_dim: str
    text_muted: str
    border: str
    border_strong: str
    accent: str
    accent_hover: str
    accent_soft: str
    success: str
    warning: str
    danger: str
    floating_top: str
    floating_bottom: str
    shadow: str

    def color(self, role: str) -> QColor:
        return QColor(getattr(self, role))


LIGHT_TOKENS = ThemeTokens(
    "light", False, "#f5f6f8", "#ffffff", "#f9fafb", "#f0f2f5",
    "#ffffff", "#eef1f5", "#e2e7ee", "#1d232d", "#5f6876",
    "#8a93a1", "#dde2e9", "#cbd2dc", "#1677ff", "#0b66df",
    "#dcecff", "#16866f", "#b76819", "#c43b42", "#ffffff",
    "#f5f7fa", "#26000000",
)

DARK_TOKENS = ThemeTokens(
    "dark", True, "#111316", "#181b20", "#20242a", "#292e36",
    "#15181c", "#2b3038", "#383e48", "#f2f4f7", "#aeb6c2",
    "#7f8997", "#303640", "#414955", "#4b95ff", "#6aa8ff",
    "#203b61", "#53bda5", "#e2a15c", "#ef7278", "#20242a",
    "#171a1f", "#80000000",
)

_ACTIVE = DARK_TOKENS


def theme_tokens() -> ThemeTokens:
    return _ACTIVE


def _rgba(color: QColor | str, alpha: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def widget_qss(tokens: ThemeTokens | None = None) -> str:
    t = tokens or theme_tokens()
    hover = _rgba(t.text, 22 if t.dark else 14)
    track = _rgba(t.text, 38)
    track_hover = _rgba(t.text, 62)
    return f"""
QLabel {{ color: {t.text}; background: transparent; }}
QLabel#title  {{ color: {t.text_dim}; font-size: 11px; letter-spacing: 1px; }}
QLabel#count  {{ color: {t.text}; font-size: 34px; font-weight: 600; }}
QLabel#unit   {{ color: {t.text_dim}; font-size: 12px; }}
QLabel#sub    {{ color: {t.text_dim}; font-size: 11px; }}
QLabel#fname  {{ color: {t.text}; font-size: 11px; }}
QLabel#fmeta  {{ color: {t.text_dim}; font-size: 10px; }}
QLabel#dot    {{ color: {t.success}; font-size: 14px; }}
QPushButton#tool {{ color: {t.text_dim}; background: {hover}; border: none;
 border-radius: 8px; padding: 5px 10px; font-size: 11px; }}
QPushButton#tool:hover {{ background: {t.button_hover}; color: {t.text}; }}
QPushButton#close {{ color: {t.text_dim}; background: transparent; border: none;
 border-radius: 8px; font-size: 16px; font-weight: 600; padding: 0;
 min-width: 28px; min-height: 28px; }}
QPushButton#close:hover {{ color: {t.text}; background: {hover}; }}
QScrollArea#recentScroll, QWidget#recentHost {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px 0; }}
QScrollBar::handle:vertical {{ background: {track}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {track_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


def panel_qss(tokens: ThemeTokens | None = None) -> str:
    t = tokens or theme_tokens()
    selection = _rgba(t.accent, 72)
    subtle = _rgba(t.text, 14 if t.dark else 10)
    divider = _rgba(t.text, 20 if t.dark else 15)
    scroll = _rgba(t.text, 38)
    return f"""
QWidget#panelRoot, QDialog, QMessageBox {{ background: {t.window}; color: {t.text}; }}
QLabel {{ color: {t.text}; background: transparent; }}
QScrollArea, QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; border: none; }}
QLabel#h1 {{ font-size: 18px; font-weight: 600; }}
QLabel#dim {{ color: {t.text_dim}; font-size: 12px; }}
QLabel#statValue {{ font-size: 20px; font-weight: 600; }}
QLabel#banner {{ color: {t.warning}; font-size: 12px; background: {_rgba(t.warning, 26)};
 border: 1px solid {_rgba(t.warning, 82)}; border-radius: 8px; padding: 7px 10px; }}
QFrame#card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
QLineEdit, QPushButton#dayPicker {{ background: {t.field}; color: {t.text};
 border: 1px solid {t.border}; border-radius: 8px; padding: 6px 9px; min-height: 20px; }}
QPushButton#dayPicker {{ text-align: left; padding-right: 22px; }}
QPushButton#dayPicker:hover {{ background: {t.button_hover}; }}
QListWidget#dayPickerPopup {{ background: {t.surface}; color: {t.text};
 selection-background-color: {t.accent}; border: 1px solid {t.border_strong};
 border-radius: 8px; outline: none; }}
QListWidget#dayPickerPopup::item {{ padding: 7px 10px; }}
QListWidget#dayPickerPopup::item:selected {{ background: {t.accent}; color: #ffffff; }}
QPushButton {{ background: {t.button}; color: {t.text}; border: 1px solid {t.border};
 border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: {t.button_hover}; }}
QPushButton:focus {{ border: 1px solid {t.accent}; }}
QPushButton#primary {{ background: {t.accent}; border: none; color: #ffffff; }}
QPushButton#primary:hover {{ background: {t.accent_hover}; }}
QTableWidget, QTableView, QTreeView {{ background: {t.base};
 alternate-background-color: {t.surface_raised}; color: {t.text}; gridline-color: {divider};
 border: 1px solid {t.border}; border-radius: 10px; selection-background-color: {selection}; }}
QHeaderView::section {{ background: {t.field}; color: {t.text_dim}; border: none;
 border-bottom: 1px solid {t.border}; padding: 7px; font-weight: 500; }}
QTableWidget::item, QTableView::item, QTreeView::item {{ padding: 5px 7px; }}
QTreeView::branch {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {scroll}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QCheckBox, QSpinBox, QPlainTextEdit, QListWidget {{ color: {t.text}; }}
QCheckBox {{ spacing: 7px; padding: 2px 0; }}
QPlainTextEdit, QListWidget {{ background: {t.base}; border: 1px solid {t.border};
 border-radius: 8px; padding: 5px; }}
QListWidget::item {{ padding: 4px 5px; }}
QListWidget::item:selected {{ background: {selection}; }}
QListWidget#settingsNav {{ background: {t.surface}; border: 1px solid {t.border};
 border-radius: 10px; padding: 6px; outline: none; }}
QListWidget#settingsNav::item {{ color: {t.text_dim}; border: none;
 border-radius: 7px; padding: 8px 10px; margin: 1px 0; outline: none; }}
QListWidget#settingsNav::item:hover {{ color: {t.text}; background: {t.button}; }}
QListWidget#settingsNav::item:selected {{ color: {t.accent};
 background: {t.accent_soft}; border: none; outline: none; }}
QListWidget#settingsNav::item:focus {{ border: none; outline: none; }}
QSpinBox {{ background: {t.field}; border: 1px solid {t.border};
 border-radius: 8px; padding: 5px 7px; }}
QTabWidget::pane {{ background: {t.surface}; border: 1px solid {t.border};
 border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {t.text_dim}; padding: 8px 16px; border: none; }}
QTabBar::tab:hover {{ color: {t.text}; background: {subtle}; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
 padding: 5px 7px; }}
"""


def range_button_qss(tokens: ThemeTokens | None = None) -> str:
    t = tokens or theme_tokens()
    return f"""
QPushButton#rangeBtn {{ color: {t.text_dim}; background: {t.button}; border: none;
 border-radius: 8px; padding: 5px 10px; font-size: 11px; }}
QPushButton#rangeBtn:hover {{ background: {t.button_hover}; color: {t.text}; }}
QPushButton#rangeBtn:checked {{ background: {t.accent}; color: #ffffff; }}
"""


# 兼容外部脚本；应用内部使用动态函数。
WIDGET_QSS = widget_qss(DARK_TOKENS)
PANEL_QSS = panel_qss(DARK_TOKENS)
ACCENT = DARK_TOKENS.color("accent")
ACCENT_2 = QColor("#79b4ff")
TEXT = DARK_TOKENS.color("text")
TEXT_DIM = DARK_TOKENS.color("text_dim")
OK = DARK_TOKENS.color("success")
WARN = DARK_TOKENS.color("warning")
BG_TOP = DARK_TOKENS.color("floating_top")
BG_BOTTOM = DARK_TOKENS.color("floating_bottom")
BORDER = DARK_TOKENS.color("border")
DIM_FG = DARK_TOKENS.color("text_muted")
GROUP_FG = DARK_TOKENS.color("text")


def _sync_compat_colors(t: ThemeTokens) -> None:
    """让旧的 QColor 导入也能随主题变化，逐步淘汰后可删除。"""
    mapping = (
        (ACCENT, "accent"), (ACCENT_2, "accent_hover"), (TEXT, "text"),
        (TEXT_DIM, "text_dim"), (OK, "success"), (WARN, "warning"),
        (BG_TOP, "floating_top"), (BG_BOTTOM, "floating_bottom"),
        (BORDER, "border"), (DIM_FG, "text_muted"), (GROUP_FG, "text"),
    )
    for color, role in mapping:
        color.setRgba(QColor(getattr(t, role)).rgba())


def enable_titlebar(widget: QWidget, dark: bool | None = None) -> None:
    """同步 Windows 标题栏主题，并为窗口启用 Windows 11 圆角。"""
    if sys.platform != "win32" or widget is None:
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return
    if not hwnd:
        return
    value = ctypes.c_int(1 if (theme_tokens().dark if dark is None else dark) else 0)
    for attr in (20, 19):
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass
    # Windows 11: 圆角偏好。无边框主窗口也可获得与系统一致的外轮廓。
    corner = ctypes.c_int(2)  # DWMWCP_ROUND
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner)
        )
    except Exception:
        pass


class _ThemeWindowFilter(QObject):
    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Show, QEvent.WinIdChange) and isinstance(obj, QWidget):
            if obj.isWindow() and not obj.windowFlags() & Qt.FramelessWindowHint:
                enable_titlebar(obj)
        return False


def prefer_ui_font(app) -> None:
    available = set(QFontDatabase.families())
    for name in (
        "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "PingFang SC",
        "Noto Sans CJK SC", "Source Han Sans SC", "SimHei",
    ):
        if name in available:
            app.setFont(QFont(name, 10))
            return


class _CheckStyle(QProxyStyle):
    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element != QStyle.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return
        t = theme_tokens()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect
        box = QRectF(r.x() + 0.5, r.y() + 0.5, r.width() - 1, r.height() - 1)
        hovered = bool(option.state & QStyle.State_MouseOver)
        checked = bool(option.state & QStyle.State_On)
        if checked:
            # 选中：填充主题色再画白勾。空心框上的白勾在浅色底上完全看不见，
            # 那样看起来就只是“变了下颜色/边框”。
            painter.setPen(Qt.NoPen)
            painter.setBrush(t.color("accent"))
            painter.drawRoundedRect(box, 4, 4)
            pen = QPen(QColor("#ffffff"), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            x, y, w, h = r.x(), r.y(), r.width(), r.height()
            path.moveTo(x + w * 0.24, y + h * 0.54)
            path.lineTo(x + w * 0.44, y + h * 0.72)
            path.lineTo(x + w * 0.78, y + h * 0.32)
            painter.drawPath(path)
        else:
            border = t.color("text_dim") if hovered else t.color("border_strong")
            painter.setPen(QPen(border, 1.4))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box, 4, 4)
        painter.restore()

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return 16
        return super().pixelMetric(metric, option, widget)


class ThemeController(QObject):
    """解析 system/light/dark，并把变化广播给已打开窗口。"""

    changed = Signal(str)

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.app = app
        self.mode = "system"
        self._filter = _ThemeWindowFilter(self)
        app.installEventFilter(self._filter)
        try:
            app.styleHints().colorSchemeChanged.connect(self._system_changed)
        except Exception:
            pass

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in {"system", "light", "dark"} else "system"
        self._apply()

    def _system_changed(self, _scheme) -> None:
        if self.mode == "system":
            self._apply()

    def _resolved(self) -> ThemeTokens:
        if self.mode == "dark":
            return DARK_TOKENS
        if self.mode == "light":
            return LIGHT_TOKENS
        try:
            scheme = self.app.styleHints().colorScheme()
            return DARK_TOKENS if scheme == Qt.ColorScheme.Dark else LIGHT_TOKENS
        except Exception:
            return LIGHT_TOKENS

    def _apply(self) -> None:
        global _ACTIVE
        target = self._resolved()
        _ACTIVE = target
        _sync_compat_colors(target)
        self.app.setPalette(_palette(target))
        for widget in self.app.topLevelWidgets():
            hook = getattr(widget, "apply_theme", None)
            if callable(hook):
                hook()
            # 悬浮卡片/迷你球是无边框自绘窗口，没有原生标题栏；给它们设置
            # DWM 圆角/阴影属性会让系统在矩形外轮廓上画出与胶囊弧度不吻合的
            # 阴影（四角出现圆弧）。与 _ThemeWindowFilter 保持一致：只处理有框窗口。
            if widget.isWindow() and not widget.windowFlags() & Qt.FramelessWindowHint:
                enable_titlebar(widget, target.dark)
            widget.update()
        self.changed.emit(target.name)


def _palette(t: ThemeTokens) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, t.color("window"))
    pal.setColor(QPalette.WindowText, t.color("text"))
    pal.setColor(QPalette.Base, t.color("base"))
    pal.setColor(QPalette.AlternateBase, t.color("surface_raised"))
    pal.setColor(QPalette.Text, t.color("text"))
    pal.setColor(QPalette.Button, t.color("button"))
    pal.setColor(QPalette.ButtonText, t.color("text"))
    pal.setColor(QPalette.ToolTipBase, t.color("surface"))
    pal.setColor(QPalette.ToolTipText, t.color("text"))
    pal.setColor(QPalette.PlaceholderText, t.color("text_muted"))
    pal.setColor(QPalette.Highlight, t.color("accent"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, t.color("accent"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, t.color("text_muted"))
    return pal


def install_theme(app: QApplication, mode: str = "system") -> ThemeController:
    controller = app.property("diskwatchThemeController")
    if not isinstance(controller, ThemeController):
        app.setStyle("Fusion")
        app.setStyle(_CheckStyle(app.style()))
        prefer_ui_font(app)
        controller = ThemeController(app)
        app.setProperty("diskwatchThemeController", controller)
    controller.set_mode(mode)
    return controller


def apply_dark_theme(app) -> None:
    """兼容测试与旧工具的固定深色入口。"""
    install_theme(app, "dark")


_ICON_CACHE: QIcon | None = None


def set_app_user_model_id(app_id: str = "DiskWatch.Desktop") -> None:
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
    grad = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    grad.setColorAt(0.0, QColor("#1677ff"))
    grad.setColorAt(1.0, QColor("#64a7ff"))
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(pad, pad, size - 2 * pad, size - 2 * pad),
        size * 0.28, size * 0.28,
    )
    p.fillPath(path, grad)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f7faff"))
    r, c = size * 0.30, size / 2
    p.drawEllipse(QPointF(c, c), r, r)
    p.setBrush(QColor("#1d4f91"))
    p.drawEllipse(QPointF(c, c), r * 0.30, r * 0.30)
    p.setBrush(QColor("#42b89b"))
    p.drawEllipse(QPointF(size * 0.76, size * 0.76), size * 0.11, size * 0.11)
    p.end()
    return pm


def app_icon(size: int = 64) -> QIcon:
    global _ICON_CACHE
    if _ICON_CACHE is None:
        icon = QIcon()
        for s in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(_paint_app_pixmap(s))
        _ICON_CACHE = icon
    return _ICON_CACHE


def apply_window_icon(widget: QWidget) -> None:
    widget.setWindowIcon(app_icon())
