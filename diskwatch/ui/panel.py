"""详情面板：按天查看新增文件、汇总统计、搜索与导出。

打开/刷新时后台查库；默认按应用树状分组，也可平铺。
"""

from __future__ import annotations

import csv
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QPoint,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
)
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
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..grouping import assign_groups
from ..i18n import tr
from ..storage import FileRecord, Storage, human_size, today_str
from ..watcher import open_in_explorer
from .style import (
    ACCENT,
    ACCENT_2,
    DIM_FG,
    GROUP_FG,
    PANEL_QSS,
    TEXT_DIM,
    apply_window_icon,
    enable_dark_titlebar,
)

AUTO_REFRESH_MS = 5000
SEARCH_DEBOUNCE_MS = 280
PATH_ROLE = Qt.UserRole + 1
IS_GROUP_ROLE = Qt.UserRole + 2

_HEADERS = ("时间", "文件名", "大小", "类型", "所在目录")


@dataclass
class _Group:
    key: str
    label: str
    files: list[FileRecord] = field(default_factory=list)
    # 编译阶段预计算（finalize），避免展示时反复 sum/max
    total_size: int = 0
    latest_at: float = 0.0

    def finalize(self) -> None:
        self.total_size = sum(f.size for f in self.files)
        self.latest_at = max((f.added_at for f in self.files), default=0.0)


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


def compile_view(
    records: list[FileRecord],
    sort_col: int,
    sort_order: Qt.SortOrder,
    grouped: bool,
) -> tuple[list[_Group | FileRecord], list[int]]:
    """后台线程执行：排序 + 分组，产出可直接交给模型的 (top, expand_rows)。

    top 为排序后的顶层行（_Group 或提升的单文件 FileRecord）；
    expand_rows 为分组模式下应默认展开的顶层行下标（子文件数 < 3）。
    全部计算放在工作线程，主线程只做 beginResetModel/endResetModel。
    """
    sorted_files = _sort_records(records, sort_col, sort_order)
    if not grouped:
        return list(sorted_files), []

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
            g.files = _sort_records(g.files, sort_col, sort_order)
            g.finalize()
            top.append(g)

    reverse = sort_order == Qt.DescendingOrder
    top.sort(key=lambda item: _top_sort_key(item, sort_col), reverse=reverse)
    expand_rows = [
        i
        for i, item in enumerate(top)
        if isinstance(item, _Group) and len(item.files) < 3
    ]
    return top, expand_rows


