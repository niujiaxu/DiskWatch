"""存储层补充测试：删除标记、清理、聚合查询。"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

from diskwatch.storage import Storage, make_record, today_str


def _storage(tmp: Path, name: str = "t.db") -> Storage:
    return Storage(tmp / name)


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


def test_files_for_day_keyword_and_limit() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    day = today_str()
    try:
        s.add_files(
            [
                make_record(r"C:\a\report.pdf", 1),
                make_record(r"C:\b\Report2.pdf", 1),
                make_record(r"C:\c\photo.jpg", 1),
            ]
        )
        assert len(s.files_for_day(day, "report")) == 2
        assert len(s.files_for_day(day, "REPORT")) == 2  # 大小写不敏感
        assert len(s.files_for_day(day, limit=2)) == 2
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


def test_top_folders_and_extensions() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    day = today_str()
    try:
        s.add_files(
            [
                make_record(r"C:\a\f1.jpg", 10),
                make_record(r"C:\a\f2.jpg", 20),
                make_record(r"C:\b\f3.png", 30),
            ]
        )
        folders = s.top_folders(day)
        assert folders[0][0] == r"C:\a" and folders[0][1] == 2, folders
        exts = s.top_extensions(day)
        assert exts[0][0] == ".jpg" and exts[0][1] == 2, exts
    finally:
        s.close()


def test_fetch_day_view() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    day = today_str()
    now = time.time()
    try:
        s.add_files(
            [
                make_record(r"C:\a\1.txt", 10, added_at=now - 1),
                make_record(r"C:\a\2.txt", 20, added_at=now),
            ]
        )
        s.record_disk_space([(day, "C:", 7, 8)])
        view = s.fetch_day_view(day)
        assert view["count"] == 2
        assert view["size"] == 30
        assert len(view["records"]) == 2
        assert view["records"][0].name == "2.txt"  # added_at 倒序
        assert view["spaces"] == [("C:", 7, 8)]
        assert view["folders"] and view["exts"]
    finally:
        s.close()


def test_mark_deleted_records_timestamp() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    now = time.time()
    try:
        s.add_files([make_record(r"C:\a\x.txt", 10, added_at=now - 10)])
        s.mark_deleted([r"C:\a\x.txt"])
        records = s.files_for_day(today_str(), include_deleted=True)
        assert len(records) == 1
        assert records[0].deleted
        assert records[0].deleted_at is not None
        assert records[0].deleted_at >= now - 1  # 容差
    finally:
        s.close()


def test_fetch_day_view_deleted() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_store_"))
    s = _storage(tmp)
    day = today_str()
    now = time.time()
    try:
        s.add_files(
            [
                make_record(r"C:\a\keep.txt", 10, added_at=now),
                make_record(r"C:\a\gone.txt", 20, added_at=now - 1),
            ]
        )
        s.mark_deleted([r"C:\a\gone.txt"])
        added = s.fetch_day_view(day)
        assert added["count"] == 1, added
        deleted = s.fetch_day_view(day, event_type="deleted")
        assert deleted["count"] == 1, deleted
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
