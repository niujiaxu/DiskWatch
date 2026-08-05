# Changelog

All notable changes to DiskWatch are documented in this file.

## [Unreleased]

## [1.2.0] - 2026-08-05

### Added
- 目录 mtime 剪枝：启动补扫对旧目录跳过直接文件的 stat，显著加速全盘补扫
- 详情面板事件类型切换（新增 / 已删除 / 全部），删除行标色并显示删除时间
- 详情面板近 14 天新增趋势迷你柱状图
- 表格右键菜单：打开 / 资源管理器定位 / 复制路径 / 复制文件名
- 语言热切换：设置中改语言即时生效，无需重启
- 过滤预设：一键添加开发目录过滤（`__pycache__` / `node_modules` / `.pytest_cache` 等）

### Changed
- 测试框架迁至 pytest（原 8 个手写脚本均已迁移），新增 5 个测试文件覆盖 filters / config / storage / panel / settings
- 新增 ruff 静态检查 + mypy 类型检查，零问题
- 版本号升至 1.2.0

## [1.1.6] - 2026-08-02

### Added
- 中英双语：设置「外观与启动 → 界面语言」可切换中文 / English（保存后需重启生效），全部界面文案已接入翻译
- 启动补扫：程序没在跑（或电脑睡眠）期间落地、事件层收不到的文件，启动时后台按磁盘现状对账补回；默认开启，可在设置「外观与启动」里关掉或调回看天数

### Fixed
- 下载临时名（`.part` / `.crdownload` 等被过滤的扩展名）重命名为正式名时记录丢失：改名事件对 `src` 未入库的文件直接登记 `dst`，不再先插后删
- 整目录移动后同一批文件重复计数：删除/移动事件对路径做「自身 + 子树」级联标记，旧路径残留不再参与统计

## [1.1.5] - 2026-07-31

### Changed
- Mini-ball progress ring = today’s volume / last 7 days’ total (easier to see day-to-day change than vs peak)
- Unified cooler tech-blue UI palette across card, ball, detail, and settings

## [1.1.4] - 2026-07-31

### Added
- Detail panel **按应用分组**: tree view that clusters same-day files under one path root (e.g. `Tencent Files`, AppData apps) without a hard-coded allowlist

### Changed
- Detail panel no longer forces always-on-top (floating card/ball still follow Settings)

## [1.1.3] - 2026-07-31

### Fixed
- Launching DiskWatch again activates the existing instance (shows the floating surface) instead of only showing “already running”
- Detail panel search stats (count / size / top folder / top ext) now match the filtered table; status shows “筛选到 N / 当日共 M”
- Hiding from the mini-ball updates `widget_visible` and the tray “显示悬浮组件” checkbox like the card does

### Changed
- Empty floating card explains that AppData / Program Files are filtered by default

## [1.1.2] - 2026-07-30

### Changed
- Detail panel table uses a virtual `QTableView` model (no more chunked cell fill)

### Fixed
- Settings title-bar close button broken by PySide6 `~WindowContextHelpButtonHint` clearing `CloseButtonHint`
- Date dropdown on the always-on-top detail panel jumping to the screen corner / leaving a ghost frame
- Opening **详情** after minimizing the detail window no longer stays stuck in the taskbar (`showNormal`)

## [1.1.1] - 2026-07-30

### Added
- Tray menu **重启** — relaunch the whole app (works for portable `.exe` and source runs)
- Detail panel: click **大小** header to sort by real byte size (asc/desc), same UX as **时间**
- Floating card: scrollable **最近** list (fixed row pool, thin scrollbar)

### Fixed
- Opening the detail panel no longer freezes the floating card / tray (async DB load + chunked table fill)
- UI freezes when clicking during heavy ingest (SQLite read/write connections split; purge off UI thread)
- Detail panel search no longer rebuilds the whole table on every keystroke (debounce + row cap)
- Dragging the card/ball no longer writes `config.json` on every mouse release (debounced save)
- Opening Explorer no longer does sync `is_file()` probes that can hang on slow paths
- Detail panel toolbar no longer overlaps date dropdown and search field
- Detail panel stays above the always-on-top floating card

## [1.1.0] - 2026-07-30

### Added
- Custom config / database file locations in **Settings → Data** (with migrate + restart)
- Windows dark title bar for settings, detail panel, and message boxes
- PyInstaller portable Windows x64 build

### Changed
- Mini-ball preview and docs reflect total size display (not file count)

## [1.0.0] - 2026-07-30

### Added
- Real-time whole-disk file creation monitoring on Windows (watchdog)
