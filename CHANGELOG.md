# Changelog

All notable changes to DiskWatch are documented in this file.

## [2.0.6] - 2026-08-30

### Changed
- **启动补扫大幅提速（约 6 倍）**：实测 29 万目录从 187.7 秒（1544 目录/秒）降到 30.5 秒（9505 目录/秒）。做法：多线程并行遍历（6 个工作线程，目录元数据系统调用可重叠 I/O 延迟）、目录 mtime 改取父目录 scandir 枚举缓存（原先每个目录单独 stat 一次，占 39% 时间）、去掉“每 128 个条目 sleep 1ms”的让出（240 万条目要睡 19 秒）
- 主窗口可自由拖边缘缩放：设置页嵌入时改用滚动区兜底、活动页筛选区与详情卡放宽下限，窗口最小尺寸由 956×746 降到 760×558
- 概览图表随窗口等比放大/收缩：柱状图、累计面积、剩余空间折线不再写死高度，横向条形行高自适应，拉大或最大化窗口时卡片内元素填满可用空间
- 文件活动页右侧详情卡随窗口宽度按比例缩放（约 26%，夹在 220–460 之间）

### Removed
- 移除已废弃的旧详情面板 `DetailPanel` 与分组模块 `grouping.py`（主窗口改版后应用不再使用，仅测试还在引用），同步删除对应测试
- 清理 59 个已无引用的翻译键（旧详情面板/看板遗留），并把 `关闭/最小化/最大化/还原` 四个缺失的键补进英文翻译；i18n 覆盖检查改为扫描全部 `diskwatch/**/*.py`
- 测试套件收尾时自动清理临时目录：42 处 `mkdtemp` 在 Windows 上不会自动回收，实测累积 4800+ 个目录 / 1GB+；清理前缀统一覆盖 `dw_*`、`diskwatch_test_*`（旧前缀，另清掉 146 个存量）、`diskwatch-preview-*`，并把 pytest 的 `tmp_path` 保留策略设为 `none`（不再累积 `pytest-of-*`）

