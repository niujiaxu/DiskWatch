"""详情面板 FilesTreeModel 逻辑测试：分组、排序、平铺。

列索引: 0=时间, 1=文件名, 2=大小, 3=类型, 4=所在目录
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt

from diskwatch.storage import DaySummary, Storage, human_size, make_record
from diskwatch.ui.panel import FilesTreeModel, TrendChart


def _rec(path: str, size: int, added_at: float) -> object:
    return make_record(str(Path(path)), size, added_at=added_at)


def _days() -> list[DaySummary]:
    return [
        DaySummary("2026-08-01", 5, 1_000_000),
        DaySummary("2026-08-02", 12, 8_500_000),
        DaySummary("2026-08-03", 3, 250_000),
        DaySummary("2026-08-04", 0, 0),  # 零体积天不画柱
    ]


def _model(records: list) -> FilesTreeModel:
    m = FilesTreeModel()
    m.set_records(records)
    return m


def test_flat_sort_by_name() -> None:
    m = _model(
        [
            _rec(r"D:\b.txt", 100, 100.0),
            _rec(r"D:\a.txt", 200, 200.0),
        ]
    )
    m.set_grouped(False)
    m.sort_by(1, Qt.AscendingOrder)  # col 1 = 文件名
    names = [m.data(m.index(i, 1), Qt.DisplayRole) for i in range(m.rowCount())]
    assert names == ["a.txt", "b.txt"], names


def test_flat_sort_by_size_desc() -> None:
    m = _model(
        [
            _rec(r"D:\small.txt", 100, 100.0),
            _rec(r"D:\large.txt", 200, 200.0),
        ]
    )
    m.set_grouped(False)
    m.sort_by(2, Qt.DescendingOrder)  # col 2 = 大小
    names = [m.data(m.index(i, 1), Qt.DisplayRole) for i in range(m.rowCount())]
    assert names == ["large.txt", "small.txt"], names


def test_grouped_top_level() -> None:
    """2 文件和 1 独立文件 → 1 组 + 1 提升行。"""
    m = _model(
        [
            _rec(r"D:\SomeApp\a.txt", 10, 100.0),
            _rec(r"D:\SomeApp\b.txt", 20, 200.0),
            _rec(r"D:\Solo\c.txt", 30, 300.0),
        ]
    )
    assert m.grouped
    assert m.rowCount() == 2  # SomeApp 组 + Solo 单文件提升
    top_names = [m.data(m.index(i, 1), Qt.DisplayRole) for i in range(m.rowCount())]
    assert "c.txt" in top_names, top_names  # 提升的单文件在顶层


def test_grouped_children() -> None:
    m = _model(
        [
            _rec(r"D:\App\a.txt", 10, 100.0),
            _rec(r"D:\App\b.txt", 20, 200.0),
        ]
    )
    assert m.rowCount() == 1  # 只有 App 组
    group = m.index(0, 0)
    assert m.rowCount(group) == 2
    child = m.index(0, 1, group)  # col 1 = 文件名
    assert m.data(child, Qt.DisplayRole) == "b.txt"  # added_at 倒序，b.txt(200) 先于 a.txt(100)


def test_expand_rows_small_groups() -> None:
    """子文件数 < 3 的组应自动展开。"""
    m = _model(
        [
            _rec(r"D:\One\a.txt", 10, 100.0),
            _rec(r"D:\One\b.txt", 20, 200.0),
            _rec(r"D:\Two\c.txt", 30, 300.0),
            _rec(r"D:\Two\d.txt", 40, 400.0),
            _rec(r"D:\Two\e.txt", 50, 500.0),
        ]
    )
    rows = m.expand_rows()
    assert len(rows) == 1  # One(<3) 展开, Two(3) 不展开


def test_toggle_grouped() -> None:
    m = _model([_rec(r"D:\1.txt", 1, 1.0)])
    m.set_grouped(False)
    assert not m.grouped
    m.set_grouped(True)
    assert m.grouped


def test_file_count_and_records() -> None:
    recs = [_rec(r"D:\1.txt", 1, 1.0), _rec(r"D:\2.txt", 2, 2.0)]
    m = _model(recs)
    assert m.file_count() == 2
    assert m.records() == recs


def test_panel_smoke(tmp_path) -> None:
    """DetailPanel 端到端冒烟：写库 → 展示 → 模型有数据。"""
    from PySide6.QtWidgets import QApplication

    from diskwatch.ui.panel import DetailPanel

    QApplication.instance() or QApplication([])
    s = Storage(tmp_path / "t.db")
    now = time.time()
    try:
        s.add_files([make_record(r"C:\a\x.txt", 5, added_at=now)])
        panel = DetailPanel(s)
        panel.show()
        panel.reload(keep_day=False)
        payload = s.fetch_days_with_data()
        panel._on_days_ready(panel._days_req, payload)
        assert panel.day_box.count() >= 1

        day = panel.day_box.currentData()
        view = s.fetch_day_view(day)
        panel._on_day_ready(panel._day_req, view)
        assert panel._model.file_count() == 1
    finally:
        s.close()


def test_event_filter_switches_to_deleted() -> None:
    """切换事件类型下拉 → 加载删除文件列表。"""
    from PySide6.QtWidgets import QApplication

    from diskwatch.ui.panel import DetailPanel

    QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="dw_panel_et_"))
    s = Storage(tmp / "t.db")
    now = time.time()
    try:
        s.add_files([make_record(r"C:\a\live.txt", 1, added_at=now)])
        s.add_files([make_record(r"C:\a\dead.txt", 2, added_at=now - 1)])
        s.mark_deleted([r"C:\a\dead.txt"])
        panel = DetailPanel(s)
        panel.show()
        panel.reload(keep_day=False)
        payload = s.fetch_days_with_data()
        panel._on_days_ready(panel._days_req, payload)
        day = panel.day_box.currentData()

        # 默认 "新增" 只看到 live.txt
        view = s.fetch_day_view(day)
        panel._on_day_ready(panel._day_req, view)
        assert panel._model.file_count() == 1

        # 切换到 "已删除"
        panel._event_type = "deleted"
        view_del = s.fetch_day_view(day, event_type="deleted")
        panel._on_day_ready(panel._day_req, view_del)
        assert panel._model.file_count() == 1
        assert panel._model.records()[0].deleted
    finally:
        s.close()


def test_deleted_row_timestamp_display() -> None:
    """删除文件应显示 deleted_at 时间而非 added_at。"""
    from datetime import datetime

    rec = make_record(r"D:\d.txt", 100, added_at=1000.0)
    rec_normal = make_record(r"D:\n.txt", 200, added_at=2000.0)
    # 用 dataclass 不可直接 setattr，需要构造新对象

    rec_deleted = replace(rec, deleted=True, deleted_at=3000.0)
    assert rec_deleted.deleted
    time_str = datetime.fromtimestamp(3000.0).strftime("%H:%M:%S")

    from diskwatch.ui import panel as pmod

    assert pmod._file_display(rec_normal, 0, Qt.DisplayRole) != time_str
    assert pmod._file_display(rec_deleted, 0, Qt.DisplayRole) == time_str


def test_trend_chart_data_dimension() -> None:
    """趋势图以体积为主维度，零体积天不画柱，旧→新排序。"""
    c = TrendChart()
    c.set_days(_days())
    assert c.isVisible()
    assert len(c._data) == 3, c._data  # 08-04 体积为 0 被过滤
    assert c._data[0][0] == "2026-08-03", c._data  # 最新在左
    assert c._data[-1][0] == "2026-08-01"
    assert c._data[0][1] == 250_000  # size 为主维度
    c.set_days([DaySummary("2026-08-05", 0, 0)])
    assert not c.isVisible()  # 全零 → 隐藏


def test_trend_chart_tip_text() -> None:
    c = TrendChart()
    c.set_days(_days())
    tip = c._tip_text(1)  # 08-02, 8_500_000 B, 12 个
    assert "2026-08-02" in tip
    assert human_size(8_500_000) in tip
    assert "12" in tip


def test_trend_chart_hover_hit() -> None:
    c = TrendChart()
    c.set_days(_days())
    c.resize(300, 64)
    bw, gap, x0, _ = c._geometry()
    assert c._index_at(x0 + bw // 2) == 0
    assert c._index_at(x0 + bw + gap // 2) == -1  # 间隙不命中
    assert c._index_at(x0 + 2 * (bw + gap) + bw // 2) == 2
    assert c._index_at(-5) == -1
    assert c._index_at(9999) == -1
