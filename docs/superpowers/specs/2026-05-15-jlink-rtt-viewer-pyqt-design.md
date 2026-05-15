# J-Link RTT Viewer PyQt 重构设计

- **日期**：2026-05-15
- **目标**：把原 `Charging_Pile/RTT_Viewer/RTT-T`（PyWebView + pylink）重写为 PySide6 + QFluentWidgets 桌面应用
- **保留功能**：① J-Link RTT Viewer（含发送、通道选择、日志记录、电源/暂停/重置等），② 内存查看 + Hex 转储 + 固件导出
- **去掉功能**：固件烧录、批量烧录、Excel 查看、计算器、SysInfo_t 域特定解析

---

## 1. 背景与动机

原项目用 PyWebView 跑前端 HTML/JS，存在两个长期问题：

1. **维护负担**：HTML/CSS/JS + Python 双语言并行，前后端通过 `webview.expose` 桥接 29 个方法，签名一变全断
2. **关闭/重开 J-Link 时序脆弱**：pylink `rtt_stop()` / `close()` 在 WebView2 拆卸语境下偶发死锁，需 `os._exit(0)` 兜底；连接逻辑里又做了 "open → 取 serial → close → 再 open" 的双开循环，进一步增加跨线程并发风险

PySide6 + QFluentWidgets 原生 Qt 信号槽通信，能让 pylink 调用集中到一条线程，从根本上消除上述问题；同时 QFluentWidgets 直接提供 Fluent 设计系统，UI 美观度对比手撸 CSS 显著提升。

---

## 2. 总体方案：单 JLinkWorker QThread

**核心思想**：所有 pylink 调用都在一条 `JLinkWorker` 线程内完成，UI 主线程零阻塞，通过 Qt 信号下达命令、接收数据。

### 2.1 与备选方案对比

| 方案 | 线程模型 | 优点 | 缺点 | 评估 |
|---|---|---|---|---|
| **A（采纳）** | 单 QThread + Qt 事件循环 + QTimer 轮询 RTT | pylink 单点持有，无并发隐患；UI 响应；和 qfluentwidgets 风格一致 | 需设计命令/响应信号 | 推荐 |
| B | 沿用 `threading.Thread` + `Queue`，UI 用 QTimer 轮询 | 移植成本最低 | 死锁/双开问题原样保留 | 弃 |
| C | asyncio + qasync | 异步代码线性 | pylink 无原生 async；多一层依赖；和文档示例不一致 | 弃 |

### 2.2 关键时序修正

**连接序列（不再双开）**：
```python
from pylink.enums import JLinkInterfaces

if not self.jlink.opened():
    self.jlink.open()  # 不传 serial_no, pylink 自动选第一个
# SWD/JTAG 互斥，按用户选择二选一，必须在 connect() 之前
tif = JLinkInterfaces.SWD if interface == 'SWD' else JLinkInterfaces.JTAG
self.jlink.set_tif(tif)
self.jlink.set_speed(speed)
self.jlink.connect(target)
self.jlink.rtt_start()
self._reset_utf8_decoder()
```

**断开序列（全部加守卫）**：
```python
self._stop_reading = True  # 停 QTimer 轮询
try:
    if self.jlink.connected():
        self.jlink.rtt_stop()
except Exception as e:
    self.logger.warning(f"rtt_stop 失败：{e}")
try:
    if self.jlink.opened():
        self.jlink.close()
except Exception as e:
    self.logger.warning(f"close 失败：{e}")
```

---

## 3. 目录结构

```
J-Link RTT Viewer PyQt/
├── .git/                                  # 本次 git init
├── .gitignore
├── CLAUDE.md                              # 踩坑笔记（中文，"现象/原因/处理"三段式）
├── README.md
├── pyproject.toml                         # ruff/black 配置
├── requirements.txt
├── start.bat                              # 激活 venv + 启动应用
├── build_nuitka.bat                       # Nuitka 打包脚本
├── docs/
│   └── superpowers/specs/                 # 本文件所在
├── img/                                   # 已存在，应用图片资源
├── resource/                              # 应用图标、字体（如需）
├── src/
│   ├── main.py                            # 程序入口
│   ├── core/
│   │   ├── __init__.py
│   │   ├── jlink_worker.py                # QThread + JLink 全生命周期
│   │   ├── memory_service.py              # 内存读取 / Hex 转储 / 固件导出
│   │   ├── ansi_parser.py                 # ANSI 转义 → [(text, attrs)]
│   │   ├── config_service.py              # config.json + user_prefs.json
│   │   └── logger.py
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── main_window.py                 # FluentWindow + 导航
│   │   ├── rtt_monitor_page.py
│   │   ├── memory_viewer_page.py
│   │   ├── settings_page.py
│   │   ├── about_page.py
│   │   └── widgets/                       # 复用小组件
│   └── config.json                        # 默认配置（芯片列表/速度选项等）
└── venv/                                  # gitignore
```

