"""中英双语：tr() 查表翻译 + 全局语言切换。

设计：
- 源文本（key）就是中文原文，lang=zh_CN 时原样返回，零开销。
- lang=en_US 时查 _TRANSLATIONS 字典，找不到就兜底回中文（不崩溃）。
- 插值统一用命名参数：tr("今日新增 {count} 个文件", count=n)，英文可调换顺序。
- 语言在 app.main() 启动时 set_language() 设置一次，重启生效。
"""

from __future__ import annotations

_lang: str = "zh_CN"

# key = 中文原文，value = 英文译文。{name} 为插值占位。
_TRANSLATIONS: dict[str, str] = {
    # ---------- app.py ----------
    "DiskWatch 磁盘空间监控": "DiskWatch Disk Space Monitor",
    "显示悬浮组件": "Show Floating Widget",
    "迷你球模式": "Mini Ball Mode",
    "详情面板…": "Detail Panel…",
    "设置…": "Settings…",
    "重新开始监控": "Restart Monitor",
    "重启": "Restart",
    "关于 {name} {version}": "About {name} {version}",
    "退出": "Quit",
    "部分位置监控失败：\n": "Monitor errors:\n",
    "今日新增 {count} 个文件 · {size}": "Today +{count} files · {size}",
    "实时记录硬盘上每天新增了哪些文件。": "Shows what files land on your disk today.",
    "数据库：{db}": "Database: {db}",
    "配置：{config}": "Config: {config}",
    "左键点击托盘图标可显示/隐藏悬浮组件，双击打开详情面板。": "Left-click tray icon to show/hide; double-click to open the detail panel.",
    "点卡片上的「－」收成迷你球，单击球可再展开。": "Click the card's 「－」 to collapse into the mini ball; click the ball to expand.",
    "{name} 已经在运行了（见系统托盘）。": "{name} is already running (see the system tray).",
    "当前系统没有可用的托盘区，无法运行。": "No system tray is available on this system.",
    "位置已更新": "Location Updated",
    "配置：{config}\n数据库：{db}\n\n程序将重启以加载新位置。": "Config: {config}\nDatabase: {db}\n\nThe app will restart to load the new location.",
    "更改位置失败": "Failed to Change Location",
    "自动重启失败": "Auto-restart Failed",
    "请手动重新运行程序。\n{exc}": "Please restart the app manually.\n{exc}",
    "关于 {name}": "About {name}",
    "修改语言后即时生效": "Language change applies instantly",
    "{root} 监控失败: {exc}": "{root} monitor error: {exc}",
    "最近错误": "Recent Errors",
    "最近错误 ({n})": "Recent Errors ({n})",
    "最近错误（最近 {n} 条）": "Recent Errors (last {n})",
    "暂无错误记录。\n日志文件：{path}": "No errors. Log file: {path}",

    # ---------- main_window.py ----------
    "DiskWatch · 磁盘空间": "DiskWatch · Disk Space",
    "磁盘空间去向": "Where space went",
    "关闭": "Close",
    "最小化": "Minimize",
    "最大化": "Maximize",
    "还原": "Restore",
    "概览": "Overview",
    "概览…": "Overview…",
    "DiskWatch · 概览": "DiskWatch · Overview",
    "文件活动": "File Activity",
    "磁盘实际变化": "Actual Disk Change",
    "已归因变化": "Attributed Change",
    "未归因变化": "Unattributed Change",
    "分类空间变化": "Space Change by Category",
    "占用增长最大的文件": "Files with the Largest Growth",
    "每日已归因变化": "Daily Attributed Change",
    "累计已归因净变化": "Cumulative Attributed Net Change",
    "净变化": "Net Change",
    "事件数": "Events",
    "{count} 个空间事件": "{count} space events",
    "近 {days} 天：记录 {count} 个空间事件 · 已归因 {size}": "Last {days} days: {count} events · attributed {size}",

    # ---------- activity.py ----------
    "今天": "Today",
    "最近 24 小时": "Last 24 Hours",
    "最近 7 天": "Last 7 Days",
    "最近 30 天": "Last 30 Days",
    "全部时间": "All Time",
    "全部事件": "All Events",
    "全部视图": "All View",
    "关注视图": "Focus View",
    "空间活动.csv": "space-activity.csv",
    "原大小": "Old Size",
    "新大小": "New Size",
    "搜索文件名或路径": "Search file name or path",
    "活动类型": "Activity",
    "空间影响": "Space Impact",
    "分类": "Category",
    "选择一条活动": "Select an activity",
    "上一页": "Previous",
    "下一页": "Next",
    "全部磁盘": "All Drives",
    "全部分类": "All Categories",
    "不分组": "No Grouping",
    "按分类分组": "Group by Category",
    "按目录分组": "Group by Folder",
    "{marker} {label} · {count} 条": "{marker} {label} · {count} items",
    "共 {count} 条 · 第 {page}/{pages} 页": "{count} items · Page {page}/{pages}",
    "每页最多 {count} 条": "Up to {count} per page",
    "{event} · {category}\n{time}": "{event} · {category}\n{time}",
    "原大小 {old}\n新大小 {new}\n空间影响 {delta}": "Old size {old}\nNew size {new}\nSpace impact {delta}",
    "大小变化历史": "Size Change History",
    "{time}  {old} → {new}  ({delta})": "{time}  {old} → {new}  ({delta})",
    "暂无历史": "No history yet",
    "正在加载历史…": "Loading history…",
    "历史加载失败：{err}": "Unable to load history: {err}",
    "无法定位文件": "Unable to Reveal File",
    "导出结果格式无效": "Invalid export result",
    "用户文件": "User Files",
    "下载文件": "Downloads",
    "系统与更新": "System and Updates",
    "软件安装": "Software Installation",
    "应用缓存": "App Cache",
    "开发产物": "Development Artifacts",
    "虚拟机和容器": "VMs and Containers",
    "未分类": "Uncategorized",
    "创建": "Created",
    "修改": "Modified",
    "删除": "Deleted",
    "移动": "Moved",
    "跨盘移入": "Moved In Across Drives",
    "跨盘移出": "Moved Out Across Drives",
    "重新创建": "Recreated",

    # ---------- widget.py ----------
    "今日空间变化": "Today's Space Change",
    "已归因 {pct}%": "{pct}% attributed",
    "暂无空间变化。普通文件会全部记录并自动分类。": "No space changes yet. Regular files are recorded and categorized automatically.",
    "收成迷你悬浮球": "Collapse to mini ball",
    "隐藏组件（托盘图标可再次唤出）": "Hide (tray icon can restore)",
    "个": "",  # 英文不显示单位词
    "最近": "Recent",
    "详情": "Details",
    "设置": "Settings",
    "正在恢复离线文件变化…": "Recovering offline file changes…",
    "取消补扫": "Cancel Scan",
    "正在取消…": "Cancelling…",
    "正在补扫：{directories} 个目录 · {files} 个文件": "Scanning: {directories} folders · {files} files",
    "补扫已取消，已补回 {count} 条": "Scan cancelled · restored {count}",
    "补扫完成，已补回 {count} 条": "Scan complete · restored {count}",
    "监控 {roots} 个位置": "Watching {roots} locations",
    " · 队列 {queue}": " · queue {queue}",
    " · 丢弃 {dropped}": " · dropped {dropped}",
    " · 剩余 {free}": " · free {free}",
    "打开详情面板": "Open Detail Panel",
    "收成迷你球": "Collapse to Mini Ball",
    "隐藏组件": "Hide Widget",

    # ---------- ball.py ----------
    "今日净变化": "Today's net change",
    "占用增加": "space used",
    "释放空间": "space freed",
    "今日{direction} {impact}\n已归因 {pct}%\n记录 {count} 个文件": "Today: {direction} {impact}\n{pct}% attributed\n{count} files recorded",
    "今日 {today}\n近{days}天合计 {total}\n今日占比 {pct}%": "Today {today}\nLast {days}d {total}\nShare {pct}%",
    "展开卡片": "Expand Card",
    "隐藏（保留托盘图标）": "Hide (keep tray icon)",

    # ---------- 活动页 / 表格共用 ----------
    "时间": "Time",
    "文件名": "File Name",
    "所在目录": "Directory",
    "导出 CSV": "Export CSV",
    "刷新": "Refresh",
    "在资源管理器中定位": "Reveal in Explorer",
    "复制路径": "Copy Path",
    "开发目录过滤": "Dev Filters",
    "应用模板": "Apply",
    "已应用": "Applied",
    "已添加 {n} 条开发目录过滤规则。": "{n} dev directory filter(s) added.",
    "把 __pycache__ / node_modules / .git / .pytest_cache 等常见开发目录加入排除列表": "Add __pycache__ / node_modules / .git / .pytest_cache etc. to exclusion list",
    "加载中…": "Loading…",
    "加载失败：{err}": "Load failed: {err}",
    "正在导出…": "Exporting…",
    "导出失败": "Export Failed",
    "导出完成": "Export Complete",
    "已导出 {n} 条记录到：\n{path}": "Exported {n} records to:\n{path}",

    # ---------- dashboard.py ----------
    "看板": "Dashboard",
    "24 小时磁盘剩余空间": "24-hour Disk Free Space",
    "{drive} 当前剩余 {free}": "{drive} currently {free} free",
    "TOP 目录": "Top Folders",
    "{n} 天": "{n} days",
    "对数": "Log",
    "线性": "Linear",
    "单击增长柱可打开该天的详情": "Click a growth bar to open that day's details",
    "截至 {day} · 累计 {size}": "Up to {day} · cumulative {size}",
    "{label}\n{count} 个文件 · {size}": "{label}\n{count} files · {size}",
    "完整路径": "Full Path",
    "{count} 个文件": "{count} files",

    # ---------- settings.py ----------
    "通用": "General",
    "监控": "Monitoring",
    "外观": "Appearance",
    "高级": "Advanced",
    "启动恢复": "Startup Recovery",
    "USN 增量恢复（推荐）": "USN Incremental Recovery (Recommended)",
    "目录补扫": "Directory Reconciliation Scan",
    "不恢复离线变化": "Do Not Recover Offline Changes",
    "USN 不可用时补扫用户目录": "Scan user folders when USN is unavailable",
    "补扫范围": "Scan Scope",
    "仅用户目录（推荐）": "User Folders Only (Recommended)",
    "全部监控根目录": "All Watched Roots",
    "下列排除项在全量采集和关注模式下都生效：命中即不计入账本。VM 磁盘镜像、浏览器缓存、临时文件这类高频读写建议保持排除，否则会把真正有意义的变化淹没。自身数据库与设备文件始终安全排除。": "These exclusions apply in both full-capture and focus modes: matched files are never recorded. Keep high-churn items (VM disk images, browser caches, temp files) excluded, or they will drown out meaningful changes. DiskWatch's own data and device paths are always excluded.",
    "立即清理过期记录": "Clean Expired Records Now",
    "打开文件活动并导出…": "Open File Activity to Export…",
    "数据库大小：{size} · 近 24 小时 {events} 条 · 写入 {rate}/秒 · 预计每日增长 {growth}": "Database size: {size} · {events} events in 24h · {rate}/s writes · estimated daily growth {growth}",
    "警告：事件队列持续高负载，正在优先合并事件。": "Warning: the event queue remains under high load; events are being coalesced first.",
    "已清理 {count} 条过期记录": "Cleaned {count} expired records",
    "性能诊断会在主程序运行时显示。": "Performance diagnostics are shown while the main app is running.",
    "事件诊断：原始 {seen} · 已处理 {processed} · 已合并 {coalesced} · 丢弃 {dropped} · 队列 {queued}": "Event diagnostics: raw {seen} · processed {processed} · coalesced {coalesced} · dropped {dropped} · queued {queued}",
    "USN 状态：{status}": "USN status: {status}",
    "主题": "Theme",
    "跟随系统": "Follow System",
    "浅色": "Light",
    "深色": "Dark",
    "减少动态效果": "Reduce motion",
    "保存并应用": "Save & Apply",
    "取消": "Cancel",
    "恢复默认过滤规则": "Reset Default Filters",
    "清空所有记录": "Clear All Records",
    "浏览…": "Browse…",
    "恢复默认位置": "Reset to Default",
    "数据": "Data",
    "勾选要监控的磁盘：": "Drives to watch:",
    "同时监控可移动磁盘（U 盘 / 移动硬盘）": "Also watch removable drives (USB / external)",
    "额外监控的文件夹（可选，留空表示只按磁盘监控）：": "Extra folders to watch (optional; empty = drives only):",
    "添加文件夹…": "Add Folder…",
    "移除选中": "Remove Selected",
    "只监控上面这些文件夹（忽略磁盘勾选）": "Only watch the folders above (ignore drive selection)",
    "排除的路径片段（每行一条，路径里包含即忽略，不区分大小写）：": "Excluded path fragments (one per line, case-insensitive):",
    "排除的扩展名（每行一条，含点号）：": "Excluded extensions (one per line, with dot):",
    "排除的文件名（每行一条，支持 * 通配）：": "Excluded filenames (one per line, supports * wildcard):",
    "自定义分类规则（每行：分类=路径片段；优先于内置规则）：": "Custom category rules (one per line: category=path fragment; applied before built-ins):",
    "示例：development=\\Work\\build\\": "Example: development=\\Work\\build\\",
    "最小体积": "Min Size",
    "不限制": "No Limit",
    "忽略隐藏文件与系统文件": "Ignore hidden and system files",
    "忽略点号开头的目录（.git / .venv / .idea / .cursor 等）": "Ignore dot-directories (.git / .venv / .idea / .cursor etc.)",
    "组件透明度": "Opacity",
    "始终置顶": "Always on Top",
    "开机自动启动": "Start with Windows",
    "启动时只显示托盘图标，不显示悬浮组件": "Start minimized (tray icon only)",
    "补扫回看窗口": "Scan Lookback",
    " 天": " days",
    "只补创建时间落在最近 N 天内的文件": "Only backfill files created within the last N days",
    "历史数据保留": "Data Retention",
    "永久保留": "Forever",
    "文件位置（可改到其他盘；保存后需重启生效）": "File locations (can be moved to another drive; requires restart)",
    "配置文件": "Config File",
    "数据库": "Database",
    "引导文件始终留在 AppData\\DiskWatch\\location.json，用来记住你自定义的路径。改位置时会自动拷贝现有文件。": "A stub file stays at AppData\\DiskWatch\\location.json to remember your custom paths; existing files are copied when you change location.",
    "界面语言": "Language",
    "配置文件路径（.json）": "Config file path (.json)",
    "数据库路径（.db）": "Database path (.db)",
    "当前已记录 {count} 条文件记录": "{count} records in database",
    "还没选文件夹": "No Folder Selected",
    "选择了只监控文件夹，但列表是空的。": "You chose folder-only mode but the list is empty.",
    "路径为空": "Empty Path",
    "配置文件和数据库路径都不能为空。": "Config and database paths cannot be empty.",
    "更改文件位置": "Change File Location",
    "将把现有配置/数据库复制到新位置，并在下次启动时使用新路径。\n应用需要重启才能生效，是否继续？": "Config and database will be copied to the new location and used from next launch. The app needs a restart. Continue?",
    "确认清空": "Confirm Clear",
    "将删除全部历史记录，且不可恢复。继续？": "This will delete all records and cannot be undone. Continue?",
    "选择配置文件位置": "Choose Config File Location",
    "选择数据库位置": "Choose Database Location",
    "选择要监控的文件夹": "Choose Folder to Watch",
    "默认目录：{home}": "Default: {home}",
    "中文": "中文",
    "English": "English",

    # ---------- 分类标签 ----------
    "临时文件": "Temp Files",

    # ---------- storage.py ----------
    "(无扩展名)": "(no extension)",
}


# 支持的语言：code -> 下拉框显示名
SUPPORTED_LOCALES: dict[str, str] = {
    "zh_CN": "中文",
    "en_US": "English",
}


def set_language(lang: str) -> None:
    global _lang
    _lang = lang


def language() -> str:
    return _lang


def tr(text: str, **kwargs: object) -> str:
    """查表翻译；kwargs 传给 str.format() 做插值。"""
    if _lang == "zh_CN":
        result = text
    else:
        result = _TRANSLATIONS.get(text, text)
    if kwargs:
        result = result.format(**kwargs)
    return result
