"""启动补扫：把「程序没在跑期间落地」的文件补进库。

事件监控只能处理到达的事件；程序没运行、电脑睡眠、队列溢出时，
文件落地是收不到事件的。补扫反过来做：不看事件，直接拿磁盘现状
和库对账——沿被监控根目录走一遍，把创建时间落在回看窗口内、
通过过滤规则的缺失文件补进库。

跑在后台线程，不阻塞启动；与实时 watcher 共用同一套 PathFilter，
过滤口径完全一致。
"""

from __future__ import annotations

import os
import time

from .config import Config
from .filters import PathFilter
from .storage import Storage, make_record

SCAN_BATCH = 500
MTIME_MARGIN = 3.0  # FAT 目录时间戳 2 秒粒度，剪枝判断留 3 秒余量


def _dir_mtime(path: str) -> float | None:
    """目录最近修改时间；stat 失败返回 None（保守：不剪枝）。"""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def scan_and_backfill(
    config: Config,
    storage: Storage,
    roots: list[str],
    lookback_days: int = 3,
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
    pfilter = PathFilter(config)
    cutoff = time.time() - max(1, lookback_days) * 86400
    added = 0
    batch: list = []

    def flush() -> None:
        nonlocal added
        if batch:
            storage.backfill_records(batch)
            added += len(batch)
            batch.clear()

    for root in roots:
        if not os.path.isdir(root):
            continue
        stack = [root]
        while stack:
            dirpath = stack.pop()
            if pfilter.excludes_dir(dirpath):
                continue
            # 目录 mtime 只反映「直接子项」的变更；子目录里的新文件不会
            # 刷新祖先目录的 mtime，所以旧目录只能跳过直接文件 stat，
            # 子目录仍必须深入（它们的 mtime 会各自判定）。
            old_dir = _dir_mtime(dirpath)
            if old_dir is not None and old_dir + MTIME_MARGIN < cutoff:
                try:
                    for ent in os.scandir(dirpath):
                        try:
                            if ent.is_dir(follow_symlinks=False):
                                stack.append(ent.path)
                        except OSError:
                            continue
                except OSError:
                    continue
                continue
            try:
                entries = list(os.scandir(dirpath))
            except OSError:
                continue
            for ent in entries:
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
                batch.append(make_record(ent.path, st.st_size, added_at=created))
                if len(batch) >= SCAN_BATCH:
                    flush()
    flush()
    return added
