"""通用下拉选择控件 DayPicker：弹层画在宿主窗内部，避免原生弹层错位。

项目里所有下拉选择（日期 / 事件类型 / 语言 / 预设）都用它，
API 与 QComboBox 对齐（addItem/currentData/setCurrentIndex/setItemText/…）。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)


class DayPicker(QWidget):
    """选择控件：列表画在宿主窗内部，避免原生下拉弹层错位/残影。

    项目里所有下拉选择（日期 / 事件类型 / 语言 / 预设）都用它。
    API 与 QComboBox 对齐：addItem(text, userData) 存值、currentData()
    取值；setItemData 仅支持 Qt.ToolTipRole（悬浮提示），其余 role 忽略。
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