### 3.1 依赖（requirements.txt）

```
PySide6>=6.6
PySide6-Fluent-Widgets>=1.6,<2.0   # base 包够用；若缺组件再换 [full]
pylink-square>=1.6.0
nuitka>=2.0   # 仅本地打包用

# dev / test
pytest>=8.0
```

去掉原项目的 `pywebview / pandas / xlsxwriter / openpyxl`。

---

## 4. core 层详细设计

### 4.1 JLinkWorker（`core/jlink_worker.py`）

继承 `QThread`，独占 pylink `JLink` 实例。`run()` 内进入 `exec()` 跑自己的事件循环。

> **线程归属警告**：`QThread` 子类的 `__init__` **仍跑在主线程**——`run()` 才是新线程。所有依赖事件循环的 QObject（包括 `QTimer`、`JLink` 句柄、`IncrementalDecoder`）**必须在 `run()` 内创建**，不能放 `__init__`，否则它们的 thread affinity 是主线程，`QTimer` 不会触发、queued connection 也跨错线程。`__init__` 只保存配置和创建输入/输出信号对象。
>
> 备选方案：用 `QObject` worker + `moveToThread`，能在 `__init__` 创建所有子对象，但 `moveToThread` 后不能再访问其属性——本设计采用 QThread 子类 + run() 内构造的写法。

**输入信号（UI → Worker, `Qt.QueuedConnection`）**：
- `connect_requested(target: str, interface: str, speed: int, rtt_channel: int)`  ← `interface` 传字符串 `"SWD"` / `"JTAG"`，worker 内部转 `JLinkInterfaces` 枚举
- `disconnect_requested()`
- `send_data_requested(data: str, is_hex: bool)`
- `reset_target_requested()`
- `set_rtt_channel_requested(channel: int)`
- `set_pause_receive_requested(paused: bool)`
- `set_power_output_requested(enable: bool)`
- `read_memory_requested(addr: int, size: int)`
- `export_firmware_requested(save_path: str, start_addr: int, size: int)`
- `start_log_recording_requested(log_dir: str)` / `stop_log_recording_requested()`
- `stop_requested()`  ← 退出兜底

> **配置访问**：worker 不直接 import `ConfigService`；所有需要配置的命令信号都把配置值作为参数传入（如 `log_dir` 由 UI 从 `ConfigService` 取出后传给 worker）。这样 worker 完全独立于 UI 模块，便于单元测试。

**输出信号（Worker → UI）**：
- `rtt_data_received(text: str)`  ← UTF-8 增量解码后字符串段，纯文本含 ANSI 转义；UI 端调 `ansi_parser` 着色
- `connection_state_changed(connected: bool, device_info: dict)`
- `log_message(level: str, msg: str)`
- `command_result(command: str, success: bool, payload: dict)`
- `memory_read_finished(addr: int, raw_bytes: bytes)`  ← 只回原始字节，UI 端用纯函数 `format_hex_dump(bytes) -> str` 渲染；避免大块数据复制两份
- `firmware_export_progress(current: int, total: int)`
- `firmware_export_finished(success: bool, path: str, error: str)`

**内部状态机**：`IDLE → CONNECTING → CONNECTED → DISCONNECTING → IDLE`。读循环（QTimer 20 ms）只在 `CONNECTED` 跑。

