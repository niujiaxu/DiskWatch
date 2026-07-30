"""详情面板：按天查看新增文件、汇总统计、搜索与导出。"""

from __future__ import annotations

import csv
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
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
# 一次往 QTableWidget 塞太多行会明显卡 UI；超出部分只在状态栏提示
MAX_TABLE_ROWS = 2500
# 文件完整路径（勿占用 UserRole：时间/大小列的 UserRole 留给数值排序）
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
    def __init__(self, storage: Storage) -> None:
        super().__init__(objectName="panelRoot")
        self._storage = storage
        self.setWindowTitle("硬盘新增文件 · 详情")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(PANEL_QSS)
        self.resize(1000, 640)
        self._load_signature: tuple | None = None
        self._build()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

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
        self.day_box.currentIndexChanged.connect(lambda _: self.reload(keep_day=True))
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

    # ---------- 数据 ----------

    def reload(self, keep_day: bool = False) -> None:
        current = self.day_box.currentData() if keep_day else None
        days = self._storage.days_with_data()
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
        self.day_box.blockSignals(False)

        target = current or today
        idx = self.day_box.findData(target)
        self.day_box.setCurrentIndex(idx if idx >= 0 else 0)

        self._load_day(self.day_box.currentData() or today)

    def _auto_refresh(self) -> None:
        if not self.isVisible():
            return
        day = self.day_box.currentData()
        if day == today_str():
            self._load_day(day)

    def _load_day(self, day: str) -> None:
        keyword = self.search.text().strip()
        # 多取 1 条用来判断是否截断，避免把上万行一次性塞进表格
        records = self._storage.files_for_day(
            day, keyword, limit=MAX_TABLE_ROWS + 1
        )
        truncated = len(records) > MAX_TABLE_ROWS
        if truncated:
            records = records[:MAX_TABLE_ROWS]
        count, size = self._storage.day_stats(day)
        folders = self._storage.top_folders(day, 1)
        exts = self._storage.top_extensions(day, 1)
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
        if signature == self._load_signature:
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

        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(records))
            for row, rec in enumerate(records):
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
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)
            # 刷新后恢复用户选的列/升降序（含按字节排序的「大小」）
            self.table.sortItems(self._sort_col, self._sort_order)
            self.table.horizontalHeader().setSortIndicator(
                self._sort_col, self._sort_order
            )

        shown = len(records)
        if truncated:
            self.count_label.setText(
                f"显示前 {shown:,} 条 / 共 {count:,} 条（请用搜索缩小范围）"
            )
        else:
            self.count_label.setText(
                f"显示 {shown:,} 条" + (f" / 共 {count:,} 条" if keyword else "")
            )

    def _on_sort_indicator_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        if logical_index < 0:
            return
        self._sort_col = logical_index
        self._sort_order = order

    def _apply_filter(self) -> None:
        day = self.day_box.currentData()
        if day:
            self._load_day(day)

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
        records = self._storage.files_for_day(day, self.search.text().strip())
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["时间", "文件名", "大小(字节)", "可读大小", "类型", "所在目录", "完整路径"])
                for r in records:
                    writer.writerow([
                        datetime.fromtimestamp(r.added_at).strftime("%Y-%m-%d %H:%M:%S"),
                        r.name,
                        r.size,
                        human_size(r.size),
                        r.ext,
                        r.folder,
                        r.path,
                    ])
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {len(records)} 条记录到：\n{path}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        enable_dark_titlebar(self)
        self._load_signature = None
        self.reload(keep_day=True)
        self._timer.start(AUTO_REFRESH_MS)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        self._search_timer.stop()


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
