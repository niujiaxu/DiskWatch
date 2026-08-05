"""启动补扫回归测试。

覆盖：
- 磁盘上有、库里没有的新文件 → 补进库（按创建时间落到正确天）
- 扩展名过滤、点目录剪枝、排除路径片段剪枝都生效
- 已跟踪的正常行不被覆盖（实时 watcher 的数据更准）
- 曾删除又重建的路径被复活（deleted 清 0）
- 创建时间落在回看窗口外的文件不补
- 目录 mtime 剪枝：旧目录整体跳过，新目录正常遍历
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import diskwatch.scan as scanmod
from diskwatch.config import Config
from diskwatch.scan import scan_and_backfill
from diskwatch.storage import Storage, make_record


def _config(tmp: Path) -> Config:
    """测试配置：不排除 Temp 路径本身，只保留一个自定义排除片段。"""
    config = Config()
    config.set("watch_mode", "folders")
    config.set("watch_folders", [str(tmp)])
    config.set("min_size_kb", 0)
    config.set("exclude_dirs", ["\\appdata_like\\"])
    return config


def _row(storage: Storage, path: Path) -> dict | None:
    r = storage._read.execute(
        "SELECT * FROM files WHERE path = ?", (str(path),)
    ).fetchone()
    return dict(r) if r else None


def test_backfill() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_scan_test_"))
    config = _config(tmp)
    storage = Storage(tmp / "t.db")

    tracked = tmp / "tracked.txt"
    dead = tmp / "deleted_then_back.txt"
    tracked.write_text("tracked")
    dead.write_text("back")
    storage.add_files([make_record(str(tracked), 999, added_at=1000.0)])
    storage.add_files([make_record(str(dead), 123, added_at=2000.0)])
    storage.mark_deleted([str(dead)])

    (tmp / "new_doc.txt").write_text("hello")
    (tmp / "new_big.dat").write_bytes(b"z" * 5000)
    (tmp / "ignore_me.tmp").write_text("x")
    (tmp / ".hidden_dir").mkdir()
    (tmp / ".hidden_dir" / "secret.txt").write_text("s")
    (tmp / "appdata_like").mkdir()
    (tmp / "appdata_like" / "inner.txt").write_text("i")

    scan_and_backfill(config, storage, [str(tmp)], lookback_days=3)

    assert _row(storage, tmp / "new_doc.txt") is not None
    assert _row(storage, tmp / "new_big.dat") is not None
    assert _row(storage, tmp / "ignore_me.tmp") is None
    assert _row(storage, tmp / ".hidden_dir" / "secret.txt") is None
    assert _row(storage, tmp / "appdata_like" / "inner.txt") is None

    tr = _row(storage, tracked)
    assert tr is not None and tr["size"] == 999, tr
    assert tr is not None and tr["added_at"] == 1000.0, tr

    dd = _row(storage, dead)
    assert dd is not None and dd["deleted"] == 0, dd
    assert dd is not None and dd["size"] == 123, dd
    storage.close()


def test_lookback_window(monkeypatch) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_scan_old_"))
    config = _config(tmp)
    storage = Storage(tmp / "t.db")
    (tmp / "recent.txt").write_text("r")

    real_time = scanmod.time.time
    monkeypatch.setattr(scanmod.time, "time", lambda: real_time() + 10 * 86400)
    added = scan_and_backfill(config, storage, [str(tmp)], lookback_days=3)

    assert added == 0, added
    assert _row(storage, tmp / "recent.txt") is None
    storage.close()


def test_mtime_pruning() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_scan_prune_"))
    config = _config(tmp)
    storage = Storage(tmp / "t.db")

    old = tmp / "old_dir"
    old.mkdir()
    (old / "old_file.txt").write_text("x")
    old_time = time.time() - 10 * 86400
    os.utime(old, (old_time, old_time))

    fresh = tmp / "fresh_dir"
    fresh.mkdir()
    (fresh / "new_file.txt").write_text("n")

    real_scandir = scanmod.os.scandir
    visited: list[str] = []

    def counting_scandir(path, *a, **k):
        visited.append(str(path))
        return real_scandir(path, *a, **k)

    scanmod.os.scandir = counting_scandir
    try:
        scan_and_backfill(config, storage, [str(tmp)], lookback_days=3)
    finally:
        scanmod.os.scandir = real_scandir

    assert str(old) not in visited, visited
    assert str(fresh) in visited
    assert _row(storage, old / "old_file.txt") is None
    assert _row(storage, fresh / "new_file.txt") is not None
    storage.close()


def test_mtime_pruning_nested() -> None:
    """外层目录 mtime 旧、内层新：剪枝以外层目录 mtime 为准（文档化契约）。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_scan_prune2_"))
    config = _config(tmp)
    storage = Storage(tmp / "t.db")

    outer = tmp / "outer_old"
    inner = outer / "inner_new"
    inner.mkdir(parents=True)
    (inner / "b.txt").write_text("b")
    old_time = time.time() - 10 * 86400
    os.utime(outer, (old_time, old_time))

    scan_and_backfill(config, storage, [str(tmp)], lookback_days=3)

    assert _row(storage, inner / "b.txt") is None
    storage.close()