**RTT 读循环**：在 `run()` 内创建 `QTimer(interval=20)`（**不要**在 `__init__`），触发 `_poll_rtt()`：
```python
def run(self):
    self.jlink = pylink.JLink()
    self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    self._poll_timer = QTimer()  # 无 parent，归属当前线程
    self._poll_timer.setInterval(20)
    self._poll_timer.timeout.connect(self._poll_rtt)
    self.exec()

def _poll_rtt(self):
    if self._state != CONNECTED or self._paused:
        return
    try:
        data = self.jlink.rtt_read(self._channel, 4096)
    except Exception as e:
        self.log_message.emit('error', f'RTT 读异常：{e}')
        self._transition_to_idle()
        return
    if not data:
        return
    # IncrementalDecoder 自己维护半字节缓冲，不要在外层再叠一层 byte_buffer
    decoded = self._decoder.decode(bytes(data))
    if decoded:
        self.rtt_data_received.emit(decoded)
        self._write_log_file(decoded)
```

> `_reset_utf8_decoder()` 在每次重连时重建 decoder，避免上次掉线残留的半字节污染新连接。

### 4.2 ANSI 解析（`core/ansi_parser.py`）

纯函数 `parse_ansi(text: str) -> list[tuple[str, AnsiAttrs]]`，把
`"\x1b[31mhello\x1b[0m world"` 解成 `[("hello", AnsiAttrs(fg="red")), (" world", AnsiAttrs())]`。
不依赖 QtGui，UI 端拿到后转 `QTextCharFormat`。

### 4.3 内存服务（`core/memory_service.py`）

纯函数模块，**调用必须在 worker 线程内**（由 worker 在响应 `read_memory_requested` / `export_firmware_requested` 时调用）：

- `read_memory(jlink, addr: int, size: int) -> bytes`：用 `memory_read(addr, word_count, nbits=32)`，返回原始字节
- `export_firmware(jlink, save_path: str, start_addr: int, size: int, progress_cb: Callable[[int, int], None])`：按 4 KB 分块流式写入，回调进度
- `format_hex_dump(data: bytes, base_addr: int = 0) -> str`：纯字符串格式化（`addr  HH HH HH ... | ascii`），UI 端用，不依赖 pylink

去掉原项目 `parse_memory_data`（SysInfo_t 充电桩域专属逻辑）。

> **RTT 与内存操作互斥**：worker 单线程，导出 2 MB 固件期间（~40s）`_poll_rtt` 不会被调度，J-Link 端 RTT 缓冲区有溢出风险。UI 在调用 `export_firmware_requested` 前应 `InfoBar.warning` 提示「导出期间 RTT 接收会暂停」，并禁用 RTT 监控页的发送/连接按钮直到 `firmware_export_finished`。

### 4.4 配置服务（`core/config_service.py`）

**两个文件**：
- `src/config.json`（应用打包随附，只读）：芯片型号列表、速度选项、默认接口、默认字体
- `%APPDATA%/JLinkRTTViewer/user_prefs.json`（运行时生成，**始终在 %APPDATA%**，开发期不回落到工作目录，避免与打包后行为不一致）：上次选的芯片、接口、速度、RTT 通道、发送历史（≤50）、主题（light/dark/auto）、主题色、字体大小、窗口几何

**写入**：临时文件 + `os.replace` 原子化。沿用原项目做法。

**写盘节流**：`set()` 不立即落盘，启动一个 200 ms 单次触发的 `QTimer`，在该 timer 触发时统一 flush。窗口几何/字体 SpinBox 这类高频变化值才不会反复写盘 + atomic replace。退出时 `closeEvent` 强制 flush 一次。

**接口**：
```python
class ConfigService(QObject):
    theme_changed = Signal(str)
    theme_color_changed = Signal(str)
    font_changed = Signal(str, int)

    def get(self, key: str) -> Any
    def set(self, key: str, value: Any) -> None  # 节流后落盘
    def flush(self) -> None                       # 强制立即落盘
    def get_chip_list(self) -> list[str]
    def get_default_speeds(self) -> list[int]
```

主线程单例，UI 任意页面都能取到。所有信号统一 **snake_case**（PEP 8）。

### 4.5 日志（`core/logger.py`）

`logging` 标准库 + `RotatingFileHandler`，文件 `%APPDATA%/JLinkRTTViewer/logs/app.log`，控制台同步输出 INFO+。worker 的 `log_message` 信号同时写文件，UI 不强制显示，但设置页提供「打开日志目录」按钮。

