# DiskWatch

<p align="center">
  <a href="README.md">English</a> · <strong>中文</strong>
</p>

<p align="center">
  <strong>今天硬盘上新长了哪些文件、一共多大？</strong><br/>
  <sub>Windows 桌面组件 · 实时新增文件监控 · 数据只留在本机</sub>
</p>

<p align="center">
  一张悬浮卡片 / 一颗迷你球，实时告诉你——<strong>不联网</strong>。
</p>

<p align="center">
  <a href="https://github.com/niujiaxu/DiskWatch/releases/latest"><img src="https://img.shields.io/github/v/release/niujiaxu/DiskWatch?style=flat-square&label=release" alt="release" /></a>
  <a href="https://github.com/niujiaxu/DiskWatch/stargazers"><img src="https://img.shields.io/github/stars/niujiaxu/DiskWatch?style=flat-square" alt="stars" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="license" /></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-green?style=flat-square" alt="python" />
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey?style=flat-square" alt="platform" />
</p>

<p align="center">
  <a href="https://github.com/niujiaxu/DiskWatch/releases/download/v1.1.1/DiskWatch-1.1.1-win64-portable.zip"><strong>⬇ 便携版 .exe（Windows x64）</strong></a>
  ·
  <a href="https://github.com/niujiaxu/DiskWatch/releases/download/v1.1.1/DiskWatch-1.1.1-src.zip">源码包</a>
  ·
  <a href="https://github.com/niujiaxu/DiskWatch/releases/tag/v1.1.1">发布说明</a>
</p>

<p align="center">
  <img src="docs/widget-preview.png" alt="悬浮卡片" width="280" />
  &nbsp;&nbsp;
  <img src="docs/ball-preview.png" alt="迷你球" width="120" />
</p>

---

## 为什么做 DiskWatch？

安装包、解压、下载、AI 编程助手……硬盘上每天都会冒出**成百上千个你根本没空看的新文件**。

多数工具要么甩出一长串变更列表（太吵），要么直接帮你整理文件夹（太越界）。  
DiskWatch 只做一件事：

> **盯住「今天新建了什么」，摊在桌面上，把噪音挡在外面。**

| | FolderChangesView 类列表 | 自动整理类工具 | **DiskWatch** |
|--|--|--|--|
| 实时捕获创建 | ✅ | ✅ | ✅ |
| 按天汇总（数量 + 体积） | ❌ | ❌ | ✅ |
| 悬浮卡片 / 迷你球 | ❌ | ❌ | ✅ |
| 智能过滤（AppData / 缓存 / PF） | 弱 | N/A | ✅ |
| 本地 SQLite 历史 + CSV 导出 | ❌ | ❌ | ✅ |
| 会挪动你的文件 | ❌ | ✅ | ❌ 绝不 |

一句话：**不是装完软件的流水账，也不是桌面整理器——是「今日新增文件」的桌面看板。**

---

## 功能

- **实时监控** — 通过 `watchdog` 听 Windows 目录通知（不做全盘扫描）
- **悬浮卡片** — 无边框、半透明、可拖动、可置顶；**最近列表可滚动**（滚轮 / 细滚动条，行控件复用）
- **迷你球** — 66px 小球显示**今日新增总大小**，进度环对比近 7 天峰值
- **详情面板** — 打开不卡悬浮窗；顶栏分两行不遮挡；置顶不被卡片盖住；按天查看 / 搜索防抖 / **按时间或大小排序** / 导出 CSV / 资源管理器定位
- **界面流畅** — SQLite 读写分离，后台入库不堵点击；配置延迟保存
- **智能过滤** — 默认跳过 `Windows`、`Program Files`、`AppData`、缓存、`.git` / `.venv`…
- **自定义数据路径** — 配置 / 数据库可放到任意盘（设置 → 数据）
- **深色界面** — Fusion 深色主题 + Windows 原生深色标题栏
- **托盘 + 开机自启** — 单实例；托盘菜单可 **重启** 整个程序
- **隐私** — 默认数据在 `%APPDATA%\DiskWatch\`，不联网

<details>
<summary>更多截图</summary>

**设置**

![设置](docs/settings-preview.png)

**详情面板**

![详情面板](docs/panel-preview.png)

</details>

---

## 快速开始

### 便携版（推荐）

1. 下载 [`DiskWatch-1.1.1-win64-portable.zip`](https://github.com/niujiaxu/DiskWatch/releases/download/v1.1.1/DiskWatch-1.1.1-win64-portable.zip)
2. 解压到任意目录 → 运行 **`DiskWatch.exe`**（或 `Start DiskWatch.bat`）
3. 无需安装 Python。配置 / 数据库默认仍在 `%APPDATA%\DiskWatch\`。

### 从源码运行

**环境：** Windows 10/11 · Python 3.10+（勾选 *Add to PATH*）

```bat
git clone https://github.com/niujiaxu/DiskWatch.git
cd DiskWatch
start.bat
```

或下载源码包 → 解压 → 双击 **`start.bat`** / **`启动.bat`**  
（首次运行会创建 `.venv` 并安装依赖，然后看托盘图标。）

手动：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\pythonw.exe run.pyw
```

