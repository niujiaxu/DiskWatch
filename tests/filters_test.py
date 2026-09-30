"""路径/体积过滤规则测试。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from diskwatch.classification import CapturePolicy, FileClassifier
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


def test_exclusions_apply_in_both_capture_modes(tmp_path) -> None:
    """排除项在两种采集模式下都生效。

    回归：以前全量模式直接放行，设置里的排除项形同虚设——VM 磁盘镜像
    （swap.vhdx / ext4.vhdx）、缓存、临时文件这类高频读写会把账本刷满，
    真正有意义的变化反而看不见。
    """
    config, _ = _filter()
    db = tmp_path / "diskwatch.db"

    for mode in ("all", "focus"):
        config.set("capture_mode", mode)
        policy = CapturePolicy(config, storage_path=db)
        # 命中排除规则的（扩展名 / 目录片段 / 点目录）一律不记录
        assert not policy.accepts_path(str(tmp_path / "cache.tmp")), mode
        assert not policy.accepts_path(r"C:\x\node_modules\a.js"), mode
        assert not policy.accepts_path(r"C:\x\.git\objects\a.bin"), mode
        # 普通文件照常记录
        assert policy.accepts_path(str(tmp_path / "report.docx")), mode
        # 自身数据库及其旁路文件永远排除
        assert not policy.accepts_path(str(db)), mode
        assert not policy.accepts_path(str(db) + "-wal"), mode


def test_file_classifier() -> None:
    classifier = FileClassifier()
    assert classifier.classify(r"C:\Users\niu\Downloads\movie.mkv") == "download"
    assert classifier.classify(r"C:\proj\node_modules\pkg\index.js") == "development"
    assert classifier.classify(r"C:\Users\niu\AppData\Local\Temp\a.tmp") == "temporary"
    assert classifier.classify(r"C:\VMs\dev.vhdx") == "vm_container"


def test_custom_category_rule_has_priority() -> None:
    config = Config()
    config.set("category_rules", {"software": [r"\CompanyTools"]})
    classifier = FileClassifier(config)
    assert classifier.classify(r"C:\Users\niu\CompanyTools\cache.tmp") == "software"
