# DiskWatch

<p align="center">
  <a href="README.md">English</a> · <strong>中文</strong>
</p>

<p align="center">
  <strong>解释磁盘空间去了哪里。</strong><br/>
  <sub>全量记录普通文件的空间变化，本地归因，不读取文件内容。</sub>
</p>

## 它能回答什么

DiskWatch 同时观察文件变化和磁盘剩余空间，把两条数据线对在一起：

```text
C: 可用空间减少    8.4 GB
已归因             7.9 GB
未归因             0.5 GB
```

- **磁盘实际变化**：定时读取文件系统提供的剩余空间数值。
- **已归因变化**：创建、增长、缩小、删除和跨盘移动产生的真实字节差。
- **未归因变化**：实际变化与文件账本之间的差值。
- 同盘移动净变化为 0；跨盘移动在源盘记释放、目标盘记占用。

## 当前功能

- **全量采集**：普通缓存、临时文件、AppData 和开发产物不再直接丢弃，而是记录后分类。
- **安全边界**：始终排除 DiskWatch 自身数据库/WAL/日志、设备路径和非普通文件，避免反馈循环。
- **自动分类**：用户文件、下载、系统与更新、软件安装、应用缓存、临时文件、开发产物、虚拟机与容器、未分类；高级设置可添加优先匹配的路径规则。
- **空间变化账本**：保存旧大小、新大小、字节差、事件类型、分类、磁盘和时间。
- **连续写入合并**：同一路径停止写入约 6 秒后读取最终元数据，减少重复事件和 SQLite 写入。
- **磁盘采样**：每 5 分钟记录一次剩余空间，支持实际/已归因/未归因对照。
- **启动恢复**：NTFS 优先使用 USN Change Journal 游标；不可用时按所选范围补扫，且只读元数据；界面显示进度并可随时取消。
- **分页活动页**：每页最多 200 条，支持日期、全部/关注、磁盘、分类、事件类型、路径搜索，以及按分类/目录分组折叠。
- **CSV 导出**：按当前筛选条件在后台分批导出，不把全部数据一次载入内存。
- **分层保留**：原始事件默认 30 天；小时汇总一年；每日汇总长期保留。
- **运行诊断**：显示原始、已处理、合并、丢弃、队列数量、写入速率、数据库大小/预计每日增长和 USN 状态。
- **统一主窗口**：概览和文件活动使用同一窗口，托盘和悬浮组件都打开该窗口。
- **浅色/深色/跟随系统**：运行时即时切换，Windows 原生标题栏同步变化。
- **悬浮卡片与迷你胶囊**：显示今日净空间变化和归因率，而不是只显示文件数量。
- **中英双语、单实例、开机启动、可自定义数据库位置**。

## 界面

当前界面截图由仓库内的离屏渲染脚本生成，不会打开资源管理器，也不会读取真实用户数据库。

<p align="center">
  <img src="docs/main-dark-preview.png" alt="深色主窗口" width="760" />
</p>
<p align="center"><sub>深色主题：概览与空间归因</sub></p>

<p align="center">
  <img src="docs/main-light-preview.png" alt="浅色主窗口" width="760" />
</p>
<p align="center"><sub>浅色主题：文件活动</sub></p>

## 扫盘会伤硬盘吗

DiskWatch 不读取文件内容，只读取目录项、文件大小、时间和属性等元数据。正常实时监控由文件系统事件驱动，不会持续遍历整盘。

- NTFS 启动恢复只读取 USN Journal 元数据。
- USN 不可用时，默认仅补扫桌面、下载、文档、图片和视频等用户目录；也可明确改成全部监控根目录。
- 目录补扫在线程中以较低优先级运行，主窗口显示已扫描目录/文件数并提供取消按钮。
- 可在“设置 → 监控”关闭启动恢复或目录补扫。
- 大量解压或构建时会合并同路径修改；活动表固定分页，避免 UI 一次加载几十万行。

机械硬盘仍会因目录补扫产生寻道，因此默认不做传统全盘补扫。需要全盘对账时应在设置中明确选择监控范围。

合成 10 万事件基准、控制策略和适用边界见[性能与磁盘影响](docs/performance.md)。

## 隐私

- 不读取或上传文件内容。
- 不发送文件名、路径、统计或数据库到网络。
- 数据默认保存在 `%APPDATA%\DiskWatch\diskwatch.db`。
- 配置默认保存在 `%APPDATA%\DiskWatch\config.json`。
- 可在设置中迁移数据库，迁移 schema 前会创建一致性备份。
- “在资源管理器中定位”只在用户主动点击时调用 Windows Explorer。

## 快速开始

### 便携版

从 [Releases](https://github.com/niujiaxu/DiskWatch/releases/latest) 下载最新版，解压后运行 `DiskWatch.exe`。

### 从源码运行

要求 Windows 10/11、Python 3.10+：

```bat
git clone https://github.com/niujiaxu/DiskWatch.git
cd DiskWatch
start.bat
```

首次运行会创建 `.venv` 并安装 `PySide6` 与 `watchdog`。

## 平台说明

当前桌面发行版仍以 Windows 为目标。SQLite 账本、分类和大部分 UI 可复用；macOS/Linux 需要分别实现 FSEvents/inotify 监控适配器，NTFS USN 功能会自动不可用并使用目录恢复策略。

## 开发验证

```bat
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python.exe -m ruff check diskwatch tests
```

项目禁止在 UI 中使用原生 `QComboBox`，全部下拉选择统一使用 `DayPicker`。

## 许可证

[MIT](LICENSE) © DiskWatch contributors
