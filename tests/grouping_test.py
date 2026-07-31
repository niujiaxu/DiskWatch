"""分组逻辑自测。

运行： python tests/grouping_test.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from diskwatch.grouping import assign_groups
from diskwatch.storage import FileRecord

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


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


def main() -> None:
    print("1) 自动归到 Documents 下第一层")
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
    check("Tencent Files", by.get("Tencent Files") == ["a.png", "b.zip"], str(by))
    check("WeChat Files", by.get("WeChat Files") == ["c.jpg", "d.jpg"], str(by))
    check("DingDing", by.get("DingDing") == ["e.dat", "f.dat"], str(by))
    check("散文件用父目录", by.get("Documents") == ["alone.txt"], str(by))
    check("不会落到用户名", "niu" not in by, str(by.keys()))

    print("\n2) 应用自建 temp 不被当成系统临时目录")
    by = _labels(
        [
            r"C:\Users\niu\Documents\Tencent Files\x\temp\a.bin",
            r"C:\Users\niu\Documents\Tencent Files\x\temp\b.bin",
        ]
    )
    check("仍归 Tencent Files", "Tencent Files" in by and "临时文件" not in by, str(by))

    print("\n3) 系统 Temp / AppData / Program Files")
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
    check("系统 Temp", by.get("临时文件") == ["x.tmp", "y.tmp"], str(by))
    check("Chrome", by.get("Google / Chrome") == ["f", "g"], str(by))
    check("Steam", by.get("Steam") == ["a.dll", "b.dll"], str(by))

    print("\n4) 盘符根下自定义目录")
    by = _labels(
        [
            r"D:\SomeApp\cache\a.tmp",
            r"D:\SomeApp\cache\b.tmp",
        ]
    )
    check("SomeApp", by.get("SomeApp") == ["a.tmp", "b.tmp"], str(by))

    if failures:
        print(f"\nFAILED: {len(failures)}")
        sys.exit(1)
    print("\nALL PASSED")


if __name__ == "__main__":
    main()
