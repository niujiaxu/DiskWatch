"""桌面悬浮组件：无边框、置顶、可拖动的小卡片。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..storage import Storage, human_size, today_str
from ..watcher import FileMonitor, open_in_explorer
from .style import BG_BOTTOM, BG_TOP, BORDER, OK, WARN, WIDGET_QSS

REFRESH_MS = 2000
# 视口大约显示这么多行；超出用滚轮 / 细滚动条浏览
RECENT_VISIBLE = 5
# 固定控件池上限：只改文案、不反复 new，避免卡顿
RECENT_MAX = 24
ROW_HEIGHT = 40


class FileRow(QWidget):
    """最近新增文件的一行：名称 + 目录/时间，双击定位。"""

    def __init__(self) -> None:
        super().__init__()
        self._path = ""
        self.setFixedHeight(ROW_HEIGHT)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(1)
        self.name = QLabel("—", objectName="fname")
        self.meta = QLabel("", objectName="fmeta")
        self.name.setTextInteractionFlags(Qt.NoTextInteraction)
        lay.addWidget(self.name)
        lay.addWidget(self.meta)
        self.setCursor(Qt.PointingHandCursor)

    def set_record(self, rec) -> None:
        self._path = rec.path
        self.name.setText(_elide(rec.name, 34))
        when = datetime.fromtimestamp(rec.added_at).strftime("%H:%M")
        folder = _elide_left(rec.folder, 30)
        self.meta.setText(f"{when}  ·  {human_size(rec.size)}  ·  {folder}")
        self.setToolTip(rec.path)
        if not self.isVisible():
            self.show()

    def clear(self) -> None:
        self._path = ""
        if self.isVisible():
            self.hide()

    def mouseDoubleClickEvent(self, event) -> None:
        if self._path:
            open_in_explorer(self._path)


class FloatingWidget(QWidget):
    open_panel = Signal()
    open_settings = Signal()
    request_quit = Signal()
    hidden_by_user = Signal()
    collapse_requested = Signal()

    def __init__(self, storage: Storage, monitor: FileMonitor, config) -> None:
        super().__init__()
        self._storage = storage
        self._monitor = monitor
        self._config = config
        self._drag_offset: QPoint | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(272)
        self.setStyleSheet(WIDGET_QSS)
        self._signature: tuple | None = None
        self._build()
        self._restore_geometry()

        # 半透明窗口每次重绘都要走合成，代价不低。
        # 所以定时器只在可见时跑，且内容没变化时直接返回、不碰任何控件。
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 13, 16, 13)
        root.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.dot = QLabel("●", objectName="dot")
        self.title = QLabel(tr("今日新增文件"), objectName="title")
        btn_min = QPushButton("－", objectName="close")
        btn_min.setFixedSize(28, 28)
        btn_min.setToolTip(tr("收成迷你悬浮球"))
        btn_min.clicked.connect(self.collapse_requested.emit)
        btn_close = QPushButton("✕", objectName="close")
        btn_close.setFixedSize(28, 28)
        btn_close.setToolTip(tr("隐藏组件（托盘图标可再次唤出）"))
        btn_close.clicked.connect(self._hide_self)
        head.addWidget(self.dot)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(btn_min)
        head.addWidget(btn_close)
        root.addLayout(head)

        num = QHBoxLayout()
        num.setSpacing(6)
        self.count = QLabel("0", objectName="count")
        unit = QLabel(tr("个"), objectName="unit")
        unit.setAlignment(Qt.AlignBottom)
        num.addWidget(self.count)
        num.addWidget(unit)
        num.addStretch(1)
        self.total_size = QLabel("0 B", objectName="sub")
        self.total_size.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        num.addWidget(self.total_size)
        root.addLayout(num)

        self.sep_label = QLabel(tr("最近"), objectName="title")
        root.addWidget(self.sep_label)

        host = QWidget(objectName="recentHost")
        self._list_lay = QVBoxLayout(host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)

        self.rows: list[FileRow] = []
        for _ in range(RECENT_MAX):
            row = FileRow()
            row.hide()
            self.rows.append(row)
            self._list_lay.addWidget(row)
        self._list_lay.addStretch(1)

        self.scroll = QScrollArea(objectName="recentScroll")
        self.scroll.setWidget(host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        # 固定视口高度：卡片尺寸稳定，滚动只发生在列表内部
        self.scroll.setFixedHeight(RECENT_VISIBLE * ROW_HEIGHT)
        root.addWidget(self.scroll)

        self.empty = QLabel(
            tr("暂无记录。默认不监控 AppData、Program Files 等系统目录，"
               "可在「设置 → 过滤规则」里调整。"),
            objectName="sub",
        )
        self.empty.setWordWrap(True)
        root.addWidget(self.empty)

        foot = QVBoxLayout()
        foot.setSpacing(4)
        self.status = QLabel("", objectName="fmeta")
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_detail = QPushButton(tr("详情"), objectName="tool")
        btn_setting = QPushButton(tr("设置"), objectName="tool")
        btn_detail.clicked.connect(self.open_panel.emit)
        btn_setting.clicked.connect(self.open_settings.emit)
        foot.addWidget(self.status)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_detail)
        btn_row.addWidget(btn_setting)
        foot.addLayout(btn_row)
        root.addLayout(foot)

    # ---------- 数据 ----------

    def refresh(self) -> None:
        day = today_str()
        count, size = self._storage.day_stats(day)
        recent = self._storage.recent_files(day, RECENT_MAX)
        spaces = self._storage.disk_space_for_day(day)
        _, dropped, pending = self._monitor.stats()
        roots = len(self._monitor.roots)

        signature = (
            day,
            count,
            size,
            roots,
            dropped,
            pending,
            tuple((r.path, r.size, r.added_at) for r in recent),
            tuple(spaces),
        )
        if signature == self._signature:
            return
        self._signature = signature

        self.count.setText(f"{count:,}")
        self.total_size.setText(human_size(size))

        # 保留滚动位置，避免刷新时列表被弹回顶部
        bar = self.scroll.verticalScrollBar()
        scroll_pos = bar.value()

        n = len(recent)
        for i, row in enumerate(self.rows):
            if i < n:
                row.set_record(recent[i])
            else:
                row.clear()

        has_any = n > 0
        self.empty.setVisible(not has_any)
        self.sep_label.setVisible(has_any)
        self.scroll.setVisible(has_any)

        parts = [tr("监控 {roots} 个位置", roots=roots)]
        if spaces:
            drive, free, _total = min(spaces, key=lambda s: s[1])
            parts.append(tr(" · 剩余 {free}", free=f"{drive} {human_size(free)}"))
        if pending:
            parts.append(tr(" · 队列 {queue}", queue=pending))
        if dropped:
            parts.append(tr(" · 丢弃 {dropped}", dropped=dropped))
        self.status.setText("".join(parts))
        self.dot.setText("●" if roots else "○")
        self.dot.setStyleSheet(
            f"color:{OK.name()};" if roots else f"color:{WARN.name()};"
        )

        bar.setValue(scroll_pos)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start(REFRESH_MS)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    # ---------- 外观 ----------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, BG_TOP)
        grad.setColorAt(1.0, BG_BOTTOM)
        p.fillPath(path, grad)
        p.setPen(QPen(BORDER, 1))
        p.drawPath(path)

    # ---------- 交互 ----------

    def _press_on_scroll(self, pos: QPoint) -> bool:
        w = self.childAt(pos)
        while w is not None:
            if w is self.scroll:
                return True
            w = w.parentWidget()
        return False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # 列表区域留给滚轮/滚动条，不开始拖窗口，避免手感打架
            if self._press_on_scroll(event.position().toPoint()):
                self._drag_offset = None
                return
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self._config.set("widget_pos", [self.x(), self.y()])
            self._config.save_soon()

    def contextMenuEvent(self, event) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction(tr("打开详情面板"), self.open_panel.emit)
        menu.addAction(tr("设置"), self.open_settings.emit)
        menu.addSeparator()
        menu.addAction(tr("收成迷你球"), self.collapse_requested.emit)
        menu.addAction(tr("隐藏组件"), self._hide_self)
        menu.addAction(tr("退出"), self.request_quit.emit)
        menu.exec(event.globalPos())

    def _hide_self(self) -> None:
        self.hide()
        self._config.set("widget_visible", False)
        self._config.save_soon()
        self.hidden_by_user.emit()

    def show_widget(self) -> None:
        self.show()
        self.raise_()
        self._config.set("widget_visible", True)
        self._config.save_soon()

    def apply_appearance(self) -> None:
        self.setWindowOpacity(float(self._config.get("widget_opacity", 0.95)))
        on_top = bool(self._config.get("always_on_top", True))
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on_top)
        if visible:
            self.show()

    def _restore_geometry(self) -> None:
        self.apply_appearance()
        pos = self._config.get("widget_pos")
        screen = QApplication.primaryScreen().availableGeometry()
        if isinstance(pos, list) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
            if screen.contains(QPoint(x + 40, y + 40)):
                self.move(x, y)
                return
        self.adjustSize()
        self.move(screen.right() - self.width() - 28, screen.top() + 60)


def _elide(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _elide_left(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]
