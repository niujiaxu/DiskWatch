"""配置读写：存放在 %APPDATA%\\DiskWatch\\config.json。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from . import APP_NAME


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = data_dir() / "config.json"
DB_PATH = data_dir() / "diskwatch.db"


# 过滤规则的版本号。升级默认规则时 +1，老配置会被自动刷新一次。
FILTER_VERSION = 4

# 默认排除的路径片段（小写子串匹配，命中即忽略）。
# 取向：默认只保留"你自己产生的文件"。系统目录、AppData、软件安装目录里
# 每分钟都有大量后台写入，全留下来会把真正有意义的记录冲掉。
# 想看这些位置，在设置里删掉对应行即可。
# 注意：必须用普通字符串写 "\\"，raw 字符串结尾无法表示单个反斜杠。
DEFAULT_EXCLUDE_DIRS = [
    # 系统与保留区
    "\\$recycle.bin",
    "\\system volume information",
    "\\windows\\",
    "\\windowsapps\\",
    "\\hiberfil.sys",
    "\\pagefile.sys",
    "\\swapfile.sys",
    "\\dumpstack.log",
    # 软件自己的数据目录（噪音的最大来源）
    "\\appdata\\",
    "\\programdata\\",
    "\\program files\\",
    "\\program files (x86)\\",
    # 临时与缓存（覆盖 Chrome / Edge / Electron / QQ / 微信等共同命名）
    # 注意：以点号开头的隐藏目录由「忽略隐藏目录」选项统一处理，不必在此逐条列出
    "\\temp\\",
    "\\tmp\\",
    "\\cache\\",
    "\\code cache\\",
    "\\gpucache\\",
    "\\dawncache\\",
    "\\shadercache\\",
    "\\cachestorage\\",
    "\\blob_storage\\",
    "\\service worker\\",
    "\\indexeddb\\",
    "\\local storage\\",
    "\\session storage\\",
    "\\crashpad\\",
    "\\webcache\\",
    # 开发产物
    "\\node_modules\\",
    "\\__pycache__\\",
    "\\venv\\scripts\\",
    "\\site-packages\\",
    "\\target\\classes\\",
    "\\build\\intermediates\\",
    "\\dist\\assets\\",
]

# 默认排除的扩展名（临时文件、下载中间态、数据库副本）
DEFAULT_EXCLUDE_EXTS = [
    ".tmp",
    ".temp",
    ".part",
    ".partial",
    ".crdownload",
    ".download",
    ".lock",
    ".swp",
    ".swx",
    ".old",
    ".etl",
    ".dmp",
    ".ldb",
    ".pyc",
    ".db-journal",
    ".db-wal",
    ".db-shm",
]

# 默认排除的文件名模式（前缀 / 后缀匹配用通配符）
DEFAULT_EXCLUDE_NAMES = [
    "~$*",
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
    "*.tmp.*",
]


DEFAULTS: dict[str, Any] = {
    "filter_version": FILTER_VERSION,

    # 监控范围
    "watch_mode": "drives",          # drives | folders
    "watch_folders": [],             # watch_mode = folders 时生效
    "include_removable": False,      # 是否监控 U 盘 / 移动硬盘
    "excluded_drives": [],           # 排除的盘符，如 ["D:"]

    # 过滤
    "exclude_dirs": DEFAULT_EXCLUDE_DIRS,
    "exclude_exts": DEFAULT_EXCLUDE_EXTS,
    "exclude_names": DEFAULT_EXCLUDE_NAMES,
    "min_size_kb": 0,                # 小于该大小的文件不记录（0 = 不限制）
    "ignore_hidden": True,           # 忽略隐藏 / 系统文件
    "ignore_dot_dirs": True,         # 忽略 .git / .venv / .cursor 这类点号开头的目录

    # 保留
    "retention_days": 90,            # 数据保留天数，0 = 永久

    # 界面
    "widget_pos": None,              # [x, y]
    "ball_pos": None,                # 迷你球的位置
    "collapsed": False,              # True = 收成迷你球
    "widget_opacity": 0.95,
    "widget_visible": True,
    "always_on_top": True,
    "start_minimized": False,
}


class Config:
    def __init__(self) -> None:
        self._data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not CONFIG_PATH.exists():
            return
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(saved, dict):
            return
        self._data.update(saved)
        if int(saved.get("filter_version", 0)) < FILTER_VERSION:
            self.reset_filters()
            self._data["filter_version"] = FILTER_VERSION
            self.save()

    def save(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)

    def reset_filters(self) -> None:
        for key in ("exclude_dirs", "exclude_exts", "exclude_names"):
            self._data[key] = copy.deepcopy(DEFAULTS[key])

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)
