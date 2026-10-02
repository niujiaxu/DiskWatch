"""数据看板测试：聚合查询、图表组件、窗口冒烟。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QRectF

from diskwatch.storage import SpaceDaySummary, Storage, make_record
from diskwatch.ui.charts import TrendChart, _non_overlapping_labels
from diskwatch.ui.dashboard import (
    CumulativeChart,
    DashboardPanel,
    SpaceTrendChart,
    TopBarsChart,
)


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


def test_day_short_formats_day_and_timestamp() -> None:
    """轴标签：ISO 日期取 MM-DD；采样时间戳只取到秒（HH:MM:SS）。"""
    from diskwatch.ui.charts import _day_short

    assert _day_short("2026-09-29") == "09-29"
    assert _day_short("09-29 08:28:03") == "08:28:03"
    assert _day_short("09-29 08:28:03.581034") == "08:28:03"


def test_label_left_keeps_labels_inside_widget() -> None:
    """柱顶标签夹进控件内：首/末柱的数字不会被窗口边缘裁掉（如 '+26.81M'）。"""
    from diskwatch.ui.charts import _label_left

    assert _label_left(12.0, 40.0, 200.0) == 0.0     # 太靠左 → 贴左边
    assert _label_left(100.0, 40.0, 200.0) == 80.0   # 正常居中
    assert _label_left(195.0, 40.0, 200.0) == 160.0  # 太靠右 → 贴右边
    assert _label_left(10.0, 400.0, 200.0) == 0.0    # 标签比控件还宽 → 从 0 开始


def test_space_day_and_folder_summaries(qapp, tmp_path) -> None:
    s = _storage(tmp_path)
    try:
        _seed(s)
        summaries = s.space_day_summaries(3)
        assert len(summaries) == 3
        assert sum(item.event_count for item in summaries) == 9
        assert sum(item.net_bytes for item in summaries) > 0
        folders = s.top_space_folders(3, 5)
        assert folders[0][0] == r"C:\AppA"
        assert folders[0][2] > folders[1][2]
        files = s.top_space_files(3, 5)
        assert files
        assert files[0][2] >= files[-1][2] > 0
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 图表组件
# ---------------------------------------------------------------------------


def test_trend_chart_log_scale(qapp) -> None:
    """对数刻度下小值柱清晰可见：3MB 与 30MB 高度接近，线性下 3MB 近乎消失。"""
    c = TrendChart()
    c.resize(320, 78)
    c.set_space_days(
        [
            SpaceDaySummary("2026-01-01", 3_000_000, 0, 3_000_000, 10),
            SpaceDaySummary("2026-01-02", 30_000_000, 0, 30_000_000, 20),
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
    c.set_space_days(
        [
            SpaceDaySummary("2026-01-01", 50_000_000, 0, 50_000_000, 10),
            SpaceDaySummary("2026-01-02", 1_000_000, 0, 1_000_000, 100),
        ]
    )
    c.set_metric("count")
    assert c._metric == "count"
    c.set_log_scale(False)
    h = [c._bar_height(v, 100, 40) for v in (10, 100)]
    assert h[1] == 40 and h[0] == 4  # 数量 10 vs 100（线性）
    c.grab()


def test_trend_chart_signed_space_days(qapp) -> None:
    c = TrendChart()
    c.resize(320, 78)
    c.set_space_days(
        [
            SpaceDaySummary("2026-01-02", 0, 20, -20, 2),
            SpaceDaySummary("2026-01-01", 10, 0, 10, 1),
        ]
    )
    assert c._data == [("2026-01-01", 10, 1), ("2026-01-02", -20, 2)]
    assert "−" in c._tip_text(1)
    c.grab()


def test_trend_chart_spreads_sparse_bars_and_removes_label_collisions(qapp) -> None:
    c = TrendChart()
    c.resize(620, 78)
    c.set_space_days(
        [
            SpaceDaySummary(f"2026-01-{day:02d}", day * 1_000_000, 0, day * 1_000_000, day)
            for day in range(1, 6)
        ]
    )
    _bw, gap, _x0, _height = c._geometry()
    assert gap >= 40

    labels = [
        ("+1.0 GB", QRectF(0, 0, 54, 10)),
        ("+2.0 GB", QRectF(40, 0, 54, 10)),
        ("+3.0 GB", QRectF(100, 0, 54, 10)),
    ]
    visible = _non_overlapping_labels(labels)
    assert [text for text, _rect in visible] == ["+1.0 GB", "+3.0 GB"]
    assert not visible[0][1].adjusted(-2, -1, 2, 1).intersects(
        visible[1][1].adjusted(-2, -1, 2, 1)
    )
    c.grab()


def test_non_overlapping_labels_keep_fitting_neighbours() -> None:
    """排得下的相邻标签都要显示：柱顶数字不应无故整根缺一个。

    回归：标签矩形以前固定 bw+48（约 72px），而实际文字只有约 40px，
    间距 76px 的相邻柱也被判成碰撞丢弃。
    """
    labels = [(f"+{i}M", QRectF(20.0 + i * 76.0, 0.0, 46.0, 8.0)) for i in range(6)]
    visible = _non_overlapping_labels(labels)
    assert len(visible) == 6


def test_bar_label_rect_width_follows_text(qapp) -> None:
    """标签矩形宽度按文字宽度算，相邻柱距足够时两个矩形不相交。"""
    from PySide6.QtGui import QFontMetricsF

    font = qapp.font()
    font.setPointSizeF(max(6.5, font.pointSizeF() - 2.5))  # 与图表标签同字号
    metrics = QFontMetricsF(font)
    rect = TrendChart._bar_label_rect(10.0, 24.0, "+26.81M", 5.0, metrics)
    assert rect.width() >= metrics.horizontalAdvance("+26.81M") + 6.0 - 0.01
    right = TrendChart._bar_label_rect(86.0, 24.0, "+26.81M", 5.0, metrics)
    assert not rect.adjusted(-2, -1, 2, 1).intersects(right.adjusted(-2, -1, 2, 1))


def test_bar_label_rect_is_tall_enough_for_text(qapp) -> None:
    """标签矩形高度要够放下整行文字。

    回归：以前写死 8px，比字体行高矮，drawText 按矩形裁剪，柱顶数字的
    下半截正好在柱子顶边处被切掉（"+18.05G" 只剩上半截）。
    """
    from PySide6.QtGui import QFontMetricsF

    font = qapp.font()
    font.setPointSizeF(max(6.5, font.pointSizeF() - 2.5))  # 与图表标签同字号
    metrics = QFontMetricsF(font)
    rect = TrendChart._bar_label_rect(10.0, 24.0, "+18.05G", 5.0, metrics)
    assert rect.height() >= metrics.height()
    assert rect.height() > 8.0


def test_trend_chart_keeps_all_labels_for_sparse_bars(qapp) -> None:
    """常见卡片宽度下 6 根柱子的柱距足够，6 个柱顶数字都要保留。"""
    from PySide6.QtGui import QFontMetricsF

    c = TrendChart()
    c.resize(560, 78)
    c.set_space_days(
        [
            SpaceDaySummary(f"2026-01-{d:02d}", d * 1_000_000, 0, d * 1_000_000, d)
            for d in range(1, 7)
        ]
    )
    bw, gap, x0, _height = c._geometry()
    font = qapp.font()
    font.setPointSizeF(max(6.5, font.pointSizeF() - 2.5))  # 与图表标签同字号
    metrics = QFontMetricsF(font)
    rects = [
        (
            f"+{d}M",
            TrendChart._bar_label_rect(
                x0 + i * (bw + gap), bw, f"+{d}M", 0.0, metrics
            ),
        )
        for i, d in enumerate(range(1, 7))
    ]
    assert len(_non_overlapping_labels(rects)) == 6


def test_cumulative_chart(qapp) -> None:
    c = CumulativeChart()
    c.resize(320, 120)
    c.set_space_days(
        [
            SpaceDaySummary("2026-01-03", 30, 0, 30, 3),  # 新→旧（set_space_days 约定）
            SpaceDaySummary("2026-01-02", 20, 0, 20, 2),
            SpaceDaySummary("2026-01-01", 10, 0, 10, 1),
        ]
    )
    assert c._data == [("2026-01-01", 10), ("2026-01-02", 30), ("2026-01-03", 60)]
    assert c._tip_text(2)  # 累计 60
    c.grab()


def test_cumulative_chart_signed_net_change(qapp) -> None:
    c = CumulativeChart()
    c.resize(320, 120)
    c.set_space_days(
        [
            SpaceDaySummary("2026-01-03", 0, 40, -40, 1),
            SpaceDaySummary("2026-01-02", 20, 0, 20, 1),
            SpaceDaySummary("2026-01-01", 10, 0, 10, 1),
        ]
    )
    assert c._data == [("2026-01-01", 10), ("2026-01-02", 30), ("2026-01-03", -10)]
    assert "−" in c._tip_text(2)
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
            "trend": s.space_day_summaries(14),
            "folders": s.top_space_folders(14, 10),
            "files": s.top_space_files(14, 10),
            "recent_spaces": s.disk_samples(
                since=(datetime.now() - timedelta(days=1)).timestamp()
            ),
            "categories": s.category_space_totals(
                (datetime.now() - timedelta(days=14)).timestamp(),
                datetime.now().timestamp(),
            ),
            "attribution": s.daily_attribution_summary(
                (date.today() - timedelta(days=13)).isoformat(),
                date.today().isoformat(),
            ),
            "seq": s.change_seq,
        }
        panel._on_ready(panel._req, payload)
        assert len(panel._chart_growth._data) == 3  # 3 天柱
        assert panel._chart_cum.isVisible()
        assert panel._chart_folders.isVisible()
        assert panel._chart_files.isVisible()
        assert panel._chart_exts.isVisible()
        assert panel._chart_space.isVisible()
        assert "9" in panel.count_label.text()  # 近 14 天记录 9 个空间事件
        # 剩余空间轴的采样标签只到秒，不带微秒
        assert panel._chart_space._days
        for label in panel._chart_space._days:
            assert "." not in label, label
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
        panel._chart_growth.set_space_days(s.space_day_summaries(14), 14)
        day = panel._chart_growth._data[0][0]
        panel._chart_growth.day_selected.emit(day)
        assert picked == [day], picked
        panel.close()
    finally:
        s.close()


def test_dashboard_charts_scale_with_window(qapp, tmp_path) -> None:
    """窗口放大时卡片内图表应跟着放大（回归：TrendChart 曾被钉死最大高度）。"""
    from PySide6.QtWidgets import QSizePolicy

    s = _storage(tmp_path)
    try:
        _seed(s)
        small = DashboardPanel(s)
        small.resize(960, 620)
        small.show()
        large = DashboardPanel(s)
        large.resize(1720, 1040)
        large.show()
        qapp.processEvents()

        for chart in (
            small._chart_growth,
            small._chart_cum,
            small._chart_space,
            small._chart_exts,
            small._chart_files,
            small._chart_folders,
        ):
            # 不再有写死的最大高度，且双向可扩展
            assert chart.maximumHeight() >= 10_000
            policy = chart.sizePolicy()
            assert policy.horizontalPolicy() == QSizePolicy.Expanding
            assert policy.verticalPolicy() == QSizePolicy.Expanding

        # 网格行分配了拉伸，多余高度分给各行而不是留白
        grid = small._scroll_area.widget().layout()
        assert all(grid.rowStretch(r) > 0 for r in range(4))

        # 更大的窗口 → 图表更大
        assert large._chart_growth.width() > small._chart_growth.width()
        assert large._chart_files.height() > small._chart_files.height()
        small.close()
        large.close()
    finally:
        s.close()
