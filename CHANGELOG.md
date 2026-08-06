# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.10.0] - 2026-08-06

### Features

- **界面缩放（DPI）配置** - 设置页新增「界面缩放」选项，支持自动（跟随系统）+ 100%-300% 固定倍率；应用启动时读取，高 DPI 下文字/控件不再模糊。`_apply_dpi_scale` 在 QApplication 构造前应用 `PassThrough` 取整策略或 `QT_SCALE_FACTOR` 环境变量。

### Fixes

- **固件烧录页 J-Link 设备不显示** - 修复烧录页枚举不出 J-Link 设备（RTT 监控页却正常）的问题。RTT worker emit 三段格式 `kind|serial|product`，烧录页旧用 `partition("|")` 只切一次把 kind 当 serial、`isdigit()` 再全部过滤；改为三段解析 + kind 过滤，与 RTT 页同形态。

## [0.9.0] - 2026-07-29

### Features

- **🔐 STM32 选项字读写保护** - 新增 RDP 读保护 + WRP 写保护功能，数据驱动覆盖 71 颗芯片 / 19 家族；烧录流程支持自动解除/添加读写保护，无需安装 CubeProgrammer。
- **🔌 RTT 监控支持 ST-Link/CMSIS-DAP** - 不再限于 J-Link，ST-Link/CMSIS-DAP 调试器也可进行 RTT 实时监控。
- **🚀 CMSIS-Pack 目标枚举大幅提速** - 首次进入烧录页目标设备列表加载约 4s -> 0.5s。

### Fixes

- **🔍 搜索/替换 emoji 位置错位修复** - 修复 emoji 等非 BMP 字符搜索高亮选中错误文本的问题；新增 VSCode 风格当前匹配橙色高亮。
- **🎨 修复 Pack 管理页字体大小** - 标题控件字号现已正确跟随全局界面字体设置。

### Performance

- **⚡ 页面懒加载优化** - 首次切换到各功能页时立即显示骨架，不再阻塞主线程。

## [0.8.0] - 2026-07-25

### Features

- **📦 CMSIS-Pack 管理页面** - 新增独立页面管理 CMSIS-Pack：下载/迁移/查重 + 目标设备实时刷新；`cmsis_pack_manager.Cache` 的 `json_path` 与 `data_path` 一致性保证 download 落盘正确；pack 增删后 `get_pyocd_target_infos` 的 `functools.cache` 失效（避免目标列表要重启才刷新）；`download_pack` 返回 `skipped`/`downloaded`/`failed` 三态区分；结果提示用 `_infobar` 气泡（TOP），不被表格遮挡；已安装/下载卡片撑满页面垂直空间。
- **🔌 多烧录器烧录** - 支持 J-Link + ST-Link/CMSIS-DAP via pyOCD 烧录；FlashWorker backend 按 `FlashParams.burner_kind` 动态创建（`make_backend`），不预建固定 jlink；pyOCD `target_override` 支持 CMSIS-Pack `part_number` 'x' 封装通配匹配；pyOCD SWD 通信失败给出接线排查提示 + 连接后校验 DP IDCODE；`mass_erase` 前先 `halt()`（DAPLink "IPSR=3" 偶发失败）。
- **🎯 多 J-Link + 目标设备增强** - 多 J-Link 按 serial 连接 + auto_reconnect 串行匹配（`SerialNumber` 是唯一稳定 ID）；目标设备自动发现，下拉限制 8 条 + 页面历史；cfg serial 与在线设备不匹配时保持离线占位 + 红点，不自动切其它在线设备；烧录器下拉 label 单点真源（`_burner_label(kind, serial, product)`）。
- **📁 固件变化检测 + 自动烧录** - 固件变化检测改用 QFileSystemWatcher（替代 mtime 轮询），变化指示重设计，支持检测到变化后自动烧录。
- **🗑️ 全片擦除** - 烧录页新增全片擦除功能，复用烧录流程（`erase_only` 标记贯穿），不另写一套。
- **📊 紧凑 Flash 占用条** - 烧录页新增紧凑 Flash 占用条；修复 Flash 容量/地址数据错误（用 `supported_device` 的 `aFlashArea` Size 最大区域，不信 legacy `FlashAddr/FlashSize`）。
- **💾 控件状态持久化** - RTT/Flash 页面控件状态跨重启保留 + 日志记录提示 + 固件文件选择修正（`EditableComboBox.setCurrentText(不在 items 的文本)` 是 no-op 的坑）。
- **⏱️ 自动断帧阈值持久化** - 自动断帧空闲阈值（`auto_frame_timeout`）跨重启保留。