### Fixed
- 修复补扫结束后底部仍显示“正在补扫：N 个目录”不动（看着像卡死）：收尾原先只在 `scan_strip.isVisible()` 为真时执行，而补扫结束的瞬间如果主窗口是隐藏的（只看悬浮卡片/最小化）它永远是 False，strip 便保留最后一条进度文案，下次打开主窗口就“卡”在那里。现在收尾无条件执行
- 测试不再污染用户环境：`ui_state_test` 的完整 App 夹具改用临时日志目录并关闭启动恢复（此前测试进程会往用户真实日志写入、还会读真实磁盘做 USN 恢复）
- 修复补扫**看似“很慢/卡住”**实为收尾卡死的问题：并行工作线程的 `pending` 计数在“收尾写库抛异常”时会被跳过，其它线程于是永远等不到归零、补扫线程不返回，界面永远停在“正在补扫：10,143 个目录”。实测同范围扫描本身只要 0.7 秒。现在写库失败也保证递减计数；写库等待加 10 秒上限（超时跳过本批、下次补扫仍能发现）；刷新悬浮组件出错也不再阻止“正在补扫”收尾
- 启动恢复的耗时写入日志（USN 恢复与目录补扫各一行 INFO），便于定位“到底慢在哪一步”
- 复选框选中态改为“填充主题色 + 白色对勾”：此前只把边框改色、白勾画在空心框上，浅色主题下完全看不见，看起来只是“变了下颜色”
- 概览柱状图柱顶数字的**下半截不再被裁**（"+18.05G" 只剩上半截）：标签矩形高度写死 8px，比字体 ascent（约 10-11px）还矮，drawText 按矩形裁剪把下半截切掉了；改为按字体行高，并垂直居中
- 柱顶数字不再整根柱子缺失：标签宽度改为文字实际宽度（之前固定 bw+48，排得下的相邻标签也被判碰撞丢弃），首/末柱的标签还会夹进控件内不被窗口边缘裁掉
- 概览“24 小时磁盘剩余空间”轴标签精确到秒（此前带一串微秒，如 08:28:03.581034）
- 修复“取消补扫”仍会长时间卡在“正在取消”的根因：启动清理（purge）此前把所有过期数据放在一个大事务里删（实测 80 万行库里删 40 万行持写锁 3.5 秒），补扫的 flush 只能干等这把写锁、期间检查不到取消标志。现在 purge 分批（每批 4000 行、各自小事务），补扫在已请求取消时也直接跳过收尾写库（记录下次补扫还能再发现）——实测端到端取消延迟 0.15 秒
- 主窗口边缘缩放再加一道兜底：若系统没把边缘按下当作非客户区（窗口失活等状态下会这样），Qt 层按位置主动调用 startSystemResize 发起系统缩放，避免“有箭头但拖不动”
- 修复主窗口边缘“有箭头但拖不动”：边缘命中测试（WM_NCHITTEST）改到应用级 native filter 里用原生 API（lParam + GetWindowRect）计算，不再依赖 Qt 的窗口几何/光标状态；并增加 2 秒兜底自愈 WS_THICKFRAME（个别路径下 Qt 会重置窗口样式，丢了样式就进不了缩放循环）
- 修复主窗口边缘缩放光标“时有时无”（刚打开正常、页面数据加载后消失）：鼠标压在概览图表等带手型光标的子控件上时，Qt 会按控件光标把缩放光标覆盖回箭头；改为应用级 native event filter，在 Qt 光标逻辑之前接管 WM_SETCURSOR
- 修复无边框主窗口边缘拖拽不能缩放的问题：仅返回 WM_NCHITTEST 命中码只会改变边缘光标，真正按下拖拽需要窗口带 WS_THICKFRAME 才会进入系统缩放循环；现补上该样式并用 WM_NCCALCSIZE 把客户区保持为整窗（外观不变，最大化仍不盖任务栏）
- 优化“取消补扫”的响应：补扫改为每个条目都检查取消标志（此前 mtime 剪枝分支完全没有检查，遇到大旧目录要等整目录扫完），取消时把被降过优先级的补扫线程临时提回正常（系统繁忙时不再卡在“正在取消”）
- USN 启动恢复阶段现在也可取消（此前点取消要等整个 Journal 恢复完），被打断的磁盘不推进游标、下次启动重放；恢复写库改为攒批事务，Journal 大时快很多
- 修复主窗口拖边缘缩放时光标不变成缩放箭头的问题：Qt 处理 WM_SETCURSOR 会把无边框窗口边缘的光标改回箭头，现一并接管 WM_SETCURSOR，按命中码显式设置水平/垂直/对角缩放光标
- 修复调整悬浮组件透明度后胶囊四角出现系统阴影圆弧的问题：无边框自绘窗不再被套用 DWM 圆角/标题栏属性，并显式声明 NoDropShadowWindowHint 抑制矩形轮廓投影
- 修复文件活动页偶发沿用初始化分栏宽度、右侧留下大块空白的问题
- 限制超长文件名和路径的横向尺寸提示，避免详情卡撑乱整体布局
- 窗口置前时保留最大化状态，减少页面切换和窗口恢复之间的布局竞争

## [2.0.5] - 2026-08-30

### Fixed
- 全局下拉弹层按实际选项高度收缩，并限制在宿主窗口边界内，避免空白区域遮挡后续控件
- 每日变化柱状图改为自适应铺开并自动隐藏碰撞标签，避免数值文字互相覆盖
- 文件活动筛选器与操作区拆分为两行，避免最小窗口和高 DPI 下控件挤压
- 多磁盘图例改为横向换行并为折线保留绘图区，横向排行按实际像素省略长文本

## [2.0.4] - 2026-08-30

### Added
- 文件活动表支持点击“空间影响”列，在全量分页数据上切换数值降序和升序排列
- CSV 导出沿用当前空间影响排序，表头显示当前排序方向

## [2.0.3] - 2026-08-30

### Changed
- 主窗口改为跟随浅色/深色主题的自绘标题栏，统一应用图标、背景、边框与窗口控制按钮
- 自绘标题栏保留拖动、双击最大化、最小化、还原、关闭和 Windows 边缘缩放
- 设置页导航统一为圆角浅色选中态，移除 Qt 原生虚线焦点框并保留键盘导航

## [2.0.2] - 2026-08-30

