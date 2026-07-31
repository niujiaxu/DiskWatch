"""迷你悬浮球：收起状态下只占一个小圆，显示今日新增总大小。

进度环的比例 = 今日新增体积 / 近 7 天单日体积峰值，用来一眼看出今天忙不忙，
而不是拿一个凭空定的最大值凑百分比。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from ..storage import Storage, today_str
from ..watcher import FileMonitor
from .style import ACCENT, ACCENT_2, TEXT, TEXT_DIM

REFRESH_MS = 2000
FLASH_MS = 60
DRAG_SLOP = 4  # 位移小于这个值算点击，不算拖动


class MiniBall(QWidget):
    expand_requested = Signal()
    open_panel = Signal()
    open_settings = Signal()
    request_quit = Signal()
    hidden_by_user = Signal()

    SIZE = 66
    RING = 5

    def __init__(self, storage: Storage, monitor: FileMonitor, config) -> None:
        super().__init__()
        self._storage = storage
        self._monitor = monitor
        self._config = config

        self._count = 0
        self._size_total = 0
        self._ratio = 0.0
        self._glow = 0.0
        self._hover = False
        self._press_pos: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._moved = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)

        self._signature: tuple | None = None

        # 同卡片一样：隐藏时不跑定时器，数据没变化时不重绘
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._decay_glow)

        self._restore_geometry()
        self.refresh(initial=True)

    # ---------- 数据 ----------

    def refresh(self, initial: bool = False) -> None:
        count, total = self._storage.day_stats(today_str())
        peak = max(self._storage.max_day_size(7), 1)

        if not initial and total > self._size_total:
            self._start_flash()

        signature = (count, total, peak, len(self._monitor.roots))
        if signature == self._signature:
            return
        self._signature = signature

        self._count = count
        self._size_total = total
        self._ratio = min(total / peak, 1.0)
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start(REFRESH_MS)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        self._flash_timer.stop()
        self._glow = 0.0

    def _start_flash(self) -> None:
        self._glow = 1.0
        if not self._flash_timer.isActive():
            self._flash_timer.start(FLASH_MS)

    def _decay_glow(self) -> None:
        self._glow = max(0.0, self._glow - 0.06)
        if self._glow <= 0:
            self._flash_timer.stop()
        self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pad = self.RING / 2 + 1
        outer = QRectF(pad, pad, self.SIZE - 2 * pad, self.SIZE - 2 * pad)

        # 新文件进来时短暂发光，让人注意到有变化
        if self._glow > 0:
            halo = QRadialGradient(outer.center(), self.SIZE / 2)
            c = QColor(ACCENT_2)
            c.setAlphaF(0.45 * self._glow)
            halo.setColorAt(0.55, QColor(0, 0, 0, 0))
            halo.setColorAt(1.0, c)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QRectF(0, 0, self.SIZE, self.SIZE))

        # 球体
        body = QRadialGradient(
            outer.center().x(), outer.top(), outer.height() * 1.25
        )
        body.setColorAt(0.0, QColor(46, 49, 66, 242))
        body.setColorAt(1.0, QColor(20, 21, 30, 246))
        p.setPen(Qt.NoPen)
        p.setBrush(body)
        p.drawEllipse(outer)

        # 进度环底色
        track = QPen(QColor(255, 255, 255, 34 if not self._hover else 54), self.RING)
        track.setCapStyle(Qt.FlatCap)
        p.setPen(track)
        p.drawArc(outer, 0, 360 * 16)

        # 进度环
        if self._ratio > 0:
            grad = QConicalGradient(outer.center(), 90)
            grad.setColorAt(0.0, ACCENT_2)
            grad.setColorAt(0.5, ACCENT)
            grad.setColorAt(1.0, ACCENT_2)
            pen = QPen(grad, self.RING)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(outer, 90 * 16, -int(360 * 16 * self._ratio))

        # 中间显示今日总大小（压缩成 2.7M / 128K 这类，66px 里才放得下）
        # 继承应用字体（含中文），只改字号/粗细，避免默认西文字体缺字
        p.setPen(QColor(TEXT))
        f = QFont(self.font())
        f.setBold(True)
        text = _compact_size(self._size_total)
        f.setPointSizeF(13.0 if len(text) <= 4 else 10.5)
        p.setFont(f)
        p.drawText(outer.adjusted(0, -5, 0, -5), Qt.AlignCenter, text)

        p.setPen(QColor(TEXT_DIM))
        small = QFont(self.font())
        small.setPointSizeF(7.0)
        p.setFont(small)
        p.drawText(outer.adjusted(0, 17, 0, 17), Qt.AlignCenter, "今日")

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
        menu.addAction("展开卡片", self.expand_requested.emit)
        menu.addAction("详情面板…", self.open_panel.emit)
        menu.addAction("设置…", self.open_settings.emit)
        menu.addSeparator()
        menu.addAction("隐藏（保留托盘图标）", self._hide_self)
        menu.addAction("退出", self.request_quit.emit)
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
        screen = QApplication.primaryScreen().availableGeometry()
        x = min(rect.right() - self.SIZE, screen.right() - self.SIZE - 8)
        y = max(rect.top(), screen.top() + 8)
        self.move(int(x), int(y))
        self._save_pos()

    def _restore_geometry(self) -> None:
        self.setWindowOpacity(float(self._config.get("widget_opacity", 0.95)))
        self.setWindowFlag(
            Qt.WindowStaysOnTopHint, bool(self._config.get("always_on_top", True))
        )
        pos = self._config.get("ball_pos")
        screen = QApplication.primaryScreen().availableGeometry()
        if isinstance(pos, list) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
            if screen.contains(QPoint(x + self.SIZE // 2, y + self.SIZE // 2)):
                self.move(x, y)
                return
        self.move(screen.right() - self.SIZE - 28, screen.top() + 60)

    def apply_appearance(self) -> None:
        visible = self.isVisible()
        self._restore_geometry()
        if visible:
            self.show()


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
