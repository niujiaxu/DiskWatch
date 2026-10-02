"""无界面自测：验证监控 -> 过滤 -> 入库 -> 查询整条链路。"""

import tempfile
import time
from pathlib import Path

from diskwatch.config import Config
from diskwatch.storage import Storage, human_size, today_str
from diskwatch.watcher import FileMonitor, list_drives


def test_enumerate_drives() -> None:
    drives = list_drives()
    assert drives, drives


def test_full_chain() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dw_smoke_"))
    db = tmp / "test.db"

    config = Config()
    config.set("watch_mode", "folders")
    config.set("watch_folders", [str(tmp)])
    config.set("min_size_kb", 0)
    config.set("exclude_dirs", [])
    config.set("exclude_exts", [".tmp"])
    config.set("exclude_names", [])
    config.set("ignore_hidden", True)
    config.set("ignore_dot_dirs", False)
    config.set("excluded_drives", [])

    storage = Storage(db)
    monitor = FileMonitor(config, storage)
    monitor.start()
    assert monitor.roots == [str(tmp)], monitor.roots
    time.sleep(1.0)
    try:
        (tmp / "report.docx").write_text("hello world", encoding="utf-8")
        (tmp / "photo.png").write_bytes(b"\x89PNG" + b"0" * 5000)
        (tmp / "ignore_me.tmp").write_text("junk", encoding="utf-8")
        sub = tmp / "sub"
        sub.mkdir()
        (sub / "nested.csv").write_text("a,b,c", encoding="utf-8")

        time.sleep(3.0)

        day = today_str()
        names = sorted(r.name for r in storage.recent_files(day, limit=100))
        assert "report.docx" in names, names
        assert "photo.png" in names, names
        assert "nested.csv" in names, names
        assert "ignore_me.tmp" not in names, names

        (tmp / "photo.png").rename(tmp / "photo_final.png")
        time.sleep(3.0)
        names2 = sorted(r.name for r in storage.recent_files(day, limit=100))
        assert "photo_final.png" in names2, names2

        pending = storage.pending_size_rows(time.time() + 1)
        sizes, missing = {}, []
        for p in pending:
            try:
                sizes[p] = Path(p).stat().st_size
            except OSError:
                missing.append(p)
        storage.update_sizes(sizes, missing)
        count, total = storage.day_stats(day)
        assert total > 0, f"{count} 个 · {human_size(total)}"
        assert count > 0

        (tmp / "report.docx").unlink()
        time.sleep(3.0)
        remaining = [r.name for r in storage.recent_files(day, limit=100)]
        assert "report.docx" not in remaining, sorted(remaining)
    finally:
        monitor.stop()
        storage.close()
