"""空间活动页分页、过滤和详情。"""

import threading
import time
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QSplitter, QWidget

from diskwatch.storage import Storage, make_record
from diskwatch.ui.activity import PAGE_SIZE, ActivityPanel


def _settle(qapp, panel: ActivityPanel, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        with panel._workers_lock:
            running = bool(panel._workers)
        if not running:
            qapp.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("activity workers did not finish")


def test_activity_panel_pages_and_filters(qapp, tmp_path) -> None:
    storage = Storage(tmp_path / "activity.db")
    now = time.time()
    try:
        records = [
            make_record(
                rf"C:\data\item-{i:03d}.bin",
                i + 1,
                now - i / 1000,
                "development" if i % 2 else "user",
            )
            for i in range(PAGE_SIZE + 5)
        ]
        storage.add_files(records)
        panel = ActivityPanel(storage)
        _settle(qapp, panel)
        assert panel._total == PAGE_SIZE + 5
        assert panel.table.rowCount() == PAGE_SIZE

        panel._sort_by_column(3)
        _settle(qapp, panel)
        assert panel._space_sort_order == "delta_desc"
        assert panel._events[0].delta_bytes == PAGE_SIZE + 5
        assert panel.table.horizontalHeader().isSortIndicatorShown()

        panel._sort_by_column(3)
        _settle(qapp, panel)
        assert panel._space_sort_order == "delta_asc"
        assert panel._events[0].delta_bytes == 1

        panel._change_page(1)
        _settle(qapp, panel)
        assert panel._page == 1
        assert panel.table.rowCount() == 5

        panel.search.setText("item-007")
        panel._search_timer.stop()
        panel._filters_changed()
        _settle(qapp, panel)
        assert panel._total == 1
        assert panel.table.item(0, 1).text() == "item-007.bin"
        panel.table.selectRow(0)
        _settle(qapp, panel)
        assert "item-007.bin" in panel.detail_name.text()
        assert "→" in panel.detail_history.text()
        panel.close()
    finally:
        storage.close()


def test_activity_panel_uses_day_picker_only(qapp, tmp_path) -> None:
    from diskwatch.ui.picker import DayPicker

    storage = Storage(tmp_path / "picker.db")
    try:
        panel = ActivityPanel(storage)
        _settle(qapp, panel)
        assert len(panel.findChildren(DayPicker)) == 6
        panel.close()
    finally:
        storage.close()


def test_day_picker_popup_fits_items_and_stays_inside_host(qapp) -> None:
    from diskwatch.ui.picker import DayPicker

    host = QWidget()
    host.resize(360, 180)
    picker = DayPicker(host)
    picker.setGeometry(250, 138, 100, 34)
    for label in ("跟随系统", "浅色", "深色"):
        picker.addItem(label, label)
    host.show()
    qapp.processEvents()

    picker._open_popup()
    qapp.processEvents()
    popup = picker._popup
    assert popup is not None and popup.isVisible()
    row_h = max(24, popup.sizeHintForRow(0))
    expected_height = row_h * 3 + popup.frameWidth() * 2 + 2
    assert popup.height() <= expected_height
    assert popup.visualItemRect(popup.item(2)).bottom() >= popup.viewport().height() - 3
    assert popup.geometry().left() >= 8
    assert popup.geometry().right() <= host.width() - 8
    assert popup.geometry().top() >= 8
    assert popup.geometry().bottom() <= host.height() - 8
    host.close()


def test_activity_filter_actions_use_separate_rows(qapp, tmp_path) -> None:
    storage = Storage(tmp_path / "responsive-filters.db")
    try:
        panel = ActivityPanel(storage)
        panel.resize(724, 560)
        panel.show()
        _settle(qapp, panel)
        assert panel.search.geometry().top() > panel.range_picker.geometry().top()
        assert panel.group_picker.geometry().right() <= panel.width() - 20
        assert panel.btn_export.geometry().right() <= panel.width() - 20
        panel.close()
    finally:
        storage.close()


def test_activity_splitter_fills_width_with_long_detail_text(qapp, tmp_path) -> None:
    storage = Storage(tmp_path / "responsive-splitter.db")
    long_name = "awesun_service.20260819-074729-with-a-very-long-unbroken-name.log"
    try:
        storage.add_files(
            [
                make_record(
                    rf"C:\Users\niu\AppData\Roaming\Oray\AweSun\log\{long_name}",
                    178,
                )
            ]
        )
        panel = ActivityPanel(storage)
        panel.resize(1800, 900)
        panel.show()
        _settle(qapp, panel)
        panel.table.selectRow(0)
        _settle(qapp, panel)

        splitter = panel.findChild(QSplitter)
        assert splitter is panel.splitter
        assert panel.detail_card.minimumSizeHint().width() <= 340
        available = splitter.width() - splitter.handleWidth()
        table_width, detail_width = splitter.sizes()
        assert detail_width <= panel.detail_card.maximumWidth()
        assert table_width + detail_width >= available - 2
        assert table_width >= available - panel.detail_card.maximumWidth() - 2

        # 模拟窗口恢复后再次最大化，分栏必须继续填满最终可用宽度。
        panel.resize(1100, 700)
        qapp.processEvents()
        panel.resize(1800, 900)
        qapp.processEvents()
        table_width, detail_width = splitter.sizes()
        available = splitter.width() - splitter.handleWidth()
        assert table_width + detail_width >= available - 2
        assert table_width >= available - panel.detail_card.maximumWidth() - 2
        panel.close()
    finally:
        storage.close()


def test_activity_panel_groups_and_collapses_current_page(qapp, tmp_path) -> None:
    storage = Storage(tmp_path / "grouped.db")
    try:
        storage.add_files(
            [
                make_record(r"C:\Users\me\note.txt", 10, category="user"),
                make_record(r"C:\dev\build\a.bin", 20, category="development"),
                make_record(r"C:\dev\build\b.bin", 30, category="development"),
            ]
        )
        panel = ActivityPanel(storage)
        panel.group_picker.setCurrentIndex(panel.group_picker.findData("category"))
        _settle(qapp, panel)
        assert panel.table.rowCount() == 5  # 3 events + 2 group headers
        first_group = panel.table.item(0, 0)
        assert first_group.data(Qt.UserRole + 1) is not None

        before = panel.table.rowCount()
        panel._table_clicked(0, 0)
        assert panel.table.rowCount() < before
        _settle(qapp, panel)
        panel.close()
    finally:
        storage.close()


def test_detail_history_never_blocks_ui_thread(qapp, tmp_path, monkeypatch) -> None:
    storage = Storage(tmp_path / "nonblocking.db")
    gate = threading.Event()
    try:
        storage.add_files([make_record(r"C:\data\large-history.bin", 10)])
        panel = ActivityPanel(storage)
        _settle(qapp, panel)

        def blocked_history(_path: str, *, limit: int = 100):
            gate.wait(2.0)
            return []

        monkeypatch.setattr(storage, "file_event_history", blocked_history)
        started = time.perf_counter()
        panel._show_selected()
        elapsed = time.perf_counter() - started

        assert elapsed < 0.2
        assert panel.btn_copy.isEnabled()
        assert "加载" in panel.detail_history.text()
        gate.set()
        _settle(qapp, panel)
        panel.close()
    finally:
        gate.set()
        storage.close()


def test_page_reload_never_blocks_ui_thread(qapp, tmp_path, monkeypatch) -> None:
    storage = Storage(tmp_path / "nonblocking-page.db")
    gate = threading.Event()
    try:
        storage.add_files([make_record(r"C:\data\row.bin", 10)])
        panel = ActivityPanel(storage)
        _settle(qapp, panel)
        original_count = storage.space_event_count

        def blocked_count(**kwargs):
            gate.wait(2.0)
            return original_count(**kwargs)

        monkeypatch.setattr(storage, "space_event_count", blocked_count)
        started = time.perf_counter()
        panel.reload()
        elapsed = time.perf_counter() - started

        assert elapsed < 0.2
        assert not panel.btn_refresh.isEnabled()
        # 查询期间现有详情仍可交互，不清空表格或禁用复制按钮。
        assert panel.btn_copy.isEnabled()
        gate.set()
        _settle(qapp, panel)
        assert panel.btn_refresh.isEnabled()
        panel.close()
    finally:
        gate.set()
        storage.close()


def test_showing_loaded_panel_does_not_rebuild_table(qapp, tmp_path, monkeypatch) -> None:
    storage = Storage(tmp_path / "show-without-rebuild.db")
    try:
        storage.add_files([make_record(r"C:\data\row.bin", 10)])
        panel = ActivityPanel(storage)
        _settle(qapp, panel)
        rebuilds: list[bool] = []
        monkeypatch.setattr(panel, "_fill_table", lambda: rebuilds.append(True))

        panel.show()
        qapp.processEvents()

        assert rebuilds == []
        panel.close()
    finally:
        storage.close()


def test_table_fill_loads_selected_history_once(qapp, tmp_path, monkeypatch) -> None:
    storage = Storage(tmp_path / "single-selection.db")
    try:
        storage.add_files([make_record(r"C:\data\row.bin", 10)])
        panel = ActivityPanel(storage)
        _settle(qapp, panel)
        requested: list[str] = []
        monkeypatch.setattr(panel, "_load_history_async", requested.append)

        panel._fill_table()

        assert requested == [r"C:\data\row.bin"]
        panel.close()
    finally:
        storage.close()


def test_activity_detail_card_scales_with_width(qapp, tmp_path) -> None:
    """窗口变宽时右侧详情卡按比例变宽，而不是钉死在固定宽度。"""
    storage = Storage(tmp_path / "scale.db")
    try:
        panel = ActivityPanel(storage)
        panel.resize(900, 600)
        panel.show()
        _settle(qapp, panel)
        _table_w, small = panel.splitter.sizes()

        panel.resize(1700, 900)
        qapp.processEvents()
        _settle(qapp, panel)
        _table_w, large = panel.splitter.sizes()

        assert large > small
        assert large <= panel.detail_card.maximumWidth()
        panel.close()
    finally:
        storage.close()


def test_table_cells_have_no_focus_rect(qapp, tmp_path) -> None:
    """当前单元格不再画黑色虚线焦点框（整行高亮保留）。"""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter, QPixmap, QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    from diskwatch.ui.style import NoFocusDelegate

    storage = Storage(tmp_path / "no-focus.db")
    try:
        storage.add_files([make_record(r"C:\data\a.bin", 10)])
        panel = ActivityPanel(storage)
        _settle(qapp, panel)
        assert isinstance(panel.table.itemDelegate(), NoFocusDelegate)

        seen: dict[str, bool] = {}

        class Spy(NoFocusDelegate):
            def paint(self, painter, option, index):
                super().paint(painter, option, index)
                seen["focus"] = bool(option.state & QStyle.State_HasFocus)

        model = QStandardItemModel()
        model.appendRow(QStandardItem("x"))
        pixmap = QPixmap(60, 24)
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.state |= QStyle.State_HasFocus
        option.rect = QRect(0, 0, 60, 24)
        try:
            Spy().paint(painter, option, model.index(0, 0))
        finally:
            painter.end()
        assert seen["focus"] is False
        panel.close()
    finally:
        storage.close()


def test_activity_sorts_columns_except_folder(qapp, tmp_path) -> None:
    """时间/文件名/活动类型/分类可升降序切换；「所在目录」列不参与排序。"""
    storage = Storage(tmp_path / "sortable.db")
    now = time.time()
    try:
        storage.add_files(
            [
                make_record(r"C:\data\b.bin", 20, now - 2, "user"),
                make_record(r"C:\data\a.bin", 10, now - 1, "development"),
                make_record(r"C:\data\c.bin", 30, now - 3, "cache"),
            ]
        )
        panel = ActivityPanel(storage)
        panel.resize(1200, 500)
        panel.show()
        _settle(qapp, panel)
        header = panel.table.horizontalHeader()

        def names() -> list[str]:
            return [Path(e.path).name for e in panel._events]

        panel._sort_by_column(1)
        _settle(qapp, panel)
        assert panel._space_sort_order == "name_asc"
        assert names() == ["a.bin", "b.bin", "c.bin"]
        assert header.sortIndicatorSection() == 1

        panel._sort_by_column(1)
        _settle(qapp, panel)
        assert panel._space_sort_order == "name_desc"
        assert names() == ["c.bin", "b.bin", "a.bin"]

        panel._sort_by_column(0)
        _settle(qapp, panel)
        assert panel._space_sort_order == "time_desc"
        assert names() == ["a.bin", "b.bin", "c.bin"]
        panel._sort_by_column(0)
        _settle(qapp, panel)
        assert panel._space_sort_order == "time_asc"
        assert names() == ["c.bin", "b.bin", "a.bin"]

        panel._sort_by_column(2)
        _settle(qapp, panel)
        assert panel._space_sort_order == "type_asc"
        assert {e.event_type for e in panel._events} == {"created"}

        panel._sort_by_column(4)
        _settle(qapp, panel)
        assert panel._space_sort_order == "category_asc"
        assert [e.category for e in panel._events] == [
            "cache", "development", "user",
        ]

        # 模拟 Qt 点击表头时把指示器挪到「所在目录」：必须立即还原
        header.setSortIndicator(5, Qt.AscendingOrder)
        qapp.processEvents()
        assert header.sortIndicatorSection() == 4
        assert panel._space_sort_order == "category_asc"

        # 真实点击「所在目录」表头：排序状态不变、指示器不留在该列
        x = sum(panel.table.columnWidth(i) for i in range(5))
        x += panel.table.columnWidth(5) // 2
        QTest.mouseClick(
            header.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(x, header.height() // 2),
        )
        qapp.processEvents()
        assert panel._space_sort_order == "category_asc"
        assert header.sortIndicatorSection() == 4
        panel.close()
    finally:
        storage.close()
