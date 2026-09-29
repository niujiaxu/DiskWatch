"""自绘图表组件：柱状图（体积/数量、线性/对数）、累计面积、多折线、横向条形。

详情面板与数据看板共用；全部基于 QPainter 自绘，无第三方图表依赖。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QWidget,
)

from ..i18n import tr
from ..storage import human_size
from .style import ACCENT, ACCENT_2, OK, TEXT, TEXT_DIM, WARN, theme_tokens

TREND_DAYS = 14
CHART_H = 78
_PLOT_TOP = 22
_LABEL_H = 14


def _expanding(widget: QWidget) -> None:
    """让图表随可用空间双向放大/收缩（窗口拉大时元素跟着放缩）。"""
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


def _compact_size(n: int) -> str:
    """体积简写：1.2M / 30M / 850K，用于柱顶小标签。

    用 .4g 保留有效精度（1.2MB 显示 "1.2M" 而非舍入成 "1M"），
    自动去掉无意义的尾随零（30MB 显示 "30M" 而非 "30.0M"），
    且不会退化成科学计数法（900KB 显示 "900K" 而非 "9e+02K"）。
    """
    if n >= 1_000_000_000:
        return f"{n / 1e9:.4g}G"
    if n >= 1_000_000:
        return f"{n / 1e6:.4g}M"
    if n >= 1_000:
        return f"{n / 1e3:.4g}K"
    return f"{n}B"


def _compact_count(n: int) -> str:
    """数量简写：1.2k / 34,000，用于柱顶小标签。"""
    if n >= 100_000:
        return f"{n / 1000:.0f}k"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def _signed_size(n: int) -> str:
    if n == 0:
        return "0 B"
    return ("+" if n > 0 else "−") + human_size(abs(n))


def _non_overlapping_labels(
    labels: list[tuple[str, QRectF]],
) -> list[tuple[str, QRectF]]:
    """按屏幕顺序保留互不碰撞的标签，完整数值仍可通过悬浮查看。"""
    visible: list[tuple[str, QRectF]] = []
    occupied: list[QRectF] = []
    for text, rect in labels:
        padded = rect.adjusted(-2, -1, 2, 1)
        if any(padded.intersects(other) for other in occupied):
            continue
        visible.append((text, rect))
        occupied.append(padded)
    return visible


class TrendChart(QWidget):
    """近 N 天新增趋势图：渐变圆角柱，悬浮显示日期 / 体积 / 数量。

    数据来自 DaySummary（按天聚合）。默认对数刻度（log10），小值柱也
    清晰可辨，可切换回线性刻度；右上角小按钮切换。柱顶标注体积/数量
    简写，柱太矮或与按钮重叠时自动省略。单击柱体发出 day_selected。
    """

    day_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[tuple[str, int, int]] = []  # (day, size, count)
        self._hover: int = -1
        self._log_scale = True
        self._metric = "size"  # "size" 体积 / "count" 数量
        self._signed = False
        self._btn: QPushButton | None = None  # 惰性创建（无 QApplication 的单元测试不建）
        self.setMinimumHeight(CHART_H)
        _expanding(self)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def _ensure_btn(self) -> None:
        if self._btn is not None or QApplication.instance() is None:
            return
        btn = QPushButton(tr("对数"), self)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setStyleSheet(self._button_qss())
        btn.clicked.connect(self._toggle_scale)
        btn.setGeometry(self.width() - 62, 3, 58, 15)
        self._btn = btn

    def _button_qss(self) -> str:
        t = theme_tokens()
        return (
            f"QPushButton {{ color: {t.text_dim}; background: {t.button};"
            " border: none; border-radius: 5px; font-size: 9px; padding: 1px 6px; }"
            f" QPushButton:hover {{ color: {t.text}; background: {t.button_hover}; }}"
        )

    def apply_theme(self) -> None:
        if self._btn is not None:
            self._btn.setStyleSheet(self._button_qss())
        self.update()

    # ---------- 数据 ----------

    def set_days(self, summaries, max_days: int = TREND_DAYS) -> None:
        """接收 DaySummary 列表并绘制柱状图（按体积，旧→新左侧最早）。"""
        self._data = [
            (s.day, s.total_size, s.count)
            for s in summaries[:max_days]
            if s.total_size > 0
        ]
        self._signed = False
        self._data.reverse()
        self._hover = -1
        self.setVisible(bool(self._data))
        self.update()

    def set_space_days(self, summaries, max_days: int = TREND_DAYS) -> None:
        """接收空间账本日汇总，净占用为正、净释放为负。"""
        self._data = [
            (s.day, s.net_bytes, s.event_count)
            for s in summaries[:max_days]
            if s.net_bytes != 0 or s.event_count > 0
        ]
        self._signed = True
        self._data.reverse()
        self._hover = -1
        self.setVisible(bool(self._data))
        self.update()

    def set_log_scale(self, use_log: bool) -> None:
        """对数 / 线性刻度切换；对数下小值柱也清晰可见。"""
        if use_log == self._log_scale:
            return
        self._log_scale = use_log
        if self._btn is not None:
            self._btn.setText(tr("对数") if use_log else tr("线性"))
        self.update()

    def set_metric(self, metric: str) -> None:
        """柱高按 "size"（体积，默认）或 "count"（数量）归一化。"""
        if metric not in ("size", "count") or metric == self._metric:
            return
        self._metric = metric
        self.update()

    def _toggle_scale(self) -> None:
        self.set_log_scale(not self._log_scale)

    def retranslate(self) -> None:
        """语言热切换后刷新切换按钮文案。"""
        if self._btn is not None:
            self._btn.setText(tr("对数") if self._log_scale else tr("线性"))

    def _tip_text(self, i: int) -> str:
        day, size, count = self._data[i]
        size_text = _signed_size(size) if self._signed else human_size(size)
        count_text = (
            tr("{count} 个空间事件", count=count)
            if self._signed else tr("{count} 个文件", count=count)
        )
        return f"{day}  ·  {size_text}  ·  {count_text}"

    # ---------- 几何 ----------

    def _geometry(self) -> tuple[int, int, int, int]:
        """返回 (柱宽, 柱距, 起始 x, 柱区高)。天多时柱宽与柱距都自适应。"""
        n = len(self._data)
        w = self.width()
        bar_area_h = self.height() - _PLOT_TOP - _LABEL_H - 6
        if n == 0:
            return 0, 0, 0, bar_area_h
        usable = max(1.0, w - 16.0)
        bw: float = min(24.0, max(8.0, usable / max(1.0, n * 1.8)))
        gap: float = (
            0.0
            if n == 1
            else min(52.0, max(6.0, (usable - n * bw) / (n - 1)))
        )
        total: float = n * bw + (n - 1) * gap
        if total > usable:
            gap = 2.0
            bw = max(2.0, (usable - (n - 1) * gap) / n)
            total = n * bw + (n - 1) * gap
        x0 = max(0.0, (w - total) / 2)
        return int(bw), int(gap), int(x0), bar_area_h

    def _index_at(self, x: int) -> int:
        bw, gap, x0, _ = self._geometry()
        if bw <= 0:
            return -1
        for i in range(len(self._data)):
            left = x0 + i * (bw + gap)
            if left <= x <= left + bw:
                return i
        return -1

    def _bar_height(self, val: int, max_val: int, area_h: int) -> float:
        """归一化柱高：对数刻度（默认）或线性。"""
        if self._log_scale:
            base = math.log10(max_val + 1) or 1.0
            return max(3.0, math.log10(val + 1) / base * area_h)
        return max(3.0, val / max_val * area_h)

    # ---------- 交互 ----------

    def mouseMoveEvent(self, event) -> None:
        i = self._index_at(int(event.position().x()))
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            QToolTip.showText(
                event.globalPosition().toPoint() + QPoint(14, -10),
                self._tip_text(i),
                self,
                self.rect(),
            )
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover >= 0:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            i = self._index_at(int(event.position().x()))
            if i >= 0:
                self.day_selected.emit(self._data[i][0])
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._ensure_btn()
        if self._btn is not None:
            self._btn.setGeometry(self.width() - 62, 3, 58, 15)

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:
        if not self._data:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bw, gap, x0, bar_area_h = self._geometry()
        vals = [
            s if self._metric == "size" else c
            for _d, s, c in self._data
        ]
        signed_view = self._signed and self._metric == "size"
        positive = [value for value in vals if value > 0]
        negative = [abs(value) for value in vals if value < 0]
        if signed_view and positive and negative:
            positive_area = max(3, bar_area_h // 2 - 1)
            negative_area = max(3, bar_area_h - positive_area - 1)
            baseline = _PLOT_TOP + positive_area
        elif signed_view and negative:
            positive_area = 0
            negative_area = bar_area_h
            baseline = _PLOT_TOP
        else:
            positive_area = bar_area_h
            negative_area = 0
            baseline = _PLOT_TOP + bar_area_h
        max_positive = max(positive, default=1)
        max_negative = max(negative, default=1)
        hovered = self._hover >= 0

        axis_font = painter.font()
        axis_font.setPointSizeF(max(7.0, axis_font.pointSizeF() - 1.5))
        label_font = painter.font()
        label_font.setPointSizeF(max(6.5, label_font.pointSizeF() - 2.5))
        axis_pen = QPen(QColor(TEXT_DIM))
        n_bars = len(self._data)
        # 柱顶标签延迟到所有柱画完再统一绘制：先画的矮柱标签会被
        # 后画的相邻高柱柱身盖住（表现为数字显示不全/被遮挡）
        labels: list[tuple[str, QRectF]] = []
        # 底部只标起始 / 结束两个日期，避免相邻标签重叠
        def _show_label(i: int) -> bool:
            return i == 0 or i == n_bars - 1

        if signed_view:
            baseline_pen = theme_tokens().color("text_dim")
            baseline_pen.setAlpha(45)
            painter.setPen(QPen(baseline_pen, 1.0))
            painter.drawLine(0, baseline, self.width(), baseline)

        for i, (day, size, count) in enumerate(self._data):
            x = x0 + i * (bw + gap)
            val = size if self._metric == "size" else count
            if val < 0 and signed_view:
                bh = int(self._bar_height(abs(val), max_negative, negative_area))
                y = baseline
            else:
                bh = int(self._bar_height(abs(val), max_positive, positive_area))
                y = baseline - bh

            if hovered and i != self._hover:
                painter.setOpacity(0.45)
            else:
                painter.setOpacity(1.0)
            color = (
                theme_tokens().color("success")
                if val < 0 and signed_view else
                theme_tokens().color("warning")
                if val > 0 and signed_view else ACCENT
            )
            grad = QLinearGradient(x, y, x, y + bh)
            top_color = QColor(color)
            top_color = top_color.lighter(115)
            grad.setColorAt(0.0, top_color)
            grad.setColorAt(1.0, color)
            painter.setBrush(grad)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(x, y, bw, bh), 3, 3)

            if i == self._hover:
                painter.setOpacity(1.0)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(theme_tokens().color("text"), 1.2))
                painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, bw - 1, bh - 1), 3, 3)

            # 柱顶数值简写标签：柱太矮或与切换按钮重叠时省略
            if bh >= 10:
                text = (
                    (("+" if size > 0 else "−") + _compact_size(abs(size)))
                    if self._signed and self._metric == "size"
                    else _compact_size(size)
                    if self._metric == "size"
                    else _compact_count(count)
                )
                label_y = y - 9 if val >= 0 else min(
                    _PLOT_TOP + bar_area_h - 8, y + bh + 1
                )
                lrect = QRectF(x - 24, label_y, bw + 48, 8)
                if self._btn is None or not self._btn.geometry().intersects(lrect.toRect()):
                    labels.append((text, lrect))

            painter.setOpacity(1.0)
            if _show_label(i):  # 底部日期轴：只标起始 / 结束
                painter.setFont(axis_font)
                painter.setPen(axis_pen)
                label_y = self.height() - _LABEL_H + 2
                # 标签始终居中在柱子正下方；矩形放宽到 200px，
                # 只让矩形中心对准柱中心，文字不会被裁、也不会跑到窗口边缘
                cx = x + bw / 2
                rect = QRectF(cx - 100, label_y, 200, _LABEL_H - 2)
                painter.drawText(rect, Qt.AlignCenter, day[5:])  # ISO 日期取 MM-DD

        # 最后统一绘制柱顶标签（置顶，不被任何柱身遮挡）
        for text, lrect in _non_overlapping_labels(labels):
            painter.setOpacity(1.0)
            painter.setFont(label_font)
            painter.setPen(QColor(TEXT_DIM))
            painter.drawText(lrect, Qt.AlignHCenter, text)
        painter.end()


_LINE_COLORS = (
    ACCENT_2,
    OK,
    WARN,
    ACCENT,
    QColor(125, 141, 164),
    QColor(177, 133, 72),
)

_PLOT_LEFT = 4
_LABEL_H = 14


def _day_short(day: str) -> str:
    """ISO 日期取 MM-DD。"""
    return day[5:]


# ---------------------------------------------------------------------------
# 图表组件
# ---------------------------------------------------------------------------


class CumulativeChart(QWidget):
    """近 N 天累计新增体积面积图：折线 + 渐变填充，hover 显示截至日期累计值。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[tuple[str, int]] = []  # (day, cumulative) 旧→新
        self._hover: int = -1
        self._signed = False
        self.setMinimumHeight(120)
        _expanding(self)
        self.setMouseTracking(True)

    def set_days(self, summaries, max_days: int = 90) -> None:
        """按 DaySummary 计算累计体积（旧→新，左侧最早）。

        与 TrendChart 约定一致：summaries 按新→旧传入（fetch_days_with_data
        原始顺序），内部反转后再累加。
        """
        total = 0
        out: list[tuple[str, int]] = []
        for s in reversed(summaries[:max_days]):
            total += s.total_size
            out.append((s.day, total))
        self._data = out
        self._signed = False
        self._hover = -1
        self.setVisible(bool(self._data))
        self.update()

    def set_space_days(self, summaries, max_days: int = 90) -> None:
        """按空间账本净变化计算累计已归因变化。"""
        total = 0
        out: list[tuple[str, int]] = []
        for summary in reversed(summaries[:max_days]):
            total += summary.net_bytes
            out.append((summary.day, total))
        self._data = out
        self._signed = True
        self._hover = -1
        self.setVisible(bool(self._data))
        self.update()

    def _x(self, i: int) -> float:
        w = self.width()
        n = len(self._data)
        if n <= 1 or w <= 8:
            return w / 2
        return _PLOT_LEFT + i * (w - 8) / (n - 1)

    def _index_at(self, x: float) -> int:
        n = len(self._data)
        if n <= 1 or self.width() <= 8:
            return 0
        i = round((x - _PLOT_LEFT) / (self.width() - 8) * (n - 1))
        return max(0, min(n - 1, i))

    def _tip_text(self, i: int) -> str:
        day, cum = self._data[i]
        return tr(
            "截至 {day} · 累计 {size}",
            day=day,
            size=_signed_size(cum) if self._signed else human_size(cum),
        )

    def mouseMoveEvent(self, event) -> None:
        i = self._index_at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            QToolTip.showText(
                event.globalPosition().toPoint() + QPoint(14, -10),
                self._tip_text(i),
                self,
                self.rect(),
            )
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover >= 0:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        if not self._data:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        area_bottom = h - _LABEL_H - 4
        area_top = 6
        values = [value for _day, value in self._data]
        low = min(0, *values)
        high = max(0, *values)
        if high == low:
            high = low + 1
        n = len(self._data)

        def _y(cum: int) -> float:
            return area_bottom - (cum - low) / (high - low) * (area_bottom - area_top)

        baseline = _y(0)
        line_color = (
            theme_tokens().color("success")
            if self._signed and self._data[-1][1] < 0
            else theme_tokens().color("warning")
            if self._signed and self._data[-1][1] > 0
            else ACCENT
        )

        # 面积填充
        path = QPainterPath()
        path.moveTo(self._x(0), _y(self._data[0][1]))
        for i in range(1, n):
            path.lineTo(self._x(i), _y(self._data[i][1]))
        path.lineTo(self._x(n - 1), baseline)
        path.lineTo(self._x(0), baseline)
        path.closeSubpath()
        grad = QLinearGradient(0, area_top, 0, area_bottom)
        grad.setColorAt(
            0.0, QColor(line_color.red(), line_color.green(), line_color.blue(), 90)
        )
        grad.setColorAt(
            1.0, QColor(line_color.red(), line_color.green(), line_color.blue(), 12)
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawPath(path)

        # 折线
        pen = QPen(line_color, 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for i in range(1, n):
            painter.drawLine(
                QPoint(int(self._x(i - 1)), int(_y(self._data[i - 1][1]))),
                QPoint(int(self._x(i)), int(_y(self._data[i][1]))),
            )

        # 末点圆点 + 累计值标注
        lx, ly = int(self._x(n - 1)), int(_y(self._data[-1][1]))
        painter.setBrush(line_color)
        painter.setPen(QPen(theme_tokens().color("surface"), 1.0))
        painter.drawEllipse(QPoint(lx, ly), 3, 3)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.setFont(font)
        painter.setPen(QColor(TEXT_DIM))
        label = (
            _signed_size(self._data[-1][1])
            if self._signed else human_size(self._data[-1][1])
        )
        painter.drawText(
            QRectF(min(lx + 6, w - 110), max(2.0, ly - 8), 104, 12),
            Qt.AlignLeft | Qt.AlignVCenter,
            label,
        )

        # hover 竖线
        if self._hover >= 0:
            hx = int(self._x(self._hover))
            guide = theme_tokens().color("text_dim")
            guide.setAlpha(60)
            painter.setPen(QPen(guide, 1.0))
            painter.drawLine(hx, area_top, hx, area_bottom)

        # 首尾日期轴
        axis_pen = QPen(QColor(TEXT_DIM))
        for i in ((0,) if n == 1 else (0, n - 1)):
            painter.setFont(font)
            painter.setPen(axis_pen)
            if n == 1:
                rect = QRectF(0, h - _LABEL_H + 2, w, _LABEL_H - 2)
                align = Qt.AlignCenter
            elif i == 0:
                rect = QRectF(0, h - _LABEL_H + 2, 100, _LABEL_H - 2)
                align = Qt.AlignLeft | Qt.AlignVCenter
            else:
                rect = QRectF(w - 100, h - _LABEL_H + 2, 100, _LABEL_H - 2)
                align = Qt.AlignRight | Qt.AlignVCenter
            painter.drawText(rect, align, _day_short(self._data[i][0]))
        painter.end()


class SpaceTrendChart(QWidget):
    """各盘剩余空间趋势：多折线 + 左上角图例，hover 显示当天各盘剩余。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: dict[str, dict[str, int]] = {}  # drive -> {day: free}
        self._days: list[str] = []  # 全局日期并集（升序）
        self._hover: int = -1
        self.setMinimumHeight(120)
        _expanding(self)
        self.setMouseTracking(True)

    def set_series(self, series: dict[str, list[tuple[str, int]]]) -> None:
        """{drive: [(day, free_bytes), ...]}（按天升序）。"""
        self._series = {d: dict(pts) for d, pts in series.items() if pts}
        days = sorted({d for m in self._series.values() for d in m})
        self._days = days
        self._hover = -1
        self.setVisible(bool(self._series))
        self.update()

    def _x(self, i: int) -> float:
        n = len(self._days)
        if n <= 1 or self.width() <= 8:
            return _PLOT_LEFT + 2
        return _PLOT_LEFT + i * (self.width() - 8) / (n - 1)

    def _index_at(self, x: float) -> int:
        n = len(self._days)
        if n <= 1 or self.width() <= 8:
            return 0
        i = round((x - _PLOT_LEFT) / (self.width() - 8) * (n - 1))
        return max(0, min(n - 1, i))

    def _tip_text(self, i: int) -> str:
        day = self._days[i]
        parts = []
        for drive in sorted(self._series):
            free = self._series[drive].get(day)
            if free is not None:
                parts.append(f"{drive} {human_size(free)}")
        return day + "  ·  " + "  ·  ".join(parts) if parts else day

    def mouseMoveEvent(self, event) -> None:
        i = self._index_at(event.position().x())
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            QToolTip.showText(
                event.globalPosition().toPoint() + QPoint(14, -10),
                self._tip_text(i),
                self,
                self.rect(),
            )
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover >= 0:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        if not self._days:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        area_bottom = h - _LABEL_H - 4
        n = len(self._days)

        drives = sorted(self._series)
        legend_cell_w = 54
        legend_columns = max(1, (w - 12) // legend_cell_w)
        legend_rows = max(1, math.ceil(len(drives) / legend_columns))
        max_legend_rows = max(1, min(3, (area_bottom - 18) // 12))
        shown_legend_rows = min(legend_rows, max_legend_rows)
        legend_capacity = legend_columns * shown_legend_rows
        area_top = min(area_bottom - 10, 6 + shown_legend_rows * 12)

        all_free = [f for m in self._series.values() for f in m.values()]
        lo, hi = min(all_free), max(all_free)
        if hi - lo < 1:
            hi = lo + max(1, int(lo * 0.1))

        def _y(free: int) -> float:
            return area_bottom - (free - lo) / (hi - lo) * (area_bottom - area_top)

        # 折线（每盘一色）
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        for k, drive in enumerate(drives):
            color = _LINE_COLORS[k % len(_LINE_COLORS)]
            pen = QPen(color, 1.5)
            painter.setPen(pen)
            pts = [
                (int(self._x(i)), int(_y(free)))
                for i, day in enumerate(self._days)
                if (free := self._series[drive].get(day)) is not None
            ]
            for j in range(1, len(pts)):
                painter.drawLine(QPoint(*pts[j - 1]), QPoint(*pts[j]))

        # hover 竖线
        if self._hover >= 0:
            hx = int(self._x(self._hover))
            guide = theme_tokens().color("text_dim")
            guide.setAlpha(60)
            painter.setPen(QPen(guide, 1.0))
            painter.drawLine(hx, area_top, hx, area_bottom)

        # 图例横向排布并限制为三行，避免压住折线区域。
        legend_drives = drives[:legend_capacity]
        for k, drive in enumerate(legend_drives):
            column = k % legend_columns
            row = k // legend_columns
            lx = 6 + column * legend_cell_w
            ly = 4 + row * 12
            color = _LINE_COLORS[k % len(_LINE_COLORS)]
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(lx, ly + 2, 7, 7), 2, 2)
            painter.setFont(font)
            painter.setPen(QColor(TEXT_DIM))
            painter.drawText(
                QRectF(lx + 11, ly, legend_cell_w - 12, 11),
                Qt.AlignLeft | Qt.AlignVCenter,
                drive,
            )

        # 首尾日期轴
        if n > 1:
            for i in (0, n - 1):
                painter.setFont(font)
                painter.setPen(QColor(TEXT_DIM))
                if i == 0:
                    rect = QRectF(0, h - _LABEL_H + 2, 100, _LABEL_H - 2)
                    align = Qt.AlignLeft | Qt.AlignVCenter
                else:
                    rect = QRectF(w - 100, h - _LABEL_H + 2, 100, _LABEL_H - 2)
                    align = Qt.AlignRight | Qt.AlignVCenter
                painter.drawText(rect, align, _day_short(self._days[i]))
        painter.end()


class TopBarsChart(QWidget):
    """横向条形图：近 N 天合计体积 TOP，行内 label 截断 + 数值。"""

    ROW_H = 24

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, int, int]] = []  # (label, count, size)
        self._metric = "size"
        self._hover: int = -1
        self.setMinimumHeight(60)
        _expanding(self)
        self.setMouseTracking(True)

    def set_items(self, items: list[tuple[str, int, int]], metric: str = "size") -> None:
        """[(label, count, size)]，按展示值降序；metric: "size" / "count"。"""
        self._items = [(label or "(unknown)", c, s) for label, c, s in items]
        self._metric = metric
        self._hover = -1
        self.setMinimumHeight(12 + len(self._items) * self.ROW_H)
        self.setVisible(bool(self._items))
        self.update()

    def _row_h(self) -> float:
        """行高随卡片高度自适应：窗口拉大时行也放大（上限避免过疏）。"""
        n = max(1, len(self._items))
        available = max(float(self.ROW_H), (self.height() - 12) / n)
        return min(self.ROW_H * 1.8, available)

    def _row_rect(self, i: int) -> QRectF:
        h = self._row_h()
        return QRectF(6, 6 + i * h, self.width() - 12, h - 4)

    def _row_at(self, y: float) -> int:
        # 横向条形图整行命中，只按 y 判区间（与 x 无关）
        for i in range(len(self._items)):
            rect = self._row_rect(i)
            if rect.top() <= y <= rect.bottom():
                return i
        return -1

    def _value(self, item: tuple[str, int, int]) -> int:
        _label, count, size = item
        return size if self._metric == "size" else count

    def _tip_text(self, i: int) -> str:
        label, count, size = self._items[i]
        return tr(
            "{label}\n{count} 个文件 · {size}",
            label=label,
            count=f"{count:,}",
            size=human_size(size),
        )

    def mouseMoveEvent(self, event) -> None:
        i = self._row_at(event.position().y())
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            QToolTip.showText(
                event.globalPosition().toPoint() + QPoint(14, -10),
                self._tip_text(i),
                self,
                self.rect(),
            )
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover >= 0:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        if not self._items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        max_val = max((self._value(it) for it in self._items), default=1) or 1
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        val_w = min(96, max(56, w // 4))
        label_w = min(int(w * 0.34), 220, max(40, w - val_w - 44))
        bar_x = 6 + label_w + 10
        bar_w_max = max(0, w - 12 - label_w - 10 - val_w - 8)

        for i, item in enumerate(self._items):
            label, _count, _size = item
            rect = self._row_rect(i)
            y = rect.y()

            if i == self._hover:
                painter.setPen(Qt.NoPen)
                hover = theme_tokens().color("text")
                hover.setAlpha(10)
                painter.setBrush(hover)
                painter.drawRoundedRect(rect, 5, 5)

            # label
            painter.setFont(font)
            painter.setPen(QColor(TEXT_DIM))
            label_text = painter.fontMetrics().elidedText(
                label, Qt.ElideMiddle, max(1, label_w)
            )
            painter.drawText(
                QRectF(6, y + 2, label_w, rect.height() - 4),
                Qt.AlignLeft | Qt.AlignVCenter,
                label_text,
            )
            # 条形
            val = self._value(item)
            bh = rect.height() - 8
            bw = max(2.0, bar_w_max * val / max_val) if bar_w_max > 0 else 0
            grad = QLinearGradient(bar_x, 0, bar_x + bw, 0)
            grad.setColorAt(0.0, ACCENT)
            grad.setColorAt(1.0, ACCENT_2)
            painter.setPen(Qt.NoPen)
            painter.setBrush(grad)
            if bw > 0:
                painter.drawRoundedRect(
                    QRectF(bar_x, y + 4, bw, bh), 3, 3
                )
            # 数值
            text = (
                human_size(val)
                if self._metric == "size"
                else f"{val:,}"
            )
            painter.setPen(QColor(TEXT))
            painter.drawText(
                QRectF(w - 12 - val_w, y + 2, val_w, rect.height() - 4),
                Qt.AlignRight | Qt.AlignVCenter,
                text,
            )
        painter.end()


# ---------------------------------------------------------------------------
# 看板窗口
# ---------------------------------------------------------------------------

