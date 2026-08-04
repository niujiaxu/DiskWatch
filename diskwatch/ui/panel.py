"""详情面板：按天查看新增文件、汇总统计、搜索与导出。

打开/刷新时后台查库；默认按应用树状分组，也可平铺。
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QPoint,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
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
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..grouping import assign_groups
from ..i18n import tr
from ..storage import FileRecord, Storage, human_size, today_str
from ..watcher import open_in_explorer
from .style import (
    DIM_FG,
    GROUP_FG,
    PANEL_QSS,
    apply_window_icon,
    enable_dark_titlebar,
)

AUTO_REFRESH_MS = 5000
SEARCH_DEBOUNCE_MS = 280
MAX_TABLE_ROWS = 2500
PATH_ROLE = Qt.UserRole + 1
IS_GROUP_ROLE = Qt.UserRole + 2

_HEADERS = ("时间", "文件名", "大小", "类型", "所在目录")


@dataclass
class _Group:
    key: str
    label: str
    files: list[FileRecord] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def latest_at(self) -> float:
        return max((f.added_at for f in self.files), default=0.0)


def _record_sort_key(rec: FileRecord, col: int):
    if col == 0:
        return rec.added_at
    if col == 2:
        return rec.size
    if col == 1:
        return rec.name.lower()
    if col == 3:
        return (rec.ext or "").lower()
    return (rec.folder or "").lower()


def _sort_records(
    records: list[FileRecord], col: int, order: Qt.SortOrder
) -> list[FileRecord]:
    reverse = order == Qt.DescendingOrder
    return sorted(records, key=lambda r: _record_sort_key(r, col), reverse=reverse)


def _top_sort_key(item: _Group | FileRecord, col: int):
    if isinstance(item, _Group):
        if col == 0:
            return item.latest_at
        if col == 2:
            return item.total_size
        return item.label.lower()
    return _record_sort_key(item, col)


def _file_display(rec: FileRecord, col: int, role: int):
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
    if role == IS_GROUP_ROLE:
        return False
    if role == Qt.UserRole:
        if col == 0:
            return float(rec.added_at)
        if col == 2:
            return float(rec.size)
        return None
    return None


class FilesTreeModel(QAbstractItemModel):
    """两级树：应用组 → 文件；平铺时全部为顶层文件行。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._grouped = True
        self._sort_col = 0
        self._sort_order = Qt.DescendingOrder
        self._raw: list[FileRecord] = []
        # 顶层：_Group（多文件）或 FileRecord（单文件提升）
        self._top: list[_Group | FileRecord] = []

    @property
    def grouped(self) -> bool:
        return self._grouped

    @property
    def sort_col(self) -> int:
        return self._sort_col

    @property
    def sort_order(self) -> Qt.SortOrder:
        return self._sort_order

    def records(self) -> list[FileRecord]:
        return self._raw

    def file_count(self) -> int:
        return len(self._raw)

    def set_grouped(self, grouped: bool) -> None:
        if grouped == self._grouped:
            return
        self._grouped = grouped
        self._rebuild()

    def set_records(self, records: list[FileRecord]) -> None:
        self._raw = list(records)
        self._rebuild()

    def sort_by(self, column: int, order: Qt.SortOrder) -> None:
        if column < 0:
            return
        same = column == self._sort_col and order == self._sort_order
        self._sort_col = column
        self._sort_order = order
        if same or not self._raw:
            return
        self._rebuild()

    def expand_rows(self) -> list[int]:
        """分组模式下应默认展开的顶层行（子文件数 < 3）。"""
        if not self._grouped:
            return []
        rows = []
        for i, item in enumerate(self._top):
            if isinstance(item, _Group) and len(item.files) < 3:
                rows.append(i)
        return rows

    def _rebuild(self) -> None:
        self.beginResetModel()
        sorted_files = _sort_records(self._raw, self._sort_col, self._sort_order)
        if not self._grouped:
            self._top = list(sorted_files)
            self.endResetModel()
            return

        buckets: dict[str, _Group] = {}
        order_keys: list[str] = []
        labels = assign_groups(sorted_files)
        for rec in sorted_files:
            key, label = labels[rec.path]
            if key not in buckets:
                buckets[key] = _Group(key=key, label=label)
                order_keys.append(key)
            buckets[key].files.append(rec)

        top: list[_Group | FileRecord] = []
        for key in order_keys:
            g = buckets[key]
            if len(g.files) == 1:
                # 单文件不套空壳组，直接提到顶层
                top.append(g.files[0])
            else:
                g.files = _sort_records(g.files, self._sort_col, self._sort_order)
                top.append(g)

        reverse = self._sort_order == Qt.DescendingOrder
        col = self._sort_col
        self._top = sorted(
            top, key=lambda item: _top_sort_key(item, col), reverse=reverse
        )
        self.endResetModel()

    # ----- QAbstractItemModel -----

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 5

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if not parent.isValid():
            return len(self._top)
        if parent.internalId() != 0:
            return 0
        item = self._top[parent.row()]
        if isinstance(item, _Group):
            return len(item.files)
        return 0

    def index(  # noqa: N802
        self, row: int, column: int, parent: QModelIndex = QModelIndex()
    ) -> QModelIndex:
        if row < 0 or column < 0 or column >= 5:
            return QModelIndex()
        if not parent.isValid():
            if row >= len(self._top):
                return QModelIndex()
            return self.createIndex(row, column, 0)
        if parent.internalId() != 0:
            return QModelIndex()
        item = self._top[parent.row()]
        if not isinstance(item, _Group) or row >= len(item.files):
            return QModelIndex()
        return self.createIndex(row, column, parent.row() + 1)

    def parent(self, child: QModelIndex) -> QModelIndex:  # noqa: N802
        if not child.isValid() or child.internalId() == 0:
            return QModelIndex()
        return self.createIndex(child.internalId() - 1, 0, 0)

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(_HEADERS):
                return tr(_HEADERS[section])  # 显示时翻译，语言在启动时已确定
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        if index.internalId() == 0:
            item = self._top[index.row()]
            if isinstance(item, _Group):
                return self._group_data(item, index.column(), role)
            return _file_display(item, index.column(), role)
        g = self._top[index.internalId() - 1]
        if not isinstance(g, _Group):
            return None
        return _file_display(g.files[index.row()], index.column(), role)

    @staticmethod
    def _group_data(g: _Group, col: int, role: int):
        if role == Qt.DisplayRole:
            if col == 0:
                return datetime.fromtimestamp(g.latest_at).strftime("%H:%M:%S")
            if col == 1:
                return tr("{label}  ·  {count} 个", label=g.label, count=len(g.files))
            if col == 2:
                return human_size(g.total_size)
            if col == 3:
                return tr("应用")
            if col == 4:
                return tr("{count} 个文件", count=len(g.files))
            return None
        if role == Qt.TextAlignmentRole and col == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            return GROUP_FG
        if role == Qt.FontRole:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ToolTipRole:
            return g.label
        if role == PATH_ROLE:
            return None
        if role == IS_GROUP_ROLE:
            return True
        if role == Qt.UserRole:
            if col == 0:
                return float(g.latest_at)
            if col == 2:
                return float(g.total_size)
            return None
        return None


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
        self.setWindowTitle(tr("硬盘新增文件 · 详情"))
        # 普通顶层窗即可，不要强制置顶（避免盖住其它软件）
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        # setWindowFlags 会重建原生窗口，图标必须放在其后
        apply_window_icon(self)
        self.setStyleSheet(PANEL_QSS)
        self.resize(1040, 680)

        self._load_signature: tuple | None = None
        self._days_req = 0
        self._day_req = 0
        self._pending_keep_day: str | None = None
        self._fill_meta: dict | None = None
        self._model = FilesTreeModel(self)

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
        title_row.addWidget(QLabel(tr("新增文件明细"), objectName="h1"))
        title_row.addStretch(1)
        btn_export = QPushButton(tr("导出 CSV"))
        btn_export.clicked.connect(self._export_csv)
        btn_refresh = QPushButton(tr("刷新"), objectName="primary")
        btn_refresh.clicked.connect(lambda: self.reload(keep_day=True))
        title_row.addWidget(btn_export)
        title_row.addWidget(btn_refresh)
        root.addLayout(title_row)

        # 第二行：日期 + 分组切换 + 可伸展的搜索框
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        filter_row.addWidget(QLabel(tr("日期"), objectName="dim"))
        self.day_box = DayPicker()
        self.day_box.setMinimumWidth(260)
        self.day_box.setMaximumWidth(380)
        self.day_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.day_box.currentIndexChanged.connect(self._on_day_changed)
        filter_row.addWidget(self.day_box)

        self.chk_group = QCheckBox(tr("按应用分组"))
        self.chk_group.setChecked(True)
        self.chk_group.setToolTip(tr("把同一应用下的文件收成可折叠分组（类似进程树）"))
        self.chk_group.toggled.connect(self._on_group_toggled)
        filter_row.addWidget(self.chk_group)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("按文件名或目录筛选…"))
        self.search.setMinimumWidth(180)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search.textChanged.connect(
            lambda: self._search_timer.start(SEARCH_DEBOUNCE_MS)
        )
        filter_row.addWidget(self.search, 1)
        root.addLayout(filter_row)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_count = StatCard(tr("新增文件"))
        self.card_size = StatCard(tr("占用空间"))
        self.card_free = StatCard(tr("今日剩余空间"))
        self.card_folder = StatCard(tr("最活跃目录"))
        self.card_ext = StatCard(tr("最多的类型"))
        for c in (self.card_count, self.card_size, self.card_free, self.card_folder, self.card_ext):
            cards.addWidget(c)
        root.addLayout(cards)

        self.table = QTreeView()
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.setUniformRowHeights(True)
        self.table.setRootIsDecorated(True)
        self.table.setItemsExpandable(True)
        self.table.setAnimated(True)
        self.table.setIndentation(18)

        hh = self.table.header()
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
        self.hint = QLabel(
            tr("双击文件行可在资源管理器中定位；双击应用分组可展开/折叠"),
            objectName="dim",
        )
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
        self.count_label.setText(tr("加载中…"))

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
            self.count_label.setText(tr("加载失败：{err}", err=payload))
            return

        days = list(payload)
        today = today_str()
        if not any(d.day == today for d in days):
            days.insert(0, _empty_day(today))

        self.day_box.blockSignals(True)
        self.day_box.clear()
        for d in days:
            free = human_size(d.total_free) if d.total_free is not None else None
            if d.day == today:
                if free:
                    label = tr(
                        "今天 · {count} 个 · {size} · 剩 {free}",
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                        free=free,
                    )
                    tip = tr(
                        "今天  ·  {count} 个  ·  {size}  ·  剩 {free}",
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                        free=free,
                    )
                else:
                    label = tr(
                        "今天 · {count} 个 · {size}",
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                    )
                    tip = tr(
                        "今天  ·  {count} 个  ·  {size}",
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                    )
            else:
                if free:
                    label = tr(
                        "{day} · {count} 个 · {size} · 剩 {free}",
                        day=d.day,
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                        free=free,
                    )
                    tip = tr(
                        "{day}  ·  {count} 个  ·  {size}  ·  剩 {free}",
                        day=d.day,
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                        free=free,
                    )
                else:
                    label = tr(
                        "{day} · {count} 个 · {size}",
                        day=d.day,
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                    )
                    tip = tr(
                        "{day}  ·  {count} 个  ·  {size}",
                        day=d.day,
                        count=f"{d.count}",
                        size=human_size(d.total_size),
                    )
            self.day_box.addItem(label, d.day)
            self.day_box.setItemData(
                self.day_box.count() - 1,
                tip,
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
        self.count_label.setText(tr("加载中…"))

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
            self.count_label.setText(tr("加载失败：{err}", err=payload))
            return

        data = payload
        records = data["records"]
        truncated = data["truncated"]
        count = data["count"]
        size = data["size"]
        day_total = int(data.get("day_total", count))
        folders = data["folders"]
        exts = data["exts"]
        spaces = data.get("spaces") or []
        day = data["day"]
        keyword = data["keyword"]
        folder_key = folders[0] if folders else None
        ext_key = exts[0] if exts else None
        space_sig = tuple(spaces)

        signature = (
            day,
            keyword,
            count,
            size,
            day_total,
            folder_key,
            ext_key,
            truncated,
            tuple((r.path, r.size, r.added_at) for r in records[:40]),
            len(records),
            space_sig,
        )
        if signature == self._load_signature and self._model.file_count() == len(records):
            self.count_label.setText(
                self._status_text(len(records), count, keyword, truncated, day_total)
            )
            return
        self._load_signature = signature

        self.card_count.set_value(f"{count:,}")
        self.card_size.set_value(human_size(size))
        self.card_free.set_value(
            " · ".join(f"{drive} {human_size(free)}" for drive, free, _total in spaces)
            if spaces
            else "—"
        )
        self.card_folder.set_value(
            _shorten(folders[0][0], 26) + f"  ({folders[0][1]})" if folders else "—"
        )
        ext_name = exts[0][0] if exts else ""
        if ext_name == "(无扩展名)":
            ext_name = tr("(无扩展名)")
        self.card_ext.set_value(
            f"{ext_name}  ({exts[0][1]})" if exts else "—"
        )

        self._fill_meta = {
            "count": count,
            "keyword": keyword,
            "truncated": truncated,
            "day_total": day_total,
        }
        self._model.set_records(records)
        hh = self.table.header()
        hh.blockSignals(True)
        hh.setSortIndicator(self._model.sort_col, self._model.sort_order)
        hh.blockSignals(False)
        self._apply_expand_policy()
        self.count_label.setText(
            self._status_text(len(records), count, keyword, truncated, day_total)
        )

    @staticmethod
    def _status_text(
        shown: int,
        count: int,
        keyword: str,
        truncated: bool,
        day_total: int | None = None,
    ) -> str:
        if truncated:
            return tr(
                "显示前 {shown} 条 / 筛选共 {count} 条（请再缩小关键词）",
                shown=f"{shown:,}",
                count=f"{count:,}",
            )
        if keyword:
            total = count if day_total is None else day_total
            return tr(
                "筛选到 {shown} 条 / 当日共 {total} 条",
                shown=f"{shown:,}",
                total=f"{total:,}",
            )
        return tr("显示 {shown} 条", shown=f"{shown:,}")

    def _apply_expand_policy(self) -> None:
        self.table.collapseAll()
        for row in self._model.expand_rows():
            self.table.expand(self._model.index(row, 0))

    def _on_group_toggled(self, checked: bool) -> None:
        self._model.set_grouped(checked)
        self._apply_expand_policy()

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
        self._apply_expand_policy()

    def _apply_filter(self) -> None:
        day = self.day_box.currentData()
        if day:
            self._load_day_async(day)

    # ---------- 动作 ----------

    def _open_selected(self, index: QModelIndex | None = None) -> None:
        idx = index if index is not None and index.isValid() else self.table.currentIndex()
        if not idx.isValid():
            return
        if self._model.data(idx, IS_GROUP_ROLE):
            if self.table.isExpanded(idx):
                self.table.collapse(idx)
            else:
                self.table.expand(idx)
            return
        path = self._model.data(idx.sibling(idx.row(), 1), PATH_ROLE)
        if not path:
            path = self._model.data(idx, PATH_ROLE)
        if path:
            open_in_explorer(path)

    def _export_csv(self) -> None:
        day = self.day_box.currentData() or today_str()
        default = tr("新增文件_{day}.csv", day=day)
        path, _ = QFileDialog.getSaveFileName(self, tr("导出 CSV"), default, "CSV (*.csv)")
        if not path:
            return
        self.count_label.setText(tr("正在导出…"))
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
                        [
                            tr("时间"),
                            tr("文件名"),
                            tr("大小(字节)"),
                            tr("可读大小"),
                            tr("类型"),
                            tr("所在目录"),
                            tr("完整路径"),
                        ]
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
                    QMessageBox.warning(self, tr("导出失败"), str(err))
                    self.count_label.setText(tr("导出失败"))
                    return
                QMessageBox.information(
                    self,
                    tr("导出完成"),
                    tr("已导出 {n} 条记录到：\n{path}", n=n, path=path),
                )
                meta = self._fill_meta
                if meta is not None:
                    self.count_label.setText(
                        self._status_text(
                            self._model.file_count(),
                            int(meta.get("count", n)),
                            str(meta.get("keyword", "")),
                            bool(meta.get("truncated", False)),
                            int(meta.get("day_total", meta.get("count", n))),
                        )
                    )

            QTimer.singleShot(0, done)

        threading.Thread(target=work, name="dw-export", daemon=True).start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_window_icon(self)
        enable_dark_titlebar(self)
        self._load_signature = None
        self.count_label.setText(tr("加载中…"))
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
