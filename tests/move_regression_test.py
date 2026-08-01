"""重命名 / 目录移动的回归测试。

覆盖两个历史 bug：
1. 临时名(被过滤，未入库) -> 正式名的重命名会让记录整体丢失
   （Storage.move_file 的 fallback 分支 + watchdog 集成场景）
2. 整目录移动导致同一文件出现新旧两条（Storage.move_subtree）

运行：
  .venv\\Scripts\\python.exe tests\\move_regression_test.py     # storage 层 + watchdog 集成
  python tests\\move_regression_test.py --storage-only          # 只跑 storage 层（无需 venv）
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diskwatch.storage import Storage, make_record

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


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


def test_storage_layer() -> None:
    print("storage 层")
    tmp = Path(tempfile.mkdtemp(prefix="dw_move_test_"))

    # Bug1a：src 未入库（临时名被过滤）-> dst 必须保留
    s = Storage(tmp / "a.db")
    s.move_file(
        r"C:\Users\niu\Downloads\report.pdf.part",
        r"C:\Users\niu\Downloads\report.pdf",
        make_record(r"C:\Users\niu\Downloads\report.pdf", 12345),
    )
    check("临时名->正式名：dst 保留", paths_of(s) == [r"C:\Users\niu\Downloads\report.pdf"])
    s.close()

    # Bug1b：src 已入库 -> 行整体移动，src 消失
    s = Storage(tmp / "b.db")
    s.add_files([make_record(r"C:\temp\a.txt", 100)])
    s.move_file(r"C:\temp\a.txt", r"C:\temp\b.txt", None)
    check("真实改名：src 消失且 dst 存在", paths_of(s) == [r"C:\temp\b.txt"])
    s.close()

    # Bug2a：目录移动，新路径已被 on_created 补发 -> 旧行是残留，清掉
    s = Storage(tmp / "c.db")
    s.add_files([make_record(r"C:\old\proj\src\main.py", 500),
                 make_record(r"C:\old\proj\src\util.py", 300)])
    s.add_files([make_record(r"C:\new\proj\src\main.py", 500),
                 make_record(r"C:\new\proj\src\util.py", 300)])
    s.move_subtree(r"C:\old\proj", r"C:\new\proj")
    check(
        "目录移动(已补发)：无重复、旧路径清除",
        paths_of(s) == [r"C:\new\proj\src\main.py", r"C:\new\proj\src\util.py"],
        str(paths_of(s)),
    )
    s.close()

    # Bug2b：目录移动，on_created 没补发 -> 旧行整体平移，不丢
    s = Storage(tmp / "d.db")
    s.add_files([make_record(r"C:\old\proj\src\main.py", 500),
                 make_record(r"C:\old\proj\src\util.py", 300)])
    s.move_subtree(r"C:\old\proj", r"C:\new\proj")
    check(
        "目录移动(未补发)：旧行平移到新路径",
        paths_of(s) == [r"C:\new\proj\src\main.py", r"C:\new\proj\src\util.py"],
        str(paths_of(s)),
    )
    s.close()


def test_watchdog_integration() -> None:
    """真实监控链路：验证重命名与目录移动的端到端行为。"""
    print("watchdog 集成")
    from diskwatch.config import Config
    from diskwatch.watcher import FileMonitor

    tmp = Path(tempfile.mkdtemp(prefix="dw_move_watch_"))
    db = tmp / "t.db"
    config = Config()
    config.set("watch_mode", "folders")
    config.set("watch_folders", [str(tmp)])
    config.set("min_size_kb", 0)
    config.set("exclude_dirs", [])   # 测试目录在系统 Temp 下，不排除
    # exclude_exts 保持默认：.part 应被扩展名过滤

    storage = Storage(db)
    monitor = FileMonitor(config, storage)
    monitor.start()
    time.sleep(1.0)
    try:
        # Bug1 集成：下载临时名(.part) -> 正式名重命名
        draft = tmp / "report_final.docx.part"
        draft.write_text("x" * 50)
        time.sleep(2.0)
        check("临时名 .part 未被记录", "report_final.docx.part" not in paths_of(storage))

        final = tmp / "report_final.docx"
        draft.rename(final)
        time.sleep(3.0)
        check("重命名后正式名已记录", final.name in [Path(p).name for p in paths_of(storage)],
              str(paths_of(storage)))

        # Bug2 集成：整目录移动
        old = tmp / "old"; (old / "proj" / "src").mkdir(parents=True)
        (old / "proj" / "src" / "main.py").write_text("x" * 100)
        (old / "proj" / "src" / "util.py").write_text("y" * 100)
        time.sleep(2.0)
        moved = tmp / "new"; moved.mkdir()
        shutil.move(str(old / "proj"), str(moved / "proj"))
        time.sleep(3.0)

        visible = visible_paths(storage)
        names = [Path(p).name for p in visible]
        flags = deleted_flags(storage)
        old_rows = [p for p in paths_of(storage) if "\\old\\proj\\" in p]
        check("目录移动后 main.py 只有一条(可见)", names.count("main.py") == 1, str(visible))
        check("目录移动后 util.py 只有一条(可见)", names.count("util.py") == 1, str(visible))
        check("旧路径已被标记删除",
              old_rows and all(flags[p] for p in old_rows), str(old_rows))
        check("新路径存在(可见)",
              any("\\new\\proj\\src\\main.py" in p for p in visible), str(visible))
    finally:
        monitor.stop()
        storage.close()


def main() -> int:
    storage_only = "--storage-only" in sys.argv
    test_storage_layer()
    if not storage_only:
        try:
            test_watchdog_integration()
        except ImportError:
            print("  (跳过 watchdog 集成：缺少 watchdog，仅验证 storage 层)")
    print("\n" + ("全部通过" if not failures else f"失败 {len(failures)} 项: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
