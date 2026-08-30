"""安全的合成性能探针，不读取真实配置、不监控真实磁盘。

运行： .venv\\Scripts\\python.exe tests\\perf_probe.py --events 100000

场景：
1. 同一路径高频修改事件合并（不启动 watchdog）。
2. 生成指定数量的空间账本记录并测试计数、筛选、分页查询。
3. 在离屏 Qt 环境中构造 200 行文件活动页。

所有数据库和路径都位于 TemporaryDirectory，脚本退出后自动清理。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import ClassVar

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from diskwatch.storage import Storage
from diskwatch.watcher import FileMonitor


class _ProbeConfig:
    """只提供监控器需要的配置键，避免 Config() 读取真实用户设置。"""

    _values: ClassVar[dict[str, object]] = {
        "capture_mode": "all",
        "watch_mode": "folders",
        "watch_folders": [],
        "exclude_dirs": [],
        "exclude_exts": [],
        "exclude_names": [],
        "ignore_hidden": False,
        "min_size_kb": 0,
    }

    def get(self, key: str, default=None):
        return self._values.get(key, default)


def _rss_mb() -> float:
    """当前进程工作集；不可用时返回 -1。"""
    if os.name != "nt":
        return -1.0
    import ctypes
    import ctypes.wintypes as wt

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("PageFaultCount", wt.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    get_memory = ctypes.windll.kernel32.K32GetProcessMemoryInfo
    get_memory.argtypes = [wt.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wt.DWORD]
    get_memory.restype = wt.BOOL
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    ok = get_memory(
        ctypes.windll.kernel32.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    )
    return counters.WorkingSetSize / 1024 / 1024 if ok else -1.0


def _measure(label: str, operation):
    started = time.perf_counter()
    result = operation()
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"{label:<28} {elapsed_ms:>9.2f} ms")
    return result, elapsed_ms


def _seed_events(storage: Storage, count: int) -> float:
    sql = """
        INSERT INTO space_events(
            path, old_path, name, ext, drive, folder, category, event_type,
            old_size, new_size, delta_bytes, occurred_at, day
        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    categories = (
        "user", "download", "system", "software", "cache",
        "temporary", "development", "vm_container", "unclassified",
    )
    event_types = ("created", "modified", "deleted")
    chunk_size = 5000
    started = time.perf_counter()
    with storage._write_lock:
        for offset in range(0, count, chunk_size):
            rows = []
            for i in range(offset, min(offset + chunk_size, count)):
                drive = "C:" if i % 3 else "D:"
                category = categories[i % len(categories)]
                event_type = event_types[i % len(event_types)]
                old_size = 0 if event_type == "created" else 4096
                new_size = 0 if event_type == "deleted" else 8192
                rows.append(
                    (
                        f"{drive}\\synthetic\\{category}\\file_{i:07d}.bin",
                        f"file_{i:07d}.bin",
                        ".bin",
                        drive,
                        f"{drive}\\synthetic\\{category}",
                        category,
                        event_type,
                        old_size,
                        new_size,
                        new_size - old_size,
                        1_700_000_000.0 + i,
                        f"2023-11-{15 + (i % 14):02d}",
                    )
                )
            storage._write.executemany(sql, rows)
        storage._write.commit()
        storage._write.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return time.perf_counter() - started


def _coalescing_probe(storage: Storage, root: Path, count: int) -> None:
    monitor = FileMonitor(_ProbeConfig(), storage)  # type: ignore[arg-type]
    path = str(root / "burst.bin")
    started = time.perf_counter()
    for _ in range(count):
        monitor.submit(("modify", path))
    elapsed = time.perf_counter() - started
    diagnostics = monitor.diagnostics()
    assert diagnostics["pending_modifications"] == 1
    assert diagnostics["coalesced"] == count - 1
    print("\n事件洪峰合并")
    print(f"提交 {count:,} 次同路径修改     {elapsed * 1000:>9.2f} ms")
    print(
        f"合并 {diagnostics['coalesced']:,} 次，待处理 "
        f"{diagnostics['pending_modifications']} 条，队列 {diagnostics['queued']} 条"
    )


def _database_probe(storage: Storage, db_path: Path, count: int) -> dict[str, float]:
    print("\nSQLite 与分页 UI")
    seed_seconds = _seed_events(storage, count)
    print(f"写入 {count:,} 条合成记录       {seed_seconds * 1000:>9.2f} ms")
    _, count_ms = _measure("全量计数", storage.space_event_count)
    _, filter_ms = _measure(
        "磁盘+分类筛选计数",
        lambda: storage.space_event_count(drive="C:", category="development"),
    )
    page, page_ms = _measure(
        "读取最新 200 行",
        lambda: storage.space_events(limit=200, offset=0),
    )
    assert len(page) == min(200, count)

    from PySide6.QtWidgets import QApplication

    from diskwatch.ui.activity import ActivityPanel

    app = QApplication.instance() or QApplication(["diskwatch-perf-probe"])
    rss_before = _rss_mb()
    ui_started = time.perf_counter()
    panel = ActivityPanel(storage)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        app.processEvents()
        with panel._workers_lock:
            if not panel._workers:
                break
        time.sleep(0.005)
    app.processEvents()
    ui_ms = (time.perf_counter() - ui_started) * 1000
    print(f"构造并加载文件活动页（200 行） {ui_ms:>9.2f} ms")
    show_started = time.perf_counter()
    panel.show()
    app.processEvents()
    show_ms = (time.perf_counter() - show_started) * 1000
    print(f"显示已加载活动页（200 行）     {show_ms:>9.2f} ms")
    rss_after = _rss_mb()
    panel.close()
    panel.deleteLater()
    app.processEvents()

    size_mb = db_path.stat().st_size / 1024 / 1024
    print(f"数据库文件                  {size_mb:>9.2f} MB")
    if rss_before >= 0 and rss_after >= 0:
        print(f"活动页工作集增量            {rss_after - rss_before:>9.2f} MB")
    return {
        "seed_seconds": seed_seconds,
        "count_ms": count_ms,
        "filter_ms": filter_ms,
        "page_ms": page_ms,
        "ui_ms": ui_ms,
        "show_ms": show_ms,
        "db_mb": size_mb,
        "ui_rss_delta_mb": rss_after - rss_before,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--burst", type=int, default=100_000)
    args = parser.parse_args()
    if args.events < 1 or args.burst < 1:
        parser.error("--events 和 --burst 必须大于 0")

    with tempfile.TemporaryDirectory(prefix="diskwatch_perf_") as temp:
        root = Path(temp)
        db_path = root / "probe.db"
        storage = Storage(db_path)
        try:
            _coalescing_probe(storage, root, args.burst)
            _database_probe(storage, db_path, args.events)
        finally:
            storage.close()
    print("\n探针只使用临时目录，数据已自动清理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