def _file_display(rec: FileRecord, col: int, role: int):
    if role == Qt.DisplayRole:
        if col == 0:
            ts = rec.deleted_at if rec.deleted and rec.deleted_at else rec.added_at
            return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        if col == 1:
            return rec.name
        if col == 2:
            return human_size(rec.size)
        if col == 3:
            return rec.ext or "\u2014"
        if col == 4:
            return rec.folder
        return None
    if role == Qt.ForegroundRole and rec.deleted:
        return DIM_FG
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
        # 应默认展开的顶层行下标（后台编译时预计算）
        self._expand_rows: list[int] = []

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
        """同步切换分组模式并重建（小数据/测试用；面板走 set_grouped_only）。"""
        if grouped == self._grouped:
            return
        self._grouped = grouped
        self._rebuild()

    def set_grouped_only(self, grouped: bool) -> None:
        """只改标志不重建，由后台线程随后重新编译视图。"""
        if grouped == self._grouped:
            return
        self._grouped = grouped

    def set_records(self, records: list[FileRecord]) -> None:
        """同步载入并重建（小数据/测试用；面板走 set_compiled）。"""
        self._raw = list(records)
        self._rebuild()

    def set_compiled(
        self,
        records: list[FileRecord],
        top: list[_Group | FileRecord],
        expand_rows: list[int],
    ) -> None:
        """应用后台线程编译好的视图：只做模型重置，不重算任何内容。"""
        self._raw = records
        self._top = top
        self._expand_rows = expand_rows
        self.beginResetModel()
        self.endResetModel()

    def sort_by(self, column: int, order: Qt.SortOrder) -> None:
        """同步排序并重建（小数据/测试用；面板走 set_sort_only）。"""
        if column < 0:
            return
        same = column == self._sort_col and order == self._sort_order
        self._sort_col = column
        self._sort_order = order
        if same or not self._raw:
            return
        self._rebuild()

    def set_sort_only(self, column: int, order: Qt.SortOrder) -> None:
        """只改排序标志不重建，由后台线程随后重新编译视图。"""
        if column < 0:
            return
        self._sort_col = column
        self._sort_order = order

    def expand_rows(self) -> list[int]:
        """分组模式下应默认展开的顶层行（子文件数 < 3）。"""
        return self._expand_rows

    def _rebuild(self) -> None:
        top, expand_rows = compile_view(
            self._raw, self._sort_col, self._sort_order, self._grouped
        )
        self._top = top
        self._expand_rows = expand_rows
        self.beginResetModel()
        self.endResetModel()

    # ----- QAbstractItemModel -----

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 5

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._top)
        if parent.internalId() != 0:
            return 0
        item = self._top[parent.row()]
        if isinstance(item, _Group):
            return len(item.files)
        return 0

    def index(
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

    def parent(self, child: QModelIndex) -> QModelIndex:
        if not child.isValid() or child.internalId() == 0:
            return QModelIndex()
        return self.createIndex(child.internalId() - 1, 0, 0)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(_HEADERS):
                return tr(_HEADERS[section])  # 显示时翻译，语言在启动时已确定
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
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
    """选择控件：列表画在宿主窗内部，避免原生下拉弹层错位/残影。

    项目里所有下拉选择（日期 / 事件类型 / 语言 / 预设）都用它，
    API 与 QComboBox 对齐（addItem/currentData/setItemText/…）。
    """

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

    def setMinimumWidth(self, w: int) -> None:
        super().setMinimumWidth(w)
        self._btn.setMinimumWidth(w)

    def setMaximumWidth(self, w: int) -> None:
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

    def addItem(self, text: str, userData=None) -> None:
        self._items.append((text, userData))
        if self._index < 0:
            self.setCurrentIndex(0)

    def setItemData(self, index: int, value, role: int = Qt.UserRole) -> None:
        if role == Qt.ToolTipRole and 0 <= index < len(self._items):
            self._tips[index] = str(value)
            if index == self._index:
                self._btn.setToolTip(str(value))

    def setItemText(self, index: int, text: str) -> None:
        if 0 <= index < len(self._items):
            self._items[index] = (text, self._items[index][1])
            if index == self._index:
                self._btn.setText(text)

    def itemText(self, index: int) -> str:
        if 0 <= index < len(self._items):
            return self._items[index][0]
        return ""

    def setFixedWidth(self, w: int) -> None:
        super().setFixedWidth(w)
        self._btn.setFixedWidth(w)

    def findData(self, data) -> int:
        for i, (_t, d) in enumerate(self._items):
            if d == data:
                return i
        return -1

    def currentData(self, role: int = Qt.UserRole):
        if 0 <= self._index < len(self._items):
            return self._items[self._index][1]
        return None

    def currentIndex(self) -> int:
        return self._index

    def setCurrentIndex(self, index: int) -> None:
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

    def eventFilter(self, obj, event) -> bool:
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

    def hideEvent(self, event) -> None:
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


TREND_DAYS = 14
BAR_GAP = 8
BAR_W = 18
BAR_MAX_H = 40
CHART_H = 78
_PLOT_TOP = 22
_LABEL_H = 14


def _compact_size(n: int) -> str:
    """体积简写：30M / 1.2G / 850K，用于柱顶小标签。"""
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}G"
    if n >= 1_000_000:
        return f"{n / 1e6:.0f}M"
    if n >= 1_000:
        return f"{n / 1e3:.0f}K"
    return f"{n}B"


