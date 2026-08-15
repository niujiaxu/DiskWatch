"""数据看板：从多个角度观察数据增长。

独立顶层窗（风格对齐详情面板，纯自绘图表）：
- 增长趋势：每日新增体积 / 数量柱状图（线性 / 对数可切），点柱可跳详情
- 累计增长：累计体积面积折线图（看增长斜率与高峰拐点）
- 磁盘剩余空间：各盘剩余空间折线（看空间消耗速度）
- TOP 目录 / TOP 文件类型：近 N 天合计横向条形图

范围 7 / 14 / 30 / 90 天可切；全部查询在后台线程；打开时 5 秒自动
刷新，数据未变（storage.change_seq 脏检查）则跳过。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..storage import Storage, human_size
from .panel import TrendChart
from .style import (
    ACCENT,
    ACCENT_2,
    OK,
    PANEL_QSS,
    TEXT,
    TEXT_DIM,
    WARN,
    apply_window_icon,
    enable_dark_titlebar,
)

RANGES = (7, 14, 30, 90)
TOP_FOLDER_LIMIT = 10
TOP_EXT_LIMIT = 8
DASH_REFRESH_MS = 5000

# 卡片标题 key → 中文原文（retranslate 用）
_CARD_TITLES = {
    "growth": "增长趋势",
    "cum": "累计增长",
    "space": "磁盘剩余空间",
    "exts": "TOP 文件类型",
    "folders": "TOP 目录",
}

# 多折线色板：青 / 青绿 / 橙 / 蓝 / 紫 / 黄
_LINE_COLORS = (
    ACCENT_2,
    OK,
    WARN,
    ACCENT,
    QColor(212, 176, 244),
    QColor(240, 210, 120),
)

_PLOT_LEFT = 4
_LABEL_H = 14


def _elide_end(text: str, limit: int) -> str:
    """尾部省略：保留开头（路径场景看主要目录）。"""
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
        self.setMinimumHeight(120)
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
        self._hover = -1
        self.setVisible(bool(self._data))
        self.update()

    def _x(self, i: int) -> float:
        w = self.width()
        n = len(self._data)
        if n <= 1:
            return 4.0
        return _PLOT_LEFT + i * (w - 8) / (n - 1)

    def _index_at(self, x: float) -> int:
        n = len(self._data)
        if n <= 1:
            return 0
        i = round((x - _PLOT_LEFT) / (self.width() - 8) * (n - 1))
        return max(0, min(n - 1, i))

    def _tip_text(self, i: int) -> str:
        day, cum = self._data[i]
        return tr(
            "截至 {day} · 累计 {size}",
            day=day,
            size=human_size(cum),
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
        max_cum = self._data[-1][1] or 1
        n = len(self._data)

        def _y(cum: int) -> float:
            return area_bottom - cum / max_cum * (area_bottom - area_top)

        # 面积填充
        path = QPainterPath()
        path.moveTo(self._x(0), _y(self._data[0][1]))
        for i in range(1, n):
            path.lineTo(self._x(i), _y(self._data[i][1]))
        path.lineTo(self._x(n - 1), area_bottom)
        path.lineTo(self._x(0), area_bottom)
        path.closeSubpath()
        grad = QLinearGradient(0, area_top, 0, area_bottom)
        grad.setColorAt(0.0, QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 90))
        grad.setColorAt(1.0, QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 12))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawPath(path)

        # 折线
        pen = QPen(ACCENT, 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for i in range(1, n):
            painter.drawLine(
                QPoint(int(self._x(i - 1)), int(_y(self._data[i - 1][1]))),
                QPoint(int(self._x(i)), int(_y(self._data[i][1]))),
            )

        # 末点圆点 + 累计值标注
        lx, ly = int(self._x(n - 1)), int(_y(self._data[-1][1]))
        painter.setBrush(ACCENT)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.0))
        painter.drawEllipse(QPoint(lx, ly), 3, 3)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.setFont(font)
        painter.setPen(QColor(TEXT_DIM))
        label = human_size(self._data[-1][1])
        painter.drawText(
            QRectF(min(lx + 6, w - 110), max(2.0, ly - 8), 104, 12),
            Qt.AlignLeft | Qt.AlignVCenter,
            label,
        )

        # hover 竖线
        if self._hover >= 0:
            hx = int(self._x(self._hover))
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
            painter.drawLine(hx, area_top, hx, area_bottom)

        # 首尾日期轴
        axis_pen = QPen(QColor(TEXT_DIM))
        for i in (0, n - 1):
            painter.setFont(font)
            painter.setPen(axis_pen)
            rect = QRectF(self._x(i) - 100, h - _LABEL_H + 2, 200, _LABEL_H - 2)
            painter.drawText(rect, Qt.AlignCenter, _day_short(self._data[i][0]))
        painter.end()


class SpaceTrendChart(QWidget):
    """各盘剩余空间趋势：多折线 + 左上角图例，hover 显示当天各盘剩余。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: dict[str, dict[str, int]] = {}  # drive -> {day: free}
        self._days: list[str] = []  # 全局日期并集（升序）
        self._hover: int = -1
        self.setMinimumHeight(120)
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
        if n <= 1:
            return _PLOT_LEFT + 2
        return _PLOT_LEFT + i * (self.width() - 8) / (n - 1)

    def _index_at(self, x: float) -> int:
        n = len(self._days)
        if n <= 1:
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
        _w, h = self.width(), self.height()
        area_bottom = h - _LABEL_H - 4
        area_top = 8
        n = len(self._days)

        all_free = [f for m in self._series.values() for f in m.values()]
        lo, hi = min(all_free), max(all_free)
        if hi - lo < 1:
            hi = lo + max(1, int(lo * 0.1))

        def _y(free: int) -> float:
            return area_bottom - (free - lo) / (hi - lo) * (area_bottom - area_top)

        # 折线（每盘一色）
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        drives = sorted(self._series)
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
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1.0))
            painter.drawLine(hx, area_top, hx, area_bottom)

        # 左上角图例
        ly = 4
        for k, drive in enumerate(drives[:6]):
            color = _LINE_COLORS[k % len(_LINE_COLORS)]
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(6, ly + 2, 7, 7), 2, 2)
            painter.setFont(font)
            painter.setPen(QColor(TEXT_DIM))
            painter.drawText(
                QRectF(17, ly, 80, 11), Qt.AlignLeft | Qt.AlignVCenter, drive
            )
            ly += 12

        # 首尾日期轴
        if n > 1:
            for i in (0, n - 1):
                painter.setFont(font)
                painter.setPen(QColor(TEXT_DIM))
                rect = QRectF(self._x(i) - 100, h - _LABEL_H + 2, 200, _LABEL_H - 2)
                painter.drawText(rect, Qt.AlignCenter, _day_short(self._days[i]))
        painter.end()


