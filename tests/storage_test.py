"""存储层补充测试：删除标记、清理、聚合查询。"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

from diskwatch.storage import Storage, make_record, today_str


def _storage(tmp: Path, name: str = "t.db") -> Storage:
    return Storage(tmp / name)


def _paths(s: Storage, *, include_deleted: bool = False) -> list[str]:
    """直接查 files 表拿路径（原 files_for_day 检查器已删除）。"""
    rows = s._read.execute("SELECT path, deleted FROM files ORDER BY path").fetchall()
    return [
        str(r["path"]) for r in rows if include_deleted or not int(r["deleted"])
    ]


def test_add_files_dedup() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    try:
        rec = make_record(r"C:\a\b.txt", 100)
        s.add_files([rec])
        s.add_files([rec])
        assert s.total_count() == 1
    finally:
        s.close()


def test_mark_deleted_drive_root_does_not_wipe_drive() -> None:
    """盘符根路径标记删除时不得展开子路径匹配（误删整盘）。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    try:
        s.add_files(
            [
                make_record(r"C:\a\x.txt", 10),
                make_record(r"C:\b\y.txt", 20),
            ]
        )
        s.mark_deleted(["C:" + "\\"])  # 盘根（带尾分隔符）
        assert len(_paths(s)) == 2, "盘根删除不得标记盘内文件"
        # 精确路径仍可删除
        s.mark_deleted([r"C:\a\x.txt"])
        assert _paths(s) == [r"C:\b\y.txt"]
    finally:
        s.close()


def test_change_seq_untouched_by_noop_tx() -> None:
    """无实际写入的事务（move 无匹配等）不得推进数据版本。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    try:
        s.add_files([make_record(r"C:\a\x.txt", 10)])
        seq = s.change_seq
        # src 未入库且无 fallback → 事务内无任何 DML
        s.move_file(r"C:\nowhere\a.txt", r"C:\nowhere\b.txt", None)
        assert s.change_seq == seq, "空操作事务不应自增 change_seq"
        # 有实际写入时仍要自增
        s.add_files([make_record(r"C:\a\y.txt", 5)])
        assert s.change_seq == seq + 1
    finally:
        s.close()


def test_delete_subtree() -> None:
    """目录整体删除：按前缀物理删除子树所有行。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    try:
        s.add_files(
            [
                make_record(r"C:\a\x.txt", 10),
                make_record(r"C:\a\sub\y.txt", 20),
                make_record(r"C:\b\keep.txt", 30),
            ]
        )
        s.delete_subtree(r"C:\a" + "\\")
        assert _paths(s) == [r"C:\b\keep.txt"]
        # 盘符根只精确匹配，不得误删盘内文件
        s.delete_subtree("C:" + "\\")
        assert _paths(s) == [r"C:\b\keep.txt"]
    finally:
        s.close()


def test_mark_deleted_and_stats() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    day = today_str()
    try:
        recs = [
            make_record(r"C:\a\keep.txt", 100, added_at=time.time() - 1),
            make_record(r"C:\a\gone.txt", 50, added_at=time.time()),
        ]
        s.add_files(recs)
        s.mark_deleted([r"C:\a\gone.txt"])
        assert s.total_count() == 2  # 物理仍在
        count, total = s.day_stats(day)
        assert count == 1 and total == 100, (count, total)
    finally:
        s.close()


def test_delete_paths() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    try:
        s.add_files([make_record(r"C:\a\x.txt", 10)])
        s.delete_paths([r"C:\a\x.txt"])
        assert s.total_count() == 0
    finally:
        s.close()


def test_purge_older_than_files() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    try:
        s.add_files([make_record(r"C:\a\old.txt", 1, added_at=time.time() - 100 * 86400)])
        s.add_files([make_record(r"C:\a\new.txt", 1, added_at=time.time())])
        removed = s.purge_older_than(90)
        assert removed == 1, removed
        assert s.total_count() == 1
    finally:
        s.close()


