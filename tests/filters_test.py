"""路径/体积过滤规则测试。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from diskwatch.config import Config
from diskwatch.filters import PathFilter


def _filter() -> tuple[Config, PathFilter]:
    config = Config()
    config.set("exclude_dirs", ["\\appdata_like\\", "\\node_modules"])
    config.set("exclude_exts", [".tmp", ".part"])
    config.set("exclude_names", ["*.bak", "desktop.ini"])
    config.set("min_size_kb", 0)
    config.set("ignore_hidden", True)
    config.set("ignore_dot_dirs", True)
    config.set("excluded_drives", ["E:"])
    return config, PathFilter(config)


def test_accepts_regular_file() -> None:
    _, f = _filter()
    assert f.accepts_path(r"C:\Users\niu\Documents\a.txt")
    assert not f.accepts_path(r"C:\Users\niu\Documents\a.tmp")
    assert not f.accepts_path(r"C:\Users\niu\Documents\a.part")


def test_accepts_excluded_fragments() -> None:
    _, f = _filter()
    assert not f.accepts_path(r"C:\Users\niu\AppData\Local\appdata_like\x.bin")
    assert not f.accepts_path(r"D:\proj\node_modules\a.js")
    # 子串匹配语义：含 \node_modules 的目录同样被命中
    assert not f.accepts_path(r"C:\Users\niu\Documents\node_modules_x\a.js")
    assert f.accepts_path(r"C:\Users\niu\Documents\custom\a.js")


def test_accepts_excluded_names() -> None:
    _, f = _filter()
    assert not f.accepts_path(r"C:\x\backup.bak")
    assert not f.accepts_path(r"C:\x\desktop.ini")
    assert f.accepts_path(r"C:\x\desktop.ini.copy")


def test_accepts_dot_dirs() -> None:
    _, f = _filter()
    assert not f.accepts_path(r"C:\proj\.git\objects\a.bin")
    assert not f.accepts_path(r"C:\proj\.venv\Lib\a.py")
    assert f.accepts_path(r"C:\proj\.gitignore")


def test_accepts_excluded_drives() -> None:
    _, f = _filter()
    assert not f.accepts_path(r"E:\movies\a.mkv")
    assert f.accepts_path(r"D:\movies\a.mkv")


def test_excludes_dir() -> None:
    _, f = _filter()
    # 子串匹配要求路径内部有分隔符片段；directory 名正好在末尾会缺尾部 \\
    assert f.excludes_dir(r"D:\proj\node_modules\subdir")
    assert f.excludes_dir(r"C:\proj\.git")
    assert f.excludes_dir(r"E:\anything")
    assert not f.excludes_dir(r"C:\Users\niu\Documents")


def test_is_candidate() -> None:
    _, f = _filter()
    tmp = Path(tempfile.mkdtemp(prefix="dw_filter_"))
    try:
        p = tmp / "n.txt"
        p.write_text("x")
        assert f.is_candidate(os.stat(p))
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def test_meets_size() -> None:
    config, f = _filter()
    config.set("min_size_kb", 4)
    f.reload(config)
    assert not f.meets_size(100)
    assert f.meets_size(4 * 1024)
    assert f.meets_size(4097)


def test_min_size_property() -> None:
    config, f = _filter()
    config.set("min_size_kb", 2)
    f.reload(config)
    assert f.min_size == 2 * 1024