**单例策略**：模块级 `get_logger() -> logging.Logger`，首次调用时初始化 handler，后续返回同一 logger。`main.py` 启动时第一行就调用一次，确保后续 ConfigService / worker 都能拿到已配置好的 logger。

### 4.6 程序入口（`src/main.py`）

启动序列必须严格按下面顺序执行，否则会踩坑：

```python
def main():
    # 1. 高 DPI（PySide6 6.6+ 默认开启缩放，但显式声明更明确）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)

    # 2. logger 先于一切业务模块初始化
    logger = get_logger()

    # 3. DLL 检测（致命错误前置）
    try:
        pylink.JLink()  # 触发 JLinkARM.dll 加载
    except Exception as e:
        QMessageBox.critical(None, "启动失败", f"加载 JLinkARM.dll 失败：{e}")
        return 1

    # 4. 配置 & 主题
    cfg = ConfigService()
    setTheme(Theme[cfg.get('theme').upper()])  # qfluentwidgets API
    setThemeColor(cfg.get('theme_color'))

    # 5. 主窗口（内部创建并 start() JLinkWorker）
    win = MainWindow(cfg)
    win.show()
    return app.exec()
```

---

## 5. UI 层详细设计

### 5.1 MainWindow（`ui/main_window.py`）

继承 `qfluentwidgets.FluentWindow`，左侧导航 + 顶部标题栏（mica/acrylic）。

**导航项**：

| 位置 | 图标 | 文本 | route key | 页面 |
|---|---|---|---|---|
| TOP | `FIF.SPEED_HIGH` | RTT 监控 | `rtt-monitor` | `RTTMonitorPage` |
| TOP | `FIF.CODE` | 内存查看 | `memory-viewer` | `MemoryViewerPage` |
| BOTTOM | `FIF.SETTING` | 设置 | `settings` | `SettingsPage` |
| BOTTOM | `FIF.INFO` | 关于 | `about` | `AboutPage` |

**职责**：
1. 持有唯一的 `JLinkWorker` 实例，构造时启动 `worker.start()`
2. 把 worker 输出信号连接到关心的页面；页面通过构造参数拿到 `worker` 引用，自己 connect/emit
3. 加载/保存窗口几何到 `user_prefs.json`（`saveGeometry()` → base64）
4. **closeEvent 关闭序列**：
   ```python
   def closeEvent(self, event):
       # 主线程只发意图，不在主线程调用任何 pylink API
       self.worker.stop_requested.emit()
       # worker 的 stop_requested 槽函数内部：
       #   _stop_reading = True → 停 timer → rtt_stop(守卫) → close(守卫) → self.quit()
       # 主线程仅 wait
       if not self.worker.wait(3000):
           self.logger.error("worker 退出超时，强制 terminate")
           self.worker.terminate()
           self.worker.wait(1000)
       self.cfg.flush()
       event.accept()
   ```
   **关键**：`quit()` 必须由 worker **自己**在清理完 pylink 后调用，**不能**从主线程外部 `worker.quit()`——否则 `quit()` 会和阻塞中的 `rtt_read`/`close` C 调用赛跑，复现原项目"必须 `os._exit` 兜底"的死锁场景。

### 5.2 RTT 监控页（`ui/rtt_monitor_page.py`）

自上而下：

1. **控制栏**：目标设备 `EditableComboBox`（模糊搜索，列表来自 `config.json`）+ 接口 `ComboBox` + 速度 `ComboBox` + RTT 通道 `SpinBox 0-15` + `PrimaryPushButton 连接` + `PushButton 重置`
2. **选项栏**：`CheckBox` 自动滚动 / 暂停接收 / 电源输出 / 实时日志记录 + `PushButton 清除` / `PushButton 💾 保存当前`
3. **设备信息折叠卡片**：`ExpandSettingCard` 风格，展开后显示固件版本/硬件版本/序列号/核心名称/核心 ID/CPU 类型/连接信息
4. **显示区**：`QPlainTextEdit`（性能远胜 QTextEdit；通过 `QTextCharFormat` + `QTextCursor` 仍可着色 ANSI），调 `setMaximumBlockCount(10000)` 防 OOM（此 API 仅 `QPlainTextEdit` 有，QTextEdit 没有）。自动滚动注意时序：**插入文本前**取 `at_bottom = sb.value() >= sb.maximum() - 4`，插入后再视 `at_bottom` 调 `sb.setValue(sb.maximum())`；不能插入后再判断（maximum 已变）
5. **搜索栏**：`SearchLineEdit` + 上/下匹配按钮 + 匹配计数；用 `QTextEdit.find()` + `extraSelections()` 着背景色
6. **发送栏**：`LineEdit` + Hex 模式 `CheckBox` + `PushButton 发送` + 历史下拉（最多 50 条，存 `user_prefs.json`）

