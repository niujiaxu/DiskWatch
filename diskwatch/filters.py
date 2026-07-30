"""判断一条文件事件是否值得记录。

全盘监控会产生大量系统与软件的临时写入，绝大部分对用户毫无意义，
这里用「目录片段 + 扩展名 + 文件名通配 + 体积」四道过滤把噪音挡掉。
"""

from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path

from .config import Config

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4


class PathFilter:
    def __init__(self, config: Config) -> None:
        self.reload(config)

    def reload(self, config: Config) -> None:
        self._exclude_dirs = [d.lower() for d in config.get("exclude_dirs", [])]
        self._exclude_exts = {e.lower() for e in config.get("exclude_exts", [])}
        self._exclude_names = [n.lower() for n in config.get("exclude_names", [])]
        self._min_size = int(config.get("min_size_kb", 0)) * 1024
        self._ignore_hidden = bool(config.get("ignore_hidden", True))
        self._ignore_dot_dirs = bool(config.get("ignore_dot_dirs", True))
        self._excluded_drives = {
            d.rstrip("\\/").upper() for d in config.get("excluded_drives", [])
        }

    def accepts_path(self, path: str) -> bool:
        """只看路径本身，不碰磁盘。事件洪峰时先用它快速过滤。"""
        low = path.lower()

        drive = os.path.splitdrive(path)[0].upper()
        if drive and drive in self._excluded_drives:
            return False

        for frag in self._exclude_dirs:
            if frag in low:
                return False

        head, name = os.path.split(low)
        if not name:
            return False

        # 点号开头的目录基本都是工具自己的状态目录：.git / .venv / .idea / .cursor …
        # 这一条比逐个列举有效得多。文件名本身以点开头（.gitignore）不受影响。
        if self._ignore_dot_dirs and "\\." in head:
            return False

        ext = os.path.splitext(name)[1]
        if ext in self._exclude_exts:
            return False

        for pattern in self._exclude_names:
            if fnmatch.fnmatch(name, pattern):
                return False

        return True

    def is_candidate(self, st: os.stat_result | None) -> bool:
        """与体积无关的磁盘侧判断：必须是普通文件，且不是隐藏/系统文件。"""
        if st is None:
            return False
        if not stat.S_ISREG(st.st_mode):
            return False
        if self._ignore_hidden:
            attrs = getattr(st, "st_file_attributes", 0)
            if attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                return False
        return True

    def meets_size(self, size: int) -> bool:
        return not self._min_size or size >= self._min_size

    @property
    def min_size(self) -> int:
        return self._min_size


def safe_stat(path: str) -> os.stat_result | None:
    try:
        return Path(path).stat()
    except (OSError, ValueError):
        return None
