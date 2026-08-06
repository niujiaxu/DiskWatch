# DiskWatch

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">中文</a>
</p>

<p align="center">
  <strong>See what landed on your disk today.</strong><br/>
  <sub>A tiny Windows desktop widget that tracks newly created files — in real time, locally, quietly.</sub>
</p>

<p align="center">
  <a href="https://github.com/niujiaxu/DiskWatch/releases/latest"><img src="https://img.shields.io/github/v/release/niujiaxu/DiskWatch?style=for-the-badge&label=release" alt="release" /></a>
  <a href="https://github.com/niujiaxu/DiskWatch/stargazers"><img src="https://img.shields.io/github/stars/niujiaxu/DiskWatch?style=for-the-badge" alt="stars" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="license" /></a>
</p>

<p align="center">
  <a href="https://github.com/niujiaxu/DiskWatch/releases/latest"><strong>⬇ Download portable .exe</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/niujiaxu/DiskWatch/releases/download/v1.2.1/DiskWatch-1.2.1-win64-portable.zip">v1.2.1 zip</a>
  &nbsp;·&nbsp;
  <a href="#-quick-start">Quick start</a>
</p>

---

## Demo

<p align="center">
  <img src="docs/widget-preview.png" alt="Floating card" width="320" />
  &nbsp;&nbsp;
  <img src="docs/ball-preview.png" alt="Mini ball" width="140" />
</p>

<p align="center"><sub>Floating card · Mini ball</sub></p>

<p align="center">
  <img src="docs/panel-preview.png" alt="Detail panel" width="720" />
</p>

<p align="center"><sub>Detail panel — by day, search, sort, group by app, event types, trend chart, export</sub></p>

<p align="center">
  <img src="docs/settings-preview.png" alt="Settings" width="560" />
</p>

<p align="center"><sub>Settings — scope, filters, appearance, data paths</sub></p>

---

## Why this exists

Installers, zip extracts, downloads, AI coding agents… Windows creates **thousands of files** you never consciously look at.

Most tools either dump a noisy change list, or start rearranging your folders.  
DiskWatch does **one** job:

> **Track “what was created today”, put it on your desktop, filter the junk.**

No cloud. No file moving. Just visibility.

| | Change-list tools | Auto-organizers | **DiskWatch** |
|--|:--:|:--:|:--:|
| Real-time creates | ✅ | ✅ | ✅ |
| Today’s count + size | ❌ | ❌ | ✅ |
| Desktop card / mini ball | ❌ | ❌ | ✅ |
| Smart noise filters | weak | — | ✅ |
| Local history + CSV | ❌ | ❌ | ✅ |
| Moves your files | ❌ | ✅ | ❌ never |

---

## Highlights

- **Live monitoring** — Windows directory notifications (not a full-disk scan)
- **Floating card** — translucent tech-blue glass, draggable, always-on-top, scrollable recent list
- **Mini ball** — today’s size + ring showing today’s share of the last 7 days’ volume (hover for %)
- **Detail panel** — virtualized tree/table, optional **group by app** (same path root → one folder), day switch, search that matches the stats, sort by time/size, CSV export, jump to Explorer
- **Event types** — Added / Deleted / All; deleted rows are dimmed and show deletion time
- **Trend chart** — 14-day mini bar chart of daily new-file counts
- **Context menu** — open, reveal in Explorer, copy path, copy name
- **Bilingual** — Chinese / English, switch takes effect instantly without restart
- **Smart filters** — skips `AppData`, `Program Files`, caches, `.git` / `.venv`… by default; one-click dev-folder preset
- **Startup backfill** — files created while the app was off are reconciled at launch (accelerated by directory mtime pruning)
- **Look** — unified cool tech-blue palette across card, ball, detail, and settings
- **Single instance** — launch again → brings the UI forward
- **Portable** — unzip and run; optional autostart from tray
- **Private** — SQLite under `%APPDATA%\DiskWatch\`, zero network

---

## Quick start

### Portable (recommended)

1. Grab the latest build from [Releases](https://github.com/niujiaxu/DiskWatch/releases/latest)  
   or direct: [`DiskWatch-1.2.1-win64-portable.zip`](https://github.com/niujiaxu/DiskWatch/releases/download/v1.2.1/DiskWatch-1.2.1-win64-portable.zip)
2. Unzip → run **`DiskWatch.exe`**
3. Done. No Python required.

### From source

Windows 10/11 · Python 3.10+

```bat
git clone https://github.com/niujiaxu/DiskWatch.git
cd DiskWatch
start.bat
```

First run creates `.venv` and installs `PySide6` + `watchdog`.

---

## Everyday use

| You do | It does |
|--------|---------|
| Drag card / ball | Remembers position |
| Card **－** | Collapse to mini ball |
| Click ball | Expand card |
| Double-click a file | Reveal in Explorer |
| Tray left-click | Show / hide |
| Tray double-click | Open detail panel |
| Detail **按应用分组** | Collapse same-day files under one path root |

**Filters matter:** by default you won’t see `Program Files` / `AppData` noise — on purpose.  
Need those paths? **Settings → Filters**.

---

## Footprint

| Metric | Approx. (whole C:, idle) |
|--------|--------------------------|
| RAM | ~100–110 MB (Qt); monitor-only ~24 MB |
| CPU | ~0.6%–1.7% of one core |
| Noise | ~93% of events filtered by default |

---

## Star if it helped

If DiskWatch ever answered *“where did that file go?”* for you, a ⭐ helps other Windows users find it.

Issues and PRs welcome · roadmap includes optional “modified” tracking and more languages.

---

## License

[MIT](LICENSE) © DiskWatch contributors