**信号连接**：
- 连接按钮 → emit `connect_requested(target, iface, speed, channel)`（iface 是 `"SWD"` / `"JTAG"` 字符串）
- 接收 `rtt_data_received(text)` → `ansi_parser.parse_ansi(text)` → 各段 `QTextCursor.insertText(seg, fmt)`
- 接收 `connection_state_changed(connected, info)` → 切按钮文案、刷新设备信息卡片

### 5.3 内存查看页（`ui/memory_viewer_page.py`）

三块：

1. **读取区**：起始地址 / 大小 `LineEdit` + `PushButton 读取` + `PushButton 清空`
2. **Hex 显示区**：等宽字体 `QPlainTextEdit`，只读
3. **固件导出 `CardWidget`**：起始地址 + 大小（预设 128KB/256KB/512KB/1MB/2MB 或自定义）+ 保存路径选择 + 开始按钮 + `ProgressBar`

**未连接保护**：J-Link 未连接时整页置灰 + 上方 `InfoBar` 提示「请先到 RTT 监控页连接 J-Link」。

**信号连接**：
- 读取 → emit `read_memory_requested(addr, size)`，接收 `memory_read_finished(addr, raw_bytes)` → `format_hex_dump(raw_bytes, addr)` → 填进显示区
- 开始导出 → emit `export_firmware_requested(path, addr, size)`，**前置**：弹 `InfoBar.warning` 提示 RTT 接收将暂停 + 禁用 RTT 页发送/连接按钮；接收 `firmware_export_progress` → 更新 ProgressBar；接收 `firmware_export_finished` → `InfoBar` + 恢复 RTT 页按钮

### 5.4 设置页（`ui/settings_page.py`）

`SettingCardGroup` + 各种 `OptionsSettingCard`/`PushSettingCard`：

**外观组**：
- 主题模式（OptionsSettingCard：浅色/深色/跟随系统）
- 主题色（ColorPickerCard 自定义或 qfluentwidgets 的 `ColorSettingCard`）
- 显示字体（FontSettingCard）
- 字体大小（SpinBox 8-24）

**RTT 行为组**：
- 显示区最大行数（SpinBox）
- Rx Timeout（SpinBox ms）
- 日志保存目录（FolderListSettingCard）
- 「打开日志目录」按钮

所有变更立即写 `user_prefs.json`；主题/字体改动通过 `ConfigService` 信号广播到各页面热应用。

### 5.5 关于页（`ui/about_page.py`）

参考原项目 about 卡片样式，用 fluent 组件重做：
- 顶部 LOGO + 「J-Link RTT Viewer」+ 版本号
- 功能介绍两张 `CardWidget`：📊 RTT 监控 / 🔍 内存查看
- 作者信息一张 `CardWidget` + GitHub `HyperlinkButton`
  - **TBD**：作者名 / GitHub 链接由用户在实施阶段最终敲定（首版可暂用「待定」占位）
- 底部版权 + 第三方致谢（pylink-square、QFluentWidgets）

---

## 6. 接线总览

