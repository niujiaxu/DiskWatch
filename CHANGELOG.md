# Changelog

All notable changes to DiskWatch are documented in this file.

## [Unreleased]

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
