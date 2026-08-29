"""空间活动页分页、过滤和详情。"""

import time

from PySide6.QtCore import Qt

from diskwatch.storage import Storage, make_record
from diskwatch.ui.activity import PAGE_SIZE, ActivityPanel


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
        assert panel._total == PAGE_SIZE + 5
        assert panel.table.rowCount() == PAGE_SIZE

        panel._change_page(1)
        assert panel._page == 1
        assert panel.table.rowCount() == 5

        panel.search.setText("item-007")
        panel._filters_changed()
        assert panel._total == 1
        assert panel.table.item(0, 1).text() == "item-007.bin"
        panel.table.selectRow(0)
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
        assert panel.table.rowCount() == 5  # 3 events + 2 group headers
        first_group = panel.table.item(0, 0)
        assert first_group.data(Qt.UserRole + 1) is not None

        before = panel.table.rowCount()
        panel._table_clicked(0, 0)
        assert panel.table.rowCount() < before
        panel.close()
    finally:
        storage.close()
