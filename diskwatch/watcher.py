"""文件系统监控：watchdog 抓事件 -> 队列 -> 后台线程过滤入库。

设计要点：
- 事件回调里只做最廉价的路径过滤，然后立刻塞进队列，避免拖慢 watchdog 线程。
- 后台消费线程做 stat、体积过滤、批量写库。
- 刚创建的文件常常是 0 字节，另有一个 settle 线程稍后回填真实体积。
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .filters import PathFilter, safe_stat
from .storage import FileRecord, Storage, make_record

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5

QUEUE_MAX = 20000
FLUSH_INTERVAL = 1.5
FLUSH_BATCH = 300
SETTLE_DELAY = 30.0
SETTLE_INTERVAL = 45.0


def list_drives(include_removable: bool = False) -> list[str]:
    """列出可监控的盘符根目录，如 ['C:\\\\', 'D:\\\\']。"""
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    wanted = {DRIVE_FIXED}
    if include_removable:
        wanted.add(DRIVE_REMOVABLE)

    drives: list[str] = []
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        root = f"{chr(ord('A') + i)}:\\"
        if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) in wanted:
            drives.append(root)
    return drives


class _Handler(FileSystemEventHandler):
    def __init__(self, monitor: "FileMonitor") -> None:
        self._monitor = monitor

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._monitor.submit(("add", event.src_path))

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        # 从临时名重命名成正式文件是极常见的写入模式，目标路径才是"新增"。
        self._monitor.submit(("move", event.src_path, event.dest_path))

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._monitor.submit(("del", event.src_path))


class FileMonitor:
    def __init__(self, config: Config, storage: Storage) -> None:
        self._config = config
        self._storage = storage
        self._filter = PathFilter(config)
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._observer: Observer | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        self._lock = threading.Lock()
        self._added_total = 0
        self._dropped = 0
        self._seen = 0        # watchdog 递过来的原始事件数
        self._passed = 0      # 通过路径过滤、真正进队列的数量
        self._roots: list[str] = []
        self._errors: list[str] = []

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self._stop.clear()
        self._filter.reload(self._config)
        self._roots = self._resolve_roots()
        self._errors = []

        observer = Observer()
        handler = _Handler(self)
        for root in self._roots:
            try:
                observer.schedule(handler, root, recursive=True)
            except OSError as exc:
                self._errors.append(f"{root} 监控失败: {exc}")
        observer.daemon = True
        observer.start()
        self._observer = observer

        self._threads = [
            threading.Thread(target=self._consume_loop, name="dw-consume", daemon=True),
            threading.Thread(target=self._settle_loop, name="dw-settle", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3)
            except RuntimeError:
                pass
            self._observer = None
        for t in self._threads:
            t.join(timeout=2)
        self._threads = []

    def restart(self) -> None:
        self.stop()
        self.start()

    # ---------- 状态 ----------

    @property
    def roots(self) -> list[str]:
        return list(self._roots)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def stats(self) -> tuple[int, int, int]:
        """(累计入库数, 丢弃的事件数, 当前队列长度)"""
        with self._lock:
            return self._added_total, self._dropped, self._queue.qsize()

    def event_counters(self) -> tuple[int, int]:
        """(原始事件数, 通过过滤的数量)，用于评估监控开销。"""
        with self._lock:
            return self._seen, self._passed

    def submit(self, item: tuple) -> None:
        # 这里在 watchdog 线程上执行，且全盘监控时调用极其频繁，
        # 所以只做纯字符串判断，计数用一次锁合并。
        path = item[-1]
        ok = self._filter.accepts_path(path)
        with self._lock:
            self._seen += 1
            if ok:
                self._passed += 1
        if not ok:
            return
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    # ---------- 内部 ----------

    def _resolve_roots(self) -> list[str]:
        if self._config.get("watch_mode") == "folders":
            folders = [
                f for f in self._config.get("watch_folders", []) if os.path.isdir(f)
            ]
            return folders
        excluded = {
            d.rstrip("\\/").upper() for d in self._config.get("excluded_drives", [])
        }
        drives = list_drives(bool(self._config.get("include_removable", False)))
        return [d for d in drives if d.rstrip("\\/").upper() not in excluded]

    def _consume_loop(self) -> None:
        pending: list[FileRecord] = []
        deletes: list[str] = []
        renames: list[tuple[str, str]] = []
        last_flush = time.monotonic()

        while not self._stop.is_set():
            timeout = max(0.1, FLUSH_INTERVAL - (time.monotonic() - last_flush))
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = None

            if item is not None:
                kind = item[0]
                if kind == "add":
                    rec = self._build_record(item[1])
                    if rec:
                        pending.append(rec)
                elif kind == "move":
                    src, dst = item[1], item[2]
                    rec = self._build_record(dst)
                    if rec:
                        pending.append(rec)
                    renames.append((src, dst))
                elif kind == "del":
                    deletes.append(item[1])

            due = (
                time.monotonic() - last_flush >= FLUSH_INTERVAL
                or len(pending) >= FLUSH_BATCH
                or len(deletes) >= FLUSH_BATCH
            )
            if due:
                self._flush(pending, deletes, renames)
                pending, deletes, renames = [], [], []
                last_flush = time.monotonic()

        self._flush(pending, deletes, renames)

    def _flush(
        self,
        pending: list[FileRecord],
        deletes: list[str],
        renames: list[tuple[str, str]],
    ) -> None:
        try:
            if pending:
                # 同一批里同路径可能重复，保留最后一次
                unique = {r.path: r for r in pending}
                self._storage.add_files(list(unique.values()))
                with self._lock:
                    self._added_total += len(unique)
            if deletes:
                self._storage.mark_deleted(deletes)
            for src, dst in renames:
                if src != dst:
                    self._storage.rename(src, dst)
        except Exception:
            # 后台线程里绝不能因为单批失败而退出
            pass

    def _build_record(self, path: str) -> FileRecord | None:
        st = safe_stat(path)
        if not self._filter.is_candidate(st):
            return None
        assert st is not None
        if st.st_size == 0:
            # 刚创建、还在写入的文件。先登记，settle 阶段回填真实体积并复核。
            return make_record(path, 0)
        if not self._filter.meets_size(st.st_size):
            return None
        return make_record(path, st.st_size)

    def _settle_loop(self) -> None:
        while not self._stop.wait(SETTLE_INTERVAL):
            try:
                paths = self._storage.pending_size_rows(time.time() - SETTLE_DELAY)
                if not paths:
                    continue
                sizes: dict[str, int] = {}
                missing: list[str] = []
                too_small: list[str] = []
                for p in paths:
                    st = safe_stat(p)
                    if st is None:
                        missing.append(p)
                    elif not self._filter.meets_size(st.st_size):
                        # 写完之后才知道它没达到体积门槛，直接撤回这条记录
                        too_small.append(p)
                    else:
                        sizes[p] = st.st_size
                self._storage.update_sizes(sizes, missing)
                self._storage.delete_paths(too_small)
            except Exception:
                pass


def open_in_explorer(path: str) -> None:
    """在资源管理器里定位到该文件；文件已不存在时退回打开所在目录。

    不能用 os.system：它会先弹一个 cmd 黑框，再等 explorer 返回，又闪又慢。
    用 ShellExecute 直接调 explorer，无控制台、立刻返回。
    """
    p = Path(path)
    try:
        if p.is_file() or p.is_dir():
            # /select,"完整路径" —— 逗号后带引号，路径有空格也能正确定位
            ctypes.windll.shell32.ShellExecuteW(
                None,
                "open",
                "explorer.exe",
                f'/select,"{p}"',
                None,
                1,  # SW_SHOWNORMAL
            )
        elif p.parent.exists():
            # 文件已删：至少打开它所在的目录
            os.startfile(str(p.parent))
    except OSError:
        pass
