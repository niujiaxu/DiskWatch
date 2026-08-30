"""空间活动页分页、过滤和详情。"""

import threading
import time

from PySide6.QtCore import Qt

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