### Fixed
- 文件活动页显示时不再重复重建已经加载好的 200 行表格
- 表格批量更新时暂停重绘与选择信号，并移除逐项触发的自动列宽测量
- 启动补扫改为流式遍历大目录并定期让出执行时间，避免与 Qt 主线程争抢导致窗口长时间无响应

## [2.0.1] - 2026-08-30

### Fixed
- 打开文件活动或切换记录时，跨重命名历史查询因 `old_path` 未索引而反复全表扫描，导致窗口与按钮假死
- 详情历史、分页计数、筛选和翻页改为后台读取；慢查询期间保留现有表格和复制等交互
- 数据库关闭不再跨线程强制关闭仍在查询的 SQLite 连接，避免 Python 3.14/Windows 原生 access violation
- “在资源管理器中定位”改为快速拉起独立 Explorer 进程，不再在 UI 线程同步解析离线或网络路径

## [2.0.0] - 2026-08-29

### Added
- 全量空间变化账本：创建、增长、缩小、删除、同盘移动和跨盘移动均记录旧大小、新大小、字节差、分类、磁盘与时间
- 磁盘剩余空间 5 分钟时间序列，以及“实际变化 / 已归因 / 未归因”对照
- 文件活动页：日期、全部/关注、磁盘、分类、事件类型、搜索、按分类/目录分组折叠、200 条分页、详情和后台分批 CSV 导出
- NTFS USN Change Journal 启动恢复：保存每盘 Journal ID/USN 游标，Journal 不可用时受控补扫用户目录
- schema v3 分层数据：原始事件默认 30 天、小时汇总一年、每日汇总长期保留
- 浅色、深色和跟随系统主题，支持运行时切换和 Windows 原生标题栏同步
- 统一主窗口内嵌概览、文件活动和左侧五分区设置；事件队列/数据库大小/USN 状态诊断
- 启动补扫进度与取消、低优先级运行、用户目录/全部监控根目录范围选择
- 文件详情跨重命名大小历史、自定义路径分类规则、数据库写入速率/每日增长估算与持续高负载告警
- 概览显示近 24 小时真实磁盘采样及各盘当前剩余空间
- 概览新增占用增长最大的文件排行
- 悬浮卡片和迷你胶囊改为显示今日净空间变化及归因率
- 迁移前 SQLite 一致性备份，以及独立线程读连接池
- 不接触真实配置和磁盘的 10 万条合成性能探针与开发机基线
- 详情面板全量显示：取消 2500 条上限，当天全部记录一次载入
- 数据看板窗口：增长趋势（体积/数量）、累计增长、磁盘剩余空间、TOP 目录、TOP 文件类型，7/14/30/90 天范围可切，点增长柱可跳详情；详情面板标题行新增「数据面板」入口
- 详情趋势图：默认对数刻度（小值柱清晰可辨），可切换线性；柱顶显示体积/数量简写

