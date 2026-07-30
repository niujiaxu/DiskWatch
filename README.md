# DiskWatch

**Windows 桌面悬浮组件：实时记录硬盘上每天新增了哪些文件。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey.svg)](#)

<p align="center">
  <img src="docs/widget-preview.png" alt="浮动卡片" width="280" />
  &nbsp;
  <img src="docs/ball-preview.png" alt="迷你球" width="120" />
</p>

---

## 它解决什么问题

装软件、解压、下载、AI 改工程……硬盘上每天都会冒出大量新文件。  
DiskWatch 常驻托盘，用一张小卡片（或一颗迷你球）告诉你：

- **今天新增了多少个文件、一共多大**
- **最近落盘的是哪些**（双击即可在资源管理器中定位）
- **按日期翻历史、搜索、导出 CSV**

数据只存在本机 SQLite，**不联网**。

---

## 功能一览

| 能力 | 说明 |
|------|------|
| 实时监控 | 基于 Windows `ReadDirectoryChangesW`（watchdog），不用定时扫盘 |
| 浮动卡片 | 无边框、半透明、可拖动、可置顶 |
| 迷你球 | 收成 66px 圆球，显示今日总大小 + 近 7 天体积峰值进度环 |
| 详情面板 | 按日切换、关键字过滤、排序、导出 CSV |
| 智能降噪 | 默认排除系统目录、AppData、缓存、点号目录（`.git` / `.venv` 等） |
| 托盘 | 显隐组件、开机自启、单实例运行 |

设置界面：

![设置](docs/settings-preview.png)

详情面板：

![详情](docs/panel-preview.png)

---

## 系统要求

- Windows 10 / 11
- Python **3.10+**（安装时勾选 *Add python.exe to PATH*）

---

## 安装与启动

### 方式一：源码（推荐）

1. 下载本仓库，或：

```bat
git clone https://github.com/<你的用户名>/DiskWatch.git
cd DiskWatch
```

2. 双击 **`start.bat`**（或 `启动.bat`）  
   首次会自动创建 `.venv` 并安装依赖，之后直接启动。

3. 右下角托盘出现图标即表示已运行。

### 方式二：手动

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\pythonw.exe run.pyw
```

### 依赖

见 [`requirements.txt`](requirements.txt)：

- `PySide6` — 界面
- `watchdog` — 文件系统监控

---

## 使用说明

| 操作 | 效果 |
|------|------|
| 拖动卡片 / 球 | 移动位置（各自记住） |
| 卡片右上角 **－** | 收成迷你球 |
| 卡片右上角 **✕** | 隐藏（托盘可再唤出） |
| 单击迷你球 | 展开回卡片 |
| 双击列表中的文件 | 资源管理器定位 |
| 托盘左键 | 显隐（记住上次是卡片还是球） |
| 托盘双击 | 打开详情面板 |

### 过滤规则（很重要）

默认**看不到**写入 `Program Files`、`AppData`、缓存目录的文件——这是故意的，否则装一次软件就可能刷出上万条噪音。

想看安装过程或缓存增长：打开 **设置 → 过滤规则**，删掉对应行后保存。  
改规则只影响之后的事件，过去被滤掉的找不回来。

### 数据位置

```
%APPDATA%\DiskWatch\
  config.json      设置
  diskwatch.db     记录（SQLite）
```

默认保留 **90 天**。可在设置里清空或缩短保留期。

---

## 资源占用（实测）

监控整个 C 盘、稳定运行约 20 分钟后：

| 指标 | 大约 |
|------|------|
| 内存（工作集） | 100–110 MB（其中 Qt 占大头；纯监控约 24 MB） |
| CPU（空闲） | 0.6%–1.7% 单核 |
| 常态事件 | 约 3–10 个/秒，约 93% 被过滤 |

一次写入 3000 个文件可全部捕获；正常装到 Program Files 的软件默认不会进库。

---

## 项目结构

```
DiskWatch/
├── start.bat / 启动.bat   一键启动
├── run.pyw                无控制台入口
├── requirements.txt
├── LICENSE                MIT
├── CHANGELOG.md
├── diskwatch/             应用源码
│   ├── app.py             托盘与生命周期
│   ├── watcher.py         监控与入库
│   ├── storage.py         SQLite
│   ├── filters.py / config.py
│   └── ui/                卡片、迷你球、面板、设置
├── docs/                  截图
└── tests/                 自测脚本
```

---

## 开发与自测

```bat
.venv\Scripts\python.exe tests\smoke_test.py
.venv\Scripts\python.exe tests\ui_state_test.py
.venv\Scripts\python.exe tests\perf_test.py 60
.venv\Scripts\python.exe tests\render_preview.py
```

打源码发布包（不含 `.venv`）：

```bat
powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1
```

产物在 `release/` 目录。

---

## 实现要点（简述）

- **洪峰**：过滤在 watchdog 回调里只做字符串匹配；入库走有界队列批量写库。
- **0 字节**：新建文件先记后补体积（settle 线程）。
- **重命名**：以 `on_moved` 的目标路径为准，避免临时名污染。
- **过滤版本号**：升级默认规则时自动刷新一次用户配置中的规则表。

---

## 路线图（可选）

- [ ] 可选监控「修改」而不只是「新增」
- [ ] 无安装包 / 便携版（PyInstaller）
- [ ] 多语言界面

欢迎 Issue / PR。

---

## License

[MIT](LICENSE)
