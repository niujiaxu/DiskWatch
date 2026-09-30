"""迷你胶囊：收起状态显示今日净空间变化。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QFont,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from ..i18n import tr
from ..storage import Storage, human_size, today_str
from ..watcher import FileMonitor
from .style import menu_icon, polish_menu, theme_tokens

REFRESH_MS = 2000
DRAG_SLOP = 4  # 位移小于这个值算点击，不算拖动
RING_DAYS = 7


class MiniBall(QWidget):
    expand_requested = Signal()
    open_panel = Signal()
    open_dashboard = Signal()
    open_settings = Signal()
    request_quit = Signal()
    hidden_by_user = Signal()

    WIDTH = 132
    HEIGHT = 44

    def __init__(self, storage: Storage, monitor: FileMonitor, config) -> None:
        super().__init__()
        self._storage = storage
        self._monitor = monitor
        self._config = config

        self._count = 0
        self._size_total = 0
        self._period_total = 0
        self._ratio = 0.0
        self._delta = 0
        self._attribution_pct = 0
        self._hover = False
        self._press_pos: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._moved = False

        # NoDropShadowWindowHint：抑制 DWM 给矩形窗口加的投影，
        # 否则胶囊四角透明处会露出与弧度不吻合的系统阴影。
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        self._signature: tuple | None = None

        # 同卡片一样：隐藏时不跑定时器，数据没变化时不重绘
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

        self._restore_geometry()
        self.refresh(initial=True)

    def set_storage(self, storage: Storage) -> None:
        """换用新的数据库连接（位置变更失败回滚时由宿主调用）。"""
        self._storage = storage
        self.refresh()

    # ---------- 数据 ----------

    def refresh(self, initial: bool = False) -> None:
        count, total = self._storage.day_stats(today_str())
        period = max(self._storage.period_total_size(RING_DAYS), 1)
        day_start = datetime.combine(datetime.now().date(), datetime.min.time()).timestamp()
        summaries = self._storage.attribution_summary(day_start, datetime.now().timestamp())
        delta = sum(item.attributed_delta for item in summaries)
        actual = sum(item.actual_delta for item in summaries)
        attribution_pct = min(100, round(abs(delta) / abs(actual) * 100)) if actual else 0

        signature = (count, total, period, delta, actual, len(self._monitor.roots))
        if signature == self._signature:
            return
        self._signature = signature

        self._count = count
        self._size_total = total
        self._period_total = period
        self._ratio = min(total / period, 1.0) if total > 0 else 0.0
        self._delta = delta
        self._attribution_pct = attribution_pct
        self._update_tooltip()
        self.update()

    def _update_tooltip(self) -> None:
        impact = human_size(abs(self._delta))
        direction = tr("占用增加") if self._delta >= 0 else tr("释放空间")
        self.setToolTip(
            tr(
                "今日{direction} {impact}\n已归因 {pct}%\n记录 {count} 个文件",
                direction=direction,
                impact=impact,
                pct=self._attribution_pct,
                count=self._count,
            )
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 隐藏期间的写入不会更新 _size_total，重新显示时先对齐基线，
        # 否则第一次 refresh 会把隐藏期间的累积当成"刚刚新增"误闪一次
        self.refresh(initial=True)
        self._timer.start(REFRESH_MS)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:
        t = theme_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        outer = QRectF(0.5, 0.5, self.WIDTH - 1, self.HEIGHT - 1)
        background = t.color("floating_top")
        if self._hover:
            background = t.color("surface_raised")
        p.setBrush(background)
        p.setPen(QPen(t.color("border_strong"), 1))
        p.drawRoundedRect(outer, self.HEIGHT / 2, self.HEIGHT / 2)

        status = t.color("warning" if self._delta >= 0 else "success")
        status.setAlpha(220)
        p.setPen(Qt.NoPen)
        p.setBrush(status)
        p.drawRoundedRect(QRectF(8, 9, 4, self.HEIGHT - 18), 2, 2)

        p.setPen(t.color("text"))
        f = QFont(self.font())
        f.setWeight(QFont.DemiBold)
        sign = "+" if self._delta >= 0 else "−"
        text = sign + _compact_size(abs(self._delta))
        f.setPointSizeF(11.5)
        p.setFont(f)
        p.drawText(QRectF(19, 5, 76, 22), Qt.AlignLeft | Qt.AlignVCenter, text)

        p.setPen(t.color("text_dim"))
        small = QFont(self.font())
        small.setPointSizeF(8.0)
        p.setFont(small)
        p.drawText(QRectF(19, 23, 102, 14), Qt.AlignLeft | Qt.AlignVCenter, tr("今日净变化"))

    # ---------- 交互 ----------

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is None or not (event.buttons() & Qt.LeftButton):
            return
        pos = event.globalPosition().toPoint()
        if self._press_pos is not None:
            delta = pos - self._press_pos
            if abs(delta.x()) > DRAG_SLOP or abs(delta.y()) > DRAG_SLOP:
                self._moved = True
        if self._moved:
            self.move(pos - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._moved:
            self._save_pos()
        else:
            self.expand_requested.emit()
        self._press_pos = None
        self._drag_offset = None

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        polish_menu(menu)
        menu.addAction(menu_icon("window"), tr("展开卡片"), self.expand_requested.emit)
        menu.addAction(menu_icon("list"), tr("详情面板…"), self.open_panel.emit)
        menu.addAction(menu_icon("chart"), tr("概览…"), self.open_dashboard.emit)
        menu.addAction(menu_icon("sliders"), tr("设置…"), self.open_settings.emit)
        menu.addSeparator()
        menu.addAction(menu_icon("hide"), tr("隐藏（保留托盘图标）"), self._hide_self)
        menu.addAction(menu_icon("power"), tr("退出"), self.request_quit.emit)
        menu.exec(event.globalPos())

    def _hide_self(self) -> None:
        self.hide()
        self._config.set("widget_visible", False)
        self._config.save_soon()
        self.hidden_by_user.emit()

    # ---------- 位置 ----------

    def _save_pos(self) -> None:
        self._config.set("ball_pos", [self.x(), self.y()])
        self._config.save_soon()

    def place_near(self, rect) -> None:
        """从卡片收起时，让球出现在卡片右上角附近，视觉上有连续感。"""
        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        if area is None:
            return
        x = min(rect.right() - self.WIDTH, area.right() - self.WIDTH - 8)
        y = max(rect.top(), area.top() + 8)
        self.move(int(x), int(y))
        self._save_pos()

    def _restore_geometry(self) -> None:
        self.setWindowOpacity(float(self._config.get("widget_opacity", 0.95)))
        self.setWindowFlag(
            Qt.WindowStaysOnTopHint, bool(self._config.get("always_on_top", True))
        )
        pos = self._config.get("ball_pos")
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        if (
            isinstance(pos, list)
            and len(pos) == 2
            and all(isinstance(v, (int, float)) for v in pos)
            and area is not None
            and area.contains(QPoint(int(pos[0]) + self.WIDTH // 2, int(pos[1]) + self.HEIGHT // 2))
        ):
            self.move(int(pos[0]), int(pos[1]))
            return
        if area is None:
            return
        self.move(area.right() - self.WIDTH - 28, area.top() + 60)

    def apply_appearance(self) -> None:
        visible = self.isVisible()
        self._restore_geometry()
        if visible:
            self.show()

    def retranslate(self) -> None:
        # tooltip 是 i18n 的；签名不含语言，先清掉才会重刷
        self._signature = None
        self._update_tooltip()
        self.update()

    def apply_theme(self) -> None:
        self.update()


def _compact_size(num: int | float) -> str:
    """66px 球心专用：2.7M / 128K / 1.2G，比 '2.7 MB' 更省宽度。"""
    n = float(num)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            if unit == "B":
                return f"{int(n)}B"
            if n < 10:
                return f"{n:.1f}{unit}"
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}T"
