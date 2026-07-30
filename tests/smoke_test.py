"""无界面自测：验证监控 -> 过滤 -> 入库 -> 查询整条链路。

运行： .venv\\Scripts\\python.exe tests\\smoke_test.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diskwatch.config import Config
from diskwatch.storage import Storage, human_size, today_str
from diskwatch.watcher import FileMonitor, list_drives

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def main():
    print("1) 枚举磁盘")
    drives = list_drives()
    check("至少发现一个固定磁盘", bool(drives), str(drives))

    tmp = Path(tempfile.mkdtemp(prefix="diskwatch_test_"))
    db = tmp / "test.db"
    print(f"\n2) 临时目录 {tmp}")

    config = Config()
    config.set("watch_mode", "folders")
    config.set("watch_folders", [str(tmp)])
    config.set("min_size_kb", 0)
    config.set("exclude_dirs", [])  # 临时目录本身在 Temp 下，测试时不排除

    storage = Storage(db)
    monitor = FileMonitor(config, storage)
    monitor.start()
    check("监控已启动", monitor.roots == [str(tmp)], str(monitor.roots))
    time.sleep(1.0)

    print("\n3) 造文件")
    (tmp / "report.docx").write_text("hello world", encoding="utf-8")
    (tmp / "photo.png").write_bytes(b"\x89PNG" + b"0" * 5000)
    (tmp / "ignore_me.tmp").write_text("junk", encoding="utf-8")  # 应被扩展名过滤
    sub = tmp / "sub"
    sub.mkdir()
    (sub / "nested.csv").write_text("a,b,c", encoding="utf-8")

    time.sleep(3.0)

    day = today_str()
    records = storage.files_for_day(day)
    names = sorted(r.name for r in records)
    print(f"   入库 {len(records)} 条: {names}")

    check("捕获到 report.docx", "report.docx" in names)
    check("捕获到 photo.png", "photo.png" in names)
    check("捕获到子目录 nested.csv", "nested.csv" in names)
    check("已过滤 .tmp 文件", "ignore_me.tmp" not in names)

    print("\n4) 重命名（临时名 -> 正式名 是常见写入模式）")
    (tmp / "photo.png").rename(tmp / "photo_final.png")
    time.sleep(3.0)
    names2 = sorted(r.name for r in storage.files_for_day(day))
    check("重命名后记录为新名字", "photo_final.png" in names2, str(names2))

    print("\n5) 体积回填")
    monitor._settle_loop  # noqa: B018  (仅说明存在)
    pending = storage.pending_size_rows(time.time() + 1)
    sizes, missing = {}, []
    for p in pending:
        try:
            sizes[p] = Path(p).stat().st_size
        except OSError:
            missing.append(p)
    storage.update_sizes(sizes, missing)
    count, total = storage.day_stats(day)
    check("统计有体积", total > 0, f"{count} 个 · {human_size(total)}")

    print("\n6) 汇总查询")
    check("按天汇总可用", bool(storage.days_with_data()))
    check("热门目录可用", bool(storage.top_folders(day)))
    check("扩展名统计可用", bool(storage.top_extensions(day)))
    check("关键字搜索可用", len(storage.files_for_day(day, "nested")) == 1)

    print("\n7) 删除标记")
    (tmp / "report.docx").unlink()
    time.sleep(3.0)
    remaining = [r.name for r in storage.files_for_day(day)]
    check("已删除的文件不再计入", "report.docx" not in remaining, str(sorted(remaining)))

    monitor.stop()
    storage.close()
    print("\n" + ("全部通过" if not failures else f"失败 {len(failures)} 项: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