### Performance

- **🚀 启动速度优化（dev 2.97s -> 1.79s，-39.8%）**
  - **pyocd 预热后台化** - `prepare_pyocd_for_flash(background=True)` 在主线程 PySide6 import 后立即起 daemon 线程预热，dev 直接 python 启动 2.97s -> 2.52s。
  - **平台缓存后台预热** - daemon 线程预调 `platform.release()` / `platform.version()` 填 win32_ver 缓存，2.52s -> 2.46s。
  - **5 个非默认页懒构造** - `_LazyPageWrapper` + `built` Signal：非默认页首次 showEvent 时才构造，2.46s -> 1.92s；懒页首次构建后通过 `built` Signal 补应用全局字体。
  - **worker_thread.start 移到构造末尾** - 让 pyocd 预热有更多时间，1.92s -> 1.79s；`wait_for_pyocd_prepare` 仍在 worker 启动前（DLL 安全）。
- **🐍 Python 3.13 推荐** - CI 矩阵纳入 3.13；Python 版本对比文档（3.11/3.13/3.14/3.15 实测，3.15a8 hid DLL 测试 fail）。

### Fixes

- **💥 J-Link DLL 并发崩溃（access violation 0x14）** - DLL 同句柄并发不安全：所有 pylink DLL 调用必须串行经 `_dll_lock`（RLock）。
- **💥 0xc000001d 崩溃** - pyocd 预热 wait 误从 MainWindow 移到 FlashPage 构造，预热线程 `import pyocd.probe.aggregator` 扫描时 `JLinkProbePlugin.should_load -> pylink.JLink()` 碰 DLL，与 RTT worker `connected_emulators()` 并发崩 0xc000001d。已回退（commit 5a3b55f -> 37bda17）；`wait_for_pyocd_prepare` 必须在任何 worker 启动前（含 RTT worker）。
- **🐛 设备下拉时序竞态** - 设备下拉时序竞态、烧录后暂停、DAPLink 烧录修复。
- **🐛 懒页字体初始化遗漏** - 懒页首次构建后未应用全局字体（qfluentwidgets 控件用 QSS 默认字号），通过 `built` Signal -> `_on_lazy_page_built` 重应用字体修复。
- **🐛 RTT 监控页设备下拉宽度** - 修正 J-Link 设备选择下拉框的宽度。

### Refactor

- **🔧 RTT 监控页 UI 拆分（Step 1-5）** - `rtt_monitor_page.py` 3083 -> 1929 行（-37%）：Step 1 拆出辅助类到独立模块；Step 2 拆出 SearchHandler；Step 3 拆出 SendBar；Step 4 拆出 DisplayArea + 清理死代码；Step 5 拆出 FloatingPanel。

### Docs

- **📖 README 全面重写** - 安装 / 使用 / 打包 / 性能 / 技术栈；start.bat 加 venv/.venv 守卫 + 补 uv 路径。
- **📖 通用性能优化 playbook** - `docs/perf-optimization-playbook.md`：通用启动/运行时性能优化方法，任何 Python 项目可借鉴；§2.4 标注 DLL 竞态踩坑（本项目已回退）。
- **📖 Python 版本对比** - `docs/python-version-comparison.md`：3.11/3.13/3.14/3.15 实测对比（subagent worktree 隔离）。
- **📖 启动/运行时优化调研报告** - A-class 5 + B-class 7 优化手段调研。
- **📖 CLAUDE.md 拆分** - 拆为索引（本文件）+ 正文（`docs/pitfalls.md`），常驻 context 降 ~90%。
- **📖 编码规范文档** - `docs/coding-style.md` + 核心 UI 模块 docstring 补全。
- **📖 standalone packager 对比报告** - Nuitka vs alternatives（Nuitka 启动最快，保持现状）。
- **📖 pitfalls 更新** - 新增 pyocd 预热 wait 移走导致 0xc000001d 崩、`aFlashArea` Size 最大区域、`EditableComboBox` 坑等多条经验。