def _compact_count(n: int) -> str:
    """数量简写：1.2k / 34,000，用于柱顶小标签。"""
    if n >= 100_000:
        return f"{n / 1000:.0f}k"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


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
        self._btn: QPushButton | None = None  # 惰性创建（无 QApplication 的单元测试不建）
        self.setMinimumHeight(CHART_H)
        self.setMaximumHeight(CHART_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def _ensure_btn(self) -> None:
        if self._btn is not None or QApplication.instance() is None:
            return
        btn = QPushButton(tr("对数"), self)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setStyleSheet(
            "QPushButton { color: #a8b6cc; background: rgba(255,255,255,0.06);"
            " border: none; border-radius: 4px; font-size: 9px; padding: 1px 6px; }"
            " QPushButton:hover { color: #e8eef8; background: rgba(255,255,255,0.14); }"
        )
        btn.clicked.connect(self._toggle_scale)
        btn.setGeometry(self.width() - 62, 3, 58, 15)
        self._btn = btn

    # ---------- 数据 ----------

    def set_days(self, summaries, max_days: int = TREND_DAYS) -> None:
        """接收 DaySummary 列表并绘制柱状图（按体积，旧→新左侧最早）。"""
        self._data = [
            (s.day, s.total_size, s.count)
            for s in summaries[:max_days]
            if s.total_size > 0
        ]
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
        return f"{day}  ·  {human_size(size)}  ·  {tr('{count} 个文件', count=count)}"

    # ---------- 几何 ----------

    def _geometry(self) -> tuple[int, int, int, int]:
        """返回 (柱宽, 柱距, 起始 x, 柱区高)。天多时柱宽与柱距都自适应。"""
        n = len(self._data)
        w = self.width()
        bar_area_h = self.height() - _PLOT_TOP - _LABEL_H - 6
        if n == 0:
            return 0, 0, 0, bar_area_h
        gap = BAR_GAP
        bw = BAR_W
        total = n * bw + (n - 1) * gap
        if total > w - 8:
            gap = 2.0
            bw = max(2.0, (w - 8 - (n - 1) * gap) / n)
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
        max_val = max(vals) or 1
        hovered = self._hover >= 0

        axis_font = painter.font()
        axis_font.setPointSizeF(max(7.0, axis_font.pointSizeF() - 1.5))
        label_font = painter.font()
        label_font.setPointSizeF(max(6.5, label_font.pointSizeF() - 2.5))
        axis_pen = QPen(QColor(TEXT_DIM))
        n_bars = len(self._data)
        # 底部只标起始 / 结束两个日期，避免相邻标签重叠
        def _show_label(i: int) -> bool:
            return i == 0 or i == n_bars - 1

        for i, (day, size, count) in enumerate(self._data):
            x = x0 + i * (bw + gap)
            val = size if self._metric == "size" else count
            bh = int(self._bar_height(val, max_val, bar_area_h))
            y = _PLOT_TOP + bar_area_h - bh

            if hovered and i != self._hover:
                painter.setOpacity(0.45)
            else:
                painter.setOpacity(1.0)
            grad = QLinearGradient(x, y, x, y + bh)
            grad.setColorAt(0.0, ACCENT_2)
            grad.setColorAt(1.0, ACCENT)
            painter.setBrush(grad)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(x, y, bw, bh), 3, 3)

            if i == self._hover:
                painter.setOpacity(1.0)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(255, 255, 255, 220), 1.2))
                painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, bw - 1, bh - 1), 3, 3)

            # 柱顶数值简写标签：柱太矮或与切换按钮重叠时省略
            if bh >= 10:
                text = (
                    _compact_size(size)
                    if self._metric == "size"
                    else _compact_count(count)
                )
                lrect = QRectF(x - 24, y - 9, bw + 48, 8)
                if self._btn is None or not self._btn.geometry().intersects(lrect.toRect()):
                    painter.setOpacity(1.0)
                    painter.setFont(label_font)
                    painter.setPen(QColor(TEXT_DIM))
                    painter.drawText(lrect, Qt.AlignHCenter, text)

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
        painter.end()