def test_purge_older_than_deletes_every_chunk() -> None:
    """过期数据超过分块大小时必须全部删掉，不能漏尾批。

    分块删除是为了避免单个大事务长时间持有写锁（会卡住补扫的 flush）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="dw_purge_chunk_"))
    s = _storage(tmp)
    try:
        old = time.time() - 100 * 86400
        total = 4500  # 大于 4000 的分块大小，至少走两批
        s.add_files([make_record(rf"C:\a\old{i}.txt", 1, added_at=old) for i in range(total)])
        assert s.total_count() == total
        removed = s.purge_older_than(90)
        assert removed == total, removed
        assert s.total_count() == 0
    finally:
        s.close()


def test_clear_all_files_and_meta() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    try:
        s.add_files([make_record(r"C:\a\x.txt", 1)])
        s.record_disk_space([(today_str(), "C:", 1, 2)])
        s.clear_all()
        assert s.total_count() == 0
        assert not s.disk_space_for_day(today_str())
    finally:
        s.close()


def test_recent_files_order_and_limit() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    day = today_str()
    try:
        now = time.time()
        s.add_files(
            [
                make_record(r"C:\a\1.txt", 1, added_at=now - 10),
                make_record(r"C:\a\2.txt", 1, added_at=now),
            ]
        )
        recent = s.recent_files(day, limit=1)
        assert len(recent) == 1
        assert recent[0].name == "2.txt"
    finally:
        s.close()


def test_mark_deleted_records_timestamp() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    now = time.time()
    try:
        s.add_files([make_record(r"C:\a\x.txt", 10, added_at=now - 10)])
        s.mark_deleted([r"C:\a\x.txt"])
        row = s._read.execute(
            "SELECT deleted, deleted_at FROM files WHERE path = ?",
            (r"C:\a\x.txt",),
        ).fetchone()
        assert row is not None
        assert int(row["deleted"]) == 1
        assert row["deleted_at"] is not None
        assert float(row["deleted_at"]) >= now - 1  # 容差
    finally:
        s.close()


def test_resurrect_clears_deleted_at() -> None:
    """文件删除后重建（复活）：deleted 清 0，残留的 deleted_at 也必须清掉。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    now = time.time()
    try:
        s.add_files([make_record(r"C:\a\x.txt", 10, added_at=now - 10)])
        s.mark_deleted([r"C:\a\x.txt"])
        s.add_files([make_record(r"C:\a\x.txt", 20, added_at=now)])
        row = s._read.execute(
            "SELECT deleted, deleted_at, size FROM files WHERE path = ?",
            (r"C:\a\x.txt",),
        ).fetchone()
        assert row is not None
        assert int(row["deleted"]) == 0
        assert row["deleted_at"] is None, row["deleted_at"]
        assert int(row["size"]) == 20
    finally:
        s.close()


def test_schema_migration() -> None:
    """模拟老库（无 deleted_at 列），验证迁移后列存在且可正常写入。"""

    tmp = Path(tempfile.mkdtemp(prefix="dw_schema_"))
    # 手工建一个老版本 schema（无 deleted_at 列）
    old = sqlite3.connect(str(tmp / "old.db"))
    old.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    old.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path        TEXT PRIMARY KEY,
            name        TEXT,
            ext         TEXT,
            drive       TEXT,
            folder      TEXT,
            size        INTEGER DEFAULT 0,
            added_at    REAL NOT NULL,
            day         TEXT NOT NULL,
            size_final  INTEGER DEFAULT 0,
            deleted     INTEGER DEFAULT 0
        )
    """)
    old.execute("INSERT INTO meta VALUES ('schema_version', '0')")
    old.commit()
    old.close()
    # 打开 → 应自动迁移
    s = Storage(tmp / "old.db")
    try:
        s.add_files([make_record(r"C:\a\mig.txt", 1, added_at=time.time())])
        assert s.total_count() == 1
    finally:
        s.close()

def test_backfill_records_times_out_when_write_lock_busy() -> None:
    """写锁被长时间占用时，backfill_records 按 timeout 抛 TimeoutError。

    回归背景：补扫收尾如果无限等写锁，界面会一直停在“正在补扫”。
    """
    import threading

    import pytest

    tmp = Path(tempfile.mkdtemp(prefix="dw_lock_timeout_"))
    s = _storage(tmp)
    release = threading.Event()
    try:
        def hold() -> None:
            s._write_lock.acquire()
            release.wait(5)
            s._write_lock.release()

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        time.sleep(0.1)
        with pytest.raises(TimeoutError):
            s.backfill_records(
                [make_record(r"C:\a\locked.txt", 1)], timeout=0.2
            )
        release.set()
        holder.join(timeout=2)
        # 锁释放后恢复正常
        assert s.backfill_records([make_record(r"C:\a\ok.txt", 1)], timeout=1.0) == 1
    finally:
        release.set()
        s.close()

def test_compact_reclaims_space_after_clear() -> None:
    """清空数据后必须能真正回收文件空间（VACUUM + checkpoint）。

    两个回归点：
    1) SQLite 的 DELETE 只把页挂回空闲列表，不 VACUUM 的话库文件纹丝不动；
    2) WAL 模式下 VACUUM 的结果先落在 WAL，必须在 VACUUM 之后再
       checkpoint(TRUNCATE) 一次主库文件才会真正缩小。
    """
    tmp = Path(tempfile.mkdtemp(prefix="dw_compact_"))
    s = _storage(tmp)
    try:
        s.add_files(
            [make_record(rf"C:\a\f{i:05d}.bin", 1000 + i) for i in range(20000)]
        )
        before = s.database_size()
        assert before > 1_000_000, before
        s.clear_all()
        assert s.space_event_count() == 0

        after = s.compact()
        assert after < 1_000_000, after
        # 主库文件本身也要变小（清空后应只剩 schema 的几十 KB）
        assert s.path.stat().st_size < 1_000_000, s.path.stat().st_size
        conn = sqlite3.connect(str(s.path))
        try:
            assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        s.close()