class TopBarsChart(QWidget):
    """横向条形图：近 N 天合计体积 TOP，行内 label 截断 + 数值。"""

    ROW_H = 24

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, int, int]] = []  # (label, count, size)
        self._hover: int = -1
        self.setMinimumHeight(60)
        self.setMouseTracking(True)

    def set_items(self, items: list[tuple[str, int, int]], metric: str = "size") -> None:
        """[(label, count, size)]，按展示值降序；metric: "size" / "count"。"""
        self._items = [(label or "(unknown)", c, s) for label, c, s in items]
        self._metric = metric
        self._hover = -1
        self.setMinimumHeight(12 + len(self._items) * self.ROW_H)
        self.setVisible(bool(self._items))
        self.update()

    def _row_rect(self, i: int) -> QRectF:
        return QRectF(6, 6 + i * self.ROW_H, self.width() - 12, self.ROW_H - 4)

    def _row_at(self, y: float) -> int:
        for i in range(len(self._items)):
            if self._row_rect(i).contains(4, y):
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
        label_w = min(int(w * 0.34), 220)
        val_w = 96
        bar_x = 6 + label_w + 10
        bar_w_max = w - 12 - label_w - 10 - val_w - 8

        for i, item in enumerate(self._items):
            label, _count, _size = item
            rect = self._row_rect(i)
            y = rect.y()

            if i == self._hover:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 10))
                painter.drawRoundedRect(rect, 5, 5)

            # label
            painter.setFont(font)
            painter.setPen(QColor(TEXT_DIM))
            painter.drawText(
                QRectF(6, y + 2, label_w, rect.height() - 4),
                Qt.AlignLeft | Qt.AlignVCenter,
                _elide_end(label, 24),
            )
            # 条形
            val = self._value(item)
            bh = rect.height() - 8
            bw = max(2.0, bar_w_max * val / max_val)
            grad = QLinearGradient(bar_x, 0, bar_x + bw, 0)
            grad.setColorAt(0.0, ACCENT)
            grad.setColorAt(1.0, ACCENT_2)
            painter.setPen(Qt.NoPen)
            painter.setBrush(grad)
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