class _ExpandTree(QTreeView):
    """双击统一由视图自己处理，不依赖 Qt 的 pressedIndex/doubleClicked 时序。

    Qt 6 的真实双击事件序列（press → release → dblclick → release）中
    release 会清空 pressedIndex，导致 QAbstractItemView 发出的 doubleClicked
    信号奇偶次错位（第一次双击无效、第二次才生效）。因此：
    - 组行：直接展开 / 折叠（与 pressedIndex 无关，行为确定）
    - 其余行：发出 row_double_clicked 交给宿主（打开资源管理器定位）
    """

    row_double_clicked = Signal(QModelIndex)

    def mouseDoubleClickEvent(self, event) -> None:
        idx = self.indexAt(event.position().toPoint())
        if not idx.isValid():
            super().mouseDoubleClickEvent(event)
            return
        if self.model().data(idx, IS_GROUP_ROLE):
            # 展开状态是整行级的，但 Qt 的 expandedIndexes 按 (row, column)
            # 区分持久索引：双击不同列时若直接用 idx，isExpanded 会查不到
            # 展开状态而永远走 expand 分支（无法收缩）。统一归一到列 0。
            row_idx = idx.sibling(idx.row(), 0)
            if self.isExpanded(row_idx):
                self.collapse(row_idx)
            else:
                self.expand(row_idx)
            event.accept()
            return
        self.row_double_clicked.emit(idx)
        event.accept()


