"""文件分类与采集策略。

普通文件会全部记录并按类别归类（不做白名单筛选），但**排除层始终生效**：
排除目录片段 / 扩展名 / 文件名 / 点目录 / 隐藏与系统文件 / 体积下限。
否则 VM 磁盘镜像、缓存、临时文件这类高频读写会把账本刷满，真正有意义
的变化反而看不见。除此之外只有安全边界（程序自身文件、设备路径、非普通
文件）会阻止采集。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from .config import Config, default_home, paths
from .filters import ExclusionFilter

CATEGORY_USER = "user"
CATEGORY_DOWNLOAD = "download"
CATEGORY_SYSTEM = "system"
CATEGORY_SOFTWARE = "software"
CATEGORY_CACHE = "cache"
CATEGORY_TEMP = "temporary"
CATEGORY_DEVELOPMENT = "development"
CATEGORY_VM = "vm_container"
CATEGORY_UNCLASSIFIED = "unclassified"


def _normalise(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).replace("/", "\\")


class FileClassifier:
    """使用轻量路径规则给文件打标签，不访问文件内容。"""

    _KNOWN_CATEGORIES: ClassVar[set[str]] = {
        CATEGORY_USER,
        CATEGORY_DOWNLOAD,
        CATEGORY_SYSTEM,
        CATEGORY_SOFTWARE,
        CATEGORY_CACHE,
        CATEGORY_TEMP,
        CATEGORY_DEVELOPMENT,
        CATEGORY_VM,
        CATEGORY_UNCLASSIFIED,
    }

    _TEMP_EXTS: ClassVar[set[str]] = {
        ".tmp", ".temp", ".part", ".partial", ".crdownload", ".download",
        ".swp", ".swx", ".lock",
    }
    _VM_EXTS: ClassVar[set[str]] = {
        ".vhd", ".vhdx", ".vmdk", ".qcow", ".qcow2", ".vdi",
    }

    def __init__(self, config: Config | None = None) -> None:
        self._custom_rules: list[tuple[str, str]] = []
        if config is not None:
            raw = config.get("category_rules", {})
            if isinstance(raw, dict):
                for category, patterns in raw.items():
                    if (
                        category not in self._KNOWN_CATEGORIES
                        or not isinstance(patterns, list)
                    ):
                        continue
                    for pattern in patterns:
                        normal = _normalise(str(pattern)).lower()
                        if normal:
                            self._custom_rules.append((category, normal))

    def classify(self, path: str) -> str:
        low = _normalise(path).lower()
        name = os.path.basename(low)
        ext = os.path.splitext(name)[1]

        for category, pattern in self._custom_rules:
            if pattern in low:
                return category

        if ext in self._VM_EXTS or any(
            token in low
            for token in ("\\.docker\\", "\\docker\\", "\\containers\\", "\\wsl\\")
        ):
            return CATEGORY_VM
        if any(
            token in low
            for token in (
                "\\node_modules\\", "\\__pycache__\\", "\\.git\\", "\\.venv\\",
                "\\target\\", "\\build\\", "\\dist\\", "\\obj\\", "\\bin\\debug\\",
                "\\bin\\release\\",
            )
        ):
            return CATEGORY_DEVELOPMENT
        if ext in self._TEMP_EXTS or any(
            token in low for token in ("\\temp\\", "\\tmp\\", "/tmp/")
        ):
            return CATEGORY_TEMP
        if any(
            token in low
            for token in (
                "\\cache\\", "\\caches\\", "\\code cache\\", "\\gpucache\\",
                "\\shadercache\\", "\\cachestorage\\", "\\webcache\\", "\\.cache\\",
            )
        ):
            return CATEGORY_CACHE
        if any(token in low for token in ("\\downloads\\", "\\download\\")):
            return CATEGORY_DOWNLOAD
        if any(
            token in low
            for token in (
                "\\windows\\", "\\system32\\", "\\system volume information\\",
                "\\$recycle.bin\\", "/system/", "/usr/", "/var/lib/", "/private/var/",
            )
        ):
            return CATEGORY_SYSTEM
        if any(
            token in low
            for token in (
                "\\program files\\", "\\program files (x86)\\", "\\windowsapps\\",
                "/applications/", "/opt/", "/usr/local/",
            )
        ):
            return CATEGORY_SOFTWARE
        if any(
            token in low
            for token in (
                "\\users\\", "\\home\\", "/users/", "/home/", "\\documents\\",
                "\\desktop\\", "\\pictures\\", "\\videos\\", "\\music\\",
            )
        ):
            return CATEGORY_USER
        return CATEGORY_UNCLASSIFIED


class CapturePolicy:
    """采集策略：安全边界（程序自身 / 设备路径 / 非普通文件）+ 排除层。

    排除层（目录片段 / 扩展名 / 文件名 / 点目录 / 隐藏与系统文件 / 体积下限）
    由 ExclusionFilter 实现，实时监控与补扫共用；用户填进设置就是为了
    "这些不要计入"，命中即不计入账本。
    """

    def __init__(
        self,
        config: Config,
        *,
        storage_path: Path | None = None,
    ) -> None:
        self._exclusions = ExclusionFilter(config)
        self._own_files: set[str] = set()
        self._own_dirs: set[str] = set()
        self.reload(config, storage_path=storage_path)

    def reload(self, config: Config, *, storage_path: Path | None = None) -> None:
        self._exclusions.reload(config)
        db_path = Path(storage_path or paths.db)
        config_path = Path(paths.config)
        home = default_home()
        self._own_files = {
            _normalise(str(db_path)),
            _normalise(str(config_path)),
            _normalise(str(home / "location.json")),
            _normalise(str(home / "diskwatch.log")),
        }
        self._own_dirs = {_normalise(str(home))}

    def _is_own_path(self, path: str) -> bool:
        normal = _normalise(path)
        if normal in self._own_files:
            return True
        # SQLite 旁路文件和滚动日志：db-wal/db-shm、log.1 等。
        if any(normal.startswith(base + "-") for base in self._own_files):
            return True
        return any(normal.startswith(base + ".") for base in self._own_files)

    def accepts_path(self, path: str) -> bool:
        if not path or "\x00" in path:
            return False
        low = path.lower()
        if low.startswith(("\\\\.\\", "\\\\?\\globalroot", "\\\\?\\pipe\\")):
            return False
        if self._is_own_path(path):
            return False
        return self._exclusions.accepts_path(path)

    def excludes_dir(self, path: str) -> bool:
        normal = _normalise(path)
        if normal in self._own_dirs:
            return True
        return self._exclusions.excludes_dir(path)

    def is_candidate(self, st: os.stat_result | None) -> bool:
        # 体积无关的磁盘侧判断（普通文件 + 隐藏/系统文件开关）
        return self._exclusions.is_candidate(st)

    def meets_size(self, size: int) -> bool:
        return self._exclusions.meets_size(size)
