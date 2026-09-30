"""修改事件合并与最终大小入账测试。"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import diskwatch.watcher as watchermod
from diskwatch.config import Config
from diskwatch.storage import Storage
from diskwatch.watcher import FileMonitor


def test_repeated_modify_events_coalesce_to_final_size(monkeypatch) -> None:
    root = Path(tempfile.mkdtemp(prefix="dw_modify_"))
    config = Config()
    config.set("watch_mode", "folders")
    config.set("watch_folders", [str(root)])
    config.set("capture_mode", "all")
    # 测试目录在 %TEMP% 下，而默认排除项包含 \temp\：这里清空排除层，
    # 只验证"修改事件合并到最终大小"这一件事（过滤规则另有专门测试）。
    config.set("exclude_dirs", [])
    config.set("exclude_exts", [])
    config.set("exclude_names", [])
    config.set("ignore_hidden", False)
    config.set("ignore_dot_dirs", False)
    storage = Storage(root / "diskwatch.db")
    monitor = FileMonitor(config, storage)
    monkeypatch.setattr(watchermod, "MODIFY_SETTLE_DELAY", 0.15)
    monkeypatch.setattr(watchermod, "FLUSH_INTERVAL", 0.15)
    path = root / "growing.bin"
    monitor.start()
    try:
        path.write_bytes(b"a" * 10)
        monitor.submit(("add", str(path)))
        time.sleep(0.4)

        path.write_bytes(b"b" * 100)
        for _ in range(5):
            monitor.submit(("modify", str(path)))
        time.sleep(0.7)

        events = [e for e in storage.space_events(limit=20) if e.path == str(path)]
        assert sum(e.delta_bytes for e in events) == 100
        state = storage._read.execute(
            "SELECT size FROM file_state WHERE path = ?", (str(path),)
        ).fetchone()
        assert state is not None and int(state["size"]) == 100
        assert monitor.diagnostics()["coalesced"] >= 4
    finally:
        monitor.stop()
        storage.close()


def test_open_in_explorer_returns_after_process_launch(monkeypatch) -> None:
    launched: list[tuple[list[str], dict]] = []

    def fake_popen(args, **kwargs):
        launched.append((args, kwargs))
        return object()

    monkeypatch.setattr(watchermod.subprocess, "Popen", fake_popen)
    watchermod.open_in_explorer(r"C:\folder with spaces\file.txt")

    assert launched[0][0] == [
        "explorer.exe",
        r"/select,C:\folder with spaces\file.txt",
    ]
