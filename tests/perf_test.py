"""定位资源开销：分别测"只跑监控"和"监控+界面"两种情况。

运行： .venv\\Scripts\\python.exe tests\\perf_test.py [采样秒数]
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diskwatch.config import Config
from diskwatch.storage import Storage
from diskwatch.watcher import FileMonitor

WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system


import ctypes
import ctypes.wintypes as wt


class _PMC(ctypes.Structure):
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


# 现代 Windows 上 psapi 的这个导出改名成了 K32GetProcessMemoryInfo，
# 直接调 psapi.GetProcessMemoryInfo 会静默失败返回 0。
_get_mem = ctypes.windll.kernel32.K32GetProcessMemoryInfo
_get_mem.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
_get_mem.restype = wt.BOOL


def rss_mb() -> float:
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    if not _get_mem(
        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb
    ):
        return -1.0
    return pmc.WorkingSetSize / (1024 * 1024)


def sample(monitor: FileMonitor, label: str, seconds: int) -> None:
    seen0, passed0 = monitor.event_counters()
    added0 = monitor.stats()[0]
    cpu0 = cpu_seconds()
    mem0 = rss_mb()
    time.sleep(seconds)
    seen1, passed1 = monitor.event_counters()
    added1 = monitor.stats()[0]
    cpu1 = cpu_seconds()
    mem1 = rss_mb()

    dseen, dpassed = seen1 - seen0, passed1 - passed0
    print(f"\n--- {label}（{seconds}s） ---")
    print(f"  原始事件      {dseen:>8,}   ({dseen / seconds:.0f}/s)")
    print(f"  通过过滤      {dpassed:>8,}   ({dpassed / seconds:.1f}/s)")
    print(f"  实际入库      {added1 - added0:>8,}")
    if dseen:
        print(f"  被过滤掉      {100 * (1 - dpassed / dseen):.1f}%")
    print(f"  CPU           {cpu1 - cpu0:>8.2f}s  ({(cpu1 - cpu0) / seconds * 100:.2f}% of one core)")
    print(f"  内存          {mem0:.1f} -> {mem1:.1f} MB  (drift {mem1 - mem0:+.1f} MB)")


def headless() -> None:
    """阶段 1/2：只跑监控，不建任何界面。"""
    config = Config()
    probe = Path(os.environ["APPDATA"]) / "DiskWatch" / "perf_probe.db"
    storage = Storage(probe)
    monitor = FileMonitor(config, storage)
    monitor.start()
    print(f"监控位置: {monitor.roots}")

    time.sleep(3)  # 跳过启动抖动
    sample(monitor, "阶段 1：纯监控（无界面）", WINDOW)
    sample(monitor, "阶段 2：纯监控，第二段（看内存是否还在涨）", WINDOW)

    monitor.stop()
    storage.close()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(probe) + suffix)
        if p.exists():
            p.unlink()


def with_ui() -> None:
    """阶段 3/4：完整应用，对比卡片显示与全部隐藏时的开销。"""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from diskwatch.app import DiskWatchApp
    from diskwatch.ui.style import apply_dark_theme

    qt = QApplication(sys.argv[:1])
    apply_dark_theme(qt)
    app = DiskWatchApp(qt)

    def spin(seconds: int) -> None:
        """让 Qt 事件循环真正跑起来，否则测不到界面的开销。"""
        loop = QEventLoop()
        QTimer.singleShot(seconds * 1000, loop.quit)
        loop.exec()

    def measure(label: str, seconds: int) -> None:
        seen0, passed0 = app.monitor.event_counters()
        cpu0, mem0 = cpu_seconds(), rss_mb()
        spin(seconds)
        seen1, passed1 = app.monitor.event_counters()
        cpu1, mem1 = cpu_seconds(), rss_mb()
        print(f"\n--- {label}（{seconds}s） ---")
        print(f"  原始事件      {seen1 - seen0:>8,}")
        print(f"  CPU           {cpu1 - cpu0:>8.2f}s  "
              f"({(cpu1 - cpu0) / seconds * 100:.2f}% of one core)")
        print(f"  内存          {mem0:.1f} -> {mem1:.1f} MB  (drift {mem1 - mem0:+.1f} MB)")

    spin(5)
    app._show_surface()
    measure("阶段 3：完整应用，卡片显示", WINDOW)

    app._toggle_widget(False)
    measure("阶段 4：完整应用，界面全隐藏（仅托盘）", WINDOW)

    app._show_surface()
    app.collapse()
    measure("阶段 5：完整应用，迷你球显示", WINDOW)

    app.config.set("collapsed", False)
    app.config.save()
    app.monitor.stop()
    app.storage.close()
    app.tray.hide()


def main() -> int:
    headless()
    with_ui()
    print("\n探针数据库已清理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
