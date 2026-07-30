# Changelog

All notable changes to DiskWatch are documented in this file.

## [Unreleased]

### Added
- Detail panel: click **大小** header to sort by real byte size (asc/desc), same UX as **时间**

### Fixed
- UI freezes when clicking during heavy ingest (SQLite read/write connections split; purge off UI thread)
- Detail panel search no longer rebuilds the whole table on every keystroke (debounce + row cap)
- Dragging the card/ball no longer writes `config.json` on every mouse release (debounced save)
- Opening Explorer no longer does sync `is_file()` probes that can hang on slow paths

## [1.1.0] - 2026-07-30

### Added
- Custom config / database file locations in **Settings → Data** (with migrate + restart)
- Windows dark title bar for settings, detail panel, and message boxes

### Changed
- Mini-ball preview and docs reflect total size display (not file count)

## [1.0.0] - 2026-07-30

### Added
- Real-time whole-disk file creation monitoring on Windows (watchdog)
- Floating desktop card with today's count, total size, and recent files
- Mini ball mode showing today's total size and a 7-day peak progress ring
- Detail panel with day picker, search, sort, and CSV export
- Smart noise filters (system dirs, AppData, caches, dot-directories)
- System tray, always-on-top, opacity, and optional autostart
- Local SQLite storage under `%APPDATA%\DiskWatch\`
- Smoke / UI-state / performance self-tests