### Testing

- **🧪 UI 拆分兜底测试** - 4 个端到端集成测试（拆分 Step 3/4/6 兜底）；382 测试全过（发版前最终验证 66s）。

### Engineering

- **🛠 start.bat venv 守卫** - start.bat 加 venv/.venv 守卫，避免误激活错误 venv。
- **🛠 CI 矩阵纳入 Python 3.13** - 同步 standalone 版本号 + CI 矩阵纳入 3.13。
- **🛠 ruff/black 全量修复** - ruff 配置修正 + 全量 lint 修复 + black 格式化。

## [0.7.0] — 2026-07-20

### Features

- **🎯 多 J-Link 设备选择** — 电脑同时插多台 J-Link 时，顶部下拉自动枚举所有接入设备（按 serial 区分），200ms 自动刷新；上次选中的 serial 持久化，下次启动自动回选；目标设备离线时在下拉左侧显示红点状态指示，插回后红点自动消失。
- **🔗 J-Link 远程连接（Remote Server）** — 支持通过 J-Link Remote Server 连接远端 J-Link，IPv4 / 主机名均可（主机名由 Python 侧 `socket.getaddrinfo` 解析，规避 DLL 不做 DNS 的坑），2s TCP 可达性预检区分「网络不通」与「协议失败」；连接面板双行输入 + 蓝色远程标记 + 探测节流。
- **🖼️ 可配置背景图片** — 设置页「外观」新增背景图片路径（浏览 / 清除）+ 透明度滑块（0–100%）+ 填充方式（拉伸 / 覆盖 / 居中 / 平铺），主窗口统一 `paintEvent` 绘制；启用背景图自动关闭 Mica 以保证可读性，5 语言 i18n。
- **🔧 烧录页独立烧录器选择** — 烧录页可选独立 J-Link（与 RTT 会话分离），设备枚举轮询在 worker 内建；烧录开始时与 RTT 协调断开 / 烧录结束自动回连，互不干涉。
- **🐧 Linux XDG 路径支持** — 用户配置与日志目录改用 XDG 规范（`~/.config/JLinkRTTViewer/` / `~/.local/share/JLinkRTTViewer/`），新增 Linux 打包脚本。
- **⚙️ 自动重连重构** — 由 UI 轮询统一驱动，删除 worker 3s 轮询；断开提示去重，避免枚举与读线程异常同时触发时打印两次。

### Performance

- **RTT 文本批量布局** — `_on_rtt_data` 用 `beginEditBlock` / `endEditBlock` 把多段 insertText 合并为一次布局重算，高吞吐流下显著减少主线程负载。
- **背景图缩放缓存** — `paintEvent` 缓存按（源图 + 填充方式 + 尺寸）键控的缩放结果，仅在 resize / 换图 / 换方式重算，避免每帧全图 `Qt.SmoothTransformation` 重算。
- **设备枚举下拉 diff guard** — 设备列表未变时跳过 200ms 的 combo 重建（QSS 重解析 + 布局重算），空闲态零开销。

### Engineering

- **Nuitka 打包清理** — 删除多余的 `--include-package=qfluentwidgets`（资源已嵌进 `_rc/resource.py`，全库静态 import，靠 `--follow-imports` 自动跟随即可），构建零 WARNING；`--nofollow-import-to` 排除约 50 个未用到的 PySide6 Qt 模块与 cli/test stub；`--show-progress` pipe-buffer 导致构建被杀的坑已修复并记录。
- **打包脚本一键发版** — `package_release.ps1` 合并发版流程：版本 bump → 提交 → tag → 双版本编译 → 打包 → push → gh release，产物按版本号归档到 `build/dist/<version>/`。
- **工程笔记** — CLAUDE.md 新增远程连接 DNS 解析、多设备 serial 匹配、buf descriptor 通道计数、QSS font 锁定、`EditableComboBox` 坑等多条经验。

### Testing

- 新增背景图片配置（defaults / signals / 设置页控件）测试；全量回归 303 项通过。

## [0.6.0] — 2026-07-17

