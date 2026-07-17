# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

[Unreleased]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.3.0...v0.5.0
[0.3.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/releases/tag/v0.1.0
