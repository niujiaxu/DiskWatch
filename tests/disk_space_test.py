"""测试磁盘剩余空间记录与查询。

运行： .venv\\Scripts\\python.exe tests\\disk_space_test.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diskwatch.storage import Storage, today_str

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dw_disk_space_"))
    db = tmp / "test.db"
    print(f"临时库: {db}")

    storage = Storage(db)
    day = today_str()

    # --- record ---
    print("\n1) 写入磁盘空间")
    storage.record_disk_space([
        (day, "C:", 100000000000, 500000000000),
        (day, "D:", 200000000000, 1000000000000),
    ])

    rows = storage.disk_space_for_day(day)
    check("两盘写入", len(rows) == 2, str(rows))

    # --- upsert ---
    print("\n2) 覆盖写（同天同盘）")
    storage.record_disk_space([
        (day, "C:", 80000000000, 500000000000),
    ])
    rows2 = storage.disk_space_for_day(day)
    check("仍是两条", len(rows2) == 2)
    c_row = next(r for r in rows2 if r[0] == "C:")
    check("C: 值已覆盖", c_row[1] == 80000000000, str(c_row))

    # --- fetch_day_view includes spaces ---
    print("\n3) fetch_day_view 包含 spaces")
    view = storage.fetch_day_view(day)
    check("视图含 spaces", "spaces" in view)
    check("spaces 数据正确", len(view["spaces"]) == 2)

    # --- clear ---
    print("\n4) 清空所有记录")
    storage.clear_all()
    check("清空后无记录", not storage.disk_space_for_day(day))

    # --- repopulate for purge test ---
    storage.record_disk_space([
        (day, "C:", 1, 2),
        ("2020-01-01", "C:", 3, 4),
    ])
    storage.purge_older_than(30)
    check("近期记录保留", bool(storage.disk_space_for_day(day)))
    check("过期记录清除", not storage.disk_space_for_day("2020-01-01"))

    storage.close()
    print("\n" + ("全部通过" if not failures else f"失败 {len(failures)} 项: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
