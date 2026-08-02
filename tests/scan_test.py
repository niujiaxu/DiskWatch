"""启动补扫回归测试。

覆盖：
- 磁盘上有、库里没有的新文件 → 补进库（按创建时间落到正确天）
- 扩展名过滤、点目录剪枝、排除路径片段剪枝都生效
- 已跟踪的正常行不被覆盖（实时 watcher 的数据更准）
- 曾删除又重建的路径被复活（deleted 清 0）
- 创建时间落在回看窗口外的文件不补

运行： .venv\\Scripts\\python.exe tests\\scan_test.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import diskwatch.scan as scanmod
from diskwatch.config import Config
from diskwatch.scan import scan_and_backfill
from diskwatch.storage import Storage, make_record

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def _config(tmp: Path) -> Config:
    """测试配置：不排除 Temp 路径本身，只保留一个自定义排除片段。"""
    config = Config()
    config.set("watch_mode", "folders")
    config.set("watch_folders", [str(tmp)])
    config.set("min_size_kb", 0)
    config.set("exclude_dirs", ["\\appdata_like\\"])
    return config


def _row(storage: Storage, path: Path) -> dict | None:
    r = storage._read.execute(
        "SELECT * FROM files WHERE path = ?", (str(path),)
    ).fetchone()
    return dict(r) if r else None


def test_backfill() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_scan_test_"))
    config = _config(tmp)
    storage = Storage(tmp / "t.db")

    # 预置库：已跟踪的正常行 + 已删除后重建的行
    tracked = tmp / "tracked.txt"
    dead = tmp / "deleted_then_back.txt"
    tracked.write_text("tracked")
    dead.write_text("back")
    storage.add_files([make_record(str(tracked), 999, added_at=1000.0)])
    storage.add_files([make_record(str(dead), 123, added_at=2000.0)])
    storage.mark_deleted([str(dead)])

    # 磁盘上新落地一批文件
    (tmp / "new_doc.txt").write_text("hello")
    (tmp / "new_big.dat").write_bytes(b"z" * 5000)
    (tmp / "ignore_me.tmp").write_text("x")                     # 扩展名过滤
    (tmp / ".hidden_dir").mkdir()
    (tmp / ".hidden_dir" / "secret.txt").write_text("s")        # 点目录剪枝
    (tmp / "appdata_like").mkdir()
    (tmp / "appdata_like" / "inner.txt").write_text("i")        # 排除片段剪枝

    scan_and_backfill(config, storage, [str(tmp)], lookback_days=3)

    check("新文件补进", _row(storage, tmp / "new_doc.txt") is not None)
    check("新大文件补进", _row(storage, tmp / "new_big.dat") is not None)
    check(".tmp 扩展名被过滤", _row(storage, tmp / "ignore_me.tmp") is None)
    check("点目录剪枝", _row(storage, tmp / ".hidden_dir" / "secret.txt") is None)
    check("排除片段剪枝", _row(storage, tmp / "appdata_like" / "inner.txt") is None)

    tr = _row(storage, tracked)
    check("已跟踪行不被覆盖(体积)", tr is not None and tr["size"] == 999, str(tr))
    check("已跟踪行不被覆盖(时间)", tr is not None and tr["added_at"] == 1000.0, str(tr))

    dd = _row(storage, dead)
    check("曾删除又重建的行复活", dd is not None and dd["deleted"] == 0, str(dd))
    check("复活保留原统计", dd is not None and dd["size"] == 123, str(dd))
    storage.close()


def test_lookback_window() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_scan_old_"))
    config = _config(tmp)
    storage = Storage(tmp / "t.db")
    (tmp / "recent.txt").write_text("r")

    # 把"现在"拨到未来，让所有文件的创建时间都落在窗口之外
    real_time = scanmod.time.time
    scanmod.time.time = lambda: real_time() + 10 * 86400
    try:
        added = scan_and_backfill(config, storage, [str(tmp)], lookback_days=3)
    finally:
        scanmod.time.time = real_time

    check("回看窗口外的文件不补", added == 0, str(added))
    check("库里确实没有", _row(storage, tmp / "recent.txt") is None)
    storage.close()


def main() -> int:
    print("1) 补扫：新文件入库 + 过滤生效")
    test_backfill()
    print("2) 补扫：回看窗口")
    test_lookback_window()
    print("\n" + ("全部通过" if not failures else f"失败 {len(failures)} 项: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
