# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

[Unreleased]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/releases/tag/v0.1.0
