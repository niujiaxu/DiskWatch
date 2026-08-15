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
    "硬盘新增文件监控": "DiskWatch",
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
    "程序将重启以加载新位置。": "The app will restart to load the new location.",
    "配置：{config}\n数据库：{db}\n\n程序将重启以加载新位置。": "Config: {config}\nDatabase: {db}\n\nThe app will restart to load the new location.",
    "更改位置失败": "Failed to Change Location",
    "自动重启失败": "Auto-restart Failed",
    "请手动重新运行程序。\n{exc}": "Please restart the app manually.\n{exc}",
    "关于 {name}": "About {name}",
    "修改语言后即时生效": "Language change applies instantly",
    "语言已变更": "Language Changed",
    "{root} 监控失败: {exc}": "{root} monitor error: {exc}",
    "最近错误": "Recent Errors",
    "最近错误 ({n})": "Recent Errors ({n})",
    "最近错误（最近 {n} 条）": "Recent Errors (last {n})",
    "暂无错误记录。\n日志文件：{path}": "No errors. Log file: {path}",

    # ---------- widget.py ----------
    "今日新增文件": "Files Added Today",
    "收成迷你悬浮球": "Collapse to mini ball",
    "隐藏组件（托盘图标可再次唤出）": "Hide (tray icon can restore)",
    "个": "",  # 英文不显示单位词
    "最近": "Recent",
    "暂无记录。默认不监控 AppData、Program Files 等系统目录，可在「设置 → 过滤规则」里调整。": "No records yet. AppData, Program Files and similar system dirs are excluded by default; adjust in Settings → Filters.",
    "详情": "Details",
    "设置": "Settings",
    "监控 {roots} 个位置": "Watching {roots} locations",
    " · 队列 {queue}": " · queue {queue}",
    " · 丢弃 {dropped}": " · dropped {dropped}",
    " · 剩余 {free}": " · free {free}",
    "打开详情面板": "Open Detail Panel",
    "收成迷你球": "Collapse to Mini Ball",
    "隐藏组件": "Hide Widget",

    # ---------- ball.py ----------
    "今日": "Today",
    "今日 {today}\n近{days}天合计 {total}\n今日占比 {pct}%": "Today {today}\nLast {days}d {total}\nShare {pct}%",
    "展开卡片": "Expand Card",
    "隐藏（保留托盘图标）": "Hide (keep tray icon)",

    # ---------- panel.py ----------
    "时间": "Time",
    "文件名": "File Name",
    "大小": "Size",
    "类型": "Type",
    "所在目录": "Directory",
    "硬盘新增文件 · 详情": "DiskWatch · Details",
    "导出 CSV": "Export CSV",
    "刷新": "Refresh",
    "新增文件明细": "File Details",
    "全部": "All",
    "已删除": "Deleted",
    "新增": "Added",
    "打开": "Open",
    "在资源管理器中定位": "Reveal in Explorer",
    "复制路径": "Copy Path",
    "复制文件名": "Copy Name",
    "开发目录过滤": "Dev Filters",
    "应用模板": "Apply",
    "已应用": "Applied",
    "已添加 {n} 条开发目录过滤规则。": "{n} dev directory filter(s) added.",
    "把 __pycache__ / node_modules / .git / .pytest_cache 等常见开发目录加入排除列表": "Add __pycache__ / node_modules / .git / .pytest_cache etc. to exclusion list",
    "日期": "Date",
    "新增文件": "New Files",
    "占用空间": "Total Size",
    "今日剩余空间": "Free Space Today",
    "最活跃目录": "Top Folder",
    "最多的类型": "Top Type",
    "按应用分组": "Group by App",
    "把同一应用下的文件收成可折叠分组（类似进程树）": "Collapse files from the same app into expandable groups",
    "按文件名或目录筛选…": "Filter by name or folder…",
    "双击文件行可在资源管理器中定位；双击应用分组可展开/折叠": "Double-click a file to reveal in Explorer; double-click a group to expand/collapse",
    "加载中…": "Loading…",
    "排序中…": "Sorting…",
    "加载失败：{err}": "Load failed: {err}",
    "显示前 {shown} 条 / 筛选共 {count} 条（请再缩小关键词）": "Showing first {shown} / filtered {count} total (try narrowing your keyword)",
    "共 {count} 条，表格仅显示前 {shown} 条。可在搜索框缩小范围查看其余记录。": "{count} records total; the table shows only the first {shown}. Narrow the search to see the rest.",
    "筛选到 {shown} 条 / 当日共 {total} 条": "Filtered {shown} / {total} today",
    "显示 {shown} 条": "{shown} entries",
    "正在导出…": "Exporting…",
    "导出失败": "Export Failed",
    "导出完成": "Export Complete",
    "已导出 {n} 条记录到：\n{path}": "Exported {n} records to:\n{path}",
    "今天 · {count} 个 · {size}": "Today · {count} · {size}",
    "{day} · {count} 个 · {size}": "{day} · {count} · {size}",
    "今天 · {count} 个 · {size} · 剩 {free}": "Today · {count} · {size} · free {free}",
    "{day} · {count} 个 · {size} · 剩 {free}": "{day} · {count} · {size} · free {free}",
    "今天  ·  {count} 个  ·  {size}": "Today  ·  {count}  ·  {size}",
    "{day}  ·  {count} 个  ·  {size}": "{day}  ·  {count}  ·  {size}",

    # ---------- dashboard.py ----------
    "硬盘新增文件 · 数据看板": "DiskWatch · Dashboard",
    "数据面板": "Dashboard",
    "数据看板": "Dashboard",
    "数据看板…": "Dashboard…",
    "看板": "Dashboard",
    "增长趋势": "Growth Trend",
    "累计增长": "Cumulative Growth",
    "磁盘剩余空间": "Free Space",
    "TOP 文件类型": "Top File Types",
    "TOP 目录": "Top Folders",
    "{n} 天": "{n} days",
    "体积": "Size",
    "数量": "Count",
    "对数": "Log",
    "线性": "Linear",
    "单击增长柱可打开该天的详情": "Click a growth bar to open that day's details",
    "近 {days} 天：新增 {count} 个文件 · {size}": "Last {days} days: {count} files · {size}",
    "截至 {day} · 累计 {size}": "Up to {day} · cumulative {size}",
    "{label}\n{count} 个文件 · {size}": "{label}\n{count} files · {size}",
    "今天  ·  {count} 个  ·  {size}  ·  剩 {free}": "Today  ·  {count}  ·  {size}  ·  free {free}",
    "{day}  ·  {count} 个  ·  {size}  ·  剩 {free}": "{day}  ·  {count}  ·  {size}  ·  free {free}",
    "新增文件_{day}.csv": "DiskWatch_{day}.csv",
    "大小(字节)": "Size (bytes)",
    "可读大小": "Human Size",
    "完整路径": "Full Path",
    "应用": "Application",
    "{label}  ·  {count} 个": "{label}  ·  {count}",
    "{count} 个文件": "{count} files",

    # ---------- settings.py ----------
    "保存并应用": "Save & Apply",
    "取消": "Cancel",
    "恢复默认过滤规则": "Reset Default Filters",
    "清空所有记录": "Clear All Records",
    "浏览…": "Browse…",
    "恢复默认位置": "Reset to Default",
    "监控范围": "Scope",
    "过滤规则": "Filters",
    "外观与启动": "Appearance",
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
    "最小体积": "Min Size",
    "不限制": "No Limit",
    "忽略隐藏文件与系统文件": "Ignore hidden and system files",
    "忽略点号开头的目录（.git / .venv / .idea / .cursor 等）": "Ignore dot-directories (.git / .venv / .idea / .cursor etc.)",
    "组件透明度": "Opacity",
    "始终置顶": "Always on Top",
    "开机自动启动": "Start with Windows",
    "启动时只显示托盘图标，不显示悬浮组件": "Start minimized (tray icon only)",
    "启动时补扫最近创建的文件（补回程序没在跑期间遗漏的记录）": "Scan for missed files on startup (catches files created while the app was off)",
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
    "无法更改位置": "Unable to Change Location",
    "确认清空": "Confirm Clear",
    "将删除全部历史记录，且不可恢复。继续？": "This will delete all records and cannot be undone. Continue?",
    "选择配置文件位置": "Choose Config File Location",
    "选择数据库位置": "Choose Database Location",
    "选择要监控的文件夹": "Choose Folder to Watch",
    "默认目录：{home}": "Default: {home}",
    "中文": "中文",
    "English": "English",

    # ---------- grouping.py ----------
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