```
┌──── MainWindow ────────────────────────────────────────────┐
│                                                            │
│  ┌─ JLinkWorker (QThread) ──────────────────────────────┐  │
│  │  inputs:  connect_requested, disconnect_requested,   │  │
│  │           send_data_requested, reset_target_requested│  │
│  │           read_memory_requested, export_firmware_... │  │
│  │  outputs: rtt_data_received, connection_state_...    │  │
│  │           log_message, command_result,               │  │
│  │           memory_read_finished, firmware_export_...  │  │
│  └──────────────────────────────────────────────────────┘  │
│         ▲                            │                     │
│         │ QueuedConnection           │ QueuedConnection    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ RTTMonitor   │  │ MemoryViewer │  │ Settings/    │      │
│  │ Page         │  │ Page         │  │ About Page   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│         │                                                  │
│         │ theme_changed, font_changed                      │
│         ▼                                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ConfigService (singleton, main thread)               │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

**关键约束**：UI 页面**不直接持有** `pylink.JLink` 实例；只 emit 命令信号、connect 响应信号。改 UI 不影响 worker 时序。

---

## 7. 错误处理与日志

### 7.1 错误分级

| 级别 | 触发场景 | 处理 |
|---|---|---|
| 可预期错误 | 用户输入错、未连接时点读取、地址非法 | `command_result(success=False, payload={"error":...})` → UI `InfoBar.warning`，不打断流程 |
| pylink 异常 | rtt_read 出错、设备掉线 | worker 捕获 → `log_message('error', ...)` + 切回 `IDLE` + `connection_state_changed(False, {})`，UI 自动恢复"未连接" |
| 致命错误 | DLL 加载失败、找不到 J-Link | 启动时检测 → `MessageBox` 提示后退出 |

**禁止** worker 内 `bare except: pass`。每个 except 至少 `log_message('warning', ...)`。

### 7.2 日志

- 文件：`%APPDATA%/JLinkRTTViewer/logs/app.log`，`RotatingFileHandler`
- 控制台 INFO+
- 设置页有「打开日志目录」按钮

---

## 8. Git 与提交规范

- **初次提交**：`git init` → `.gitignore`（忽略 `venv/`、`__pycache__/`、`build/`、`dist/`、`*.spec`、`user_prefs.json`、`logs/`、`.idea/`、`.vscode/`）→ commit `chore: 初始化项目结构与依赖`
- **后续约定式提交 + 中文描述**：
  - `feat: 新增 RTT 监控页基础布局`
  - `feat(memory): 实现固件导出分块进度回调`
  - `fix(jlink): 修复 close 后无法再次 open 的问题`
  - `refactor(ui): 拆分发送栏为独立 widget`
  - `chore: 升级 pylink-square 到 1.6.0`
  - `docs: 更新 CLAUDE.md 关闭死锁部分`
- 按里程碑提交，每个 commit 自包含、可回滚

---

## 9. CLAUDE.md 初始踩坑条目

`CLAUDE.md` 用「现象 / 原因 / 处理」三段式（沿用原项目风格），初始预置：

1. **pylink `close()` 在未连接时抛 `JLinkException`** —— 必须 `if jlink.opened()` 守卫；同理 `rtt_stop` 需 `if jlink.connected()`
2. **不要做 "open → 取 serial → close → 再 open" 双开** —— 1.6.0 直接 `open()` 即可
3. **`set_tif(SWD | JTAG)` 是错的** —— SWD/JTAG 互斥枚举，不能 OR；按用户选择二选一调用
4. **QThread 子类陷阱：`__init__` 跑在主线程** —— `QTimer` / `JLink` / `IncrementalDecoder` 必须在 `run()` 内创建，否则 thread affinity 错位，timer 不触发
5. **worker 退出必须 worker 自己 `quit()`** —— 主线程只 `wait()`，不能外部 `quit()`，否则会和阻塞中的 `rtt_read`/`close` C 扩展调用赛跑，复现原项目 `os._exit` 兜底场景
6. **`IncrementalDecoder` 自管半字节缓冲** —— 不要在外层再叠 `byte_buffer` + `getstate()[1]`，那是抄来的 bug（`[1]` 是 int state，不是剩余字节）；直接 `decoder.decode(bytes(data))`，每次重连重建 decoder
7. **`QTextEdit` 没有 `setMaximumBlockCount`** —— 那是 `QPlainTextEdit` 的 API；RTT 显示区一律用 `QPlainTextEdit`
8. **自动滚动判断时机** —— 必须在插入文本**前**取 `at_bottom`，插入后 maximum 已变化，判断会失效
9. **`user_prefs.json` 放 `%APPDATA%`** —— 不放安装目录，避免 `Program Files` 权限问题；开发期也不回落到 `src/`，避免与打包后行为不一致
10. **`ConfigService.set()` 高频值要节流** —— 窗口几何/字体大小不能每次 setter 都 atomic replace 写盘，会拖死 UI 并损耗 SSD
11. **`closeEvent` 必须 `cfg.flush()`** —— 节流策略下，关闭前的最后一次 `set()` 可能尚未落盘
12. **Nuitka 打包 qfluentwidgets 资源** —— `--include-package-data=qfluentwidgets` 否则运行时 qss 找不到

实施过程每遇新坑即追加。

---

## 10. 构建脚本

### 10.1 start.bat

```bat
@echo off
call venv\Scripts\activate.bat
python src\main.py
```

### 10.2 build_nuitka.bat

```bat
@echo off
call venv\Scripts\activate.bat
python -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=resource\app.ico ^
    --include-package=qfluentwidgets ^
    --include-package-data=qfluentwidgets ^
    --include-data-dir=resource=resource ^
    --include-data-files=src\config.json=config.json ^
    --output-dir=build ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py