class DashboardPanel(QWidget):
    _ready = Signal(int, object)
    day_selected = Signal(str)  # 点增长柱 → 宿主打开详情面板并切到该天

    _RANGE_BTN_QSS = f"""
QPushButton#rangeBtn {{
    color: {TEXT_DIM}; background: rgba(255,255,255,0.06);
    border: none; border-radius: 6px;
    padding: 4px 10px; font-size: 11px;
}}
QPushButton#rangeBtn:hover {{ background: rgba(255,255,255,0.12); color: {TEXT}; }}
QPushButton#rangeBtn:checked {{
    background: {ACCENT.name()}; color: #ffffff;
}}
"""

    def __init__(self, storage: Storage) -> None:
        super().__init__(objectName="panelRoot")
        self._storage = storage
        self.setWindowTitle(tr("硬盘新增文件 · 数据看板"))
        # 普通顶层窗即可，不要强制置顶（避免盖住其它软件）
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        apply_window_icon(self)
        self.setStyleSheet(PANEL_QSS + self._RANGE_BTN_QSS)
        self.resize(1080, 760)

        self._range = 14
        self._req = 0
        self._data_seq = 0
        self._growth_metric = "size"
        self._range_btns: list[tuple[int, QPushButton]] = []
        self._metric_btns: dict[str, QPushButton] = {}
        self._card_titles: dict[str, QLabel] = {}

        self._build()

        self._ready.connect(self._on_ready)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # 标题行：标题 + 范围按钮组 + 刷新
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.lbl_title = QLabel(tr("数据看板"), objectName="h1")
        title_row.addWidget(self.lbl_title)
        title_row.addStretch(1)
        for d in RANGES:
            btn = QPushButton(tr("{n} 天", n=d), objectName="rangeBtn")
            btn.setCheckable(True)
            btn.setChecked(d == self._range)
            btn.clicked.connect(lambda _=False, dd=d: self._set_range(dd))
            self._range_btns.append((d, btn))
            title_row.addWidget(btn)
        btn_refresh = QPushButton(tr("刷新"), objectName="primary")
        btn_refresh.clicked.connect(self.reload)
        self.btn_refresh = btn_refresh
        title_row.addWidget(btn_refresh)
        root.addLayout(title_row)

        # 滚动区：两列卡片
        self._scroll_area = QScrollArea(objectName="recentScroll")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 4, 0)
        grid.setSpacing(12)

        # 增长趋势（体积/数量 + 线性/对数）
        self._chart_growth = TrendChart(self)
        self._chart_growth.day_selected.connect(self.day_selected.emit)
        growth, growth_lbl, growth_lay = self._card(
            "growth",
            tr("增长趋势"),
            extra_buttons=[
                ("size", tr("体积")),
                ("count", tr("数量")),
            ],
        )
        self._card_titles["growth"] = growth_lbl
        growth_lay.addWidget(self._chart_growth)
        grid.addWidget(growth, 0, 0)

        # 累计增长
        self._chart_cum = CumulativeChart(self)
        cum, cum_lbl, cum_lay = self._card("cum", tr("累计增长"))
        self._card_titles["cum"] = cum_lbl
        cum_lay.addWidget(self._chart_cum)
        grid.addWidget(cum, 0, 1)

        # 磁盘剩余空间
        self._chart_space = SpaceTrendChart(self)
        space, space_lbl, space_lay = self._card("space", tr("磁盘剩余空间"))
        self._card_titles["space"] = space_lbl
        space_lay.addWidget(self._chart_space)
        grid.addWidget(space, 1, 0)

        # TOP 文件类型
        self._chart_exts = TopBarsChart(self)
        exts, exts_lbl, exts_lay = self._card("exts", tr("TOP 文件类型"))
        self._card_titles["exts"] = exts_lbl
        exts_lay.addWidget(self._chart_exts)
        grid.addWidget(exts, 1, 1)

        # TOP 目录（跨两列，行多）
        self._chart_folders = TopBarsChart(self)
        folders, folders_lbl, folders_lay = self._card("folders", tr("TOP 目录"))
        self._card_titles["folders"] = folders_lbl
        folders_lay.addWidget(self._chart_folders)
        grid.addWidget(folders, 2, 0, 1, 2)

        self._scroll_area.setWidget(content)
        root.addWidget(self._scroll_area, 1)

        # 状态行
        foot = QHBoxLayout()
        self.hint = QLabel(
            tr("单击增长柱可打开该天的详情"),
            objectName="dim",
        )
        foot.addWidget(self.hint)
        foot.addStretch(1)
        self.count_label = QLabel("", objectName="dim")
        foot.addWidget(self.count_label)
        root.addLayout(foot)

    def _card(
        self,
        key: str,
        title: str,
        extra_buttons: list[tuple[str, str]] | None = None,
    ) -> tuple[QFrame, QLabel, QVBoxLayout]:
        """带标题行的卡片；extra_buttons: [(value, text)] 互斥小按钮组。

        返回 (card, 标题 label, 内容布局)，标题 label 供 retranslate 更新文案。
        """
        card = QFrame(objectName="card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        lbl = QLabel(title, objectName="dim")
        head.addWidget(lbl)
        head.addStretch(1)
        if extra_buttons:
            for value, text in extra_buttons:
                btn = QPushButton(text, objectName="rangeBtn")
                btn.setCheckable(True)
                btn.setChecked(value == self._growth_metric)
                btn.clicked.connect(
                    lambda _=False, v=value: self._set_growth_metric(v)
                )
                self._metric_btns[value] = btn
                head.addWidget(btn)
        lay.addLayout(head)
        return card, lbl, lay

    # ---------- 数据 ----------

    def _set_range(self, days: int) -> None:
        if days == self._range:
            return
        self._range = days
        for d, btn in self._range_btns:
            btn.setChecked(d == days)
        self.reload()

    def _set_growth_metric(self, metric: str) -> None:
        if metric == self._growth_metric:
            return
        self._growth_metric = metric
        for value, btn in self._metric_btns.items():
            btn.setChecked(value == metric)
        self._chart_growth.set_metric(metric)

    def reload(self) -> None:
        self._req += 1
        req = self._req
        self.count_label.setText(tr("加载中…"))
        storage = self._storage
        days = self._range

        def work() -> None:
            bundle: object
            try:
                bundle = {
                    "days": days,
                    "trend": storage.fetch_days_with_data(days),
                    "folders": storage.top_folders_range(days, TOP_FOLDER_LIMIT),
                    "exts": storage.top_extensions_range(days, TOP_EXT_LIMIT),
                    "spaces": storage.disk_space_trend(days),
                    "seq": storage.change_seq,
                }
            except Exception as exc:
                bundle = exc
            self._ready.emit(req, bundle)

        threading.Thread(target=work, name="dw-dashboard", daemon=True).start()

    def _on_ready(self, req: int, payload: object) -> None:
        if req != self._req or not self.isVisible():
            return
        if isinstance(payload, Exception):
            self.count_label.setText(tr("加载失败：{err}", err=payload))
            return
        if not isinstance(payload, dict):
            return  # 防御：非打包结果直接丢弃

        self._data_seq = int(payload.get("seq", self._data_seq))
        trend = payload["trend"]
        self._chart_growth.set_days(trend, self._range)
        self._chart_cum.set_days(trend, self._range)

        series: dict[str, list[tuple[str, int]]] = {}
        for day, drive, free in payload["spaces"]:
            series.setdefault(drive, []).append((day, free))
        self._chart_space.set_series(series)

        self._chart_folders.set_items(payload["folders"], "size")
        self._chart_exts.set_items(payload["exts"], "size")

        total_size = sum(s.total_size for s in trend)
        total_count = sum(s.count for s in trend)
        self.count_label.setText(
            tr(
                "近 {days} 天：新增 {count} 个文件 · {size}",
                days=payload["days"],
                count=f"{total_count:,}",
                size=human_size(total_size),
            )
        )

    def _auto_refresh(self) -> None:
        if not self.isVisible():
            return
        # 数据没变就不重载（看板查询是聚合 SQL，但也没必要空跑）
        if self._storage.change_seq == self._data_seq:
            return
        self.reload()

    # ---------- 生命周期 ----------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_window_icon(self)
        enable_dark_titlebar(self)
        self.count_label.setText(tr("加载中…"))
        # 先让窗口画出来，再启动后台加载
        QTimer.singleShot(0, self.reload)
        self._timer.start(DASH_REFRESH_MS)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        self._req += 1

    def retranslate(self) -> None:
        self.setWindowTitle(tr("硬盘新增文件 · 数据看板"))
        self.lbl_title.setText(tr("数据看板"))
        self.btn_refresh.setText(tr("刷新"))
        self.hint.setText(tr("单击增长柱可打开该天的详情"))
        self._chart_growth.retranslate()
        for d, btn in self._range_btns:
            btn.setText(tr("{n} 天", n=d))
        for key, label in self._card_titles.items():
            label.setText(tr(_CARD_TITLES[key]))
        if "size" in self._metric_btns:
            self._metric_btns["size"].setText(tr("体积"))
            self._metric_btns["count"].setText(tr("数量"))
