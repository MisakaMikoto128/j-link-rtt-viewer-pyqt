# J-Link RTT Viewer (PyQt)

> Fluent 风格的 SEGGER J-Link RTT 实时查看 + MCU 内存读写工具，PySide6 + qfluentwidgets 重写版。

[![tests](https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/actions/workflows/test.yml/badge.svg)](https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![pylink-square](https://img.shields.io/badge/pylink--square-1.6.0-orange)](https://github.com/Square/pylink)

![home](img/home.png)

## ✨ 功能

- 🚀 **实时 RTT 监控** — UTF-8 / GBK / UTF-16-LE / Latin-1 / ASCII 解码，ANSI 颜色着色，0-15 通道任意切换，文本 / 十六进制回发数据，最近 50 条发送历史
- 🔍 **内存查看** — Hex dump（8/16/32 字节/行），地址跳转，hex pattern 搜索，自动刷新 + diff 高亮，hover 实时类型解析（u8-u64 / i8-i64 / float / double，小端/大端），固件按区间分块导出 `.bin`，写内存（带 confirm）
- 🎨 **Fluent 设计** — 浅色 / 深色 / 跟随系统主题，主题色 + RTT 字体 + UI 字体可独立配置
- 📝 **会话标记** — 手动插入 + 连接/断开自动插入（颜色可配）
- ⌨️ **快捷键** — F2 连接 / F3 断开 / F4 重置（任意子页生效，幂等）
- 🔄 **可配置重置行为** — 正常重置 / 自动重连（更可靠，1s 延迟）
- 📐 **可拖动 RTT display 高度** — 自定义 resize handle，超出窗口自动整页滚
- 📦 **Nuitka 单 exe 打包** — 多分辨率图标，开发/打包一致

## 📸 截图

| RTT 监控 | 内存查看 | 设置 |
|:---:|:---:|:---:|
| ![home](img/home.png) | ![memory](img/memory.png) | ![setting](img/setting.png) |

## 🚀 快速开始

### 前置要求

- **SEGGER J-Link 驱动**（[官方下载](https://www.segger.com/downloads/jlink/)），`JLinkARM.dll` 由 pylink 自带
- 一根 J-Link 调试器（J-Link BASE / EDU / PLUS 等，或 Flasher 设备）
- 从源码运行还需要 **Python 3.10+**；下载 Release 直接用则不需要

### 直接下载使用（推荐）

1. 到 [Releases 页面](https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/releases) 下载最新版本压缩包（`JLinkRTTViewer-vX.Y.Z-win64.zip`）
2. 解压到任意目录
3. 双击 `JLinkRTTViewer.exe` 启动
4. 在 UI 顶部选择目标 MCU、接口（SWD / JTAG）、速度、RTT 通道 → 点「连接」
5. 用户偏好自动保存到 `%APPDATA%\JLinkRTTViewer\user_prefs.json`

> 不需要安装 Python，不需要 pip，**目标机器只要装了 SEGGER J-Link 驱动就能跑**。

更多用法见 [用户手册](docs/USER_GUIDE.md)。

### 从源码运行

```bash
# 1. 克隆
git clone https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt.git
cd j-link-rtt-viewer-pyqt

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate.bat

# 3. 安装依赖（pylink-square 锁定 1.6.0，详见下方）
pip install -r requirements.txt

# 4. 启动
python src/main.py
# 或直接双击 start.bat
```

### 打包成独立 .exe

```bash
build_nuitka.bat
```

输出在 `build/main.dist/JLinkRTTViewer.exe`，整个 `main.dist/` 目录可压缩分发，**不需要目标机器装 Python**。

## 📖 文档

- **用户手册**：[docs/USER_GUIDE.md](docs/USER_GUIDE.md) — 完整 UI / 配置 / 快捷键说明
- **工程笔记**：[CLAUDE.md](CLAUDE.md) — 项目演进中遇到的真实 Qt / pylink / 打包问题与解法
- **贡献指南**：[CONTRIBUTING.md](CONTRIBUTING.md)
- **更新日志**：[CHANGELOG.md](CHANGELOG.md)

## ⚠️ pylink-square 必须用 1.6.0

pylink-square 2.x 在 SEGGER J-Link DLL 下有 breaking change：`rtt_start` / `rtt_read` 内部行为变化，导致 RTT 通道永远没数据（虽然 `connected()` 返回 True）。本项目锁定 1.6.0，请**不要**升级。

详见 [CLAUDE.md → pylink 必须用 1.6.0](CLAUDE.md#pylink-必须用-160-2x-不工作)。

## 🛠️ 技术栈

| 组件 | 版本 | 用途 |
|---|---|---|
| [PySide6](https://pypi.org/project/PySide6/) | ≥ 6.6, < 7 | Qt for Python |
| [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) | ≥ 1.6 | Fluent 设计组件库 |
| [pylink-square](https://github.com/Square/pylink) | **1.6.0** | SEGGER J-Link Python 封装 |
| [Nuitka](https://nuitka.net/) | ≥ 2.0 | Python → 原生 exe |
| pytest | ≥ 8.0 | 测试 |

## 🤝 贡献

欢迎 Issue / PR！请先看 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 📄 License

[MIT](LICENSE) © 2026 [@MisakaMikoto128](https://github.com/MisakaMikoto128)

## 🙏 致谢

- [SEGGER](https://www.segger.com/) — J-Link 调试器 + RTT 协议
- [Square](https://github.com/Square/pylink) — pylink-square
- [zhiyiYo](https://github.com/zhiyiYo) — PyQt-Fluent-Widgets
