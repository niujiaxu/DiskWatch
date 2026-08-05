"""重命名 / 目录移动的回归测试。

覆盖两个历史 bug：
1. 临时名(被过滤，未入库) -> 正式名的重命名会让记录整体丢失
2. 整目录移动导致同一文件出现新旧两条
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from diskwatch.storage import Storage, make_record


def paths_of(storage: Storage) -> list[str]:
    """所有行（含 deleted 标记），用于检查物理状态。"""
    rows = storage._read.execute("SELECT path FROM files ORDER BY path").fetchall()
    return [r["path"] for r in rows]


def visible_paths(storage: Storage) -> list[str]:
    """用户可见路径：deleted=0，与 UI 查询语义一致。"""
    rows = storage._read.execute(
        "SELECT path FROM files WHERE deleted = 0 ORDER BY path"
    ).fetchall()
    return [r["path"] for r in rows]


def deleted_flags(storage: Storage) -> dict[str, int]:
    rows = storage._read.execute("SELECT path, deleted FROM files").fetchall()
    return {r["path"]: int(r["deleted"]) for r in rows}


def test_move_file_from_unrecorded_src() -> None:
    """Bug1a：src 未入库（临时名被过滤）-> dst 必须保留。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_move_test_"))
    s = Storage(tmp / "a.db")
    s.move_file(
        r"C:\Users\niu\Downloads\report.pdf.part",
        r"C:\Users\niu\Downloads\report.pdf",
        make_record(r"C:\Users\niu\Downloads\report.pdf", 12345),
    )
    assert paths_of(s) == [r"C:\Users\niu\Downloads\report.pdf"]
    s.close()


def test_move_file_from_recorded_src() -> None:
    """Bug1b：src 已入库 -> 行整体移动，src 消失。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_move_test_"))
    s = Storage(tmp / "b.db")
    s.add_files([make_record(r"C:\temp\a.txt", 100)])
    s.move_file(r"C:\temp\a.txt", r"C:\temp\b.txt", None)
    assert paths_of(s) == [r"C:\temp\b.txt"]
    s.close()


def test_move_subtree_already_recreated() -> None:
    """Bug2a：目录移动，新路径已被 on_created 补发 -> 旧行是残留，清掉。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_move_test_"))
    s = Storage(tmp / "c.db")
    s.add_files(
        [
            make_record(r"C:\old\proj\src\main.py", 500),
            make_record(r"C:\old\proj\src\util.py", 300),
        ]
    )
    s.add_files(
        [
            make_record(r"C:\new\proj\src\main.py", 500),
            make_record(r"C:\new\proj\src\util.py", 300),
        ]
    )
    s.move_subtree(r"C:\old\proj", r"C:\new\proj")
    assert paths_of(s) == [r"C:\new\proj\src\main.py", r"C:\new\proj\src\util.py"], paths_of(s)
    s.close()


def test_move_subtree_not_recreated() -> None:
    """Bug2b：目录移动，on_created 没补发 -> 旧行整体平移，不丢。"""
    tmp = Path(tempfile.mkdtemp(prefix="dw_move_test_"))
    s = Storage(tmp / "d.db")
    s.add_files(
        [
            make_record(r"C:\old\proj\src\main.py", 500),
            make_record(r"C:\old\proj\src\util.py", 300),
        ]
    )
    s.move_subtree(r"C:\old\proj", r"C:\new\proj")
    assert paths_of(s) == [r"C:\new\proj\src\main.py", r"C:\new\proj\src\util.py"], paths_of(s)
    s.close()


def test_watchdog_rename_and_dir_move() -> None:
    """真实监控链路：验证重命名与目录移动的端到端行为。"""
    from diskwatch.config import Config
    from diskwatch.watcher import FileMonitor

    tmp = Path(tempfile.mkdtemp(prefix="dw_move_watch_"))
    db = tmp / "t.db"
    config = Config()
    config.set("watch_mode", "folders")
    config.set("watch_folders", [str(tmp)])
    config.set("min_size_kb", 0)
    config.set("exclude_dirs", [])

    storage = Storage(db)
    monitor = FileMonitor(config, storage)
    monitor.start()
    time.sleep(1.0)
    try:
        draft = tmp / "report_final.docx.part"
        draft.write_text("x" * 50)
        time.sleep(2.0)
        assert "report_final.docx.part" not in paths_of(storage)

        final = tmp / "report_final.docx"
        draft.rename(final)
        time.sleep(3.0)
        assert final.name in [Path(p).name for p in paths_of(storage)], paths_of(storage)

        old = tmp / "old"
        (old / "proj" / "src").mkdir(parents=True)
        (old / "proj" / "src" / "main.py").write_text("x" * 100)
        (old / "proj" / "src" / "util.py").write_text("y" * 100)
        time.sleep(2.0)
        moved = tmp / "new"
        moved.mkdir()
        shutil.move(str(old / "proj"), str(moved / "proj"))
        time.sleep(3.0)

        visible = visible_paths(storage)
        names = [Path(p).name for p in visible]
        flags = deleted_flags(storage)
        old_rows = [p for p in paths_of(storage) if "\\old\\proj\\" in p]
        assert names.count("main.py") == 1, visible
        assert names.count("util.py") == 1, visible
        assert old_rows and all(flags[p] for p in old_rows), old_rows
        assert any("\\new\\proj\\src\\main.py" in p for p in visible), visible
    finally:
        monitor.stop()
        storage.close()
