"""分页文件活动页，直接展示空间变化账本。"""

from __future__ import annotations

import csv
import math
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..apps import (
    AppGroup,
    build_groups,
    label_for_key,
    resolve_app,
    resolve_dir_root,
)
from ..i18n import tr
from ..storage import SpaceEvent, Storage, human_size
from ..watcher import open_in_explorer
from .picker import DayPicker
from .style import NoFocusDelegate, panel_qss, theme_tokens

PAGE_SIZE = 200
SEARCH_DEBOUNCE_MS = 260

_HEADERS_EVENTS = ("时间", "文件名", "活动类型", "空间影响", "分类", "所在目录")
_HEADERS_OVERVIEW = ("最近活动", "应用", "事件数", "净变化", "占比", "主要目录")

CATEGORY_LABELS = {
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

EVENT_LABELS = {
    "created": "创建",
    "modified": "修改",
    "deleted": "删除",
    "moved": "移动",
    "moved_in": "跨盘移入",
    "moved_out": "跨盘移出",
    "resurrected": "重新创建",
    "churn": "高频变化",
}


def _event_label(event: SpaceEvent) -> str:
    """高频聚合行显示合并条数；其余走常规事件名。"""
    if event.merged_count > 1:
        return tr("高频变化 ×{n}", n=event.merged_count)
    return tr(EVENT_LABELS.get(event.event_type, event.event_type))


# 可排序列 → (升序键, 降序键, 首次点击是否用降序)。「所在目录」列不参与排序。
_SORT_COLUMNS: dict[int, tuple[str, str, bool]] = {
    0: ("time_asc", "time_desc", True),
    1: ("name_asc", "name_desc", False),
    2: ("type_asc", "type_desc", False),
    3: ("delta_asc", "delta_desc", True),
    4: ("category_asc", "category_desc", False),
}


class ActivityPanel(QWidget):
    """固定页大小，避免几十万条事件一次性进入 UI。"""

    open_dashboard = Signal()
    _export_done = Signal(object)
    _history_ready = Signal(int, str, object)
    _page_ready = Signal(int, object)
    _app_ready = Signal(int, object)

    def __init__(self, storage: Storage, parent=None) -> None:
        super().__init__(parent, objectName="panelRoot")
        self._storage = storage
        self._page = 0
        self._total = 0
        self._events: list[SpaceEvent] = []
        self._row_events: dict[int, int] = {}
        self._collapsed_groups: set[str] = set()
        self._selected_day: str | None = None
        self._space_sort_order = "time_desc"
        self._app_groups: list[AppGroup] = []
        self._app_rows: dict[int, tuple[str, str, str]] = {}
        self._app_drill: tuple[str, str, str] | None = None  # (key, sub, label)
        self._history_req = 0
        self._reload_req = 0
        self._building = True
        self._workers: list[threading.Thread] = []
        self._workers_lock = threading.Lock()
        self._splitter_reflow_timer = QTimer(self)
        self._splitter_reflow_timer.setSingleShot(True)
        self._splitter_reflow_timer.timeout.connect(self._fit_splitter)
        self._build()
        self._building = False
        self.apply_theme()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._filters_changed)
        self._export_done.connect(self._on_export_done)
        self._history_ready.connect(self._on_history_ready)
        self._page_ready.connect(self._on_page_ready)
        self._app_ready.connect(self._on_app_ready)
        self.reload_filters()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        self.title = QLabel(tr("文件活动"), objectName="h1")
        self.summary = QLabel("", objectName="dim")
        title_row.addWidget(self.title)
        title_row.addStretch(1)
        title_row.addWidget(self.summary)
        root.addLayout(title_row)

        selectors = QHBoxLayout()
        selectors.setSpacing(8)
        self.range_picker = DayPicker()
        for label, value in (
            (tr("今天"), "today"),
            (tr("最近 24 小时"), "24h"),
            (tr("最近 7 天"), "7d"),
            (tr("最近 30 天"), "30d"),
            (tr("全部时间"), "all"),
        ):
            self.range_picker.addItem(label, value)
        self.drive_picker = DayPicker()
        self.view_picker = DayPicker()
        self.view_picker.addItem(tr("全部视图"), "all")
        self.view_picker.addItem(tr("关注视图"), "focus")
        self.category_picker = DayPicker()
        self.event_picker = DayPicker()
        self.event_picker.addItem(tr("全部事件"), None)
        for key, label in EVENT_LABELS.items():
            self.event_picker.addItem(tr(label), key)
        self.group_picker = DayPicker()
        self.group_picker.addItem(tr("不分组"), None)
        self.group_picker.addItem(tr("按应用分组"), "app")
        self.group_picker.addItem(tr("按分类分组"), "category")
        for picker in (
            self.range_picker,
            self.view_picker,
            self.drive_picker,
            self.category_picker,
            self.event_picker,
            self.group_picker,
        ):
            picker.setMinimumWidth(84)
            picker.currentIndexChanged.connect(self._filters_changed)
            selectors.addWidget(picker, 1)
        # 默认落在「按应用分组」总览（信号会因 _building 提前返回，稍后统一 reload）
        self.group_picker.setCurrentIndex(1)
        root.addLayout(selectors)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索文件名或路径"))
        self.search.textChanged.connect(
            lambda _text: self._search_timer.start(SEARCH_DEBOUNCE_MS)
        )
        actions.addWidget(self.search, 1)
        self.btn_refresh = QPushButton(tr("刷新"))
        self.btn_refresh.clicked.connect(self.reload)
        actions.addWidget(self.btn_refresh)
        self.btn_export = QPushButton(tr("导出 CSV"))
        self.btn_export.clicked.connect(self._export_csv)
        actions.addWidget(self.btn_export)
        root.addLayout(actions)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table = QTableWidget(0, 6)
        self._set_header_labels("events")
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # 去掉当前单元格的黑色虚线焦点框（整行高亮已足够指示选中位置）
        self.table.setItemDelegate(NoFocusDelegate(self.table))
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        # ResizeToContents 会在可见表格逐项 setItem() 时反复测量整列，
        # 真实数据下打开页面可把一次 200 行重绘放大到数十秒。固定信息列
        # 使用稳定宽度，只让文件名和目录瓜分剩余空间。
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._sort_by_column)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        self.table.setColumnWidth(0, 132)
        self.table.setColumnWidth(2, 88)
        self.table.setColumnWidth(3, 112)
        self.table.setColumnWidth(4, 100)
        self._sync_sort_indicator()
        self.table.itemSelectionChanged.connect(self._show_selected)
        self.table.cellClicked.connect(self._table_clicked)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._reveal_selected())
        self.splitter.addWidget(self.table)

        self.detail_card = QFrame(objectName="card")
        self.detail_card.setMinimumWidth(220)
        self.detail_card.setMaximumWidth(460)
        self.detail_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        detail_lay = QVBoxLayout(self.detail_card)
        detail_lay.setContentsMargins(16, 16, 16, 16)
        detail_lay.setSpacing(8)
        self.detail_name = QLabel(tr("选择一条活动"), objectName="h1")
        self.detail_name.setWordWrap(True)
        self.detail_meta = QLabel("", objectName="dim")
        self.detail_meta.setWordWrap(True)
        self.detail_path = QLabel("", objectName="dim")
        self.detail_path.setWordWrap(True)
        self.detail_sizes = QLabel("", objectName="dim")
        self.detail_sizes.setWordWrap(True)
        self.detail_history_title = QLabel(tr("大小变化历史"), objectName="sectionTitle")
        self.detail_history = QLabel("", objectName="dim")
        self.detail_history.setWordWrap(True)
        self.detail_history.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # QLabel 会把不含空格的长文件名和 Windows 路径计入最小宽度。
        # 若不忽略这个横向 size hint，详情卡可被撑到 900px 以上，继而
        # 让 QSplitter 在窗口刚显示或最大化时保留一段未分配的空白区域。
        for label in (
            self.detail_name,
            self.detail_meta,
            self.detail_path,
            self.detail_sizes,
            self.detail_history_title,
            self.detail_history,
        ):
            policy = label.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Ignored)
            label.setSizePolicy(policy)
        detail_lay.addWidget(self.detail_name)
        detail_lay.addWidget(self.detail_meta)
        detail_lay.addWidget(self.detail_sizes)
        detail_lay.addWidget(self.detail_path)
        detail_lay.addWidget(self.detail_history_title)
        detail_lay.addWidget(self.detail_history, 1)
        self.btn_reveal = QPushButton(tr("在资源管理器中定位"))
        self.btn_reveal.clicked.connect(self._reveal_selected)
        self.btn_copy = QPushButton(tr("复制路径"))
        self.btn_copy.clicked.connect(self._copy_selected)
        detail_lay.addWidget(self.btn_reveal)
        detail_lay.addWidget(self.btn_copy)
        self.splitter.addWidget(self.detail_card)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        root.addWidget(self.splitter, 1)

        footer = QHBoxLayout()
        self.page_label = QLabel("", objectName="dim")
        self.btn_prev = QPushButton(tr("上一页"))
        self.btn_next = QPushButton(tr("下一页"))
        self.btn_prev.clicked.connect(lambda: self._change_page(-1))
        self.btn_next.clicked.connect(lambda: self._change_page(1))
        footer.addWidget(self.page_label)
        footer.addStretch(1)
        footer.addWidget(self.btn_prev)
        footer.addWidget(self.btn_next)
        root.addLayout(footer)

    def set_storage(self, storage: Storage) -> None:
        self._storage = storage
        self.reload_filters()
        self.reload()

    def reload_filters(self) -> None:
        current_drive = self.drive_picker.currentData()
        current_category = self.category_picker.currentData()
        drives, categories = self._storage.space_event_filter_values()
        self.drive_picker.blockSignals(True)
        self.category_picker.blockSignals(True)
        self.drive_picker.clear()
        self.drive_picker.addItem(tr("全部磁盘"), None)
        for drive in drives:
            self.drive_picker.addItem(drive, drive)
        self.category_picker.clear()
        self.category_picker.addItem(tr("全部分类"), None)
        for category in categories:
            self.category_picker.addItem(
                tr(CATEGORY_LABELS.get(category, "未分类")), category
            )
        for picker, value in (
            (self.drive_picker, current_drive),
            (self.category_picker, current_category),
        ):
            index = picker.findData(value)
            picker.setCurrentIndex(max(index, 0))
            picker.blockSignals(False)

    def _time_range(self) -> tuple[float | None, float | None]:
        now = time.time()
        if self._selected_day is not None:
            start = datetime.strptime(self._selected_day, "%Y-%m-%d").timestamp()
            return start, start + 86400
        value = self.range_picker.currentData()
        if value == "today":
            start = datetime.combine(datetime.now().date(), datetime.min.time()).timestamp()
            return start, now
        if value == "24h":
            return now - 86400, now
        if value == "7d":
            return now - 7 * 86400, now
        if value == "30d":
            return now - 30 * 86400, now
        return None, None

    def _filters_changed(self, *_args) -> None:
        if self._building:
            return
        self._selected_day = None
        self._collapsed_groups.clear()
        self._app_drill = None
        self._page = 0
        self.reload()

    def _current_filters(self) -> dict:
        since, until = self._time_range()
        filters: dict = {
            "since": since,
            "until": until,
            "drive": self.drive_picker.currentData(),
            "category": self.category_picker.currentData(),
            "event_type": self.event_picker.currentData(),
            "keyword": self.search.text().strip() or None,
            "focus_only": self.view_picker.currentData() == "focus",
        }
        if self._app_drill is not None:
            key, sub, _label = self._app_drill
            filters["app_key"] = key
            if sub:
                filters["app_sub"] = sub
        return filters

    def reload(self, keep_day: bool = False) -> None:
        """后台统计和分页查询；任何筛选条件都不能阻塞 Qt 主线程。"""
        filters = self._current_filters()
        if self.group_picker.currentData() == "app" and self._app_drill is None:
            self._reload_app_overview(filters)
            return
        self._reload_event_page(filters)

    def _reload_event_page(self, filters: dict) -> None:
        self._reload_req += 1
        req = self._reload_req
        requested_page = self._page
        requested_sort = self._space_sort_order
        storage = self._storage
        self.summary.setText(tr("加载中…"))
        self.btn_refresh.setEnabled(False)

        def work() -> None:
            try:
                total = storage.space_event_count(**filters)
                max_page = max(0, math.ceil(total / PAGE_SIZE) - 1)
                page = min(requested_page, max_page)
                events = storage.space_events(
                    **filters,
                    sort_order=requested_sort,
                    limit=PAGE_SIZE,
                    offset=page * PAGE_SIZE,
                )
                result: object = (total, page, events)
            except Exception as exc:
                result = exc
            finally:
                storage.release_reader()
            try:
                self._page_ready.emit(req, result)
            finally:
                current = threading.current_thread()
                with self._workers_lock:
                    if current in self._workers:
                        self._workers.remove(current)

        worker = threading.Thread(target=work, name="dw-activity-page", daemon=True)
        with self._workers_lock:
            self._workers.append(worker)
        worker.start()

    def _reload_app_overview(self, filters: dict) -> None:
        """按应用总览：整个筛选结果聚合（跨页一致），不在分页内分组。"""
        self._reload_req += 1
        req = self._reload_req
        storage = self._storage
        self.summary.setText(tr("加载中…"))
        self.btn_refresh.setEnabled(False)

        def work() -> None:
            try:
                rows = storage.app_group_rows(**filters)
                folder_rows = storage.app_group_folders(**filters)
                result: object = build_groups(rows, folder_rows)
            except Exception as exc:
                result = exc
            finally:
                storage.release_reader()
            try:
                self._app_ready.emit(req, result)
            finally:
                current = threading.current_thread()
                with self._workers_lock:
                    if current in self._workers:
                        self._workers.remove(current)

        worker = threading.Thread(target=work, name="dw-activity-apps", daemon=True)
        with self._workers_lock:
            self._workers.append(worker)
        worker.start()

    def _on_page_ready(self, req: int, result: object) -> None:
        if req != self._reload_req:
            return
        self.btn_refresh.setEnabled(True)
        if isinstance(result, Exception):
            self.summary.setText(tr("加载失败：{err}", err=result))
            return
        if not isinstance(result, tuple) or len(result) != 3:
            return
        total, page, events = result
        if not isinstance(events, list):
            return
        self._total = int(total)
        self._page = int(page)
        self._events = [event for event in events if isinstance(event, SpaceEvent)]
        self._fill_table()

    def _on_app_ready(self, req: int, result: object) -> None:
        if req != self._reload_req:
            return
        self.btn_refresh.setEnabled(True)
        if isinstance(result, Exception):
            self.summary.setText(tr("加载失败：{err}", err=result))
            return
        if not isinstance(result, list):
            return
        self._app_groups = [g for g in result if isinstance(g, AppGroup)]
        self._fill_table()

    def _fill_table(self) -> None:
        if self.group_picker.currentData() == "app":
            self._fill_app_table()
            return
        # 可见 QTableWidget 逐格插入会同步触发布局、重绘和选择信号。
        # 整批更新期间全部关闭，完成后只刷新一次详情和 viewport。
        signal_blocker = QSignalBlocker(self.table)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setSortingEnabled(False)
            self.table.clearSpans()
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self._row_events.clear()
            self._set_header_labels("events")
            t = theme_tokens()
            group_mode = self.group_picker.currentData()
            group_counts: dict[str, int] = {}
            if group_mode is None:
                rows: list[tuple[str | None, int | None]] = [
                    (None, index) for index in range(len(self._events))
                ]
            else:
                grouped: dict[str, list[int]] = {}
                for index, event in enumerate(self._events):
                    key = event.category or tr("未分类")
                    grouped.setdefault(key, []).append(index)
                rows = []
                for key, indexes in grouped.items():
                    group_counts[key] = len(indexes)
                    rows.append((key, None))
                    if key not in self._collapsed_groups:
                        rows.extend((key, index) for index in indexes)

            self.table.setRowCount(len(rows))
            for row, (group_key, event_index) in enumerate(rows):
                if event_index is None:
                    assert group_key is not None
                    marker = "▸" if group_key in self._collapsed_groups else "▾"
                    label = tr(CATEGORY_LABELS.get(group_key, "未分类"))
                    count = group_counts.get(group_key, 0)
                    item = QTableWidgetItem(
                        tr(
                            "{marker} {label} · {count} 条",
                            marker=marker,
                            label=label,
                            count=count,
                        )
                    )
                    item.setData(Qt.UserRole + 1, group_key)
                    item.setForeground(t.color("accent"))
                    item.setBackground(t.color("surface_raised"))
                    self.table.setItem(row, 0, item)
                    self.table.setSpan(row, 0, 1, 6)
                    continue

                event = self._events[event_index]
                self._put_event_row(row, event_index, t)
            if self._row_events:
                self.table.selectRow(min(self._row_events))
        finally:
            signal_blocker.unblock()
            self.table.setUpdatesEnabled(True)
        pages = max(1, math.ceil(self._total / PAGE_SIZE))
        self.page_label.setText(
            tr(
                "共 {count} 条 · 第 {page}/{pages} 页",
                count=f"{self._total:,}", page=self._page + 1, pages=pages,
            )
        )
        self.summary.setText(tr("每页最多 {count} 条", count=PAGE_SIZE))
        self.btn_prev.setEnabled(self._page > 0)
        self.btn_next.setEnabled(self._page + 1 < pages)
        self._sync_sort_indicator()
        self.table.viewport().update()
        if self._row_events:
            self._show_selected()
        else:
            self._clear_detail()

    def _put_event_row(self, row: int, event_index: int, t) -> None:
        event = self._events[event_index]
        self._row_events[row] = event_index
        path = Path(event.path)
        app_label = tr(label_for_key(resolve_app(event.path)[0]))
        tooltip = tr(
            "完整路径：{path}\n归属应用：{app}", path=event.path, app=app_label
        )
        values = (
            datetime.fromtimestamp(event.occurred_at).strftime("%m-%d %H:%M:%S"),
            path.name,
            _event_label(event),
            _signed_size(event.delta_bytes),
            tr(CATEGORY_LABELS.get(event.category, "未分类")),
            _short_path(str(path.parent)),
        )
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.UserRole, event_index)
            item.setToolTip(tooltip)
            if col == 3:
                role = "warning" if event.delta_bytes > 0 else "success"
                item.setForeground(t.color(role))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, col, item)

    def _set_header_labels(self, mode: str) -> None:
        labels = _HEADERS_OVERVIEW if mode == "overview" else _HEADERS_EVENTS
        self.table.setHorizontalHeaderLabels([tr(label) for label in labels])

    def _header_mode(self) -> str:
        if self.group_picker.currentData() == "app" and self._app_drill is None:
            return "overview"
        return "events"

    def _fill_app_table(self) -> None:
        if self._app_drill is None:
            self._fill_app_overview()
        else:
            self._fill_app_drill()

    def _fill_app_overview(self) -> None:
        """按应用总览：一行一个分组，点行下钻。"""
        signal_blocker = QSignalBlocker(self.table)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setSortingEnabled(False)
            self.table.clearSpans()
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self._row_events.clear()
            self._app_rows.clear()
            self._set_header_labels("overview")
            t = theme_tokens()
            groups = self._app_groups
            total = sum(group.count for group in groups)
            self.table.setRowCount(len(groups))
            for row, group in enumerate(groups):
                self._app_rows[row] = (group.key, group.sub, group.label)
                share = f"{group.count / total * 100:.0f}%" if total else "0%"
                # 展示"应用老家"（归属根）而不是事件最多的深层目录
                folder_full = resolve_dir_root(group.shallow or group.folder)
                if not folder_full:
                    folder_full = group.shallow or group.folder
                tooltip = tr(
                    "{path}\n点击查看该应用的活动明细", path=folder_full
                )
                values = (
                    datetime.fromtimestamp(group.last_at).strftime("%m-%d %H:%M"),
                    tr(group.label),
                    f"{group.count:,}",
                    _signed_size(group.net),
                    share,
                    _short_path(folder_full),
                )
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole + 1, group.key)
                    item.setToolTip(tooltip)
                    if col in (2, 3):
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if col == 3:
                        role = "warning" if group.net > 0 else "success"
                        item.setForeground(t.color(role))
                    self.table.setItem(row, col, item)
        finally:
            signal_blocker.unblock()
            self.table.setUpdatesEnabled(True)
        self.page_label.setText(tr("共 {count} 个应用", count=len(self._app_groups)))
        self.summary.setText(tr("点击分组查看明细"))
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)
        self._sync_sort_indicator()
        self.table.viewport().update()
        self._clear_detail()

    def _fill_app_drill(self) -> None:
        """应用明细：首行是返回总览，下面是该应用的平铺事件（可分页/排序）。"""
        assert self._app_drill is not None
        _key, _sub, label = self._app_drill
        signal_blocker = QSignalBlocker(self.table)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setSortingEnabled(False)
            self.table.clearSpans()
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self._row_events.clear()
            self._app_rows.clear()
            self._set_header_labels("events")
            t = theme_tokens()
            self.table.setRowCount(1 + len(self._events))
            back = QTableWidgetItem(tr("← 返回应用总览 · {label}", label=label))
            back.setData(Qt.UserRole + 2, True)
            back.setForeground(t.color("accent"))
            back.setBackground(t.color("surface_raised"))
            self.table.setItem(0, 0, back)
            self.table.setSpan(0, 0, 1, 6)
            for index in range(len(self._events)):
                self._put_event_row(index + 1, index, t)
            if self._row_events:
                self.table.selectRow(min(self._row_events))
        finally:
            signal_blocker.unblock()
            self.table.setUpdatesEnabled(True)
        pages = max(1, math.ceil(self._total / PAGE_SIZE))
        self.page_label.setText(
            tr(
                "共 {count} 条 · 第 {page}/{pages} 页",
                count=f"{self._total:,}", page=self._page + 1, pages=pages,
            )
        )
        self.summary.setText(tr("每页最多 {count} 条", count=PAGE_SIZE))
        self.btn_prev.setEnabled(self._page > 0)
        self.btn_next.setEnabled(self._page + 1 < pages)
        self._sync_sort_indicator()
        self.table.viewport().update()
        if self._row_events:
            self._show_selected()
        else:
            self._clear_detail()

    def _selected_event(self) -> SpaceEvent | None:
        row = self.table.currentRow()
        index = self._row_events.get(row)
        return self._events[index] if index is not None else None

    def _table_clicked(self, row: int, _column: int) -> None:
        if self.group_picker.currentData() == "app":
            if self._app_drill is None:
                entry = self._app_rows.get(row)
                if entry is None:
                    return
                self._app_drill = entry
                self._page = 0
                self.reload(keep_day=True)
            elif row == 0:
                self._app_drill = None
                self._page = 0
                self.reload(keep_day=True)
            return
        item = self.table.item(row, 0)
        group_key = item.data(Qt.UserRole + 1) if item is not None else None
        if group_key is None:
            return
        if group_key in self._collapsed_groups:
            self._collapsed_groups.remove(group_key)
        else:
            self._collapsed_groups.add(group_key)
        self._fill_table()

    def _show_selected(self) -> None:
        event = self._selected_event()
        if event is None:
            self._clear_detail()
            return
        path = Path(event.path)
        self.detail_name.setText(path.name or event.path)
        self.detail_meta.setText(
            tr("{event} · {category}\n{time}",
               event=_event_label(event),
               category=tr(CATEGORY_LABELS.get(event.category, "未分类")),
               time=datetime.fromtimestamp(event.occurred_at).strftime("%Y-%m-%d %H:%M:%S"))
        )
        if event.merged_count > 1:
            self.detail_meta.setText(
                self.detail_meta.text()
                + "\n"
                + tr("已合并 {n} 次变化", n=event.merged_count)
            )
        self.detail_sizes.setText(
            tr("原大小 {old}\n新大小 {new}\n空间影响 {delta}",
               old=human_size(event.old_size), new=human_size(event.new_size),
               delta=_signed_size(event.delta_bytes))
        )
        self.detail_path.setText(event.path)
        self.detail_history.setText(tr("正在加载历史…"))
        self._load_history_async(event.path)
        self.btn_reveal.setEnabled(True)
        self.btn_copy.setEnabled(True)

    def _load_history_async(self, path: str) -> None:
        self._history_req += 1
        req = self._history_req
        storage = self._storage

        def work() -> None:
            try:
                result: object = storage.file_event_history(path, limit=12)
            except Exception as exc:
                result = exc
            finally:
                storage.release_reader()
            try:
                self._history_ready.emit(req, path, result)
            finally:
                current = threading.current_thread()
                with self._workers_lock:
                    if current in self._workers:
                        self._workers.remove(current)

        worker = threading.Thread(target=work, name="dw-file-history", daemon=True)
        with self._workers_lock:
            self._workers.append(worker)
        worker.start()

    def _on_history_ready(self, req: int, path: str, result: object) -> None:
        event = self._selected_event()
        if req != self._history_req or event is None or event.path != path:
            return
        if isinstance(result, Exception):
            self.detail_history.setText(tr("历史加载失败：{err}", err=result))
            return
        if not isinstance(result, list):
            return
        self.detail_history.setText(
            "\n".join(
                tr(
                    "{time}  {old} → {new}  ({delta})",
                    time=datetime.fromtimestamp(item.occurred_at).strftime(
                        "%m-%d %H:%M"
                    ),
                    old=human_size(item.old_size),
                    new=human_size(item.new_size),
                    delta=_signed_size(item.delta_bytes),
                )
                for item in result
                if isinstance(item, SpaceEvent)
            )
            or tr("暂无历史")
        )

    def _clear_detail(self) -> None:
        self._history_req += 1
        self.detail_name.setText(tr("选择一条活动"))
        self.detail_meta.clear()
        self.detail_sizes.clear()
        self.detail_path.clear()
        self.detail_history.clear()
        self.btn_reveal.setEnabled(False)
        self.btn_copy.setEnabled(False)

    def _reveal_selected(self) -> None:
        event = self._selected_event()
        if event is None:
            return
        try:
            open_in_explorer(event.path)
        except OSError as exc:
            QMessageBox.warning(self, tr("无法定位文件"), str(exc))

    def _copy_selected(self) -> None:
        event = self._selected_event()
        app = QApplication.instance()
        if event is not None and app is not None:
            app.clipboard().setText(event.path)

    def _change_page(self, direction: int) -> None:
        self._page = max(0, self._page + direction)
        self.reload(keep_day=True)

    def _sort_by_column(self, column: int) -> None:
        """点击表头切换升/降序（数据库级排序，跨分页顺序正确）。

        「所在目录」列不参与排序；应用总览页不参与（点行是下钻）。
        """
        if self.group_picker.currentData() == "app" and self._app_drill is None:
            self._sync_sort_indicator()
            return
        spec = _SORT_COLUMNS.get(column)
        if spec is None:
            self._sync_sort_indicator()
            return
        asc_key, desc_key, default_desc = spec
        if self._space_sort_order == desc_key:
            self._space_sort_order = asc_key
        elif self._space_sort_order == asc_key:
            self._space_sort_order = desc_key
        else:
            self._space_sort_order = desc_key if default_desc else asc_key
        self._sync_sort_indicator()
        self._page = 0
        self.reload(keep_day=True)

    def _on_sort_indicator_changed(self, _section: int, _order) -> None:
        """Qt 点击表头时会自行挪动排序指示器（包括不可排序列），按真实状态还原。"""
        self._sync_sort_indicator()

    def _sync_sort_indicator(self) -> None:
        """排序指示器只出现在真正生效的排序列上；总览页不显示。"""
        header = self.table.horizontalHeader()
        if self.group_picker.currentData() == "app" and self._app_drill is None:
            header.setSortIndicatorShown(False)
            return
        for column, (asc_key, desc_key, _default) in _SORT_COLUMNS.items():
            if self._space_sort_order in (asc_key, desc_key):
                order = (
                    Qt.AscendingOrder
                    if self._space_sort_order == asc_key
                    else Qt.DescendingOrder
                )
                header.setSortIndicator(column, order)
                header.setSortIndicatorShown(True)
                return
        header.setSortIndicatorShown(False)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("导出 CSV"),
            tr("空间活动.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        filters = self._current_filters()
        filters["sort_order"] = self._space_sort_order
        self.btn_export.setEnabled(False)
        self.btn_export.setText(tr("正在导出…"))
        storage = self._storage

        def work() -> None:
            try:
                count = 0
                offset = 0
                with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(
                        [
                            tr("时间"), tr("文件名"), tr("活动类型"),
                            tr("空间影响"), tr("分类"), tr("完整路径"),
                            tr("原大小"), tr("新大小"),
                        ]
                    )
                    while True:
                        batch = storage.space_events(
                            **filters, limit=1000, offset=offset
                        )
                        if not batch:
                            break
                        for event in batch:
                            writer.writerow(
                                [
                                    datetime.fromtimestamp(event.occurred_at).isoformat(sep=" ", timespec="seconds"),
                                    Path(event.path).name,
                                    _event_label(event),
                                    event.delta_bytes,
                                    tr(CATEGORY_LABELS.get(event.category, "未分类")),
                                    event.path,
                                    event.old_size,
                                    event.new_size,
                                ]
                            )
                        count += len(batch)
                        offset += len(batch)
                self._export_done.emit((path, count))
            except Exception as exc:
                self._export_done.emit(exc)
            finally:
                storage.release_reader()
                current = threading.current_thread()
                with self._workers_lock:
                    if current in self._workers:
                        self._workers.remove(current)

        worker = threading.Thread(target=work, name="dw-activity-export", daemon=True)
        with self._workers_lock:
            self._workers.append(worker)
        worker.start()

    def _on_export_done(self, result: object) -> None:
        self.btn_export.setEnabled(True)
        self.btn_export.setText(tr("导出 CSV"))
        if isinstance(result, Exception):
            QMessageBox.warning(self, tr("导出失败"), str(result))
            return
        if not isinstance(result, tuple) or len(result) != 2:
            QMessageBox.warning(self, tr("导出失败"), tr("导出结果格式无效"))
            return
        path, count = str(result[0]), int(result[1])
        QMessageBox.information(
            self,
            tr("导出完成"),
            tr("已导出 {n} 条记录到：\n{path}", n=count, path=path),
        )

    def select_day(self, day: str) -> None:
        self._selected_day = day
        self._page = 0
        self.reload(keep_day=True)

    def hideEvent(self, event) -> None:
        self._search_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._reload_req += 1
        self._history_req += 1
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
        self.title.setText(tr("文件活动"))
        self.btn_refresh.setText(tr("刷新"))
        self.btn_export.setText(tr("导出 CSV"))
        self.btn_prev.setText(tr("上一页"))
        self.btn_next.setText(tr("下一页"))
        self.btn_reveal.setText(tr("在资源管理器中定位"))
        self.btn_copy.setText(tr("复制路径"))
        for index, label in enumerate(
            (tr("今天"), tr("最近 24 小时"), tr("最近 7 天"), tr("最近 30 天"), tr("全部时间"))
        ):
            self.range_picker.setItemText(index, label)
        self.view_picker.setItemText(0, tr("全部视图"))
        self.view_picker.setItemText(1, tr("关注视图"))
        self.event_picker.setItemText(0, tr("全部事件"))
        for index, label in enumerate(EVENT_LABELS.values(), start=1):
            self.event_picker.setItemText(index, tr(label))
        self.group_picker.setItemText(0, tr("不分组"))
        self.group_picker.setItemText(1, tr("按应用分组"))
        self.group_picker.setItemText(2, tr("按分类分组"))
        self._set_header_labels(self._header_mode())
        self.reload_filters()
        self.reload(keep_day=True)

    def apply_theme(self) -> None:
        self.setStyleSheet(panel_qss())
        # 主题控制器会遍历 QApplication 中尚未析构的顶层窗。测试或路径
        # 迁移后，已关闭页面可能仍握着一个已关闭的 Storage；隐藏页只需
        # 更新 QSS，等真正显示时再重绘带前景色的表格项。
        if hasattr(self, "table") and self.isVisible():
            self._fill_table()

    def _fit_splitter(self) -> None:
        """让表格吃满详情卡之外的宽度；详情卡宽度随窗口按比例放大。"""
        if not hasattr(self, "splitter"):
            return
        available = self.splitter.width() - self.splitter.handleWidth()
        if available <= 0:
            return
        # 详情卡约占 26% 宽度，夹在 [最小, 最大] 之间：窗口越大卡越宽，
        # 元素随界面尺寸一起放缩而不是钉死在固定宽度。
        detail_width = int(available * 0.26)
        detail_width = max(
            self.detail_card.minimumWidth(),
            min(self.detail_card.maximumWidth(), detail_width),
        )
        self.splitter.setSizes([max(1, available - detail_width), detail_width])

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "splitter"):
            self._splitter_reflow_timer.start(0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 外层窗口可能还在恢复或最大化，下一轮事件循环再按最终宽度重排。
        self._splitter_reflow_timer.start(0)


def _signed_size(value: int) -> str:
    if value == 0:
        return "0 B"
    return ("+" if value > 0 else "−") + human_size(abs(value))


def _short_path(path: str) -> str:
    """路径缩写：C:\\Users\\<用户> → ~；C 盘去掉盘符；其他盘保留。"""
    if not path:
        return ""
    normalized = path.replace("/", "\\")
    low = normalized.lower()
    if low.startswith("c:\\users\\"):
        rest = normalized[len("c:\\users\\"):]
        user, sep, tail = rest.partition("\\")
        if user.lower() in ("public", "default"):
            # 公共 / 默认用户目录不属于"我的主目录"，不做 ~ 缩写
            return normalized[3:]
        return "~\\" + tail if sep else "~"
    if len(normalized) > 3 and normalized[1] == ":" and normalized[0].lower() == "c":
        return normalized[3:]
    return normalized
