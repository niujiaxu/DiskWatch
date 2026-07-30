"""详情面板：按天查看新增文件、汇总统计、搜索与导出。

打开/刷新时后台查库；表格用 QTableView + Model 虚拟绘制，只画可见行。
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..storage import FileRecord, Storage, human_size, today_str
from ..watcher import open_in_explorer
from .style import PANEL_QSS, apply_window_icon, enable_dark_titlebar

AUTO_REFRESH_MS = 5000
SEARCH_DEBOUNCE_MS = 280
MAX_TABLE_ROWS = 2500
PATH_ROLE = Qt.UserRole + 1
DIM_FG = QColor("#8b8f9f")

_HEADERS = ("时间", "文件名", "大小", "类型", "所在目录")


class DayPicker(QWidget):
    """日期选择：列表画在详情窗内部，避免置顶窗里 QComboBox 弹层错位/残影。"""

    currentIndexChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, object]] = []
        self._tips: dict[int, str] = {}
        self._index = -1

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._btn = QPushButton("—", objectName="dayPicker")
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self._toggle)
        lay.addWidget(self._btn)

        self._popup: QListWidget | None = None

    def setMinimumWidth(self, w: int) -> None:  # noqa: N802
        super().setMinimumWidth(w)
        self._btn.setMinimumWidth(w)

    def setMaximumWidth(self, w: int) -> None:  # noqa: N802
        super().setMaximumWidth(w)
        self._btn.setMaximumWidth(w)

    def clear(self) -> None:
        self._close_popup()
        self._items.clear()
        self._tips.clear()
        self._index = -1
        self._btn.setText("—")
        self._btn.setToolTip("")

    def count(self) -> int:
        return len(self._items)

    def addItem(self, text: str, userData=None) -> None:  # noqa: N802
        self._items.append((text, userData))
        if self._index < 0:
            self.setCurrentIndex(0)

    def setItemData(self, index: int, value, role: int = Qt.UserRole) -> None:  # noqa: N802
        if role == Qt.ToolTipRole and 0 <= index < len(self._items):
            self._tips[index] = str(value)
            if index == self._index:
                self._btn.setToolTip(str(value))

    def findData(self, data) -> int:  # noqa: N802
        for i, (_t, d) in enumerate(self._items):
            if d == data:
                return i
        return -1

    def currentData(self, role: int = Qt.UserRole):  # noqa: N802
        if 0 <= self._index < len(self._items):
            return self._items[self._index][1]
        return None

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if index < 0 or index >= len(self._items):
            return
        changed = index != self._index
        self._index = index
        text, _data = self._items[index]
        self._btn.setText(text)
        self._btn.setToolTip(self._tips.get(index, ""))
        if changed and not self.signalsBlocked():
            self.currentIndexChanged.emit(index)

    def _host(self) -> QWidget:
        return self.window()

    def _toggle(self) -> None:
        if self._popup is not None and self._popup.isVisible():
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self) -> None:
        if not self._items:
            return
        host = self._host()
        if self._popup is None:
            self._popup = QListWidget(host)
            self._popup.setObjectName("dayPickerPopup")
            self._popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._popup.itemClicked.connect(self._on_pick)
            host.installEventFilter(self)
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
        self._popup.clear()
        for i, (text, _data) in enumerate(self._items):
            item = QListWidgetItem(text)
            tip = self._tips.get(i)
            if tip:
                item.setToolTip(tip)
            self._popup.addItem(item)
        if 0 <= self._index < self._popup.count():
            self._popup.setCurrentRow(self._index)

        top_left = self.mapTo(host, QPoint(0, self.height() + 2))
        row_h = 28
        height = min(280, max(row_h * min(len(self._items), 10) + 8, row_h + 8))
        width = max(self.width(), 220)
        if top_left.y() + height > host.height() - 8:
            top_left = self.mapTo(host, QPoint(0, -2)) - QPoint(0, height)
        self._popup.setGeometry(top_left.x(), max(8, top_left.y()), width, height)
        self._popup.raise_()
        self._popup.show()
        self._popup.setFocus(Qt.PopupFocusReason)

    def _close_popup(self) -> None:
        if self._popup is not None:
            self._popup.hide()

    def _on_pick(self, item: QListWidgetItem) -> None:
        row = self._popup.row(item) if self._popup is not None else -1
        self._close_popup()
        if row >= 0:
            self.setCurrentIndex(row)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if self._popup is None or not self._popup.isVisible():
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.MouseButtonPress:
            pos = event.globalPosition().toPoint()
            over_btn = self.rect().contains(self.mapFromGlobal(pos))
            over_list = self._popup.rect().contains(self._popup.mapFromGlobal(pos))
            if not over_btn and not over_list:
                self._close_popup()
        elif et == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self._close_popup()
            return True
        elif et in (QEvent.Resize, QEvent.Move) and obj is self._host():
            self._close_popup()
        return super().eventFilter(obj, event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._close_popup()
        super().hideEvent(event)


class FilesTableModel(QAbstractTableModel):
    """只持有 FileRecord 列表，由视图按需取单元格，避免创建上万 QTableWidgetItem。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[FileRecord] = []
        self._sort_col = 0
        self._sort_order = Qt.DescendingOrder

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else 5

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        rec = self._records[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return datetime.fromtimestamp(rec.added_at).strftime("%H:%M:%S")
            if col == 1:
                return rec.name
            if col == 2:
                return human_size(rec.size)
            if col == 3:
                return rec.ext or "—"
            if col == 4:
                return rec.folder
            return None

        if role == Qt.TextAlignmentRole and col == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == Qt.ForegroundRole and rec.size == 0:
            return DIM_FG

        if role == Qt.ToolTipRole and col == 1:
            return rec.path

        if role == PATH_ROLE:
            return rec.path

        if role == Qt.UserRole:
            if col == 0:
                return float(rec.added_at)
            if col == 2:
                return float(rec.size)
            return None

        return None

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(_HEADERS):
                return _HEADERS[section]
        return None

    @property
    def sort_col(self) -> int:
        return self._sort_col

    @property
    def sort_order(self) -> Qt.SortOrder:
        return self._sort_order

    def records(self) -> list[FileRecord]:
        return self._records

    def set_records(self, records: list[FileRecord]) -> None:
        sorted_rows = self._sorted(records, self._sort_col, self._sort_order)
        self.beginResetModel()
        self._records = sorted_rows
        self.endResetModel()

    def sort_by(self, column: int, order: Qt.SortOrder) -> None:
        if column < 0:
            return
        same = column == self._sort_col and order == self._sort_order
        self._sort_col = column
        self._sort_order = order
        if same or not self._records:
            return
        sorted_rows = self._sorted(self._records, column, order)
        self.beginResetModel()
        self._records = sorted_rows
        self.endResetModel()

    @staticmethod
    def _sorted(
        records: list[FileRecord], col: int, order: Qt.SortOrder
    ) -> list[FileRecord]:
        reverse = order == Qt.DescendingOrder
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
        # 与悬浮卡片同级置顶，避免卡片挡在详情表上面
        self.setWindowFlags(self.windowFlags() | Qt.Window | Qt.WindowStaysOnTopHint)
        # setWindowFlags 会重建原生窗口，图标必须放在其后
        apply_window_icon(self)
        self.setStyleSheet(PANEL_QSS)
        self.resize(1040, 680)

        self._load_signature: tuple | None = None
        self._days_req = 0
        self._day_req = 0
        self._pending_keep_day: str | None = None
        self._fill_meta: dict | None = None
        self._model = FilesTableModel(self)

        self._build()

        self._days_ready.connect(self._on_days_ready)
        self._day_ready.connect(self._on_day_ready)

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

        # 第一行：标题 + 操作按钮（避免和日期/搜索挤在同一行互相遮挡）
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(QLabel("新增文件明细", objectName="h1"))
        title_row.addStretch(1)
        btn_export = QPushButton("导出 CSV")
        btn_export.clicked.connect(self._export_csv)
        btn_refresh = QPushButton("刷新", objectName="primary")
        btn_refresh.clicked.connect(lambda: self.reload(keep_day=True))
        title_row.addWidget(btn_export)
        title_row.addWidget(btn_refresh)
        root.addLayout(title_row)

        # 第二行：日期 + 可伸展的搜索框
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        filter_row.addWidget(QLabel("日期", objectName="dim"))
        self.day_box = DayPicker()
        self.day_box.setMinimumWidth(200)
        self.day_box.setMaximumWidth(280)
        self.day_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.day_box.currentIndexChanged.connect(self._on_day_changed)
        filter_row.addWidget(self.day_box)

        self.search = QLineEdit()
        self.search.setPlaceholderText("按文件名或目录筛选…")
        self.search.setMinimumWidth(180)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search.textChanged.connect(
            lambda: self._search_timer.start(SEARCH_DEBOUNCE_MS)
        )
        filter_row.addWidget(self.search, 1)
        root.addLayout(filter_row)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_count = StatCard("新增文件")
        self.card_size = StatCard("占用空间")
        self.card_folder = StatCard("最活跃目录")
        self.card_ext = StatCard("最多的类型")
        for c in (self.card_count, self.card_size, self.card_folder, self.card_ext):
            cards.addWidget(c)
        root.addLayout(cards)

        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.verticalHeader().setDefaultSectionSize(28)

        hh = self.table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.setSortIndicator(0, Qt.DescendingOrder)
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
            if d.day == today:
                label = f"今天 · {d.count} 个 · {human_size(d.total_size)}"
            else:
                label = f"{d.day} · {d.count} 个 · {human_size(d.total_size)}"
            self.day_box.addItem(label, d.day)
            self.day_box.setItemData(
                self.day_box.count() - 1,
                f"{d.day}  ·  {d.count} 个  ·  {human_size(d.total_size)}",
                Qt.ToolTipRole,
            )

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
        if signature == self._load_signature and self._model.rowCount() == len(records):
            self.count_label.setText(
                self._status_text(len(records), count, keyword, truncated)
            )
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
        self._model.set_records(records)
        hh = self.table.horizontalHeader()
        hh.blockSignals(True)
        hh.setSortIndicator(self._model.sort_col, self._model.sort_order)
        hh.blockSignals(False)
        self.count_label.setText(
            self._status_text(len(records), count, keyword, truncated)
        )

    @staticmethod
    def _status_text(shown: int, count: int, keyword: str, truncated: bool) -> str:
        if truncated:
            return f"显示前 {shown:,} 条 / 共 {count:,} 条（请用搜索缩小范围）"
        return f"显示 {shown:,} 条" + (f" / 共 {count:,} 条" if keyword else "")

    def _auto_refresh(self) -> None:
        if not self.isVisible():
            return
        day = self.day_box.currentData()
        if day == today_str():
            self._load_day_async(day)

    def _on_sort_indicator_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        if logical_index < 0:
            return
        self._model.sort_by(logical_index, order)

    def _apply_filter(self) -> None:
        day = self.day_box.currentData()
        if day:
            self._load_day_async(day)

    # ---------- 动作 ----------

    def _open_selected(self, _index: QModelIndex | None = None) -> None:
        index = self.table.currentIndex()
        if not index.isValid():
            return
        path = self._model.data(index.sibling(index.row(), 1), PATH_ROLE)
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
                            self._model.rowCount(),
                            int(meta.get("count", n)),
                            str(meta.get("keyword", "")),
                            bool(meta.get("truncated", False)),
                        )
                    )

            QTimer.singleShot(0, done)

        threading.Thread(target=work, name="dw-export", daemon=True).start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_window_icon(self)
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
        self._days_req += 1
        self._day_req += 1


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


def _empty_day(day: str):
    from ..storage import DaySummary

    return DaySummary(day, 0, 0)
