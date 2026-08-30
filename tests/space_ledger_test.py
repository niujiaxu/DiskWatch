"""空间变化账本、完整采样与归因计算测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from diskwatch.storage import Storage, make_record


def _storage() -> Storage:
    root = Path(tempfile.mkdtemp(prefix="dw_ledger_"))
    return Storage(root / "ledger.db")


def test_create_modify_shrink_delete_ledger() -> None:
    storage = _storage()
    path = r"C:\data\growing.bin"
    try:
        storage.add_files([make_record(path, 100, added_at=100.0)])
        storage.add_files(
            [make_record(path, 150, added_at=101.0)],
            event_type="modified",
            occurred_at=101.0,
        )
        storage.add_files(
            [make_record(path, 120, added_at=102.0)],
            event_type="modified",
            occurred_at=102.0,
        )
        storage.mark_deleted([path], deleted_at=103.0)

        events = list(reversed(storage.space_events(limit=20)))
        assert [e.event_type for e in events] == [
            "created", "modified", "modified", "deleted",
        ]
        assert [e.delta_bytes for e in events] == [100, 50, -30, -120]
        state = storage._read.execute(
            "SELECT size, exists_now FROM file_state WHERE path = ?", (path,)
        ).fetchone()
        assert state is not None
        assert int(state["size"]) == 120
        assert int(state["exists_now"]) == 0
    finally:
        storage.close()


def test_move_accounting_same_and_cross_drive() -> None:
    storage = _storage()
    try:
        storage.add_files([make_record(r"C:\a\x.bin", 40, added_at=100.0)])
        storage.move_file(r"C:\a\x.bin", r"C:\b\x.bin", None)
        moved = storage.space_events(limit=1)[0]
        assert moved.event_type == "moved"
        assert moved.delta_bytes == 0

        storage.move_file(r"C:\b\x.bin", r"D:\b\x.bin", None)
        cross = storage.space_events(limit=2)
        assert {e.event_type for e in cross} == {"moved_out", "moved_in"}
        by_drive = {e.drive: e.delta_bytes for e in cross}
        assert by_drive == {"C:": -40, "D:": 40}
    finally:
        storage.close()


def test_file_history_follows_rename_chain() -> None:
    storage = _storage()
    try:
        started = time.time()
        storage.add_files(
            [make_record(r"C:\a\one.bin", 10, added_at=started - 3)]
        )
        storage.move_file(r"C:\a\one.bin", r"C:\a\two.bin", None)
        storage.move_file(r"C:\a\two.bin", r"C:\b\three.bin", None)
        modified_at = time.time() + 1
        storage.add_files(
            [make_record(r"C:\b\three.bin", 25, added_at=modified_at)],
            event_type="modified",
            occurred_at=modified_at,
        )

        history = storage.file_event_history(r"C:\b\three.bin")
        assert [event.event_type for event in reversed(history)] == [
            "created", "moved", "moved", "modified",
        ]
        assert history[0].new_size == 25
    finally:
        storage.close()


def test_disk_sample_series_and_attribution() -> None:
    storage = _storage()
    try:
        storage.record_disk_space(
            [("1970-01-01", "C:", 1000, 2000)], sampled_at=10.0
        )
        storage.add_files(
            [make_record(r"C:\data\known.bin", 100, added_at=11.0)],
            occurred_at=11.0,
        )
        storage.record_disk_space(
            [("1970-01-01", "C:", 850, 2000)], sampled_at=20.0
        )

        samples = storage.disk_samples(drive="C:")
        assert [s.free_bytes for s in samples] == [1000, 850]
        summary = storage.attribution_summary(10.0, 20.0, drive="C:")
        assert len(summary) == 1
        assert summary[0].actual_delta == 150
        assert summary[0].attributed_delta == 100
        assert summary[0].unattributed_delta == 50
    finally:
        storage.close()


def test_v1_migration_creates_backup_without_fabricating_events() -> None:
    root = Path(tempfile.mkdtemp(prefix="dw_ledger_migration_"))
    db = root / "old.db"
    old = sqlite3.connect(str(db))
    old.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    old.execute(
        """
        CREATE TABLE files (
            path TEXT PRIMARY KEY, name TEXT NOT NULL, ext TEXT, drive TEXT,
            folder TEXT, size INTEGER DEFAULT 0, added_at REAL NOT NULL,
            day TEXT NOT NULL, size_final INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0, deleted_at REAL
        )
        """
    )
    old.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    old.execute(
        "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (r"C:\a\old.txt", "old.txt", ".txt", "C:", r"C:\a", 7, 1.0,
         "1970-01-01", 1, 0, None),
    )
    old.commit()
    old.close()

    storage = Storage(db)
    try:
        assert storage.last_migration_backup is not None
        assert storage.last_migration_backup.exists()
        assert storage.space_events() == []
        row = storage._read.execute(
            "SELECT size, exists_now FROM file_state WHERE path = ?",
            (r"C:\a\old.txt",),
        ).fetchone()
        assert row is not None and int(row["size"]) == 7
    finally:
        storage.close()


def test_failed_migration_restores_original_database(monkeypatch, tmp_path) -> None:
    db = tmp_path / "rollback.db"
    old = sqlite3.connect(str(db))
    old.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    old.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    old.commit()
    old.close()

    def fail_migration(_self) -> None:
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(Storage, "_migrate_schema", fail_migration)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        Storage(db)

    restored = sqlite3.connect(str(db))
    try:
        version = restored.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in restored.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert version == "1"
        assert "space_events" not in tables
    finally:
        restored.close()
    assert list(tmp_path.glob("rollback.db.migration-v1-to-v3.bak"))


def test_space_event_paging_filters_and_category_totals() -> None:
    storage = _storage()
    try:
        storage.add_files(
            [
                make_record(r"C:\Users\me\Downloads\a.zip", 100, 100.0, "downloads"),
                make_record(r"D:\dev\build\b.bin", 250, 101.0, "development"),
                make_record(r"C:\Users\me\note.txt", 20, 102.0, "user"),
            ]
        )
        assert storage.space_event_count() == 3
        assert storage.space_event_count(drive="C:") == 2
        assert storage.space_event_count(category="development") == 1
        assert storage.space_event_count(keyword="note") == 1
        page = storage.space_events(limit=1, offset=1)
        assert len(page) == 1 and page[0].path.endswith("b.bin")
        descending = storage.space_events(sort_order="delta_desc")
        ascending = storage.space_events(sort_order="delta_asc")
        assert [event.delta_bytes for event in descending] == [250, 100, 20]
        assert [event.delta_bytes for event in ascending] == [20, 100, 250]
        drives, categories = storage.space_event_filter_values()
        assert drives == ["C:", "D:"]
        assert set(categories) == {"development", "downloads", "user"}
        totals = dict(storage.category_space_totals(99.0, 103.0))
        assert totals == {"development": 250, "downloads": 100, "user": 20}
    finally:
        storage.close()


def test_database_metrics_include_wal_and_recent_write_rate() -> None:
    storage = _storage()
    try:
        storage.add_files([make_record(r"C:\data\recent.bin", 64)])
        metrics = storage.database_metrics()
        assert int(metrics["size_bytes"]) > 0
        assert int(metrics["events_24h"]) == 1
        assert float(metrics["write_rate"]) > 0
        assert int(metrics["estimated_daily_bytes"]) > 0
    finally:
        storage.close()


def test_hourly_and_daily_summaries_survive_raw_purge() -> None:
    storage = _storage()
    try:
        storage.add_files(
            [make_record(r"C:\cache\x.bin", 100, 100.0, "app_cache")]
        )
        storage.add_files(
            [make_record(r"C:\cache\x.bin", 160, 101.0, "app_cache")],
            event_type="modified",
            occurred_at=101.0,
        )
        storage.mark_deleted([r"C:\cache\x.bin"], deleted_at=102.0)
        hourly = storage._read.execute(
            "SELECT occupied_bytes, released_bytes, net_bytes, event_count "
            "FROM space_hourly WHERE category = 'app_cache'"
        ).fetchone()
        daily = storage._read.execute(
            "SELECT occupied_bytes, released_bytes, net_bytes, event_count "
            "FROM space_daily WHERE category = 'app_cache'"
        ).fetchone()
        assert tuple(hourly) == (160, 160, 0, 3)
        assert tuple(daily) == (160, 160, 0, 3)

        storage.purge_older_than(30)
        assert storage.space_events() == []
        assert storage._read.execute("SELECT COUNT(*) c FROM space_hourly").fetchone()["c"] == 0
        kept = storage._read.execute(
            "SELECT event_count FROM space_daily WHERE category = 'app_cache'"
        ).fetchone()
        assert kept is not None and kept["event_count"] == 3
    finally:
        storage.close()


def test_daily_attribution_uses_long_term_summaries() -> None:
    storage = _storage()
    try:
        storage.record_disk_space(
            [("1970-01-01", "C:", 1000, 2000)], sampled_at=10.0
        )
        storage.add_files(
            [make_record(r"C:\data\known.bin", 100, 11.0, "user")]
        )
        storage.record_disk_space(
            [("1970-01-02", "C:", 850, 2000)], sampled_at=90_000.0
        )
        summary = storage.daily_attribution_summary("1970-01-01", "1970-01-02")
        assert len(summary) == 1
        assert summary[0].actual_delta == 150
        assert summary[0].attributed_delta == 100
        assert summary[0].unattributed_delta == 50
    finally:
        storage.close()