依赖：`PySide6`、`watchdog` — 见 [`requirements.txt`](requirements.txt)。

自行打包便携版：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
```

---

## 使用说明

| 操作 | 效果 |
|------|------|
| 拖动卡片 / 迷你球 | 移动（位置分别记住） |
| 卡片 **－** | 收成迷你球 |
| 卡片 **✕** | 隐藏（托盘可再唤出） |
| 单击迷你球 | 展开卡片 |
| 滚动卡片「最近」列表 | 浏览更多今日文件（最多约 24 条） |
| 详情表头 | 升降序（时间 / 大小按真实数值，不是显示文字） |
| 双击文件行 | 在资源管理器中定位 |
| 托盘左键 | 显示 / 隐藏（记住卡片或球） |
| 托盘双击 | 打开详情面板 |
| 托盘 → **重启** | 整程序重新拉起（便携版 / 源码均可） |
| 托盘 → **重新开始监控** | 只重启文件监控 |

### 过滤很重要

默认**看不到** `Program Files`、`AppData`、常见缓存里的写入——这是故意的。  
不然装一次软件就能冲进上万条垃圾记录。

需要看这些路径？到 **设置 → 过滤规则** 删掉对应行再保存。  
只影响之后的事件。

数据默认位置：

```
%APPDATA%\DiskWatch\
  config.json
  diskwatch.db
  location.json   # 仅在你改过路径时存在
```

可在 **设置 → 数据** 改两个路径。AppData 里会留一个很小的 `location.json`，下次启动还能找到文件。默认保留 **90 天**。

---

## 占用（实测）

整盘 C:，约 20 分钟稳态：

| 指标 | 大约 |
|------|------|
| 工作集 | 100–110 MB（Qt 为主；纯监控约 24 MB） |
| 空闲 CPU | 单核 0.6%–1.7% |
| 事件 | 约 3–10 次/秒，约 93% 被过滤 |

突发：约 2.6 秒内 3000 个新文件 —— 都能抓住。  
装进 Program Files 的常规安装，在默认过滤下不会进库。

---

## 目录结构

```
DiskWatch/
├── start.bat / 启动.bat
├── run.pyw / run_portable.py
├── DiskWatch.spec      # PyInstaller
├── diskwatch/          # 程序
├── docs/               # 截图
├── tests/              # 自测
├── README.md           # English
├── README.zh-CN.md     # 中文
└── scripts/
    ├── make_release.ps1
    └── build_portable.ps1
```

```bat
.venv\Scripts\python.exe tests\smoke_test.py
.venv\Scripts\python.exe tests\ui_state_test.py
.venv\Scripts\python.exe tests\perf_test.py 60
```

---

## 路线图

- [ ] 可选跟踪「修改」（不只创建）
- [x] 便携版 `.exe`（PyInstaller）
- [ ] 英文界面文案

欢迎提 Issue / PR。如果 DiskWatch 帮你找回过「那个文件到底去哪了」，给个 ⭐ 能让更多人看见。

---

## 许可证

[MIT](LICENSE) © DiskWatch contributors
