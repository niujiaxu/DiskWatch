"""启动补扫：把「程序没在跑期间落地」的文件补进库。

事件监控只能处理到达的事件；程序没运行、电脑睡眠、队列溢出时，
文件落地是收不到事件的。补扫反过来做：不看事件，直接拿磁盘现状
和库对账——沿被监控根目录走一遍，把创建时间落在回看窗口内、
通过过滤规则的缺失文件补进库。

跑在后台线程，不阻塞启动；与实时 watcher 共用同一套 CapturePolicy，
过滤口径完全一致。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

from .classification import CapturePolicy, FileClassifier
from .config import Config
from .filters import safe_stat
from .storage import FileRecord, Storage, make_record

SCAN_BATCH = 500
MTIME_MARGIN = 3.0  # FAT 目录时间戳 2 秒粒度，剪枝判断留 3 秒余量
SCAN_YIELD_EVERY = 128
SCAN_YIELD_SECONDS = 0.001


def scan_and_backfill(
    config: Config,
    storage: Storage,
    roots: list[str],
    lookback_days: int = 3,
    *,
    cancel_event: threading.Event | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """递归扫描被监控根目录，补回缺失记录。返回本次补入的条数。

    - 只处理创建时间（Windows 上 st_ctime）落在回看窗口内的文件。
    - 目录剪枝：点号目录、被排除路径片段、排除盘符直接跳过，不深入；
      目录 mtime 早于回看窗口时只跳过其直接文件（新建文件必刷父目录 mtime），
      子目录仍深入（子目录里的新文件不会刷新祖先目录的 mtime）。
    - 先做纯字符串过滤（accepts_path）命中才 stat，省去对大量
      被排除文件的 stat 开销。
    - 已入库且正常的行不会被覆盖（backfill_records 只插缺失、复活删除行）。
    """
    pfilter = CapturePolicy(config, storage_path=storage.path)
    classifier = FileClassifier(config)
    _lower_current_thread_priority()
    cutoff = time.time() - max(1, lookback_days) * 86400  # 至少回看 1 天
    added = 0
    scanned_dirs = 0
    scanned_files = 0
    reported_dirs = 0
    reported_files = 0
    visited_entries = 0
    batch: list[FileRecord] = []

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def report(path: str, *, force: bool = False) -> None:
        nonlocal reported_dirs, reported_files
        if progress is not None and (
            force
            or scanned_dirs - reported_dirs >= 32
            or scanned_files - reported_files >= 500
        ):
            progress(scanned_dirs, scanned_files, path)
            reported_dirs = scanned_dirs
            reported_files = scanned_files

    def flush() -> None:
        nonlocal added
        if batch:
            storage.backfill_records(batch)
            added += len(batch)
            batch.clear()

    def cooperate() -> None:
        """大目录遍历时定期让出 GIL，避免补扫饿死 Qt 事件循环。"""
        nonlocal visited_entries
        visited_entries += 1
        if visited_entries % SCAN_YIELD_EVERY == 0:
            time.sleep(SCAN_YIELD_SECONDS)

    for root in roots:
        if cancelled():
            break
        if not os.path.isdir(root):
            continue
        stack = [root]
        while stack:
            if cancelled():
                break
            dirpath = stack.pop()
            scanned_dirs += 1
            report(dirpath)
            if pfilter.excludes_dir(dirpath):
                continue
            # 目录 mtime 只反映「直接子项」的变更；子目录里的新文件不会
            # 刷新祖先目录的 mtime，所以旧目录只能跳过直接文件 stat，
            # 子目录仍必须深入（它们的 mtime 会各自判定）。
            mtime = safe_stat(dirpath)
            if mtime is not None and mtime.st_mtime + MTIME_MARGIN < cutoff:
                try:
                    with os.scandir(dirpath) as entries:
                        for ent in entries:
                            cooperate()
                            try:
                                if ent.is_dir(follow_symlinks=False):
                                    stack.append(ent.path)
                            except OSError:
                                continue
                except OSError:
                    continue
                continue
            try:
                entries = os.scandir(dirpath)
            except OSError:
                continue
            with entries:
                for ent in entries:
                    cooperate()
                    if cancelled():
                        break
                    try:
                        if ent.is_dir(follow_symlinks=False):
                            stack.append(ent.path)
                            continue
                        if not ent.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if not pfilter.accepts_path(ent.path):
                        continue
                    scanned_files += 1
                    report(ent.path)
                    try:
                        st = ent.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not pfilter.is_candidate(st):
                        continue
                    if not pfilter.meets_size(st.st_size):
                        continue
                    created = st.st_ctime  # Windows：文件创建时间
                    if created < cutoff:
                        continue
                    batch.append(
                        make_record(
                            ent.path,
                            st.st_size,
                            added_at=created,
                            category=classifier.classify(ent.path),
                        )
                    )
                    if len(batch) >= SCAN_BATCH:
                        flush()
    flush()
    report("", force=True)
    return added


def _lower_current_thread_priority() -> None:
    """补扫只做后台维护；Windows 上把当前线程降为低于正常优先级。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        thread = ctypes.windll.kernel32.GetCurrentThread()
        ctypes.windll.kernel32.SetThreadPriority(thread, -1)
    except (AttributeError, OSError):
        pass
