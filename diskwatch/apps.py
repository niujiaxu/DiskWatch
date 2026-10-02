"""按应用分组：从文件路径推断归属应用，并把筛选结果聚合成均衡分组。

设计（2026-09 方案，产品级合并 + 厂商命名兜底）：
- 归属根：AppData / Program Files / "<X> Files" / 点目录 / Temp 等锚点优先；
  没有锚点时从父目录向上跳过通用名（log/cache/data/账号号/版本号），
  取第一层"像应用名"的目录。
- 产品级：合并键为 厂商.产品（QQ 在 Documents 的日志与 Roaming 的数据
  归为同一组）；命名优先产品名，产品未知兜底厂商名，再兜底目录名。
- 均衡：单组超过总条数 25%（且至少 20 条）时按归属根下一级（账号/子产品）
  拆分；少于 3 条且占比 <1% 的小组并入「其它位置」；总组数上限 24。

纯函数模块：不依赖 Qt / Storage，可在写入、迁移与查询时复用。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

MAX_GROUP_SHARE = 0.25   # 单组最多占总事件比例
MAX_GROUP_EVENTS = 300   # 单组条数上限
MIN_SPLIT_EVENTS = 20    # 低于这个量级不拆组（小数据量拆了反而碎）
MIN_GROUP_EVENTS = 3     # 小组并入「其它位置」的条数阈值
MIN_GROUP_SHARE = 0.01   # 且占比低于 1% 才合并
MIN_GROUP_NET = 16 * 1024 * 1024  # 净变化超过这个量级的长尾组必须单独可见
MAX_GROUPS = 24          # 总组数上限，超出时把最小的并进「其它位置」

OTHER_KEY = "special.other"
OTHER_LABEL = "其它位置"

# 产品别名 → (归一化合并键, 展示名)。产品级合并的唯一依据。
_PRODUCTS: dict[str, tuple[str, str]] = {
    # 腾讯系
    "qq": ("tencent.qq", "QQ"),
    "qqnt": ("tencent.qq", "QQ"),
    "nt_qq": ("tencent.qq", "QQ"),
    "wechat": ("tencent.wechat", "微信"),
    "weixin": ("tencent.wechat", "微信"),
    "xwechat": ("tencent.wechat", "微信"),
    "wxwork": ("tencent.wxwork", "企业微信"),
    "tim": ("tencent.tim", "TIM"),
    "wegame": ("tencent.wegame", "WeGame"),
    "qqmusic": ("tencent.qqmusic", "QQ音乐"),
    "tencentdocs": ("tencent.docs", "腾讯文档"),
    # 常用软件（目录名即产品）
    "zotero": ("zotero", "Zotero"),
    "code": ("vscode", "VS Code"),
    "vscode": ("vscode", "VS Code"),
    "cursor": ("cursor", "Cursor"),
    "codex": ("codex", "Codex"),
    "chrome": ("chrome", "Chrome"),
    "edge": ("edge", "Edge"),
    "firefox": ("firefox", "Firefox"),
    "obsidian": ("obsidian", "Obsidian"),
    "typora": ("typora", "Typora"),
    "notion": ("notion", "Notion"),
    "discord": ("discord", "Discord"),
    "telegram": ("telegram", "Telegram"),
    "slack": ("slack", "Slack"),
    "zoom": ("zoom", "Zoom"),
    "docker": ("docker", "Docker"),
    "wsl": ("wsl", "WSL"),
    "steam": ("steam", "Steam"),
    "adobe": ("adobe", "Adobe"),
    "npm": ("npm", "npm"),
    "npm-cache": ("npm", "npm"),
    "pip": ("pip", "pip"),
    "python": ("python", "Python"),
    "nodejs": ("nodejs", "Node.js"),
    "gameviewer": ("netease.gameviewer", "GameViewer"),
    "yuque": ("yuque", "语雀"),
    "mysql": ("mysql", "MySQL"),
    "dingtalk": ("dingtalk", "钉钉"),
    "dingding": ("dingtalk", "钉钉"),
    "alipay": ("alipay", "支付宝"),
    "baidunetdisk": ("baidunetdisk", "百度网盘"),
    "thunder": ("thunder", "迅雷"),
    "xunlei": ("thunder", "迅雷"),
    "wps": ("wps", "WPS"),
    "kingsoft": ("wps", "WPS"),
    "cloudmusic": ("netease.cloudmusic", "网易云音乐"),
    "douyin": ("douyin", "抖音"),
    "awesun": ("oray.awesun", "向日葵"),
    "huorong": ("huorong", "火绒"),
    "sogou": ("sogou", "搜狗"),
}

# 厂商别名 → (归一化键, 展示名)。产品未知时兜底到厂商。
_VENDORS: dict[str, tuple[str, str]] = {
    "tencent": ("tencent", "腾讯"),
    "microsoft": ("microsoft", "微软"),
    "google": ("google", "Google"),
    "mozilla": ("mozilla", "Mozilla"),
    "alibaba": ("alibaba", "阿里"),
    "baidu": ("baidu", "百度"),
    "netease": ("netease", "网易"),
    "bytedance": ("bytedance", "字节"),
    "kingsoft": ("kingsoft", "金山"),
    "jetbrains": ("jetbrains", "JetBrains"),
    "adobe": ("adobe", "Adobe"),
    "oracle": ("oracle", "Oracle"),
    "nvidia": ("nvidia", "NVIDIA"),
    "intel": ("intel", "Intel"),
    "valve": ("valve", "Valve"),
    "github": ("github", "GitHub"),
    "openai": ("openai", "OpenAI"),
    "oray": ("oray", "向日葵"),
    "360": ("360", "360"),
}

_SPECIAL_LABELS: dict[str, str] = {
    "special.temp": "临时文件",
    "special.downloads": "下载",
    "special.desktop": "桌面",
    "special.system": "系统与更新",
    "special.programdata": "ProgramData",
    "special.programs": "程序文件",
    "special.loose": "零散文件",
    OTHER_KEY: OTHER_LABEL,
}

_KEY_LABELS: dict[str, str] = {}
for _canonical, _label in _PRODUCTS.values():
    _KEY_LABELS.setdefault(_canonical, _label)
for _canonical, _label in _VENDORS.values():
    _KEY_LABELS.setdefault(_canonical, _label)
_KEY_LABELS.update(_SPECIAL_LABELS)

# 通用目录名：向上找归属根时跳过（不承载"这是哪个应用"的信息）
_GENERIC_DIRS = {
    "appdata", "local", "locallow", "roaming", "programdata", "windows",
    "users", "user", "default", "public", "documents", "docs", "desktop",
    "downloads", "download", "music", "pictures", "videos", "onedrive",
    "data", "cache", "caches", "cache2", "log", "logs", "temp", "tmp",
    "config", "configs", "conf", "settings", "storage", "bin", "lib", "libs",
    "resources", "assets", "update", "updates", "session", "sessions", "db",
    "database", "index", "indexes", "output", "out", "build", "dist",
    "packages", "modules", "plugins", "addons", "extensions", "backup",
    "backups", "crashes", "dumps", "profiles", "profile", "shared", "common",
    "node_modules", "__pycache__", "file", "files",
    # 开发工程常见的中间层目录
    "app", "apps", "ui", "tests", "test", "src", "scripts", "tools", "utils",
    "core", "api", "server", "client", "web", "static", "pages", "components",
    "views", "models", "services", "widgets", "dialogs", "handlers",
    "controllers", "workspace", "workspaces", "work", "global",
    # 聊天/媒体类应用的内部目录（往上找应用名）
    "message", "messages", "msg", "img", "image", "images", "pic", "pics",
    "photo", "photos", "video", "media", "attach", "attachment",
    "attachments", "thumb", "thumbs", "thumbnail", "thumbnails", "emoji",
    "voice", "audio", "sticker", "stickerstore", "db_storage",
}

_ACCOUNT_RE = re.compile(r"\d{3,}")                     # 纯数字（账号/序号）
_VERSION_RE = re.compile(r"v?\d+(\.\d+)+")              # 1.2.3 / v2.0
_DATE_RE = re.compile(r"\d{4}-\d{2}(-\d{2})?")          # 2026-09 / 2026-09-30
_GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_HEX_RE = re.compile(r"[0-9a-f]{16,}")                  # 长十六进制（缓存命名）


def _is_noise(name: str) -> bool:
    """账号号 / 版本号 / 日期 / GUID / 长十六进制这类"不可能是应用名"的目录。"""
    low = name.lower()
    if len(name) <= 2 and name.isascii():
        return True
    if low.startswith(("wxid_", "pytest-")):
        return True
    return bool(
        _ACCOUNT_RE.fullmatch(name)
        or _VERSION_RE.fullmatch(low)
        or _DATE_RE.fullmatch(low)
        or _GUID_RE.fullmatch(low)
        or _HEX_RE.fullmatch(low)
    )


def _match_product(name: str) -> tuple[str, str] | None:
    low = name.lower()
    entry = _PRODUCTS.get(low)
    if entry is not None:
        return entry
    for token in re.split(r"[^a-z0-9]+", low):
        if token and token in _PRODUCTS:
            return _PRODUCTS[token]
    return None


def _match_vendor(name: str) -> tuple[str, str] | None:
    low = name.lower()
    entry = _VENDORS.get(low)
    if entry is not None:
        return entry
    for token in re.split(r"[^a-z0-9]+", low):
        if token and token in _VENDORS:
            return _VENDORS[token]
    return None


def _app_key_for(name: str, deeper: list[str]) -> tuple[str, str]:
    """(合并键, 展示名)：产品优先，产品未知兜底厂商，再兜底目录名。"""
    product = _match_product(name)
    if product is not None:
        return product
    for directory in deeper:
        product = _match_product(directory)
        if product is not None:
            return product
    vendor = _match_vendor(name)
    if vendor is not None:
        return vendor
    # 未知应用：键保留原始大小写，展示名直接用目录名
    return (name, name)


def label_for_key(key: str) -> str:
    """合并键 → 展示名；未知键原样返回。"""
    return _KEY_LABELS.get(key, key)


def resolve_app(path: str) -> tuple[str, str]:
    """文件路径 → (app_key, app_sub)。

    app_sub 是归属根下一级目录（账号 / 子产品 / 版本目录），仅供超大组拆分；
    没有可用的下一级时为 ""。
    """
    key, sub, _root = _resolve_cached(path.replace("/", "\\"))
    return (key, sub)


def resolve_dir_root(folder: str) -> str:
    """目录路径 → 归属根目录（"应用老家"那一层），供界面展示。

    例：...\\Documents\\Tencent Files\\2991799732\\nt_qq 的归属根是
    ...\\Documents\\Tencent Files；特殊位置（临时 / 下载 / 系统）返回对应目录。
    """
    cleaned = folder.rstrip("\\/").replace("/", "\\")
    if not cleaned:
        return ""
    return _resolve_cached(cleaned + "\\~")[2]


@lru_cache(maxsize=8192)
def _resolve_cached(path: str) -> tuple[str, str, str]:
    parts = [p for p in path.split("\\") if p]
    if not parts:
        return ("special.loose", "", "")
    drive_root = parts[0] + "\\"
    if len(parts) <= 1:
        return ("special.loose", "", drive_root)
    dirs = parts[1:-1]  # 去掉盘符与文件名
    if not dirs:
        return ("special.loose", "", drive_root)
    low = [d.lower() for d in dirs]

    def root_at(index: int) -> str:
        """盘符 + 到第 index 级目录为止的路径。"""
        return "\\".join([parts[0], *dirs[: index + 1]])

    home = 2 if low[0] == "users" and len(dirs) >= 2 else 0
    if home and len(dirs) <= home:
        # 直接躺在 C:\Users\xxx\ 下
        return ("special.loose", "", root_at(home - 1))

    # 标准临时目录：盘根 \Temp、Windows\Temp、AppData\Local[Low]\Temp
    for i, name in enumerate(low):
        if name != "temp":
            continue
        at_root = i == 0
        in_windows = low[0] == "windows"
        in_appdata = i >= 2 and low[i - 1] in ("local", "locallow") and low[i - 2] == "appdata"
        if at_root or in_windows or in_appdata:
            return ("special.temp", "", root_at(i))

    # 下载 / 桌面：盘根或用户主目录（含 OneDrive 下）的一级
    for i, name in enumerate(low):
        if name in ("downloads", "下载", "desktop", "桌面"):
            at_root = i == 0
            in_home = home and i == home
            in_onedrive = home and i == home + 2 and low[home] == "onedrive"
            if at_root or in_home or in_onedrive:
                key = "special.downloads" if name in ("downloads", "下载") else "special.desktop"
                sub = dirs[i + 1] if i + 1 < len(dirs) else ""
                return (key, sub, root_at(i))

    if low[0] == "windows":
        return ("special.system", dirs[1] if len(dirs) > 1 else "", root_at(0))
    if low[0] == "programdata":
        return ("special.programdata", dirs[1] if len(dirs) > 1 else "", root_at(0))

    # AppData\Roaming|Local|LocalLow\<厂商>[\<产品>]
    for i in range(len(dirs) - 2):
        if low[i] == "appdata" and low[i + 1] in ("roaming", "local", "locallow"):
            vendor = dirs[i + 2]
            deeper = dirs[i + 3:]
            sub = deeper[0] if deeper else ""
            if vendor.lower() == "packages" and deeper:
                # UWP：Packages\<包名>_<发布者哈希>
                return (
                    _app_key_for(deeper[0].split("_")[0], deeper[1:])[0],
                    sub,
                    root_at(i + 3),
                )
            return (_app_key_for(vendor, deeper)[0], sub, root_at(i + 2))

    # Program Files[(x86)]\<厂商或产品>[\<版本>]
    for i, name in enumerate(low):
        if name in ("program files", "program files (x86)"):
            if i + 1 >= len(dirs):
                return ("special.programs", "", root_at(i))
            vendor = dirs[i + 1]
            if vendor.lower() == "common files":
                return ("special.programs", "", root_at(i + 1))
            deeper = dirs[i + 2:]
            return (
                _app_key_for(vendor, deeper)[0],
                deeper[0] if deeper else "",
                root_at(i + 1),
            )

    # Documents\<厂商> Files\<账号>\...（腾讯文档目录这类）
    for i, name in enumerate(low):
        if name.endswith(" files") and name not in ("program files", "common files"):
            vendor = dirs[i][: -len(" files")]
            deeper = dirs[i + 1:]
            return (
                _app_key_for(vendor, deeper)[0],
                deeper[0] if deeper else "",
                root_at(i),
            )

    # 用户主目录下的点目录（.codex / .cursor 这类）
    for i in range(home, len(dirs)):
        if low[i].startswith(".") and len(low[i]) > 1:
            name = dirs[i].lstrip(".")
            if name.lower() in _GENERIC_DIRS:
                continue
            deeper = dirs[i + 1:]
            return (_app_key_for(name, deeper)[0], deeper[0] if deeper else "", root_at(i))

    # 路径里出现已知产品 / 厂商目录时优先认它：
    # xwechat_files\...\db_storage\contact 这类深层子目录不该盖过应用本身。
    # 从第 3 层起扫（C:\Code 这种浅层目录名容易撞产品别名，交给兜底推断）。
    scan_start = max(home, 2)
    for i in range(scan_start, len(dirs)):
        hit_product = _match_product(dirs[i])
        if hit_product is not None:
            deeper = dirs[i + 1:]
            return (hit_product[0], deeper[0] if deeper else "", root_at(i))
    for i in range(scan_start, len(dirs)):
        hit_vendor = _match_vendor(dirs[i])
        if hit_vendor is not None:
            deeper = dirs[i + 1:]
            return (hit_vendor[0], deeper[0] if deeper else "", root_at(i))

    # 兜底：从父目录向上跳过通用名，取第一层"像应用名"的目录
    for i in range(len(dirs) - 1, home - 1, -1):
        name = dirs[i]
        if name.lower() in _GENERIC_DIRS or _is_noise(name):
            continue
        deeper = dirs[i + 1:]
        return (_app_key_for(name, deeper)[0], deeper[0] if deeper else "", root_at(i))
    loose_root = root_at(home - 1) if home else drive_root
    return ("special.loose", "", loose_root)


@dataclass(frozen=True)
class AppGroup:
    """一个展示分组（可能是整组，也可能是拆出来的子组）。"""

    key: str
    label: str
    count: int
    net: int
    gross: int
    last_at: float
    folder: str = ""
    sub: str = ""  # 非空表示这是拆分子组，下钻过滤要带 sub
    shallow: str = ""  # 该组最浅的目录（嵌套合并用；空则用 folder）


def _combine(parent: AppGroup, child: AppGroup) -> AppGroup:
    """把子组并进父组（保留父组的键/名/目录）。"""
    return AppGroup(
        parent.key,
        parent.label,
        parent.count + child.count,
        parent.net + child.net,
        parent.gross + child.gross,
        max(parent.last_at, child.last_at),
        parent.folder,
        parent.sub,
        parent.shallow,
    )


def _merge_nested(groups: list[AppGroup]) -> list[AppGroup]:
    """目录嵌套在其它分组下的普通应用组并进父组。

    例：C:\\Code\\proj\\diskwatch 与 C:\\Code\\proj 各自成组时合并为一组，
    工程子目录（ui/tests/包名）不再单独占行。special.* 与拆分子组不参与，
    避免把应用并进「零散文件」或拆散 QQ 的账号子组。
    """
    result = list(groups)
    eligible = [
        i
        for i, g in enumerate(result)
        if not g.sub and not g.key.startswith("special.") and (g.shallow or g.folder)
    ]
    eligible.sort(
        key=lambda i: (
            (result[i].shallow or result[i].folder).count("\\"),
            result[i].shallow or result[i].folder,
        )
    )
    folder_to_index: dict[str, int] = {}
    absorbed: set[int] = set()
    for i in eligible:
        folder = (result[i].shallow or result[i].folder).rstrip("\\")
        prefix = folder
        parent_index: int | None = None
        while "\\" in prefix:
            prefix = prefix.rsplit("\\", 1)[0]
            parent_index = folder_to_index.get(prefix)
            if parent_index is not None:
                break
        if parent_index is None:
            folder_to_index[folder] = i
        else:
            result[parent_index] = _combine(result[parent_index], result[i])
            absorbed.add(i)
    return [g for i, g in enumerate(result) if i not in absorbed]


def build_groups(
    rows: list[tuple[str, str, int, int, int, float]],
    folder_rows: list[tuple[str, str, str, int]],
) -> list[AppGroup]:
    """把 (key, sub, count, net, gross, last_at) 聚合成均衡的分组列表。

    folder_rows 为 (key, sub, folder, count)，用于给每组挑一个"主要目录"。
    """
    if not rows:
        return []
    total = sum(row[2] for row in rows)
    if total <= 0:
        return []

    folder_top: dict[tuple[str, str], tuple[int, str]] = {}
    for key, sub, folder, count in folder_rows:
        previous = folder_top.get((key, sub))
        if previous is None or count > previous[0]:
            folder_top[(key, sub)] = (count, folder)

    folder_shallow: dict[tuple[str, str], str] = {}
    for key, sub, folder, _count in folder_rows:
        current = folder_shallow.get((key, sub))
        if current is None or (folder.count("\\"), len(folder)) < (
            current.count("\\"),
            len(current),
        ):
            folder_shallow[(key, sub)] = folder

    def top_folder(key: str, subs: list[tuple[str, int, int, int, float]]) -> str:
        best = max(subs, key=lambda s: s[1])
        return folder_top.get((key, best[0]), (0, ""))[1]

    def shallow_folder(key: str, subs: list[tuple[str, int, int, int, float]]) -> str:
        best = ""
        for sub, *_rest in subs:
            folder = folder_shallow.get((key, sub), "")
            if folder and (
                not best
                or (folder.count("\\"), len(folder)) < (best.count("\\"), len(best))
            ):
                best = folder
        return best

    limit = max(
        MIN_SPLIT_EVENTS, min(MAX_GROUP_EVENTS, math.ceil(total * MAX_GROUP_SHARE))
    )

    by_key: dict[str, list[tuple[str, int, int, int, float]]] = {}
    for key, sub, count, net, gross, last_at in rows:
        by_key.setdefault(key, []).append((sub, count, net, gross, last_at))

    groups: list[AppGroup] = []
    for key, subs in by_key.items():
        count = sum(s[1] for s in subs)
        net = sum(s[2] for s in subs)
        gross = sum(s[3] for s in subs)
        last_at = max(s[4] for s in subs)
        if count > limit and len(subs) > 1:
            # 超大组按归属根下一级拆（QQ 的多个账号、Windows 的多个子目录）
            for sub, c, n, g, t in sorted(subs, key=lambda s: s[1], reverse=True):
                label = label_for_key(key)
                if sub:
                    label = f"{label} · {sub}"
                groups.append(
                    AppGroup(
                        key, label, c, n, g, t,
                        folder_top.get((key, sub), (0, ""))[1], sub,
                        folder_shallow.get((key, sub), ""),
                    )
                )
            continue
        groups.append(
            AppGroup(key, label_for_key(key), count, net, gross, last_at,
                     top_folder(key, subs), "", shallow_folder(key, subs))
        )

    # 工程子目录等嵌套分组并进父组（避免一个项目拆成好几行）
    groups = _merge_nested(groups)

    # 长尾：条数少、占比低、净变化也不大的小组收进「其它位置」。
    # 按净变化从小到大装桶，桶内合计不超过阈值；装不下的保持单独可见，
    # 避免「其它位置」吞掉本该被看到的大变化。
    tail = [
        g for g in groups
        if not g.sub
        and not g.key.startswith("special.")
        and g.count < MIN_GROUP_EVENTS
        and g.count / total < MIN_GROUP_SHARE
    ]
    tail.sort(key=lambda g: abs(g.net))
    bucket: list[AppGroup] = []
    bucket_net = 0
    for tail_group in tail:
        if abs(tail_group.net) >= MIN_GROUP_NET or (
            bucket and bucket_net + abs(tail_group.net) > MIN_GROUP_NET
        ):
            break
        bucket.append(tail_group)
        bucket_net += abs(tail_group.net)
    if len(bucket) > 1:
        bucket_ids = {id(g) for g in bucket}
        groups = [g for g in groups if id(g) not in bucket_ids]
        groups.append(
            AppGroup(
                OTHER_KEY, OTHER_LABEL,
                sum(g.count for g in bucket),
                sum(g.net for g in bucket),
                sum(g.gross for g in bucket),
                max(g.last_at for g in bucket),
            )
        )

    # 总组数上限：最小的先并进「其它位置」
    if len(groups) > MAX_GROUPS:
        groups.sort(key=lambda g: g.count)
        overflow = groups[: len(groups) - (MAX_GROUPS - 1)]
        groups = groups[len(groups) - (MAX_GROUPS - 1):]
        existing = next((g for g in groups if g.key == OTHER_KEY), None)
        merged = AppGroup(
            OTHER_KEY, OTHER_LABEL,
            sum(g.count for g in overflow) + (existing.count if existing else 0),
            sum(g.net for g in overflow) + (existing.net if existing else 0),
            sum(g.gross for g in overflow) + (existing.gross if existing else 0),
            max([g.last_at for g in overflow] + ([existing.last_at] if existing else [0.0])),
        )
        groups = [g for g in groups if g.key != OTHER_KEY]
        groups.append(merged)

    others = [g for g in groups if g.key == OTHER_KEY]
    rest = [g for g in groups if g.key != OTHER_KEY]
    rest.sort(key=lambda g: (abs(g.net), g.count), reverse=True)
    return rest + others
