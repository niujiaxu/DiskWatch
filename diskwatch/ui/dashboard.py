"""空间概览：对照磁盘实际变化与文件空间账本。

独立顶层窗（风格对齐详情面板，纯自绘图表）：
- 每日已归因变化：净占用 / 事件数柱状图，点柱可跳活动页
- 累计已归因净变化：空间账本累计折线
- 磁盘剩余空间：各盘剩余空间折线（看空间消耗速度）
- TOP 目录 / TOP 文件类型：近 N 天合计横向条形图

范围 7 / 14 / 30 / 90 天可切；全部查询在后台线程；打开时 5 秒自动
刷新，数据未变（storage.change_seq 脏检查）则跳过。
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

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
    apply_window_icon,
    enable_titlebar,
    panel_qss,
    range_button_qss,
    theme_tokens,
)

RANGES = (7, 14, 30, 90)
TOP_FOLDER_LIMIT = 10
TOP_EXT_LIMIT = 8
DASH_REFRESH_MS = 5000

# 卡片标题 key → 中文原文（retranslate 用）
_CARD_TITLES = {
    "growth": "每日已归因变化",
    "cum": "累计已归因净变化",
    "space": "24 小时磁盘剩余空间",
    "exts": "分类空间变化",
    "files": "占用增长最大的文件",
    "folders": "TOP 目录",
}

# 多折线色板：青 / 青绿 / 橙 / 蓝 / 紫 / 黄

class DashboardPanel(QWidget):
    _ready = Signal(int, object)
    day_selected = Signal(str)  # 点增长柱 → 宿主打开详情面板并切到该天

    def __init__(self, storage: Storage) -> None:
        super().__init__(objectName="panelRoot")
        self._storage = storage
        self.setWindowTitle(tr("DiskWatch · 概览"))
        # 普通顶层窗即可，不要强制置顶（避免盖住其它软件）
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        apply_window_icon(self)
        self.apply_theme()
        self.resize(1080, 760)

        self._range = 14
        self._req = 0
        self._data_seq = 0
        self._growth_metric = "size"
        self._range_btns: list[tuple[int, QPushButton]] = []
        self._metric_btns: dict[str, QPushButton] = {}
        self._card_titles: dict[str, QLabel] = {}
        self._workers: list[threading.Thread] = []
        self._workers_lock = threading.Lock()

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
        self.lbl_title = QLabel(tr("概览"), objectName="h1")
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

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metric_actual = self._metric_card(metrics, tr("磁盘实际变化"))
        self.metric_attributed = self._metric_card(metrics, tr("已归因变化"))
        self.metric_unattributed = self._metric_card(metrics, tr("未归因变化"))
        root.addLayout(metrics)

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

        # 每日已归因净变化（净变化/事件数 + 线性/对数）
        self._chart_growth = TrendChart(self)
        self._chart_growth.day_selected.connect(self.day_selected.emit)
        growth, growth_lbl, growth_lay = self._card(
            "growth",
            tr("每日已归因变化"),
            extra_buttons=[
                ("size", tr("净变化")),
                ("count", tr("事件数")),
            ],
        )
        self._card_titles["growth"] = growth_lbl
        growth_lay.addWidget(self._chart_growth)
        grid.addWidget(growth, 0, 0)

        # 累计已归因净变化
        self._chart_cum = CumulativeChart(self)
        cum, cum_lbl, cum_lay = self._card("cum", tr("累计已归因净变化"))
        self._card_titles["cum"] = cum_lbl
        cum_lay.addWidget(self._chart_cum)
        grid.addWidget(cum, 0, 1)

        # 磁盘剩余空间
        self._chart_space = SpaceTrendChart(self)
        space, space_lbl, space_lay = self._card(
            "space", tr("24 小时磁盘剩余空间")
        )
        self._card_titles["space"] = space_lbl
        self.space_current = QLabel("", objectName="dim")
        self.space_current.setWordWrap(True)
        space_lay.addWidget(self.space_current)
        space_lay.addWidget(self._chart_space)
        grid.addWidget(space, 1, 0)

        # TOP 文件类型
        self._chart_exts = TopBarsChart(self)
        exts, exts_lbl, exts_lay = self._card("exts", tr("分类空间变化"))
        self._card_titles["exts"] = exts_lbl
        exts_lay.addWidget(self._chart_exts)
        grid.addWidget(exts, 1, 1)

        # 占用增长最大的文件（跨两列）
        self._chart_files = TopBarsChart(self)
        files, files_lbl, files_lay = self._card(
            "files", tr("占用增长最大的文件")
        )
        self._card_titles["files"] = files_lbl
        files_lay.addWidget(self._chart_files)
        grid.addWidget(files, 2, 0, 1, 2)

        # TOP 目录（跨两列，行多）
        self._chart_folders = TopBarsChart(self)
        folders, folders_lbl, folders_lay = self._card("folders", tr("TOP 目录"))
        self._card_titles["folders"] = folders_lbl
        folders_lay.addWidget(self._chart_folders)
        grid.addWidget(folders, 3, 0, 1, 2)

        # 行拉伸：窗口拉大时四行共享多余高度，图表随卡片一起放大，不留白
        for row in range(4):
            grid.setRowStretch(row, 1)

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

    def _metric_card(self, row: QHBoxLayout, title: str) -> QLabel:
        card = QFrame(objectName="card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        lay.addWidget(QLabel(title, objectName="dim"))
        value = QLabel("0 B", objectName="statValue")
        lay.addWidget(value)
        row.addWidget(card, 1)
        return value

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
                now = time.time()
                bundle = {
                    "days": days,
                    "trend": storage.space_day_summaries(days),
                    "folders": storage.top_space_folders(days, TOP_FOLDER_LIMIT),
                    "files": storage.top_space_files(days, TOP_FOLDER_LIMIT),
                    "recent_spaces": storage.disk_samples(since=now - 86400),
                    "attribution": storage.daily_attribution_summary(
                        (date.today() - timedelta(days=days - 1)).isoformat(),
                        date.today().isoformat(),
                    ),
                    "categories": storage.category_space_totals(
                        now - days * 86400, now, limit=TOP_EXT_LIMIT
                    ),
                    "seq": storage.change_seq,
                }
            except Exception as exc:
                bundle = exc
            finally:
                storage.release_reader()
            try:
                self._ready.emit(req, bundle)
            finally:
                current = threading.current_thread()
                with self._workers_lock:
                    if current in self._workers:
                        self._workers.remove(current)

        worker = threading.Thread(target=work, name="dw-dashboard", daemon=True)
        with self._workers_lock:
            self._workers.append(worker)
        worker.start()

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
        self._chart_growth.set_space_days(trend, self._range)
        self._chart_cum.set_space_days(trend, self._range)

        series: dict[str, list[tuple[str, int]]] = {}
        latest_free: dict[str, int] = {}
        for sample in payload["recent_spaces"]:
            # 秒级精度即可：微秒（%f）在轴标签上刷一长串数字且没意义
            sample_label = datetime.fromtimestamp(sample.sampled_at).strftime(
                "%m-%d %H:%M:%S"
            )
            series.setdefault(sample.drive, []).append(
                (sample_label, sample.free_bytes)
            )
            latest_free[sample.drive] = sample.free_bytes
        self._chart_space.set_series(series)
        self.space_current.setText(
            "  ·  ".join(
                tr("{drive} 当前剩余 {free}", drive=drive, free=human_size(free))
                for drive, free in sorted(latest_free.items())
            )
        )

        folders = [
            (f"{folder}  {_signed_space(delta)}", count, abs(delta))
            for folder, count, delta in payload.get("folders", [])
        ]
        self._chart_folders.set_items(folders, "size")
        categories = [
            (f"{_category_label(key)}  {_signed_space(delta)}", 0, abs(delta))
            for key, delta in payload.get("categories", [])
        ]
        self._chart_exts.set_items(categories, "size")

        files = [
            (f"{path}  {_signed_space(delta)}", count, delta)
            for path, count, delta in payload.get("files", [])
        ]
        self._chart_files.set_items(files, "size")

        attribution = payload.get("attribution", [])
        actual = sum(item.actual_delta for item in attribution)
        attributed = sum(item.attributed_delta for item in attribution)
        unattributed = sum(item.unattributed_delta for item in attribution)
        self.metric_actual.setText(_signed_space(actual))
        self.metric_attributed.setText(_signed_space(attributed))
        self.metric_unattributed.setText(_signed_space(unattributed))
        t = theme_tokens()
        for label, value in (
            (self.metric_actual, actual),
            (self.metric_attributed, attributed),
            (self.metric_unattributed, unattributed),
        ):
            label.setStyleSheet(
                f"color: {t.warning if value > 0 else t.success if value < 0 else t.text};"
            )

        net_change = sum(s.net_bytes for s in trend)
        total_count = sum(s.event_count for s in trend)
        self.count_label.setText(
            tr(
                "近 {days} 天：记录 {count} 个空间事件 · 已归因 {size}",
                days=payload["days"],
                count=f"{total_count:,}",
                size=_signed_space(net_change),
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
        enable_titlebar(self)
        self.count_label.setText(tr("加载中…"))
        # 先让窗口画出来，再启动后台加载
        QTimer.singleShot(0, self.reload)
        self._timer.start(DASH_REFRESH_MS)

    def apply_theme(self) -> None:
        self.setStyleSheet(panel_qss() + range_button_qss())
        for chart in self.findChildren(QWidget):
            hook = getattr(chart, "apply_theme", None)
            if callable(hook):
                hook()
            chart.update()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        self._req += 1

    def closeEvent(self, event) -> None:
        self._req += 1
        self.wait_for_idle()
        super().closeEvent(event)

    def wait_for_idle(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._workers_lock:
                workers = list(self._workers)
            if not workers:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            for worker in workers:
                worker.join(timeout=min(remaining, 0.5))

    def retranslate(self) -> None:
        self.setWindowTitle(tr("DiskWatch · 概览"))
        self.lbl_title.setText(tr("概览"))
        self.btn_refresh.setText(tr("刷新"))
        self.hint.setText(tr("单击增长柱可打开该天的详情"))
        self._chart_growth.retranslate()
        for d, btn in self._range_btns:
            btn.setText(tr("{n} 天", n=d))
        for key, label in self._card_titles.items():
            label.setText(tr(_CARD_TITLES[key]))
        if "size" in self._metric_btns:
            self._metric_btns["size"].setText(tr("净变化"))
            self._metric_btns["count"].setText(tr("事件数"))


def _signed_space(value: int) -> str:
    if value == 0:
        return "0 B"
    return ("+" if value > 0 else "−") + human_size(abs(value))


def _category_label(key: str) -> str:
    labels = {
        "user": "用户文件",
        "download": "下载文件",
        "system": "系统与更新",
        "software": "软件安装",
        "cache": "应用缓存",
        "temporary": "临时文件",
        "development": "开发产物",
        "vm_container": "虚拟机和容器",
        "unclassified": "未分类",
    }
    return tr(labels.get(key, "未分类"))
