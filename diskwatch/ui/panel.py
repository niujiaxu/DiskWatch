"""详情面板：按天查看新增文件、汇总统计、搜索与导出。

打开/刷新时后台查库，表格分批填入，避免卡住悬浮卡片和其他窗口。
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..storage import Storage, human_size, today_str
from ..watcher import open_in_explorer
from .style import PANEL_QSS, app_icon, enable_dark_titlebar

AUTO_REFRESH_MS = 5000
SEARCH_DEBOUNCE_MS = 280
MAX_TABLE_ROWS = 2500
FILL_CHUNK = 100
PATH_ROLE = Qt.UserRole + 1


class StatCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__(objectName="card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self._title = QLabel(title, objectName="dim")
        self._value = QLabel("—", objectName="statValue")
        lay.addWidget(self._title)
        lay.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


class DetailPanel(QWidget):
    _days_ready = Signal(int, object)
    _day_ready = Signal(int, object)

    def __init__(self, storage: Storage) -> None:
        super().__init__(objectName="panelRoot")
        self._storage = storage
        self.setWindowTitle("硬盘新增文件 · 详情")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(PANEL_QSS)
        self.resize(1000, 640)

        self._load_signature: tuple | None = None
        self._days_req = 0
        self._day_req = 0
        self._pending_keep_day: str | None = None
        self._fill_records: list = []
        self._fill_index = 0
        self._fill_meta: dict | None = None

        self._build()

        self._days_ready.connect(self._on_days_ready)
        self._day_ready.connect(self._on_day_ready)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

        self._fill_timer = QTimer(self)
        self._fill_timer.timeout.connect(self._fill_chunk)

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(QLabel("新增文件明细", objectName="h1"))
        head.addStretch(1)

        self.day_box = QComboBox()
        self.day_box.setMinimumWidth(190)
        self.day_box.currentIndexChanged.connect(self._on_day_changed)
        head.addWidget(QLabel("日期", objectName="dim"))
        head.addWidget(self.day_box)

        self.search = QLineEdit()
        self.search.setPlaceholderText("按文件名或目录筛选…")
        self.search.setMinimumWidth(220)
        self.search.textChanged.connect(
            lambda: self._search_timer.start(SEARCH_DEBOUNCE_MS)
        )
        head.addWidget(self.search)

        btn_export = QPushButton("导出 CSV")
        btn_export.clicked.connect(self._export_csv)
        btn_refresh = QPushButton("刷新", objectName="primary")
        btn_refresh.clicked.connect(lambda: self.reload(keep_day=True))
        head.addWidget(btn_export)
        head.addWidget(btn_refresh)
        root.addLayout(head)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_count = StatCard("新增文件")
        self.card_size = StatCard("占用空间")
        self.card_folder = StatCard("最活跃目录")
        self.card_ext = StatCard("最多的类型")
        for c in (self.card_count, self.card_size, self.card_folder, self.card_ext):
            cards.addWidget(c)
        root.addLayout(cards)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["时间", "文件名", "大小", "类型", "所在目录"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self._sort_col = 0
        self._sort_order = Qt.DescendingOrder
        hh = self.table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setSortIndicator(self._sort_col, self._sort_order)
        hh.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 300)
        self.table.doubleClicked.connect(self._open_selected)
        root.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.hint = QLabel("双击任意一行可在资源管理器中定位该文件", objectName="dim")
        foot.addWidget(self.hint)
        foot.addStretch(1)
        self.count_label = QLabel("", objectName="dim")
        foot.addWidget(self.count_label)
        root.addLayout(foot)

    # ---------- 异步加载 ----------

    def reload(self, keep_day: bool = False) -> None:
        """刷新日期列表 + 当前天；查询在后台线程。"""
        self._pending_keep_day = (
            self.day_box.currentData() if keep_day else None
        )
        self._days_req += 1
        req = self._days_req
        self.count_label.setText("加载中…")
        self._fill_timer.stop()

        storage = self._storage

        def work() -> None:
            try:
                payload = storage.fetch_days_with_data()
            except Exception as exc:  # noqa: BLE001
                payload = exc
            self._days_ready.emit(req, payload)

        threading.Thread(target=work, name="dw-panel-days", daemon=True).start()

    def _on_days_ready(self, req: int, payload: object) -> None:
        if req != self._days_req or not self.isVisible():
            return
        if isinstance(payload, Exception):
            self.count_label.setText(f"加载失败：{payload}")
            return

        days = list(payload)
        today = today_str()
        if not any(d.day == today for d in days):
            days.insert(0, _empty_day(today))

        self.day_box.blockSignals(True)
        self.day_box.clear()
        for d in days:
            label = f"{d.day}   ({d.count} 个 · {human_size(d.total_size)})"
            if d.day == today:
                label = f"今天 {label}"
            self.day_box.addItem(label, d.day)

        target = self._pending_keep_day or today
        idx = self.day_box.findData(target)
        self.day_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.day_box.blockSignals(False)

        day = self.day_box.currentData() or today
        self._load_day_async(day)

    def _on_day_changed(self, _index: int = 0) -> None:
        day = self.day_box.currentData()
        if day:
            self._load_day_async(day)

    def _load_day_async(self, day: str) -> None:
        keyword = self.search.text().strip()
        self._day_req += 1
        req = self._day_req
        self.count_label.setText("加载中…")
        self._fill_timer.stop()

        storage = self._storage
        limit = MAX_TABLE_ROWS + 1

        def work() -> None:
            try:
                payload = storage.fetch_day_view(day, keyword, limit)
            except Exception as exc:  # noqa: BLE001
                payload = exc
            self._day_ready.emit(req, payload)

        threading.Thread(target=work, name="dw-panel-day", daemon=True).start()

    def _on_day_ready(self, req: int, payload: object) -> None:
        if req != self._day_req or not self.isVisible():
            return
        if isinstance(payload, Exception):
            self.count_label.setText(f"加载失败：{payload}")
            return

        data = payload
        records = data["records"]
        truncated = data["truncated"]
        count = data["count"]
        size = data["size"]
        folders = data["folders"]
        exts = data["exts"]
        day = data["day"]
        keyword = data["keyword"]
        folder_key = folders[0] if folders else None
        ext_key = exts[0] if exts else None

        signature = (
            day,
            keyword,
            count,
            size,
            folder_key,
            ext_key,
            truncated,
            tuple((r.path, r.size, r.added_at) for r in records[:40]),
            len(records),
        )
        if signature == self._load_signature and self.table.rowCount() == len(records):
            self.count_label.setText(self._status_text(len(records), count, keyword, truncated))
            return
        self._load_signature = signature

        self.card_count.set_value(f"{count:,}")
        self.card_size.set_value(human_size(size))
        self.card_folder.set_value(
            _shorten(folders[0][0], 26) + f"  ({folders[0][1]})" if folders else "—"
        )
        self.card_ext.set_value(
            f"{exts[0][0]}  ({exts[0][1]})" if exts else "—"
        )

        self._fill_meta = {
            "count": count,
            "keyword": keyword,
            "truncated": truncated,
        }
        # 先按当前表头排好再填，避免最后 sortItems 再次卡死 UI
        self._fill_records = self._sorted_records(records)
        self._fill_index = 0

        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(self._fill_records))
        self.table.setUpdatesEnabled(True)

        if not self._fill_records:
            self.count_label.setText(self._status_text(0, count, keyword, False))
            self.table.setSortingEnabled(True)
            self.table.horizontalHeader().setSortIndicator(
                self._sort_col, self._sort_order
            )
            return

        # 分批填表，中间把事件循环让给悬浮窗/托盘
        self._fill_timer.setInterval(1)
        self._fill_timer.start()

    def _sorted_records(self, records: list) -> list:
        reverse = self._sort_order == Qt.DescendingOrder
        col = self._sort_col
        if col == 0:
            key = lambda r: r.added_at
        elif col == 2:
            key = lambda r: r.size
        elif col == 1:
            key = lambda r: r.name.lower()
        elif col == 3:
            key = lambda r: (r.ext or "").lower()
        else:
            key = lambda r: (r.folder or "").lower()
        return sorted(records, key=key, reverse=reverse)

    def _fill_chunk(self) -> None:
        records = self._fill_records
        if self._fill_index >= len(records):
            self._finish_fill()
            return

        end = min(self._fill_index + FILL_CHUNK, len(records))
        self.table.setUpdatesEnabled(False)
        try:
            for row in range(self._fill_index, end):
                self._write_row(row, records[row])
        finally:
            self.table.setUpdatesEnabled(True)

        self._fill_index = end
        total = len(records)
        if self._fill_index < total:
            self.count_label.setText(f"加载中… {self._fill_index:,}/{total:,}")
        else:
            self._finish_fill()

    def _finish_fill(self) -> None:
        self._fill_timer.stop()
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicator(
            self._sort_col, self._sort_order
        )
        meta = self._fill_meta or {}
        self.count_label.setText(
            self._status_text(
                len(self._fill_records),
                int(meta.get("count", 0)),
                str(meta.get("keyword", "")),
                bool(meta.get("truncated", False)),
            )
        )

    def _write_row(self, row: int, rec) -> None:
        when = datetime.fromtimestamp(rec.added_at).strftime("%H:%M:%S")
        items = [
            _numeric_item(when, rec.added_at),
            QTableWidgetItem(rec.name),
            _numeric_item(human_size(rec.size), rec.size, align_right=True),
            QTableWidgetItem(rec.ext or "—"),
            QTableWidgetItem(rec.folder),
        ]
        items[1].setData(PATH_ROLE, rec.path)
        items[1].setToolTip(rec.path)
        if rec.size == 0:
            for it in items:
                it.setForeground(QColor("#8b8f9f"))
        for col, item in enumerate(items):
            self.table.setItem(row, col, item)

    @staticmethod
    def _status_text(shown: int, count: int, keyword: str, truncated: bool) -> str:
        if truncated:
            return f"显示前 {shown:,} 条 / 共 {count:,} 条（请用搜索缩小范围）"
        return f"显示 {shown:,} 条" + (f" / 共 {count:,} 条" if keyword else "")

    def _auto_refresh(self) -> None:
        if not self.isVisible() or self._fill_timer.isActive():
            return
        day = self.day_box.currentData()
        if day == today_str():
            self._load_day_async(day)

    def _on_sort_indicator_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        if logical_index < 0:
            return
        self._sort_col = logical_index
        self._sort_order = order

    def _apply_filter(self) -> None:
        day = self.day_box.currentData()
        if day:
            self._load_day_async(day)

    # ---------- 动作 ----------

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 1)
        if item:
            path = item.data(PATH_ROLE)
            if path:
                open_in_explorer(path)

    def _export_csv(self) -> None:
        day = self.day_box.currentData() or today_str()
        default = f"新增文件_{day}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", default, "CSV (*.csv)")
        if not path:
            return
        self.count_label.setText("正在导出…")
        keyword = self.search.text().strip()
        storage = self._storage

        def work() -> None:
            err: Exception | None = None
            n = 0
            try:
                bundle = storage.fetch_day_view(day, keyword, limit=None)
                records = bundle["records"]
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        ["时间", "文件名", "大小(字节)", "可读大小", "类型", "所在目录", "完整路径"]
                    )
                    for r in records:
                        writer.writerow(
                            [
                                datetime.fromtimestamp(r.added_at).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                                r.name,
                                r.size,
                                human_size(r.size),
                                r.ext,
                                r.folder,
                                r.path,
                            ]
                        )
                n = len(records)
            except Exception as exc:  # noqa: BLE001
                err = exc

            def done() -> None:
                if err is not None:
                    QMessageBox.warning(self, "导出失败", str(err))
                    self.count_label.setText("导出失败")
                    return
                QMessageBox.information(
                    self, "导出完成", f"已导出 {n} 条记录到：\n{path}"
                )
                meta = self._fill_meta
                if meta is not None:
                    self.count_label.setText(
                        self._status_text(
                            len(self._fill_records),
                            int(meta.get("count", n)),
                            str(meta.get("keyword", "")),
                            bool(meta.get("truncated", False)),
                        )
                    )

            QTimer.singleShot(0, done)

        threading.Thread(target=work, name="dw-export", daemon=True).start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        enable_dark_titlebar(self)
        self._load_signature = None
        self.count_label.setText("加载中…")
        # 先让窗口画出来，再启动后台加载，避免点「详情」瞬间整卡
        QTimer.singleShot(0, lambda: self.reload(keep_day=True))
        self._timer.start(AUTO_REFRESH_MS)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        self._search_timer.stop()
        self._fill_timer.stop()
        self._days_req += 1
        self._day_req += 1


class _NumericItem(QTableWidgetItem):
    """显示可读文案，按 UserRole 中的数值比较（时间戳 / 字节数）。"""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # type: ignore[override]
        try:
            return float(self.data(Qt.UserRole) or 0) < float(other.data(Qt.UserRole) or 0)
        except (TypeError, ValueError):
            return super().__lt__(other)


def _numeric_item(
    text: str, value: float, *, align_right: bool = False
) -> QTableWidgetItem:
    item = _NumericItem(text)
    item.setData(Qt.UserRole, float(value))
    if align_right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


def _empty_day(day: str):
    from ..storage import DaySummary

    return DaySummary(day, 0, 0)
