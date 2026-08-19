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
from .errorlog import errorlog
from .filters import PathFilter, safe_stat
from .i18n import tr
from .storage import FileRecord, Storage, make_record, today_str

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5

QUEUE_MAX = 20000
FLUSH_INTERVAL = 1.5
FLUSH_BATCH = 300
SETTLE_DELAY = 30.0
SETTLE_INTERVAL = 45.0
SPACE_SAMPLE_INTERVAL = 300.0  # 每 5 分钟记一次磁盘剩余空间（按天+盘符覆盖写）


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


def sample_disk_space(roots: list[str]) -> list[tuple[str, str, int, int]]:
    """采样每个监控根所在盘的剩余空间，返回 [(day, drive, free, total)]。

    free 是磁盘剩余字节数，total 是磁盘总容量；与资源管理器显示一致。
    """
    kernel32 = ctypes.windll.kernel32
    seen: set[str] = set()
    samples: list[tuple[str, str, int, int]] = []
    for root in roots:
        drive = (os.path.splitdrive(root)[0] or "").upper()
        if not drive or drive in seen:
            continue
        seen.add(drive)
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        avail = ctypes.c_ulonglong(0)
        ok = kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(drive + "\\"),
            ctypes.byref(avail),
            ctypes.byref(total),
            ctypes.byref(free),
        )
        if ok:
            samples.append((today_str(), drive, int(free.value), int(total.value)))
    return samples


class _Handler(FileSystemEventHandler):
    def __init__(self, monitor: "FileMonitor") -> None:
        self._monitor = monitor

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._monitor.submit(("add", event.src_path))

    def on_moved(self, event) -> None:
        if event.is_directory:
            # 整目录移动：旧路径行会残留、新路径又会补 on_created，
            # 必须把旧子树整体处理掉，否则同一批文件重复计数。
            self._monitor.submit(("dir_move", event.src_path, event.dest_path))
            return
        # 从临时名重命名成正式文件是极常见的写入模式，目标路径才是"新增"。
        self._monitor.submit(("move", event.src_path, event.dest_path))

    def on_deleted(self, event) -> None:
        if event.is_directory:
            # 目录整体删除：依赖每个子文件各自发 on_deleted 并不可靠
            # （网络盘、事件洪峰丢事件时只到目录级通知），直接按子树删除
            self._monitor.submit(("dir_del", event.src_path))
        else:
            self._monitor.submit(("del", event.src_path))


class FileMonitor:
    def __init__(self, config: Config, storage: Storage) -> None:
        self._config = config
        self._storage = storage
        self._filter = PathFilter(config)
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._observer: "Observer | None" = None  # type: ignore[valid-type]
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
                self._errors.append(tr("{root} 监控失败: {exc}", root=root, exc=exc))
        observer.daemon = True
        observer.start()
        self._observer = observer

        self._threads = [
            threading.Thread(target=self._consume_loop, name="dw-consume", daemon=True),
            threading.Thread(target=self._settle_loop, name="dw-settle", daemon=True),
            threading.Thread(target=self._space_loop, name="dw-space", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=1.2)
            except RuntimeError:
                pass
            self._observer = None
        # 必须等到后台线程真正退出：consume 线程在循环结束后还会执行一次
        # 最终 flush（可能因 SQLite busy 阻塞数秒），若在 storage.close() 之后
        # 才写完，最后一批写入会静默丢失。所有循环都检查 _stop，必然退出。
        for t in self._threads:
            t.join()
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
        moves: list[tuple[str, str, FileRecord | None]] = []
        dir_moves: list[tuple[str, str]] = []
        dir_dels: list[str] = []
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
                    # 不要把 dst 混进 pending 批量插入：改名是 dst 的唯一事件，
                    # src 是否入库决定该「移动旧行」还是「登记新增」，由 move_file 决定。
                    src, dst = item[1], item[2]
                    moves.append((src, dst, self._build_record(dst)))
                elif kind == "dir_move":
                    dir_moves.append((item[1], item[2]))
                elif kind == "del":
                    deletes.append(item[1])
                elif kind == "dir_del":
                    dir_dels.append(item[1])

            due = (
                time.monotonic() - last_flush >= FLUSH_INTERVAL
                or len(pending) >= FLUSH_BATCH
                or len(deletes) >= FLUSH_BATCH
                or len(moves) >= FLUSH_BATCH
                or len(dir_moves) >= FLUSH_BATCH
                or len(dir_dels) >= FLUSH_BATCH
            )
            if due:
                self._flush(pending, deletes, moves, dir_moves, dir_dels)
                pending, deletes, moves, dir_moves, dir_dels = [], [], [], [], []
                last_flush = time.monotonic()

        self._flush(pending, deletes, moves, dir_moves, dir_dels)

    def _flush(
        self,
        pending: list[FileRecord],
        deletes: list[str],
        moves: list[tuple[str, str, FileRecord | None]],
        dir_moves: list[tuple[str, str]],
        dir_dels: list[str],
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
            for src, dst, rec in moves:
                if src != dst:
                    self._storage.move_file(src, dst, rec)
            for src, dst in dir_moves:
                if src != dst:
                    self._storage.move_subtree(src, dst)
            for src in dir_dels:
                self._storage.delete_subtree(src)
        except Exception as exc:
            # 后台线程里绝不能因为单批失败而退出
            errorlog.log_exception("flush", exc)

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
            except Exception as exc:
                errorlog.log_exception("settle", exc)

    def _space_loop(self) -> None:
        """周期记录各监控盘的剩余空间；启动后立即采一次，此后每 5 分钟一次。"""
        self._sample_space()
        while not self._stop.wait(SPACE_SAMPLE_INTERVAL):
            self._sample_space()

    def _sample_space(self) -> None:
        try:
            samples = sample_disk_space(self._roots)
            if samples:
                self._storage.record_disk_space(samples)
        except Exception as exc:
            errorlog.log_exception("space", exc)


def open_in_explorer(path: str) -> None:
    """在资源管理器里定位到该文件。

    不先做 is_file()/exists()：网络盘或被锁文件上同步探测会把 UI 卡死。
    直接 ShellExecute；文件已删时资源管理器自己处理。
    """
    p = Path(path)
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "open",
            "explorer.exe",
            f'/select,"{p}"',
            None,
            1,  # SW_SHOWNORMAL
        )
    except OSError:
        try:
            if p.parent.exists():
                os.startfile(str(p.parent))
        except OSError:
            pass