### Fixed
- UI 测试不再调用真实资源管理器；测试配置显式隔离真实用户过滤规则
- 看板后台查询与数据库关闭并发时可能触发 Python 3.14 原生 access violation
- 连续修改事件按路径合并，避免 watchdog 队列被持续写入淹没
- 看板 TOP 条形图 hover/tooltip 完全失效（命中判断的 x 坐标永远落在行矩形外）
- 详情面板目录行右键无菜单（组行因缺 path 恒提前 return）；组行右键/双击展开统一归一到列 0，避免 expandedIndexes 按列区分导致无法折叠
- 目录整体删除事件被忽略，被删子树的文件行残留为幽灵记录：新增 dir_del 事件与 storage.delete_subtree 按前缀物理删除
- 单文件移动落回曾标删路径时 deleted_at 未清空（复活行残留旧删除时间戳）
- 迷你球隐藏期间写入量不刷新，重新显示时误触发一次"新增"闪烁
- 每小时清理线程对象无上限累积；路径迁移前未汇合后台线程
- 配置保存失败（磁盘满/只读）静默无提示，改为记录日志
- 趋势图柱顶数字"显示不全"：体积简写原来用取整格式（1.2MB 显示成 1M、3.5MB 显示成 4M），改为保留有效精度（1.2M / 3.5M / 30M），不再是舍入丢小数
- 趋势图柱顶数字被遮挡：柱顶标签原在柱循环内绘制，矮柱标签会被后画的相邻高柱柱身盖住；改为全部柱画完后统一置顶绘制
- 详情面板双击目录无反应 / 只展开不能折叠：Qt 真实双击序列下 pressedIndex 被 release 清空，doubleClicked 信号奇偶次错位。改为子类化 QTreeView 覆写 mouseDoubleClickEvent，组行双击直接展开/折叠，文件行双击走 row_double_clicked，完全绕开 Qt 双击时序机制
- 详情面板双击目录仅时间列可收缩：expandedIndexes 按 (row, column) 区分，跨列双击时 isExpanded 查不到展开状态。组行双击统一归一到列 0 再判断展开/折叠
- 停止/重启时最后一批写入可能丢失：monitor.stop() 现在真正汇合后台消费线程（含最终 flush）后才返回，杜绝线程写已关闭连接
- 重启失败路径丢失单实例锁：Popen 成功后才 detach 锁，失败时保持持有，避免双实例同时监控
- 退出/重启前汇合补扫与清理线程（超时 5s），避免它们写已关闭的库
- 切换文件位置失败时 UI 组件仍引用已关闭连接：失败回滚现在把悬浮卡片/迷你球/详情面板/看板统一 rebind 到新连接
- 详情面板排序/分组重编译与自动刷新交叉时可能把旧快照结果应用到新数据：编译结果按记录集身份校验，过期即丢弃
- 配置损坏防御：widget_pos/ball_pos 非数值、widget_opacity 非数字、filter_version 非整数均不再导致启动崩溃或设置对话框打不开
- mark_deleted 对盘符根路径不再展开子路径匹配（防止误标记整盘删除）
- fetch_day_view 的 limit 截断改为多取一条探测，恰好等于 limit 时不误报截断、不丢记录
- 空操作写事务不再虚假推进数据版本（change_seq），避免多余的重载
- 配置保存序列化加锁，消除 save_soon 后台线程与主线程并发改 dict 导致的保存丢失

### Changed
- 代码结构整理：DayPicker 拆到 ui/picker.py，自绘图表组件（柱状图/累计面积/多折线/横向条形）拆到 ui/charts.py，详情面板与看板共用
- storage 提取公共辅助：_day_of（日归属）、_like_escape（LIKE 转义）、_event_where（事件过滤片段）、_relocate_row（移动行，顺带修正跨盘移动时 drive 列不更新）、_query_day_summaries（主线程/后台共用同一天聚合查询）
- 详情面板提取 _apply_compiled / _day_picker_text / _restore_status_from_meta，消除三处重复块；重启与退出共用 _shutdown 关闭序列；开机自启与重启共用 launch_args() 启动命令探测
- 启动补扫复用 filters.safe_stat，删除重复的 _dir_mtime；清理 config 无引用的历史别名
- 详情面板大表不卡顿：排序/分组编译移入后台线程（主线程仅做模型重置，10 万条从 ~8s 降到 ~4ms），自动刷新按数据版本脏检查跳过无变化重载，刷新/排序保持滚动位置
- 分组计算按目录聚合：同一天数万条记录时分组耗时降低约一个数量级
- 按天取数新增 (day, added_at) 复合索引


## [1.2.1] - 2026-08-06

### Fixed
- 补扫 mtime 剪枝漏文件：旧目录只跳过直接文件 stat，子目录仍深入（曾整体剪枝丢深层新文件）
- 下拉选择弹层错位到左上角：全部 QComboBox（事件过滤器 / 语言 / 预设）改用自绘 DayPicker
- 勾选框样式：填充块改为空心圆角框 + 勾号
- 语言热切换漏刷新：状态行 / tooltip 切换后仍显示旧语言
- 设置「应用模板」按钮崩溃（引用不存在的 _fill_filters）
- 语言与数据路径同时修改时路径变更被吞掉

### Changed
- 趋势图改为按新增体积统计：渐变圆角柱、悬浮显示「日期 · 体积 · 文件数」、首尾日期轴标签
- 约束固化：下拉选择禁用 QComboBox（AGENTS.md + AST 自动检查测试）

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