### Features

- **全局界面字体设置** — 设置页新增「界面字体」下拉：family 可选系统已装任意字体或「(跟随系统)」，字号独立调节；遍历全部控件热更新，RTT/内存显示区保持各自等宽专属字体。界面字号默认调整为 9pt。
- **多语言界面** — 接入 i18n，支持简体中文 / 繁體中文 / 日本語 / 한국어 / English / Français 六种语言即时切换；主题色、标记颜色等 ColorDialog 在所有语言下正确本地化（含第三方英文源控件的回退翻译）。
- **多通道 RTT** — 通道选择支持 -1=全部通道；按通道独立历史 / 统计 / 解码；实际通道数按 buf descriptor 的 SizeOfBuffer 探测，不再误信固件声明数。
- **连接健壮性** — 连接前预查 J-Link 是否在线；物理掉线自动检测并在显示区红字提示、自动重连；发送失败提示改写为可操作文案。
- **发送体验** — 换行符模式可选 CRLF/LF/CR/无；发送回显染色（色块按钮 + 网格色盘）；收发统计精简为字节数并即时刷新。
- **保持屏幕常亮** — 长会话监控时防止系统息屏。

### Fixes

- **RTT 通道数误判** — `rtt_get_num_up_buffers()` 返回的是固件声明数（含空槽），改用 buf descriptor 的 `SizeOfBuffer>0` 计数实际已分配通道，修复选超出范围通道后显示区空白、通道上限脱节。
- **内存页 hex 显示区字体** — 固定跟随 RTT 等宽字体（`font_family`），不随全局 UI 字体变，避免非等宽 UI 字体导致 hex 列错位；字号仍独立。
- **QSS `font:` 锁定控件** — RadioButton 等控件 setFont 无效（QSS 优先级更高），改用 styleSheet 追加哨兵规则覆盖，字号/family 均生效。
- **语言切换残留** — 左侧 panel 多语言内容溢出根治；RTT 通道 tooltip 切语言后不重译修复；静态按钮文字在语言切换后统一重设。
- **左侧面板布局** — 连接后变窄与英文溢出导致控件被裁；接口/速度/RTT 通道控件等分布局对齐；标记/保存按钮行右对齐。

### Engineering

- 新增发版一键脚本 `scripts/release.ps1`：版本 bump → 提交 → tag → 双版本编译 → 打包 → push → gh release。
- 翻译键缺失永不空白（translator 未命中返回 source）；zh_CN 也装 translator 以覆盖第三方英文源控件。

## [0.5.0] — 2026-07-11

### Features

- **搜索 / 替换浮动栏** — Ctrl+F 查找 / Ctrl+H 替换，支持正则 / 全词 / 大小写匹配，匹配高亮 + 染色替换，VSCode 风格浮动栏叠加在显示区右上角，Esc 关闭。
- **HEX 显示 / 发送** — 接收区一键切换十六进制查看（每字节大写 HEX），发送区支持 HEX 模式双向切换，收窄工具栏与左侧面板入口同步。
- **定时发送** — 按设定间隔（ms）自动重复发送当前输入框内容，支持文本 / HEX 两种模式。
- **CRC 发送脚本** — 内置 CRC-8 / CRC-16 / CRC-32 算法（含 CCITT / Modbus 等变体），发送时自动追加校验值到 payload 末尾，可开关；启用时发送框红色边框提示。
- **自动断帧** — 按空闲间隙（可配 ms）自动插入换行，无需 MCU 端配合即可分行显示连续流。
- **RTT 监控页 UI 重构** — 左右分栏布局（左侧配置 280px + 右侧数据区），左侧面板划分为连接 / 设备信息 / 接收设置 / 发送设置四个区域；发送框改为多行 PlainTextEdit；收窄模式工具栏行位于显示区和发送区之间。
- **收窄模式悬浮面板** — 窗口宽度 < 900px 时左侧配置面板自动转为悬浮卡片，由 ToolToggleButton（CHEVRON_RIGHT）控制显隐，fade + slide 220ms 动画，弹出卡片不退出收窄模式。
- **重置并暂停按钮** — 复位 MCU 后让 CPU 停在复位状态（`reset(halt=True)`），不运行、不断开重连，用于调试上电瞬间状态。
- **固件分析视图扩展**：烧录页底部符号面板用 SegmentedWidget 切换「符号 / 段 / 占用汇总」三视图，共用同一已选 axf/elf。
  - **段 Sections**：列出 SHF_ALLOC 段的地址 / 大小 / RWX / 对齐。
  - **占用汇总 Summary**：采用 arm-none-eabi-size 的 Berkeley 统计方式汇总 text/data/bss + Flash/RAM 总量；并显示 Entry point、Cortex-M 初始 SP、Reset_Handler。
  - **符号视图新增「% 段」列**：每个符号占其所属段大小的百分比（可数值排序）。

