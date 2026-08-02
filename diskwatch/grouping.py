"""从文件路径推断「应用 / 来源」分组，用于详情树状展示。

策略（按优先级）：
1. 系统 Temp / AppData / Program Files → 专用规则（标签更可读）
2. 其余：在当天文件集合上，落到「宽泛目录之下、文件数 ≥ 2 的最浅祖先」
   （例如 Documents\\Tencent Files\\... → 组名 Tencent Files）
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from .i18n import tr
from .storage import FileRecord

# (稳定键, 展示名)
GroupInfo = tuple[str, str]

# AppData 下常见的二级目录名（不是应用名）
_GENERIC_APP_SEGMENTS = frozenset(
    {
        "cache",
        "caches",
        "cacheddata",
        "code cache",
        "gpucache",
        "temp",
        "tmp",
        "logs",
        "log",
        "crashpad",
        "userdata",
        "user data",
        "blob_storage",
        "local storage",
        "session storage",
        "indexeddb",
        "service worker",
        "packages",
    }
)

# 这些目录太宽，不能当作「应用组」根
_BROAD_NAMES = frozenset(
    {
        "users",
        "documents",
        "desktop",
        "downloads",
        "pictures",
        "videos",
        "music",
        "appdata",
        "local",
        "roaming",
        "locallow",
        "program files",
        "program files (x86)",
        "programdata",
        "windows",
        "public",
        "common files",
        "system32",
        "syswow64",
        "temp",
        "tmp",
        "home",
    }
)

# 仅当 Temp 的父目录是这些时，才视为系统临时目录
_TEMP_PARENTS = frozenset({"local", "windows", "system32"})

_MIN_GROUP_FILES = 2


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    return os.path.normpath((path or "").replace("/", "\\"))


def _is_drive_root(path: str) -> bool:
    p = _norm(path).rstrip("\\")
    return len(p) == 2 and p[1] == ":"


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("\\/"))


def _parent_dir(path: str) -> str:
    return _norm(str(Path(_norm(path)).parent))


def _dir_segments(path: str) -> list[str]:
    """文件所在目录的路径段（不含盘符、不含文件名）。"""
    segs: list[str] = []
    for part in Path(_parent_dir(path)).parts:
        if part in ("\\", "/"):
            continue
        if len(part) == 2 and part[1] == ":":
            continue
        segs.append(part)
    return segs


def _ancestor_dirs(path: str) -> list[str]:
    """从盘符下一层到文件父目录（由浅到深）。"""
    chain: list[str] = []
    cur = _parent_dir(path)
    while cur and not _is_drive_root(cur):
        chain.append(cur)
        parent = _norm(str(Path(cur).parent))
        if parent == cur:
            break
        cur = parent
    chain.reverse()
    return chain


def _is_broad_name(name: str) -> bool:
    n = name.lower()
    if n in _BROAD_NAMES:
        return True
    # OneDrive / OneDrive - Contoso 等
    return n.startswith("onedrive")


def _is_broad_prefix(prefix: str) -> bool:
    """Documents、Users\\<name> 等容器目录不能当应用组。"""
    if _is_broad_name(_basename(prefix)):
        return True
    # C:\Users\<用户名>
    return _basename(str(Path(prefix).parent)).lower() == "users"


def _parent_group(path: str) -> GroupInfo:
    """兜底：用直接父目录名。"""
    folder = _parent_dir(path)
    if not folder or _is_drive_root(folder):
        folder = _norm(path) or "(unknown)"
    label = _basename(folder) or folder
    return f"dir:{folder.lower()}", label


# ---------------------------------------------------------------------------
# 专用规则：Temp / AppData / Program Files
# ---------------------------------------------------------------------------


def _from_temp(low: list[str]) -> GroupInfo | None:
    """仅匹配系统临时目录，避免应用自建的 temp 子目录被误伤。"""
    for i, seg in enumerate(low):
        if seg not in ("temp", "tmp"):
            continue
        parent = low[i - 1] if i else ""
        if i == 0 or parent in _TEMP_PARENTS:
            return "temp", tr("临时文件")
    return None


def _from_appdata(segs: list[str], low: list[str]) -> GroupInfo | None:
    for i, seg in enumerate(low):
        if seg != "appdata" or i + 1 >= len(low):
            continue
        bucket = low[i + 1]
        if bucket not in ("local", "roaming", "locallow"):
            continue

        rest = segs[i + 2 :]
        rest_low = low[i + 2 :]
        if not rest:
            return f"appdata:{bucket}", f"AppData\\{bucket.title()}"

        if rest_low[0] == "packages" and len(rest) >= 2:
            pkg = rest[1]
            return f"appdata:{bucket}:packages:{pkg.lower()}", pkg

        if (
            len(rest) >= 2
            and rest_low[1] not in _GENERIC_APP_SEGMENTS
            and not rest[1].startswith(".")
        ):
            vendor, app = rest[0], rest[1]
            return (
                f"appdata:{bucket}:{vendor.lower()}\\{app.lower()}",
                f"{vendor} / {app}",
            )

        name = rest[0]
        return f"appdata:{bucket}:{name.lower()}", name

    return None


def _from_program_files(segs: list[str], low: list[str]) -> GroupInfo | None:
    for i, seg in enumerate(low):
        if seg not in ("program files", "program files (x86)"):
            continue
        rest = segs[i + 1 :]
        if rest:
            return f"pf:{rest[0].lower()}", rest[0]
        return "pf", "Program Files"
    return None


def _special_group(path: str) -> GroupInfo | None:
    segs = _dir_segments(path)
    low = [s.lower() for s in segs]
    return (
        _from_temp(low)
        or _from_appdata(segs, low)
        or _from_program_files(segs, low)
    )


# ---------------------------------------------------------------------------
# 自动：最浅非宽泛公共目录
# ---------------------------------------------------------------------------


def _auto_group(path: str, ancestors: list[str], counts: dict[str, int]) -> GroupInfo:
    for prefix in ancestors:
        if _is_broad_prefix(prefix):
            continue
        if counts.get(prefix, 0) >= _MIN_GROUP_FILES:
            label = _basename(prefix) or prefix
            return f"auto:{prefix.lower()}", label
    return _parent_group(path)


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def assign_groups(records: list[FileRecord]) -> dict[str, GroupInfo]:
    """为一批文件分配分组：path → (key, label)。"""
    ancestors_by_path: dict[str, list[str]] = {}
    counts: dict[str, int] = defaultdict(int)

    for rec in records:
        ancs = _ancestor_dirs(rec.path)
        ancestors_by_path[rec.path] = ancs
        for prefix in ancs:
            counts[prefix] += 1

    out: dict[str, GroupInfo] = {}
    for rec in records:
        special = _special_group(rec.path)
        if special is not None:
            out[rec.path] = special
        else:
            out[rec.path] = _auto_group(
                rec.path, ancestors_by_path[rec.path], counts
            )
    return out
