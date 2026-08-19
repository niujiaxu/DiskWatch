"""数据看板测试：聚合查询、图表组件、窗口冒烟。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from diskwatch.storage import DaySummary, Storage, make_record
from diskwatch.ui.dashboard import (
    CumulativeChart,
    DashboardPanel,
    SpaceTrendChart,
    TopBarsChart,
)
from diskwatch.ui.panel import TrendChart


def _storage(tmp: Path) -> Storage:
    return Storage(tmp / "t.db")


def _seed(s: Storage) -> None:
    """最近 3 天数据：目录 AppA/AppB、扩展名 .zip/.txt、两盘空间采样。"""
    today = date.today()
    for k in range(3):
        day = (today - timedelta(days=2 - k)).isoformat()
        noon = datetime.strptime(day, "%Y-%m-%d").timestamp() + 43200
        s.add_files(
            [
                make_record(rf"C:\AppA\a{k}.txt", 3_000_000 + k, added_at=noon + 1),
                make_record(rf"C:\AppA\b{k}.zip", 30_000_000 + k, added_at=noon + 2),
                make_record(rf"C:\AppB\c{k}.txt", 1_000 + k, added_at=noon + 3),
            ]
        )
        s.record_disk_space(
            [(day, "C:", 100 - k * 10, 200), (day, "D:", 50 + k, 80)]
        )


# ---------------------------------------------------------------------------
# storage 聚合查询
# ---------------------------------------------------------------------------


def test_top_folders_range(qapp, tmp_path) -> None:
    s = _storage(tmp_path)
    try:
        _seed(s)
        rows = s.top_folders_range(3, limit=5)
        assert len(rows) == 2, rows
        assert rows[0][0] == r"C:\AppA"  # 体积降序
        assert rows[0][2] == 3 * (30_000_000 + 3_000_000) + 6  # 每文件 +k 共 +6
        assert rows[0][2] > rows[1][2]
        # 数量列正确：AppA 每天 2 个文件，AppB 每天 1 个
        assert rows[0][1] == 6 and rows[1][1] == 3
    finally:
        s.close()


def test_top_extensions_range(qapp, tmp_path) -> None:
    s = _storage(tmp_path)
    try:
        _seed(s)
        rows = s.top_extensions_range(3, limit=5)
        assert rows[0][0] == ".zip"
        assert rows[0][2] == 90_000_003  # 3 × 30M + 0+1+2
        assert rows[1][0] == ".txt"
        assert rows[0][2] > rows[1][2]
    finally:
        s.close()


def test_disk_space_trend(qapp, tmp_path) -> None:
    s = _storage(tmp_path)
    try:
        _seed(s)
        rows = s.disk_space_trend(3)
        assert len(rows) == 6  # 3 天 × 2 盘
        c_free = [free for day, drive, free in rows if drive == "C:"]
        d_free = [free for day, drive, free in rows if drive == "D:"]
        assert c_free == [100, 90, 80], c_free  # 按天升序
        assert d_free == [50, 51, 52], d_free
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 图表组件
# ---------------------------------------------------------------------------


def test_trend_chart_log_scale(qapp) -> None:
    """对数刻度下小值柱清晰可见：3MB 与 30MB 高度接近，线性下 3MB 近乎消失。"""
    c = TrendChart()
    c.resize(320, 78)
    c.set_days(
        [
            DaySummary("2026-01-01", 10, 3_000_000),
            DaySummary("2026-01-02", 20, 30_000_000),
        ]
    )
    assert c._log_scale
    h_log = [c._bar_height(v, 30_000_000, 40) for v in (3_000_000, 30_000_000)]
    c.set_log_scale(False)
    h_lin = [c._bar_height(v, 30_000_000, 40) for v in (3_000_000, 30_000_000)]
    # 线性：小柱贴地板（≤5px）；对数：小柱 ≥ 大柱的 80%，且明显高于线性
    assert h_lin[0] <= 5, h_lin
    assert h_log[0] >= h_log[1] * 0.8, h_log
    assert h_log[0] > h_lin[0]
    # 渲染一帧不崩（含柱顶标签）
    c.grab()


def test_trend_chart_metric_count(qapp) -> None:
    """数量模式：柱高按文件数归一化。"""
    c = TrendChart()
    c.resize(320, 78)
    c.set_days(
        [
            DaySummary("2026-01-01", 10, 50_000_000),
            DaySummary("2026-01-02", 100, 1_000_000),
        ]
    )
    c.set_metric("count")
    assert c._metric == "count"
    c.set_log_scale(False)
    h = [c._bar_height(v, 100, 40) for v in (10, 100)]
    assert h[1] == 40 and h[0] == 4  # 数量 10 vs 100（线性）
    c.grab()


def test_cumulative_chart(qapp) -> None:
    c = CumulativeChart()
    c.resize(320, 120)
    c.set_days(
        [
            DaySummary("2026-01-03", 3, 30),  # 新→旧（fetch_days_with_data 顺序）
            DaySummary("2026-01-02", 2, 20),
            DaySummary("2026-01-01", 1, 10),
        ]
    )
    assert c._data == [("2026-01-01", 10), ("2026-01-02", 30), ("2026-01-03", 60)]
    assert c._tip_text(2)  # 累计 60
    c.grab()


def test_space_trend_series(qapp) -> None:
    c = SpaceTrendChart()
    c.resize(320, 120)
    c.set_series(
        {
            "C:": [("2026-01-01", 100), ("2026-01-02", 90)],
            "D:": [("2026-01-02", 50)],
        }
    )
    assert c._days == ["2026-01-01", "2026-01-02"]
    assert c._series["C:"] == {"2026-01-01": 100, "2026-01-02": 90}
    assert "C:" in c._tip_text(1)
    c.grab()


def test_top_bars(qapp) -> None:
    c = TopBarsChart()
    c.resize(420, 120)
    c.set_items(
        [(r"C:\AppA", 3, 90_000_000), (r"C:\AppB", 1, 1_000)]
    )
    assert c.isVisible()
    assert c._tip_text(0)
    c.grab()


def test_top_bars_row_hover_hit(qapp) -> None:
    """横向条形 hover 命中：每行 y 区间都应命中（回归：旧实现 contains(4, y)
    因 x=4 永远落在行矩形左边界 6 之外，hover/tooltip 完全失效）。"""
    c = TopBarsChart()
    c.resize(420, 120)
    c.set_items(
        [(r"C:\AppA", 3, 90_000_000), (r"C:\AppB", 1, 1_000), (r"C:\AppC", 2, 500)]
    )
    for i in range(3):
        rect = c._row_rect(i)
        mid_y = rect.center().y()
        assert c._row_at(mid_y) == i, f"第 {i} 行中心应命中"
    assert c._row_at(-5) == -1
    assert c._row_at(10_000) == -1


# ---------------------------------------------------------------------------
# 窗口冒烟
# ---------------------------------------------------------------------------


def test_dashboard_smoke(qapp, tmp_path) -> None:
    s = _storage(tmp_path)
    try:
        _seed(s)
        panel = DashboardPanel(s)
        panel.show()
        panel.reload()
        # 手工派发后台线程同款打包结果
        payload = {
            "days": 14,
            "trend": s.fetch_days_with_data(14),
            "folders": s.top_folders_range(14, 10),
            "exts": s.top_extensions_range(14, 8),
            "spaces": s.disk_space_trend(14),
            "seq": s.change_seq,
        }
        panel._on_ready(panel._req, payload)
        assert len(panel._chart_growth._data) == 3  # 3 天柱
        assert panel._chart_cum.isVisible()
        assert panel._chart_folders.isVisible()
        assert panel._chart_exts.isVisible()
        assert panel._chart_space.isVisible()
        assert "9" in panel.count_label.text()  # 近 14 天：新增 9 个文件
        # 范围切换触发重载（req 递增，不崩溃）
        panel._set_range(7)
        assert panel._range == 7
        panel.close()
    finally:
        s.close()


def test_dashboard_day_selected(qapp, tmp_path) -> None:
    """点增长柱 → day_selected 信号携带该天。"""
    s = _storage(tmp_path)
    try:
        _seed(s)
        panel = DashboardPanel(s)
        picked: list[str] = []
        panel.day_selected.connect(picked.append)
        panel._chart_growth.set_days(s.fetch_days_with_data(14), 14)
        day = panel._chart_growth._data[0][0]
        panel._chart_growth.day_selected.emit(day)
        assert picked == [day], picked
        panel.close()
    finally:
        s.close()