### Fixes

- **窗口最小宽度 900 → 480**：原最小宽度等于收窄阈值，窗口永远缩不到收窄模式。
- **`_open_elf` 漏 catch `ELFError`**：内容损坏但扩展名是 `.axf` 的文件，会让 `SymbolTableView.load` / `read_sections` / `read_memory_summary` / `read_elf_meta` 直接抛 `ELFError`，UI 层无机会消化。修复后 `_open_elf` 内部 catch + close 文件句柄，统一抛 `FileParseError`。
- **收窄模式工具栏按钮被悬浮卡片遮挡**：所有按钮右对齐，避免被 280px 宽的悬浮卡片覆盖。

### Performance

- **RTT `_fmt` 预构造 QColor**：16 色 ANSI 调色板 + 默认前/背景在模块加载时一次性 `QColor(hex)` 构造好，热路径直接查 dict。微基准 1.51× 提速；高吞吐流减少每段的 alloc/parse 开销。
- **RTT 读循环改用 threading.Thread**：替代 QTimer 轮询，读线程完全独立于 Qt 事件循环，UI 侧 50ms 节流合并 insertText，避免高频信号阻塞主线程。

### Testing

- **新增 pytest-qt + offscreen UI 测试脚手架**：`QT_QPA_PLATFORM=offscreen` 全程无窗口、无焦点，CI 友好。
  - 共 190+ 个 UI 用例：SymbolTableView / FlashPage / RTTMonitorPage / MemoryViewerPage / SettingsPage / 悬浮面板 / CRC / 搜索栏。
  - 公共 fixture：`isolated_appdata`（monkeypatch APPDATA → tmp，不污染真 `user_prefs.json`）、`fixtures_dir`、`screenshot_dir`。
  - 跨页 worker 替身：`FakeWorker` / `FakeMemWorker` 复刻 JLinkWorker 信号集，解耦真 pylink / QThread。
  - `_open_elf` 的 `ELFError` 漏 catch 由本次 `test_load_corrupt_elf_does_not_crash` 首次复现。

## [0.4.0] — 2026-05-21

### Features

- **固件烧录页：固件另存为（格式转换）** — 浏览按钮右侧新增「另存为…」，把当前固件转换为 `.bin` / `.hex`（目标格式按所选后缀决定）。支持 axf/elf/hex/bin → bin、axf/elf/hex/bin → hex（bin 源用页面当前起始地址）。
- **固件烧录页：axf/elf 符号表查看器** — 选中 ELF/axf 时页面底部显示符号表卡片：名称搜索过滤、列排序（地址/大小按数值）、复制选中行、Type 列彩色 pill、统计计数。
  - 一次性读入全部符号，用同一层的 chip toggle 过滤：类别（Functions/Variables/File markers/Sections/Other，默认仅亮前两个）与绑定（Global/Local/Weak）并列，勾了就显示、不勾就隐藏，无隐藏的读取层级。
  - chip 文字中英并列 + hover tooltip 说明对应 ELF 符号类型/绑定；底部一行说明默认为何只显示函数与变量、其余类别是什么。

### Fixes

- **固件文件选择全链路失效** — `EditableComboBox.setCurrentText` 对不在 items 里的路径是 no-op，导致浏览/拖放选的文件不显示、历史列表空、烧录提示「未选择文件」。改为「更新最近文件 → 重建下拉 items → 按 index 选中」。
- 烧录页 Speed 由 SpinBox 改为与 RTT 监控页一致的 ComboBox（默认速度列表）。
- 文件更新提示 `updated` → `Updated`（首字母大写）。

