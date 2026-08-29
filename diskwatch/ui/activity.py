"""分页文件活动页，直接展示空间变化账本。"""

from __future__ import annotations

import csv
import math
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..storage import SpaceEvent, Storage, human_size
from ..watcher import open_in_explorer
from .picker import DayPicker
from .style import panel_qss, theme_tokens

PAGE_SIZE = 200
SEARCH_DEBOUNCE_MS = 260

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
}


class ActivityPanel(QWidget):
    """固定页大小，避免几十万条事件一次性进入 UI。"""

    open_dashboard = Signal()
    _export_done = Signal(object)

    def __init__(self, storage: Storage, parent=None) -> None:
        super().__init__(parent, objectName="panelRoot")
        self._storage = storage
        self._page = 0
        self._total = 0
        self._events: list[SpaceEvent] = []
        self._row_events: dict[int, int] = {}
        self._collapsed_groups: set[str] = set()
        self._selected_day: str | None = None
        self._building = True
        self._workers: list[threading.Thread] = []
        self._workers_lock = threading.Lock()
        self._build()
        self._building = False
        self.apply_theme()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._filters_changed)
        self._export_done.connect(self._on_export_done)
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

        filters = QHBoxLayout()
        filters.setSpacing(8)
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
        self.group_picker.addItem(tr("按分类分组"), "category")
        self.group_picker.addItem(tr("按目录分组"), "folder")
        for picker in (
            self.range_picker,
            self.view_picker,
            self.drive_picker,
            self.category_picker,
            self.event_picker,
            self.group_picker,
        ):
            picker.setMinimumWidth(126)
            picker.currentIndexChanged.connect(self._filters_changed)
            filters.addWidget(picker)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索文件名或路径"))
        self.search.textChanged.connect(
            lambda _text: self._search_timer.start(SEARCH_DEBOUNCE_MS)
        )
        filters.addWidget(self.search, 1)
        self.btn_refresh = QPushButton(tr("刷新"))
        self.btn_refresh.clicked.connect(self.reload)
        filters.addWidget(self.btn_refresh)
        self.btn_export = QPushButton(tr("导出 CSV"))
        self.btn_export.clicked.connect(self._export_csv)
        filters.addWidget(self.btn_export)
        root.addLayout(filters)

        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                tr("时间"), tr("文件名"), tr("活动类型"), tr("空间影响"),
                tr("分类"), tr("所在目录"),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_selected)
        self.table.cellClicked.connect(self._table_clicked)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._reveal_selected())
        splitter.addWidget(self.table)

        detail = QFrame(objectName="card")
        detail.setMinimumWidth(248)
        detail.setMaximumWidth(340)
        detail_lay = QVBoxLayout(detail)
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
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 1)
        root.addWidget(splitter, 1)

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
        self._page = 0
        self.reload()

    def reload(self, keep_day: bool = False) -> None:
        # 日期只在筛选条件变化时清除；手动刷新、翻页和自动刷新均保留。
        since, until = self._time_range()
        filters = {
            "since": since,
            "until": until,
            "drive": self.drive_picker.currentData(),
            "category": self.category_picker.currentData(),
            "event_type": self.event_picker.currentData(),
            "keyword": self.search.text().strip() or None,
            "focus_only": self.view_picker.currentData() == "focus",
        }
        self._total = self._storage.space_event_count(**filters)
        max_page = max(0, math.ceil(self._total / PAGE_SIZE) - 1)
        self._page = min(self._page, max_page)
        self._events = self._storage.space_events(
            **filters, limit=PAGE_SIZE, offset=self._page * PAGE_SIZE
        )
        self._fill_table()

    def _fill_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.clearSpans()
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self._row_events.clear()
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
                key = event.category if group_mode == "category" else event.folder
                grouped.setdefault(key or tr("未分类"), []).append(index)
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
                if group_mode == "category":
                    label = tr(CATEGORY_LABELS.get(group_key, "未分类"))
                else:
                    label = group_key
                count = group_counts.get(group_key, 0)
                item = QTableWidgetItem(
                    tr("{marker} {label} · {count} 条", marker=marker, label=label, count=count)
                )
                item.setData(Qt.UserRole + 1, group_key)
                item.setForeground(t.color("accent"))
                item.setBackground(t.color("surface_raised"))
                self.table.setItem(row, 0, item)
                self.table.setSpan(row, 0, 1, 6)
                continue

            event = self._events[event_index]
            self._row_events[row] = event_index
            path = Path(event.path)
            values = (
                datetime.fromtimestamp(event.occurred_at).strftime("%m-%d %H:%M:%S"),
                path.name,
                tr(EVENT_LABELS.get(event.event_type, event.event_type)),
                _signed_size(event.delta_bytes),
                tr(CATEGORY_LABELS.get(event.category, "未分类")),
                str(path.parent),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, event_index)
                if col == 3:
                    role = "warning" if event.delta_bytes > 0 else "success"
                    item.setForeground(t.color(role))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
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
        if self._row_events:
            self.table.selectRow(min(self._row_events))
            self._show_selected()
        else:
            self._clear_detail()

    def _selected_event(self) -> SpaceEvent | None:
        row = self.table.currentRow()
        index = self._row_events.get(row)
        return self._events[index] if index is not None else None

    def _table_clicked(self, row: int, _column: int) -> None:
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
               event=tr(EVENT_LABELS.get(event.event_type, event.event_type)),
               category=tr(CATEGORY_LABELS.get(event.category, "未分类")),
               time=datetime.fromtimestamp(event.occurred_at).strftime("%Y-%m-%d %H:%M:%S"))
        )
        self.detail_sizes.setText(
            tr("原大小 {old}\n新大小 {new}\n空间影响 {delta}",
               old=human_size(event.old_size), new=human_size(event.new_size),
               delta=_signed_size(event.delta_bytes))
        )
        self.detail_path.setText(event.path)
        history = self._storage.file_event_history(event.path, limit=12)
        self.detail_history.setText(
            "\n".join(
                tr(
                    "{time}  {old} → {new}  ({delta})",
                    time=datetime.fromtimestamp(item.occurred_at).strftime("%m-%d %H:%M"),
                    old=human_size(item.old_size),
                    new=human_size(item.new_size),
                    delta=_signed_size(item.delta_bytes),
                )
                for item in history
            )
            or tr("暂无历史")
        )
        self.btn_reveal.setEnabled(True)
        self.btn_copy.setEnabled(True)

    def _clear_detail(self) -> None:
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
        since, until = self._time_range()
        filters = {
            "since": since,
            "until": until,
            "drive": self.drive_picker.currentData(),
            "category": self.category_picker.currentData(),
            "event_type": self.event_picker.currentData(),
            "keyword": self.search.text().strip() or None,
            "focus_only": self.view_picker.currentData() == "focus",
        }
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
                                    tr(EVENT_LABELS.get(event.event_type, event.event_type)),
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
        self.group_picker.setItemText(1, tr("按分类分组"))
        self.group_picker.setItemText(2, tr("按目录分组"))
        self.reload_filters()
        self.reload(keep_day=True)

    def apply_theme(self) -> None:
        self.setStyleSheet(panel_qss())
        # 主题控制器会遍历 QApplication 中尚未析构的顶层窗。测试或路径
        # 迁移后，已关闭页面可能仍握着一个已关闭的 Storage；隐藏页只需
        # 更新 QSS，等真正显示时再重绘带前景色的表格项。
        if hasattr(self, "table") and self.isVisible():
            self._fill_table()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "table"):
            self._fill_table()


def _signed_size(value: int) -> str:
    if value == 0:
        return "0 B"
    return ("+" if value > 0 else "−") + human_size(abs(value))
