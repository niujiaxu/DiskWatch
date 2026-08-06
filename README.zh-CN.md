# DiskWatch

<p align="center">
  <a href="README.md">English</a> · <strong>中文</strong>
</p>

<p align="center">
  <strong>今天硬盘上新长了哪些文件、一共多大？</strong><br/>
  <sub>Windows 桌面悬浮组件 · 实时记录新增文件 · 数据只留本机 · 不联网</sub>
</p>

<p align="center">
  <a href="https://github.com/niujiaxu/DiskWatch/releases/latest"><img src="https://img.shields.io/github/v/release/niujiaxu/DiskWatch?style=for-the-badge&label=release" alt="release" /></a>
  <a href="https://github.com/niujiaxu/DiskWatch/stargazers"><img src="https://img.shields.io/github/stars/niujiaxu/DiskWatch?style=for-the-badge" alt="stars" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="license" /></a>
</p>

<p align="center">
  <a href="https://github.com/niujiaxu/DiskWatch/releases/latest"><strong>⬇ 下载便携版 .exe</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/niujiaxu/DiskWatch/releases/download/v1.2.1/DiskWatch-1.2.1-win64-portable.zip">v1.2.1 直链</a>
  &nbsp;·&nbsp;
  <a href="#-快速开始">快速开始</a>
</p>

---

## 界面一览

<p align="center">
  <img src="docs/widget-preview.png" alt="悬浮卡片" width="320" />
  &nbsp;&nbsp;
  <img src="docs/ball-preview.png" alt="迷你球" width="140" />
</p>

<p align="center"><sub>悬浮卡片 · 迷你球</sub></p>

<p align="center">
  <img src="docs/panel-preview.png" alt="详情面板" width="720" />
</p>

<p align="center"><sub>详情面板 — 按天、搜索、排序、按应用分组、事件类型、趋势图、导出</sub></p>

<p align="center">
  <img src="docs/settings-preview.png" alt="设置" width="560" />
</p>

<p align="center"><sub>设置 — 监控范围、过滤、外观、数据路径</sub></p>

---

## 为什么做它

安装包、解压、下载、AI 改工程……硬盘每天都在冒出**成百上千个你没空看的新文件**。

多数工具要么甩一长串变更列表（太吵），要么动手整理你的文件夹（太越界）。  
DiskWatch 只干一件事：

> **盯住「今天新建了什么」，摊在桌面上，把噪音挡在外面。**

不联网、不挪文件，只给你**看见**的能力。

| | 变更列表类 | 自动整理类 | **DiskWatch** |
|--|:--:|:--:|:--:|
| 实时捕获创建 | ✅ | ✅ | ✅ |
| 今日数量 + 体积 | ❌ | ❌ | ✅ |
| 悬浮卡片 / 迷你球 | ❌ | ❌ | ✅ |
| 智能降噪 | 弱 | — | ✅ |
| 本地历史 + CSV | ❌ | ❌ | ✅ |
| 会挪你的文件 | ❌ | ✅ | ❌ 绝不 |

---

## 亮点

- **实时监控** — 听 Windows 目录通知，不做全盘扫描
- **悬浮卡片** — 半透明科技蓝玻璃、可拖、可置顶，最近文件可滚动
- **迷你球** — 今日总大小 + 进度环（今日占近 7 天合计体积；悬停看占比）
- **详情面板** — 虚拟树表不卡；可勾选 **按应用分组**（同一路径根如 `Tencent Files` 收成一组）/ 按天 / 搜索（统计与表格一致）/ 排序 / 导出 / 资源管理器定位
- **事件类型切换** — 新增 / 已删除 / 全部，删除行标色并显示删除时间
- **趋势图** — 近 14 天每日新增数量迷你柱状图
- **右键菜单** — 打开 / 资源管理器定位 / 复制路径 / 复制文件名
- **中英双语** — 设置里改语言即时生效，无需重启
- **智能过滤** — 默认跳过 `AppData`、`Program Files`、缓存、`.git` / `.venv`…；一键应用开发目录过滤预设
- **启动补扫** — 程序没在跑期间的新文件，启动时按磁盘对账补回（目录 mtime 剪枝加速）
- **视觉** — 卡片 / 球 / 详情 / 设置统一冷科技蓝配色
- **单实例** — 再点一次启动会唤起已有界面
- **便携运行** — 解压即用；可选开机自启
- **隐私** — 数据在 `%APPDATA%\DiskWatch\`，零联网

---

## 快速开始

### 便携版（推荐）

1. 打开 [Releases](https://github.com/niujiaxu/DiskWatch/releases/latest) 下载最新包  
   或直链：[`DiskWatch-1.2.1-win64-portable.zip`](https://github.com/niujiaxu/DiskWatch/releases/download/v1.2.1/DiskWatch-1.2.1-win64-portable.zip)
2. 解压 → 运行 **`DiskWatch.exe`**
3. 完事。不用装 Python。

### 从源码跑

Windows 10/11 · Python 3.10+

```bat
git clone https://github.com/niujiaxu/DiskWatch.git
cd DiskWatch
start.bat
```

首次会自动建 `.venv` 并安装 `PySide6`、`watchdog`。

---

## 日常怎么用

| 你做什么 | 它做什么 |
|----------|----------|
| 拖动卡片 / 球 | 记住位置 |
| 卡片 **－** | 收成迷你球 |
| 单击迷你球 | 展开卡片 |
| 双击某条文件 | 资源管理器定位 |
| 托盘左键 | 显示 / 隐藏 |
| 托盘双击 | 打开详情 |
| 详情里勾选 **按应用分组** | 同一路径根下的文件收成一组 |

**过滤很重要：** 默认看不到 `Program Files` / `AppData` 里的噪音——故意的。  
需要看？去 **设置 → 过滤规则**。

---

## 占用（实测）

| 指标 | 大约（整盘 C:，空闲） |
|------|----------------------|
| 内存 | 约 100–110 MB（Qt）；纯监控约 24 MB |
| CPU | 单核约 0.6%–1.7% |
| 噪音 | 约 93% 事件被默认规则滤掉 |

---

## 觉得有用就点个 Star

如果 DiskWatch 帮你找回过「那个文件到底去哪了」，给仓库一个 ⭐，能让更多同样焦虑磁盘的人看见。

欢迎 Issue / PR。路线图：可选跟踪「修改」、更多语言等。

---

## 许可证

[MIT](LICENSE) © DiskWatch contributors