### Performance

- 符号表过滤去掉 `ResizeToContents`（每次切换 chip 会全表扫描重算列宽，上万符号时卡顿），改为 Name 拉伸 + 其余列固定宽；重填用 `setUpdatesEnabled` 批量重绘。

### Docs

- 新增 `docs/flashing-guide.md`（烧录 + 另存为）与 `docs/symbol-table-guide.md`（符号表查看器）使用指南及截图。

## [0.3.0] — 2026-05-17

### Features

- **新增固件烧录页**：支持 `.axf` / `.elf` / `.hex` / `.bin` 烧录到目标 MCU
  - 独立 `FlashWorker` + 独立 `pylink` 会话 + 独立 `QThread`，不干涉 RTT/Memory 模块
  - 拖放选文件、最近 10 个历史 + mtime 变更提示
  - 擦除模式可选（扇区 / 整片），完成动作可选（仅烧录 / 复位 / 复位+运行）
  - 详情面板（失败自动展开）+ "复制日志"按钮（含 app/OS/pylink 版本头，方便贴 issue）
  - 文件解析层（`flash_file_parser`）零 Qt 依赖，独立单元测试覆盖 axf/hex/bin 解析 + 错误路径

### Engineering

- 新增依赖：`pyelftools` / `intelhex`，已加进 Nuitka 打包脚本

## [0.2.3] — 2026-05-17

### Fixes

- **config.json 在 onefile 模式下不可编辑** — onefile 模式下 bundled `config.json` 解压到隐藏临时目录，每次升级被覆盖，用户没法加自己的 MCU
  - 改为分层：首次启动从 bundled 自动 seed 一份到 `%APPDATA%\JLinkRTTViewer\config.json`，之后优先读用户副本；删了用户副本下次启动自动重 seed
  - standalone 模式同样受益（不再需要去 `main.dist/config.json` 编辑，统一在 `%APPDATA%` 下）

## [0.2.2] — 2026-05-17

### Features

- **新增单 exe (onefile) 打包模式**：`build_nuitka_onefile.bat` 产出单个 `JLinkRTTViewer.exe`，便携性最强
  - 解压目录用 `--onefile-tempdir-spec={CACHE_DIR}\JLinkRTTViewer\Cache\{VERSION}` 固定缓存，首次启动解压后续启动直接命中缓存（避免每次 ~5s 解压等待）
  - 不喜欢解压等待仍用 standalone 模式（zip 解压一次后用 .dist 目录跑，启动最快）
- **打包脚本支持双模式**：`scripts/package_release.ps1 -Mode standalone|onefile|both`

### Performance

- **Nuitka 编译加性能标志**：`--lto=yes`（link-time optimization，二进制更小启动更快）+ `--python-flag=-O`（去 assert/docstring）+ `--python-flag=no_warnings`（跳过 warning 框架初始化）
- 启动速度提升约 5-10%（standalone 模式实测）

## [0.2.1] — 2026-05-17

### Features

- **内存页用户选择持久化**：读地址 / 读大小 / 字节每行 / 字节序 / diff 高亮 / 自动刷新间隔 / 导出地址 / 导出大小预设 / 自定义大小 / 写地址 — 共 10 项跨重启保留
  - 故意**不持久化**：`auto_refresh`（断开会被自动取消，回放无意义）、`goto` / `search`（一次性导航输入）、`write_data`（高危：重启后还在框里，误点会改写 MCU 内存）
  - LineEdit 用 `editingFinished` 触发，避免每键击都写盘

## [0.2.0] — 2026-05-17

### Features