copy JLinkARM.dll build\main.dist\
echo Build done. Output: build\main.dist\
```

`JLinkARM.dll` 由用户从原项目拷贝过来（或 pylink 自带）。

---

## 11. 验收要点

- [ ] 启动应用，左侧导航四项均可切换，无报错
- [ ] RTT 监控页：连接真实 J-Link → 显示设备信息 → MCU 端 `SEGGER_RTT_printf` 中文/英文/ANSI 颜色全部正常显示
- [ ] 连接后断开，再次连接，**不死锁、不报错**（修复原项目核心痛点）
- [ ] 发送字符串/Hex 到 MCU，MCU 端正确收到
- [ ] 切换 RTT 通道（0-15）正常工作
- [ ] 实时日志记录 + 一键保存当前显示，文件落到设置指定目录
- [ ] 内存查看页：读取 0x08000000+0x100，hex dump 正确
- [ ] 固件导出 128 KB，进度条流畅，文件大小正确
- [ ] 设置页改主题色/字体/主题模式，立即生效
- [ ] 关闭窗口，进程干净退出（无残留进程、无 `os._exit` 兜底）
- [ ] 重启应用，上次选择的芯片/速度/通道/窗口几何/主题全部恢复
- [ ] `python -m nuitka ...` 成功打包，分发包双击可运行

---

## 12. 测试策略

### 12.1 单元测试（pytest）

`core` 层有意做成 UI 无关，可独立 pytest：

| 模块 | 覆盖目标 | 关键用例 |
|---|---|---|
| `ansi_parser` | CSI/SGR 解析 | 单色、嵌套 reset、多参数 (`\x1b[1;31;42m`)、未结束序列跨段、无效序列退化为字面量 |
| `memory_service.format_hex_dump` | 纯函数格式化 | 非 16 倍数长度、空 bytes、ASCII 可视/不可视字符切换 |
| `memory_service.read_memory` | 用 `MagicMock` mock `jlink` | 返回字节顺序、`memory_read` 抛异常时上抛 |
| `config_service` | 读写 + 节流 + atomic replace | 写中途崩溃文件不损坏、`flush()` 同步落盘、并发 `set()` 合并 |

CI（GitHub Actions / 本地 hook）跑 `pytest -q`，覆盖率不强制，但 ansi_parser 应 ≥90%。

### 12.2 真机回归

§11 的验收清单即真机回归脚本。每次合入 `main` 前手动跑一遍。

---

## 13. 风险与回退

| 风险 | 触发条件 | 兜底 |
|---|---|---|
| worker `wait(3000)` 超时 | pylink C 调用真的卡死 | `worker.terminate()` + 日志 `error`，下次启动重新初始化 JLink 实例 |
| JLinkARM.dll 缺失 | 用户机器没装 J-Link 驱动 | 启动期 `MessageBox` 提示并退出，README 写明驱动下载地址 |
| qfluentwidgets API breaking change | 升级到 2.x | requirements.txt 锁 `>=1.6,<2.0` |
| RTT 高吞吐 UI 卡顿 | >200 KB/s 持续输出 | 设置页加「最大行数」调小到 5000；timer interval 改 10ms（可配置） |
