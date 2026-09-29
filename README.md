# DiskWatch

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">中文</a>
</p>

<p align="center">
  <strong>Explain where your disk space went.</strong><br/>
  <sub>Attribute regular-file space changes locally without reading file contents.</sub>
</p>

## What it explains

DiskWatch correlates file activity with sampled free space:

```text
C: free space decreased    8.4 GB
Attributed                 7.9 GB
Unattributed               0.5 GB
```

- Actual change comes from file-system free-space samples.
- Attributed change is the byte delta from creates, growth, shrink, deletion, and cross-drive moves.
- Unattributed change is the difference between those two measurements.
- Same-drive moves have zero net impact. Cross-drive moves free space on the source and consume it on the destination.

## Features

- Full capture records regular cache, temporary, AppData, and development files, then categorizes them instead of discarding them.
- Safety exclusions always protect DiskWatch's own database/WAL/log, device paths, and non-regular files.
- Categories include user files, downloads, system and updates, software, app cache, temporary files, development artifacts, VMs and containers, and uncategorized. Advanced settings can add higher-priority path rules.
- The space ledger stores old size, new size, delta, event type, drive, category, and timestamp.
- Repeated writes to one path settle for about six seconds before final metadata is recorded.
- Free space is sampled every five minutes for actual/attributed/unattributed comparison.
- Startup recovery prefers an NTFS USN Change Journal cursor. If unavailable, it reconciles the selected scope by metadata only, with visible progress and cancellation.
- File Activity is paged at 200 rows, supports collapsible category/folder groups, and filters by period, all/focus view, drive, category, event type, and path.
- CSV export runs in the background and reads matching rows in batches.
- Raw events default to 30 days, hourly summaries to one year, and daily summaries are retained long term.
- Diagnostics expose raw, processed, coalesced, dropped, and queued event counts, write rate, database size/estimated daily growth, and USN status.
- One main window hosts Overview, File Activity, and the five-section Settings page.
- Overview lists the files, folders, and categories responsible for the largest growth.
- Light, dark, and system themes switch at runtime, including the native Windows title bar.
- The floating card and mini pill show today's net space change and attribution ratio.
- English/Chinese UI, single instance, autostart, and configurable data paths.

## UI

Screenshots are generated off-screen from synthetic data. The renderer does not open Explorer or read the user's real database.

<p align="center">
  <img src="docs/main-dark-preview.png" alt="Dark main window" width="760" />
</p>
<p align="center"><sub>Dark theme: overview and attribution</sub></p>

<p align="center">
  <img src="docs/main-light-preview.png" alt="Light main window" width="760" />
</p>
<p align="center"><sub>Light theme: file activity</sub></p>

<p align="center">
  <img src="docs/settings-preview.png" alt="Settings" width="760" />
</p>
<p align="center"><sub>Settings: appearance and startup</sub></p>

<p align="center">
  <img src="docs/widget-preview.png" alt="Floating card" width="272" />
</p>
<p align="center"><sub>Floating card: today's space change and recent files</sub></p>

## Does scanning harm a drive?

DiskWatch never reads file contents. It reads directory entries and metadata such as size, timestamps, and attributes. Normal monitoring is event-driven and does not continuously walk a disk.

- NTFS startup recovery reads USN Journal metadata.
- When USN is unavailable, the default fallback is limited to common user folders; users may explicitly select all watched roots.
- Reconciliation runs at below-normal priority on Windows and can be cancelled from the main window.
- Startup recovery and fallback scanning can be disabled under Settings → Monitoring.
- Repeated writes are coalesced, and the activity table is paged.

A directory reconciliation scan can still cause seek activity on an HDD, so traditional whole-disk backfill is not the default.

See [performance and disk impact](docs/performance.md) for the synthetic 100,000-event benchmark and its safety boundaries.

## Privacy

- No file contents are read or uploaded.
- File names, paths, statistics, and the database are not sent over the network.
- Data defaults to `%APPDATA%\DiskWatch\diskwatch.db`.
- Configuration defaults to `%APPDATA%\DiskWatch\config.json`.
- Schema migration creates a consistent backup before changing the database.
- Explorer is launched only when the user explicitly asks to reveal a file.

## Quick start

Download the latest portable build from [Releases](https://github.com/niujiaxu/DiskWatch/releases/latest), extract it, and run `DiskWatch.exe`.

From source (Windows 10/11, Python 3.10+):

```bat
git clone https://github.com/niujiaxu/DiskWatch.git
cd DiskWatch
start.bat
```

The first run creates `.venv` and installs `PySide6` and `watchdog`.

## Platform status

The current desktop release targets Windows. The SQLite ledger, categorization, and most UI code are reusable; macOS and Linux require FSEvents/inotify monitoring adapters. NTFS USN is automatically unavailable there, so startup recovery uses the configured directory strategy.

## Development checks

```bat
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python.exe -m ruff check diskwatch tests
```

Native `QComboBox` is prohibited in the UI; all dropdown selections use `DayPicker`.

## License

[MIT](LICENSE) © DiskWatch contributors