- **F2 / F3 / F4 全局快捷键**：连接 / 断开 / 重置目标，任意子页生效，幂等设计
- **重置模式可配**：设置页选「正常」（5 步快速重置）或「自动重连」（断开+重连，更可靠），按钮文字与 tooltip 实时切换
- **会话标记颜色可配**：默认亮黄 `#ffff55`，设置页 ColorDialog 选；用户手动插入 + 自动标记共用
- **连接 / 断开自动插入标记**：两个独立开关，开启后每次状态切换自动在 RTT 显示区插入 ``──── 已连接 STM32H750VB @ HH:MM:SS ────`` 这样的分隔行，方便会话分段
- **关于页重写**：Hero header（logo + 标题 + 版本 + 标语 + 项目链接 + Issue 链接）+ 3 卡片功能特性 + 作者卡 + 第三方依赖 + 页脚
- **4 页统一透明 ScrollArea**：RTT / 内存 / 设置 / 关于全部用 `make_transparent_scroll` helper，窗口压扁时整页自然滚动，控件不再挤压
- **可拖动 RTT display 高度**：6px 自定义 resize handle，hover/拖动跟随主题色，拖大超过窗口时整页自动出滚条
- **标题栏左上角图标**：MainWindow 显式 setWindowIcon 触发 FluentTitleBar 刷新

### Fixes

- **重置后必须断开重连才有数据 bug**：pylink 缓存 RTT 控制块地址在 jlink.reset 后过期。`normal` 模式 5 步 dance（reset + rtt_stop/start + 重启读线程）原地修复；`auto_reconnect` 模式整个 J-Link 会话推倒重来保证 100% 可靠
- **`EditableComboBox` 无 `clearEditText` AttributeError**：换成 `setCurrentText("")`
- **手动上滚 RTT display 后自动滚动 checkbox 自动取消勾选**：UX 一致性
- **`_paused` 标志在固件导出时是假锁**：read_loop 和 export 共享 jlink 实例会抢句柄，改用真停读线程

### Refactor / Code quality

- 重置流程从 4 方法跨方法状态机 → 单方法一条龙（worker 闭环编排，UI 一行 emit）
- 信号 `reset_target_requested(bool)` → `reset_requested(str)`，避免 bool 反向心算
- UI 不再用 `btn.text() == "连接"` 当 state enum，改 `_is_connected: bool` 字段
- `_programmatic_scroll` 标志 3 处 boilerplate → `@contextmanager` 围栏
- 抽 `_pause_read_thread` / `_restart_read_thread` / `_do_connect` / `_byte_start_col` / `_insert_mark_text` 等 helper 消除重复
- 抽 `_scroll_helpers.make_transparent_scroll` / `_paths.find_app_icon|find_app_logo_png` 共享 helper
- `RESET_MODE_NORMAL` / `RESET_MODE_AUTO_RECONNECT` 模块常量，避免字面值散落
- 工程踩坑笔记（[CLAUDE.md](CLAUDE.md)）新增 9 条设计原则

## [0.1.0] — 2026-05-16

首次公开发布。

### Features

- **RTT 监控**：实时显示 SEGGER RTT 输出，支持 UTF-8 / GBK / UTF-16-LE / Latin-1 / ASCII 解码，ANSI 颜色着色，0-15 通道任意切换
- **数据回发**：文本 / 十六进制两种格式，最近 50 条发送历史下拉重发
- **会话标记**：用户手动插入 + 连接/断开自动插入（颜色可配）
- **可拖动 display 高度**：自定义 resize handle + 整页 ScrollArea 兜底
- **内存查看**：hex dump、地址跳转、hex pattern 搜索、自动刷新、diff 高亮、hover 类型解析、固件按区间导出 `.bin`、写内存（带确认）
- **设置页**：主题（浅/深/跟随系统）+ 主题色 + RTT/UI 字体 + 标记颜色 + 重置模式（正常 / 自动重连）+ 编码 + 轮询间隔等
- **快捷键**：F2 连接 / F3 断开 / F4 重置（任意子页生效）
- **Nuitka 打包**：单 exe 分发，多分辨率图标

### Engineering highlights

- worker 走标准 `QObject + moveToThread` 范式（不继承 QThread）
- RTT 读循环用 `threading.Thread` + worker 内 QTimer 50ms drain，避免 native 线程 emit Qt signal 的 cross-thread 陷阱
- pylink-square 锁 1.6.0（2.x 的 rtt_start/rtt_read 在 SEGGER DLL 下不工作）
- 配置写盘 200ms 节流，关窗 flush，避免拖窗/调字号每帧刷盘
- 详细工程踩坑笔记见 [CLAUDE.md](CLAUDE.md)

[Unreleased]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.3.0...v0.5.0
[0.3.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/releases/tag/v0.1.0
