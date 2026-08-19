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

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..storage import Storage, human_size
from .charts import CumulativeChart, SpaceTrendChart, TopBarsChart, TrendChart
from .style import (
    ACCENT,
    PANEL_QSS,
    TEXT,
    TEXT_DIM,
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

    def set_storage(self, storage: Storage) -> None:
        """换用新的数据库连接（位置变更失败回滚时由宿主调用）。"""
        self._storage = storage
        self.reload()

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
