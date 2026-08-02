"""配置读写与数据路径。

默认位置：%APPDATA%\\DiskWatch\\
自定义位置记在同目录的 location.json（体积很小，始终留在 AppData），
这样即使把 config / 数据库挪到别的盘，下次启动仍找得到。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from . import APP_NAME


def default_home() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def location_file() -> Path:
    return default_home() / "location.json"


def _read_location() -> dict[str, str]:
    lf = location_file()
    if not lf.exists():
        return {}
    try:
        data = json.loads(lf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_location(config_path: Path | None, db_path: Path | None) -> None:
    """写入引导文件。若两者都回到默认，则删掉 location.json。"""
    home = default_home()
    default_cfg = home / "config.json"
    default_db = home / "diskwatch.db"
    payload: dict[str, str] = {}
    if config_path is not None and config_path.resolve() != default_cfg.resolve():
        payload["config_path"] = str(config_path)
    if db_path is not None and db_path.resolve() != default_db.resolve():
        payload["db_path"] = str(db_path)

    lf = location_file()
    if not payload:
        if lf.exists():
            try:
                lf.unlink()
            except OSError:
                pass
        return
    lf.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class Paths:
    """运行时解析出的配置 / 数据库路径，可在设置里改完后 reload。"""

    def __init__(self) -> None:
        self.config: Path
        self.db: Path
        self.reload()

    def reload(self) -> None:
        home = default_home()
        loc = _read_location()
        cfg = Path(loc["config_path"]) if loc.get("config_path") else home / "config.json"
        db = Path(loc["db_path"]) if loc.get("db_path") else home / "diskwatch.db"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        db.parent.mkdir(parents=True, exist_ok=True)
        self.config = cfg
        self.db = db

    @property
    def using_custom_config(self) -> bool:
        return self.config.resolve() != (default_home() / "config.json").resolve()

    @property
    def using_custom_db(self) -> bool:
        return self.db.resolve() != (default_home() / "diskwatch.db").resolve()


paths = Paths()


# 兼容旧代码里的名字
def get_config_path() -> Path:
    return paths.config


def get_db_path() -> Path:
    return paths.db


# 许多地方写 CONFIG_PATH / DB_PATH：做成动态查找的薄封装
class _PathProxy:
    def __init__(self, getter) -> None:
        self._getter = getter

    def __fspath__(self) -> str:
        return str(self._getter())

    def __str__(self) -> str:
        return str(self._getter())

    def __repr__(self) -> str:
        return repr(self._getter())

    def __getattr__(self, name: str):
        return getattr(self._getter(), name)

    def __eq__(self, other: object) -> bool:
        return self._getter() == other

    def __truediv__(self, other):
        return self._getter() / other


CONFIG_PATH = _PathProxy(get_config_path)
DB_PATH = _PathProxy(get_db_path)


def data_dir() -> Path:
    """历史兼容：返回默认 AppData 主目录。"""
    return default_home()


def migrate_file(src: Path, dst: Path) -> None:
    """复制文件到新位置（若目标已存在则覆盖）。数据库会连同 WAL/SHM 一起搬。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if not src.exists():
        return
    shutil.copy2(src, dst)
    # SQLite WAL 旁路文件
    for suffix in ("-wal", "-shm", "-journal"):
        side = Path(str(src) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(dst) + suffix))


def apply_paths(
    config_path: Path | None,
    db_path: Path | None,
    *,
    migrate: bool = True,
    current_config: Path | None = None,
    current_db: Path | None = None,
) -> tuple[Path, Path]:
    """写入 location.json，可选把现有文件迁过去。返回最终路径。"""
    cur_cfg = Path(current_config or paths.config)
    cur_db = Path(current_db or paths.db)
    new_cfg = Path(config_path) if config_path else cur_cfg
    new_db = Path(db_path) if db_path else cur_db

    if not str(new_cfg).lower().endswith(".json"):
        new_cfg = new_cfg / "config.json" if new_cfg.suffix == "" else new_cfg
    if not str(new_db).lower().endswith(".db"):
        new_db = new_db / "diskwatch.db" if new_db.suffix == "" else new_db

    new_cfg.parent.mkdir(parents=True, exist_ok=True)
    new_db.parent.mkdir(parents=True, exist_ok=True)

    if migrate:
        if new_cfg.resolve() != cur_cfg.resolve():
            migrate_file(cur_cfg, new_cfg)
        if new_db.resolve() != cur_db.resolve():
            migrate_file(cur_db, new_db)

    _write_location(new_cfg, new_db)
    paths.reload()
    return paths.config, paths.db


def reset_paths_to_default(*, migrate: bool = True) -> tuple[Path, Path]:
    home = default_home()
    return apply_paths(
        home / "config.json",
        home / "diskwatch.db",
        migrate=migrate,
        current_config=paths.config,
        current_db=paths.db,
    )


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

    # 启动补扫：程序没在跑（或电脑睡眠）期间新增的文件一个事件都收不到，
    # 启动时按磁盘现状对一次账，把落在回看窗口内的文件补进库。
    "scan_on_startup": True,         # 启动时后台补扫
    "scan_lookback_days": 3,         # 只补回看窗口内的文件（按文件创建时间）

    # 界面
    "widget_pos": None,              # [x, y]
    "ball_pos": None,                # 迷你球的位置
    "collapsed": False,              # True = 收成迷你球
    "widget_opacity": 0.95,
    "widget_visible": True,
    "always_on_top": True,
    "start_minimized": False,
    "language": "zh_CN",             # zh_CN | en_US，重启生效
}


class Config:
    def __init__(self) -> None:
        self._data = copy.deepcopy(DEFAULTS)
        self._save_timer: threading.Timer | None = None
        self._save_lock = threading.Lock()
        self.load()

    def load(self) -> None:
        path = paths.config
        if not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
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
        """立刻落盘（设置保存 / 退出时用）。"""
        self._cancel_save_timer()
        self._write_now()

    def save_soon(self, delay: float = 0.45) -> None:
        """拖动位置等高频改动：合并写入，避免每次松手都卡 UI。"""
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            timer = threading.Timer(delay, self._write_now)
            timer.daemon = True
            self._save_timer = timer
            timer.start()

    def _cancel_save_timer(self) -> None:
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

    def _write_now(self) -> None:
        with self._save_lock:
            self._save_timer = None
        try:
            path = paths.config
            path.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再替换，避免写到一半进程退出把配置截断
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
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
