"""分组逻辑自测。"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from diskwatch.grouping import assign_groups
from diskwatch.storage import FileRecord


def _rec(path: str) -> FileRecord:
    p = Path(path)
    return FileRecord(
        path=str(p),
        name=p.name,
        ext=p.suffix.lower(),
        drive=(p.drive or "C:").upper(),
        folder=str(p.parent),
        size=1,
        added_at=1.0,
    )


def _labels(paths: list[str]) -> dict[str, list[str]]:
    groups = assign_groups([_rec(p) for p in paths])
    by: dict[str, list[str]] = defaultdict(list)
    for path, (_key, label) in groups.items():
        by[label].append(Path(path).name)
    return dict(by)


def test_auto_group_under_documents() -> None:
    by = _labels(
        [
            r"C:\Users\niu\Documents\Tencent Files\2991\nt_qq\nt_data\Emoji\a.png",
            r"C:\Users\niu\Documents\Tencent Files\2991\nt_qq\nt_data\File\b.zip",
            r"C:\Users\niu\Documents\WeChat Files\wxid\FileStorage\c.jpg",
            r"C:\Users\niu\Documents\WeChat Files\wxid\FileStorage\d.jpg",
            r"C:\Users\niu\Documents\DingDing\cache\e.dat",
            r"C:\Users\niu\Documents\DingDing\cache\f.dat",
            r"C:\Users\niu\Documents\alone.txt",
        ]
    )
    assert by.get("Tencent Files") == ["a.png", "b.zip"], by
    assert by.get("WeChat Files") == ["c.jpg", "d.jpg"], by
    assert by.get("DingDing") == ["e.dat", "f.dat"], by
    assert by.get("Documents") == ["alone.txt"], by
    assert "niu" not in by, by.keys()


def test_app_temp_not_system_temp() -> None:
    by = _labels(
        [
            r"C:\Users\niu\Documents\Tencent Files\x\temp\a.bin",
            r"C:\Users\niu\Documents\Tencent Files\x\temp\b.bin",
        ]
    )
    assert "Tencent Files" in by and "临时文件" not in by, by


def test_temp_appdata_program_files() -> None:
    by = _labels(
        [
            r"C:\Users\niu\AppData\Local\Temp\x.tmp",
            r"C:\Users\niu\AppData\Local\Temp\y.tmp",
            r"C:\Users\niu\AppData\Local\Google\Chrome\User Data\Cache\f",
            r"C:\Users\niu\AppData\Local\Google\Chrome\User Data\Cache\g",
            r"C:\Program Files\Steam\steamapps\common\game\a.dll",
            r"C:\Program Files\Steam\steamapps\common\game\b.dll",
        ]
    )
    assert by.get("临时文件") == ["x.tmp", "y.tmp"], by
    assert by.get("Google / Chrome") == ["f", "g"], by
    assert by.get("Steam") == ["a.dll", "b.dll"], by


def test_drive_root_custom_dir() -> None:
    by = _labels(
        [
            r"D:\SomeApp\cache\a.tmp",
            r"D:\SomeApp\cache\b.tmp",
        ]
    )
    assert by.get("SomeApp") == ["a.tmp", "b.tmp"], by