class DetailPanel(QWidget):
    _days_ready = Signal(int, object)
    _day_ready = Signal(int, object)
    _compile_ready = Signal(int, object, object, object)  # (req, top|Exc, expand_rows, records)
    open_dashboard = Signal()  # 标题行「数据面板」按钮 → 宿主打开看板窗口

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
        self._compile_req = 0
        # 最近一次成功加载的 (day, keyword, event_type)，排序/分组重编译据此判定
        self._loaded_sig: tuple | None = None
        # 当前视图对应的数据版本（storage.change_seq），自动刷新据此跳过无变化重载
        self._data_seq = 0
        self._pending_keep_day: str | None = None
        self._fill_meta: dict | None = None
        self._event_type = "added"
        self._model = FilesTreeModel(self)
        self._chart = TrendChart(self)

        self._build()

        self._days_ready.connect(self._on_days_ready)
        self._day_ready.connect(self._on_day_ready)
        self._compile_ready.connect(self._on_compile_ready)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)

    # ---------- 构建 ----------

    def set_storage(self, storage: Storage) -> None:
        """换用新的数据库连接（位置变更失败回滚时由宿主调用）。"""
        self._storage = storage
        self.reload(keep_day=True)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # 第一行：标题 + 操作按钮（避免和日期/搜索挤在同一行互相遮挡）
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.lbl_title = QLabel(tr("新增文件明细"), objectName="h1")
        title_row.addWidget(self.lbl_title)
        title_row.addStretch(1)
        btn_dashboard = QPushButton(tr("数据面板"))
        btn_dashboard.clicked.connect(self.open_dashboard.emit)
        self.btn_dashboard = btn_dashboard
        btn_export = QPushButton(tr("导出 CSV"))
        btn_export.clicked.connect(self._export_csv)
        self.btn_export = btn_export
        btn_refresh = QPushButton(tr("刷新"), objectName="primary")
        btn_refresh.clicked.connect(lambda: self.reload(keep_day=True))
        self.btn_refresh = btn_refresh
        title_row.addWidget(btn_dashboard)
        title_row.addWidget(btn_export)
        title_row.addWidget(btn_refresh)
        root.addLayout(title_row)

        # 第二行：日期 + 分组切换 + 可伸展的搜索框
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self.lbl_date = QLabel(tr("日期"), objectName="dim")
        filter_row.addWidget(self.lbl_date)
        self.day_box = DayPicker()
        self.day_box.setMinimumWidth(260)
        self.day_box.setMaximumWidth(380)
        self.day_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.day_box.currentIndexChanged.connect(self._on_day_changed)
        filter_row.addWidget(self.day_box)

        self.event_filter = DayPicker()
        self.event_filter.addItem(tr("新增"), "added")
        self.event_filter.addItem(tr("已删除"), "deleted")
        self.event_filter.addItem(tr("全部"), "all")
        self.event_filter.setCurrentIndex(0)
        self.event_filter.setFixedWidth(80)
        self.event_filter.currentIndexChanged.connect(self._on_event_filter_changed)
        filter_row.addWidget(self.event_filter)

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

        self._chart.setVisible(True)
        self._chart.day_selected.connect(self._on_chart_day_selected)
        root.addWidget(self._chart)

        self.table = _ExpandTree()
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.setUniformRowHeights(True)
        self.table.setRootIsDecorated(True)
        self.table.setItemsExpandable(True)
        # 全量数据下逐行展开动画开销大，关闭；视觉影响可忽略
        self.table.setAnimated(False)
        # 双击展开/折叠由 _open_selected 统一处理：Qt 内置 expandsOnDoubleClick
        # 会与 doubleClicked 信号双重触发（展开后立即被 _open_selected 折叠回
        # 去，表现为双击没反应），显式禁用内置处理，行为与 Qt 版本无关
        self.table.setExpandsOnDoubleClick(False)
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
        # 双击统一由 _ExpandTree 处理：组行展开/折叠，文件行走 row_double_clicked
        self.table.row_double_clicked.connect(self._open_selected)
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
            except Exception as exc:
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
        self.day_box.setCurrentIndex(max(idx, 0))
        self.day_box.blockSignals(False)

        self._chart.set_days(days)

        day = self.day_box.currentData() or today
        self._load_day_async(day)

    def _on_day_changed(self, _index: int = 0) -> None:
        day = self.day_box.currentData()
        if day:
            self._load_day_async(day)

    def _on_chart_day_selected(self, day: str) -> None:
        """点击趋势图柱子 → 日期选择器切到该天（触发 _on_day_changed 加载详情）。"""
        self.select_day(day)

    def select_day(self, day: str) -> None:
        """把面板切到指定日期（外部联动入口，如数据看板点柱）。"""
        idx = self.day_box.findData(day)
        if idx >= 0:
            self.day_box.setCurrentIndex(idx)

    def _on_event_filter_changed(self) -> None:
        self._event_type = self.event_filter.currentData()
        day = self.day_box.currentData()
        if day:
            self._load_day_async(day)

    def _on_context_menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        is_group = self._model.data(idx, IS_GROUP_ROLE)
        path = self._model.data(
            idx.sibling(idx.row(), 1) if is_group else idx, PATH_ROLE
        )
        if is_group and not path:
            return

        menu = QMenu(self)
        act_open = menu.addAction(tr("打开"))
        act_reveal = menu.addAction(tr("在资源管理器中定位"))
        menu.addSeparator()
        act_copy_path = menu.addAction(tr("复制路径"))
        act_copy_name = menu.addAction(tr("复制文件名"))
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == act_open:
            self._open_selected(idx)
        elif action == act_reveal and path:
            open_in_explorer(path)
        elif action == act_copy_path and path:
            QApplication.clipboard().setText(path)
        elif action == act_copy_name and path:
            QApplication.clipboard().setText(Path(path).name)

    def retranslate(self) -> None:
        self.setWindowTitle(tr("硬盘新增文件 · 详情"))
        self.lbl_title.setText(tr("新增文件明细"))
        self.lbl_date.setText(tr("日期"))
        self.btn_dashboard.setText(tr("数据面板"))
        self.btn_export.setText(tr("导出 CSV"))
        self.btn_refresh.setText(tr("刷新"))
        self.card_count._title.setText(tr("新增文件"))
        self.card_size._title.setText(tr("占用空间"))
        self.card_free._title.setText(tr("今日剩余空间"))
        self.card_folder._title.setText(tr("最活跃目录"))
        self.card_ext._title.setText(tr("最多的类型"))
        self.chk_group.setText(tr("按应用分组"))
        self.chk_group.setToolTip(tr("把同一应用下的文件收成可折叠分组（类似进程树）"))
        self.search.setPlaceholderText(tr("按文件名或目录筛选…"))
        self.hint.setText(
            tr("双击文件行可在资源管理器中定位；双击应用分组可展开/折叠")
        )
        self.event_filter.setItemText(0, tr("新增"))
        self.event_filter.setItemText(1, tr("已删除"))
        self.event_filter.setItemText(2, tr("全部"))
        self._chart.retranslate()

    def _load_day_async(self, day: str) -> None:
        keyword = self.search.text().strip()
        self._day_req += 1
        req = self._day_req
        self.count_label.setText(tr("加载中…"))

        storage = self._storage
        # 排序/分组状态在后台线程编译时固定，避免主线程重建模型
        sort_col = self._model.sort_col
        sort_order = self._model.sort_order
        grouped = self._model.grouped

        def work() -> None:
            try:
                payload = storage.fetch_day_view(day, keyword, None, self._event_type)
                top, expand_rows = compile_view(
                    payload["records"], sort_col, sort_order, grouped
                )
                payload["top"] = top
                payload["expand_rows"] = expand_rows
            except Exception as exc:
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
        count = data["count"]
        size = data["size"]
        day_total = int(data.get("day_total", count))
        folders = data["folders"]
        exts = data["exts"]
        spaces = data.get("spaces") or []
        day = data["day"]
        keyword = data["keyword"]
        event_type = data.get("event_type", "added")
        folder_key = folders[0] if folders else None
        ext_key = exts[0] if exts else None
        space_sig = tuple(spaces)
        top = data.get("top")
        expand_rows = data.get("expand_rows") or []

        signature = (
            day,
            keyword,
            event_type,
            count,
            size,
            day_total,
            folder_key,
            ext_key,
            tuple((r.path, r.size, r.added_at) for r in records[:40]),
            len(records),
            space_sig,
        )
        if signature == self._load_signature and self._model.file_count() == len(records):
            self._data_seq = int(data.get("seq", self._data_seq))
            self.count_label.setText(
                self._status_text(len(records), count, keyword, day_total)
            )
            return
        self._load_signature = signature

        if top is None:
            # 防御：直接喂 fetch_day_view 原始 payload（测试等）时主线程编译
            top, expand_rows = compile_view(
                records, self._model.sort_col, self._model.sort_order,
                self._model.grouped,
            )

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
            "day_total": day_total,
        }
        self._loaded_sig = (day, keyword, event_type)
        self._data_seq = int(data.get("seq", self._data_seq))

        # 保持滚动位置：刷新/换天时用户上下文不丢
        vbar = self.table.verticalScrollBar()
        prev_pos = vbar.value()
        self._model.set_compiled(records, top, expand_rows)
        hh = self.table.header()
        hh.blockSignals(True)
        hh.setSortIndicator(self._model.sort_col, self._model.sort_order)
        hh.blockSignals(False)
        self._apply_expand_policy()
        vbar.setValue(min(prev_pos, vbar.maximum()))
        self.count_label.setText(
            self._status_text(len(records), count, keyword, day_total)
        )

    @staticmethod
    def _status_text(
        shown: int,
        count: int,
        keyword: str,
        day_total: int | None = None,
    ) -> str:
        if keyword:
            total = count if day_total is None else day_total
            return tr(
                "筛选到 {shown} 条 / 当日共 {total} 条",
                shown=f"{shown:,}",
                total=f"{total:,}",
            )
        return tr("显示 {shown} 条", shown=f"{shown:,}")

    def _apply_expand_policy(self) -> None:
        """展开应默认展开的小组；模型重置后其余行天然折叠，无需 collapseAll。"""
        if not self._model.grouped:
            return
        rows = self._model.expand_rows()
        if not rows:
            return
        view = self.table
        view.setUpdatesEnabled(False)
        try:
            for row in rows:
                view.expand(self._model.index(row, 0))
        finally:
            view.setUpdatesEnabled(True)

    def _on_group_toggled(self, checked: bool) -> None:
        self._model.set_grouped_only(checked)
        self._recompile_async()

    def _auto_refresh(self) -> None:
        if not self.isVisible():
            return
        day = self.day_box.currentData()
        if day != today_str():
            return
        # 数据没变就不重载：全量视图下避免每 5 秒一次无谓的整表重建
        if self._storage.change_seq == self._data_seq:
            return
        self._load_day_async(day)

    def _on_sort_indicator_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        if logical_index < 0:
            return
        self._model.set_sort_only(logical_index, order)
        self._recompile_async()

    def _recompile_async(self) -> None:
        """排序/分组变化：后台基于内存数据重编译，不重新查库。

        若当前视图与最近一次加载不一致（如仍在加载中），回退到完整加载。
        """
        day = self.day_box.currentData()
        keyword = self.search.text().strip()
        if self._loaded_sig != (day, keyword, self._event_type):
            if day:
                self._load_day_async(day)
            return

        records = self._model.records()
        self._compile_req += 1
        req = self._compile_req
        sort_col = self._model.sort_col
        sort_order = self._model.sort_order
        grouped = self._model.grouped
        self.count_label.setText(tr("排序中…"))

        def work() -> None:
            try:
                top, expand_rows = compile_view(
                    records, sort_col, sort_order, grouped
                )
            except Exception as exc:
                top = exc
                expand_rows = None
            # 把编译所基于的记录集一并发回，供主线程校验身份
            self._compile_ready.emit(req, top, expand_rows, records)

        threading.Thread(target=work, name="dw-panel-compile", daemon=True).start()

    def _on_compile_ready(
        self, req: int, top: object, expand_rows: object, records: object
    ) -> None:
        if req != self._compile_req or not self.isVisible():
            return
        if isinstance(top, Exception):
            self.count_label.setText(tr("加载失败：{err}", err=top))
            return
        if not isinstance(top, list) or not isinstance(expand_rows, list):
            return  # 防御：非列表结果直接丢弃
        if records is not self._model.records():
            # 编译期间记录集已被新的加载替换（自动刷新/手动刷新），
            # 基于旧快照的编译结果不能应用到新数据上，直接丢弃
            return
        day = self.day_box.currentData()
        keyword = self.search.text().strip()
        if self._loaded_sig != (day, keyword, self._event_type):
            return  # 视图已切走，丢弃过期结果

        vbar = self.table.verticalScrollBar()
        prev_pos = vbar.value()
        self._model.set_compiled(
            self._model.records(), top, expand_rows
        )
        hh = self.table.header()
        hh.blockSignals(True)
        hh.setSortIndicator(self._model.sort_col, self._model.sort_order)
        hh.blockSignals(False)
        self._apply_expand_policy()
        vbar.setValue(min(prev_pos, vbar.maximum()))
        meta = self._fill_meta
        if meta is not None:
            self.count_label.setText(
                self._status_text(
                    self._model.file_count(),
                    int(meta.get("count", 0)),
                    str(meta.get("keyword", "")),
                    int(meta.get("day_total", meta.get("count", 0))),
                )
            )

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
                bundle = storage.fetch_day_view(
                    day, keyword, limit=None, event_type=self._event_type
                )
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
                        ts = r.deleted_at if r.deleted and r.deleted_at else r.added_at
                        writer.writerow(
                            [
                                datetime.fromtimestamp(ts).strftime(
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
            except Exception as exc:
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
        self._compile_req += 1


def _shorten(text: str, limit: int) -> str:
    text = text or ""  # SQL 列理论上非 NULL，防御历史脏数据
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


def _empty_day(day: str):
    from ..storage import DaySummary

    return DaySummary(day, 0, 0)
