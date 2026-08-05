"""磁盘剩余空间记录与查询测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from diskwatch.storage import Storage, today_str


def _storage(tmp: Path) -> Storage:
    return Storage(tmp / "test.db")


def test_record_and_query() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_disk_space_"))
    storage = _storage(tmp)
    day = today_str()
    try:
        storage.record_disk_space(
            [
                (day, "C:", 100000000000, 500000000000),
                (day, "D:", 200000000000, 1000000000000),
            ]
        )
        rows = storage.disk_space_for_day(day)
        assert len(rows) == 2, rows

        storage.record_disk_space([(day, "C:", 80000000000, 500000000000)])
        rows2 = storage.disk_space_for_day(day)
        assert len(rows2) == 2
        c_row = next(r for r in rows2 if r[0] == "C:")
        assert c_row[1] == 80000000000, c_row

        view = storage.fetch_day_view(day)
        assert "spaces" in view
        assert len(view["spaces"]) == 2
    finally:
        storage.close()


def test_clear_all() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_disk_space_"))
    storage = _storage(tmp)
    day = today_str()
    try:
        storage.record_disk_space([(day, "C:", 1, 2)])
        storage.clear_all()
        assert not storage.disk_space_for_day(day)
    finally:
        storage.close()


def test_purge_older_than() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_disk_space_"))
    storage = _storage(tmp)
    day = today_str()
    try:
        storage.record_disk_space([(day, "C:", 1, 2), ("2020-01-01", "C:", 3, 4)])
        storage.purge_older_than(30)
        assert bool(storage.disk_space_for_day(day))
        assert not storage.disk_space_for_day("2020-01-01")
    finally:
        storage.close()
