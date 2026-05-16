# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

[Unreleased]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/releases/tag/v0.1.0
