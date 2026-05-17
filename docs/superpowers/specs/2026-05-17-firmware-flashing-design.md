# 固件烧录页设计

**日期**：2026-05-17
**目标版本**：v0.3.0
**状态**：草案，待用户最终复审

---

## 1. 背景与目标

现有 J-Link RTT Viewer 已经覆盖 RTT 监控 + 内存查看 + 设置 + 关于。
用户希望增加 **固件烧录** 能力：
- 选择 `.axf` / `.elf` / `.hex` / `.bin` 文件烧录到目标 MCU。
- 模块完全独立，**不干涉**现有 RTT / 内存模块的代码与会话。
- 按下"烧录"按钮自动 connect → flash → reset/run → disconnect 一气呵成。
- 用户体验贴近开源项目水准（拖放、最近文件、自动检测变更、可复制日志）。

**非目标**：
- 不做"调试器"功能（断点、单步），仅烧录。
- 不做芯片 Flash 范围预检表，越界让 J-Link DLL 自己报错。
- 不做烧录中途取消（pylink 没有真正的"安全中止"；用户烧错文件覆盖式重烧即可）。

---

## 2. 关键决策摘要

| # | 决策 | 选项 |
|---|---|---|
| 1 | J-Link 会话归属 | **完全独立**：烧录页拥有自己的 `FlashWorker` + 独立 `pylink.JLink` 实例 + 独立 `QThread`。用户负责确保烧录前 RTT 页已断开（不自动协调）。 |
| 2 | bin 文件起始地址 | **页面常驻输入框 + 持久化**。默认 `0x08000000`，axf/hex 时置灰（用文件内地址）。 |
| 3 | 擦除模式 | **用户可选**：扇区擦除（默认） vs 整片擦除。 |
| 4 | 完成动作 | **下拉**：仅烧录 / 烧录+复位 / 烧录+复位+运行（默认）。**额外 byte-by-byte verify** 单独 checkbox（默认关）。 |
| 5 | 连接参数来源 | **完全独立**：烧录页有自己的 device / iface / speed 控件并独立持久化（用户可烧不同目标，无需切回 RTT 页改）。 |
| 6 | auto-connect 时机 | **按下"开始烧录"时**才连，烧完自动 disconnect 释放 J-Link 句柄。 |
| 7 | 进度展示 | **简单 ProgressBar + 阶段文字**，无中途取消（避免半砖状态）。详情面板默认折叠，失败自动展开。 |
| 8 | 最近文件历史 | **下拉 + mtime 变更提示**。最多 10 个，时间倒序持久化。 |
| 9 | 拖放 + 文件校验 | **支持拖放 + 完整文件解析**（用 pyelftools / intelhex 解析 axf/hex 地址范围）。芯片 Flash 范围检查交给 J-Link DLL。 |
| 10 | 错误展示 | **InfoBar toast + 详情面板自动展开 + 复制日志按钮**。烧录中失败固定文案提示"Flash 已部分擦除，建议整片擦除后重烧"。 |

---

## 3. 模块结构

```
src/
├── core/
│   ├── flash_worker.py        # FlashWorker(QObject) — 独立 pylink 会话 + 独立 QThread
│   └── flash_file_parser.py   # 纯函数：axf/hex/bin 地址范围解析
└── ui/
    └── flash_page.py          # FlashPage(QWidget) — 整页 UI（透明 ScrollArea 包裹）
```

**新增 Python 依赖**（加入 `requirements.txt` + Nuitka 打包脚本）：
- `pyelftools`（MIT，活跃）
- `intelhex`（MIT，活跃）

`build_nuitka.bat` / `build_nuitka_onefile.bat` 加：
```
--include-package=pyelftools
--include-package=intelhex
```

**MainWindow 集成（最小侵入）**：
- 导航栏新增"烧录"入口（FluentIcon 选合适图标，如 `SEND_FILL` / `DEVELOPER_TOOLS`）。
- `closeEvent` 多 join `_flash_thread`（如果它在运行）。
- 不动 RTT / 内存 / 设置任何代码。

**用户偏好键**（新增到 `ConfigService` DEFAULTS）：
```python
flash_device_name: str = "STM32H750VB"
flash_interface: str = "SWD"
flash_speed: int = 4000
flash_bin_address: int = 0x08000000
flash_erase_mode: str = "sector"          # "sector" | "chip"
flash_post_action: str = "reset_run"      # "none" | "reset" | "reset_run"
flash_verify: bool = False
flash_recent_files: list[str] = []        # 最多 10 个，时间倒序
flash_recent_files_mtime: dict[str, float] = {}  # path → mtime，用于变更提示
```

---

## 4. UI 布局

整页用 `_scroll_helpers.make_transparent_scroll` 包裹（4 页同款，零 padding / 无边框 / 透明）。

```
┌─ 连接参数（CardWidget） ─────────────────────────────┐
│ Device:    [STM32H750VB   ▼ EditableComboBox]      │
│ Interface: ( ) SWD  ( ) JTAG                       │
│ Speed:     [4000   ] kHz                           │
└─────────────────────────────────────────────────────┘

┌─ 固件文件（CardWidget，整卡支持 drop） ─────────────┐
│ File:    [path/to/blink.axf      ▼] [浏览...]      │
│  └─ ComboBox 下拉显示最近 10 个，文件名 + mtime     │
│  └─ 当前文件 mtime 比上次记录新时显示 "● updated"   │
│                                                     │
│ Format:  axf  (auto-detected, read-only label)     │
│ Range:   0x08000000 – 0x0801A4C0  (107 KB)         │
│  └─ axf/hex 解析后自动填；bin 时显示"需手动填地址" │
│                                                     │
│ Bin Start: [0x08000000]   ← 只在 bin 时可编辑       │
└─────────────────────────────────────────────────────┘

┌─ 烧录选项（CardWidget） ─────────────────────────────┐
│ 擦除模式:   [扇区擦除（推荐） ▼]                    │
│ 完成动作:   [烧录 + 复位 + 运行 ▼]                  │
│ ☐ 额外 byte-by-byte verify（慢一倍）                │
└─────────────────────────────────────────────────────┘

┌─ 执行 ──────────────────────────────────────────────┐
│  [ 开始烧录 ]   (PrimaryPushButton, 大)             │
│                                                     │
│  阶段: 写入中…              ProgressBar  ████░░ 67%│
│  ▶ 详情 (折叠，失败自动展开)                         │
│      [日志区，PlainTextEdit + 复制日志按钮]         │
└─────────────────────────────────────────────────────┘
```

**关键交互细节**：
- 文件选定后立即在**主线程**调 parser 填 Format / Range（解析 <10ms）。
- 后缀分派：`.axf` / `.elf` → ELF；`.hex` → Intel HEX；`.bin` → raw。
- 拖入文件落到任意 child 都触发 (`FlashPage.dragEnterEvent` + `dropEvent`)。
- 开始烧录后禁用所有输入控件 + 按钮文字改 "烧录中…"。
- 完成后（不论成功/失败）恢复输入；成功 `InfoBar.success`，失败 `InfoBar.error`。
- 详情面板默认收起；点击展开/收起；失败时自动展开。
- "复制日志"按钮：拷贝详情区全文 + 顶部段头（app version / OS / pylink version），方便贴 issue。

---

## 5. 数据流 & 接口

### 5.1 `FlashParams` dataclass (`flash_worker.py` 顶部)

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class FlashParams:
    file_path: str
    file_format: str          # "elf" | "hex" | "bin"
    bin_start_addr: int       # 仅 bin 用，其它格式忽略
    device_name: str
    interface: str            # "SWD" | "JTAG"
    speed_khz: int
    erase_mode: str           # "sector" | "chip"
    post_action: str          # "none" | "reset" | "reset_run"
    extra_verify: bool
```

### 5.2 公开常量（避免散落字面值，参考 CLAUDE.md "模式/枚举字符串必须有常量"）

```python
# erase mode
ERASE_MODE_SECTOR = "sector"
ERASE_MODE_CHIP = "chip"

# post action
POST_ACTION_NONE = "none"
POST_ACTION_RESET = "reset"
POST_ACTION_RESET_RUN = "reset_run"

# file format
FORMAT_ELF = "elf"
FORMAT_HEX = "hex"
FORMAT_BIN = "bin"

# stage
STAGE_CONNECT = "connect"
STAGE_ERASE = "erase"
STAGE_PROGRAM = "program"
STAGE_VERIFY = "verify"
STAGE_RESET = "reset"
STAGE_DISCONNECT = "disconnect"
```

### 5.3 `FlashWorker` 信号（全用基础类型，避开 PySide6 跨线程 dict 坑）

```python
class FlashWorker(QObject):
    # 输入（UI → worker）
    flash_requested = Signal()           # 配合 set_pending_params() lock
    stop_requested = Signal()            # 关窗清理用，非"取消烧录"

    # 输出（worker → UI）
    flash_started = Signal()
    flash_stage_changed = Signal(str)        # STAGE_*
    flash_progress = Signal(int, int)        # (current_bytes, total_bytes)
    flash_log = Signal(str, str)             # (level, msg) — "info"/"warn"/"error"
    flash_finished = Signal(bool, str)       # (success, summary_text)
```

### 5.4 参数传递（避开 dict 跨线程 Signal）

参考 CLAUDE.md "PySide6 跨线程 Signal 不要传 dict" 踩坑总结。
改 "setter + lock + 无参 emit"：

```python
class FlashWorker(QObject):
    def __init__(self):
        super().__init__()
        self._jlink: pylink.JLink | None = None
        self._pending_params: FlashParams | None = None
        self._params_lock = threading.Lock()

    def set_pending_params(self, params: FlashParams) -> None:
        """UI 线程调；GIL+lock 保护，不走 Qt 信号 marshalling。"""
        with self._params_lock:
            self._pending_params = params

    @Slot()
    def initialize(self) -> None:
        """thread.started → 这里。worker 线程内创建 pylink.JLink。"""
        self._jlink = pylink.JLink()

    @Slot()
    def _on_flash_requested(self) -> None:
        with self._params_lock:
            params = self._pending_params
            self._pending_params = None
        if params is None:
            return
        self._run_flash(params)
```

UI 调用模式：
```python
self._worker.set_pending_params(params)
self._worker.flash_requested.emit()   # 无参跨线程，安全
```

跨线程信号全部显式 `Qt.QueuedConnection`（参考 CLAUDE.md "worker → UI 跨线程信号一律显式 QueuedConnection"）。

### 5.5 `_run_flash` 一条龙骨架

```python
def _run_flash(self, p: FlashParams) -> None:
    self.flash_started.emit()
    try:
        # --- connect ---
        self.flash_stage_changed.emit(STAGE_CONNECT)
        self._do_connect(p.device_name, p.interface, p.speed_khz)

        # --- erase（chip 模式才显式擦；sector 模式由 flash_file 内含）---
        if p.erase_mode == ERASE_MODE_CHIP:
            self.flash_stage_changed.emit(STAGE_ERASE)
            self._jlink.erase()
            self.flash_log.emit("info", "chip erase OK")

        # --- program ---
        addr = p.bin_start_addr if p.file_format == FORMAT_BIN else 0
        self.flash_stage_changed.emit(STAGE_PROGRAM)
        self._jlink.flash_file(p.file_path, addr,
                               on_progress=self._on_pylink_progress)

        # --- extra verify (optional, byte-by-byte) ---
        if p.extra_verify:
            self.flash_stage_changed.emit(STAGE_VERIFY)
            self._verify_bytewise(p)

        # --- post action ---
        if p.post_action in (POST_ACTION_RESET, POST_ACTION_RESET_RUN):
            self.flash_stage_changed.emit(STAGE_RESET)
            self._jlink.reset(halt=(p.post_action == POST_ACTION_RESET))
        if p.post_action == POST_ACTION_RESET_RUN:
            self._jlink.restart()  # 释放 halt

        # --- disconnect ---
        self.flash_stage_changed.emit(STAGE_DISCONNECT)
        self._safe_disconnect()
        self.flash_finished.emit(True, "烧录成功")

    except Exception as e:
        self.flash_log.emit("error", f"{type(e).__name__}: {e}")
        self._safe_disconnect()
        self.flash_finished.emit(False, str(e))
```

### 5.6 连接顺序（严格按 CLAUDE.md "pylink 1.6.0 连接顺序"）

```python
def _do_connect(self, device: str, iface: str, speed: int) -> None:
    j = self._jlink
    if not j.opened():
        j.open()
        ser = j.serial_number
        j.close()
        j.open(str(ser))
        # 烧录不需要 rtt_start —— 但参考项目两阶段 open 模式保留
    tif = pylink.enums.JLinkInterfaces.SWD if iface == "SWD" \
          else pylink.enums.JLinkInterfaces.JTAG
    j.set_tif(tif)
    j.set_speed(int(speed))
    j.connect(device)
    self.flash_log.emit("info", f"J-Link SN: {j.serial_number}")
    self.flash_log.emit("info", "Target connected")
```

### 5.7 退出清理（参考 CLAUDE.md "worker 线程内的 QTimer/QObject 退出前必须 stop + deleteLater"）

```python
@Slot()
def _on_stop(self) -> None:
    self._safe_disconnect()
    t = self.thread()
    if t is not None:
        t.quit()
```

烧录页本身没有 QTimer（flash_file 是阻塞调用），比 RTT worker 简单一些。

---

## 6. 文件解析（`flash_file_parser.py`）

纯函数模块，**零 Qt 依赖**，独立可 pytest。

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class FileInfo:
    fmt: str               # "elf" | "hex" | "bin"
    addr_start: int        # bin 模式由调用方填，解析器返回 0
    addr_end: int          # exclusive
    total_bytes: int       # 实际要烧的字节数
    notes: str             # 人类可读："2 LOAD segments" / "32 hex records" 等

class FileParseError(Exception):
    """文件不存在 / 格式损坏 / 不支持的后缀都抛这个。"""

def detect_format(path: str) -> str: ...
def parse_file(path: str, bin_start_addr: int = 0) -> FileInfo: ...

# --- 内部 ---
def _parse_elf(path: str) -> FileInfo:
    """pyelftools 走 LOAD segments，取 min(p_paddr) ~ max(p_paddr + p_filesz)。"""

def _parse_hex(path: str) -> FileInfo:
    """intelhex .minaddr() / .maxaddr() + 1，total_bytes 用 len(ih)。"""

def _parse_bin(path: str, start_addr: int) -> FileInfo:
    """文件 size + start_addr。"""
```

**校验责任划分**：
- **解析层**：格式合法 + 地址范围 + 总字节数。错误 → `FileParseError`。
- **UI 层**：收 `FileParseError` 弹 toast，不进入烧录流程。
- **Worker / J-Link DLL 层**：地址是否在芯片 Flash 范围内由 DLL 兜底（不在我们这边维护芯片 Flash 表）。

---

## 7. 错误处理

### 错误分级

| 级别 | 来源 | 表现 |
|---|---|---|
| 用户操作错误 | 文件未选 / bin 模式无地址 / device 留空 | InfoBar.warning，按钮不进入烧录 |
| 文件错误 | `FileParseError` | InfoBar.error + 详情面板自动展开 |
| 连接错误 | `pylink.JLinkException`（open/connect 阶段） | UI 解锁；详情展开显示错误码；**Flash 未受影响** |
| 烧录中错误 | flash_file 抛异常 | **固定文案**：`⚠ Flash 已部分擦除/写入，建议下次用「整片擦除」重烧` 加进详情区 |
| 退出错误 | `_safe_disconnect` 内部异常 | 仅 `_logger.warning` 写日志，不打扰用户（参考 CLAUDE.md "close/rtt_stop 抛异常不致命"） |

### 详情日志结构（feeds `flash_log` 信号 + 详情 PlainTextEdit）

```
[14:23:01.123] === Flash session ===
[14:23:01.124] File: D:/proj/blink/blink.axf (elf, 107 KB)
[14:23:01.124] Range: 0x08000000 – 0x0801A4C0
[14:23:01.125] Device: STM32H750VB | SWD @ 4000 kHz
[14:23:01.130] Options: erase=sector post=reset_run verify=off
[14:23:01.130] --- connect ---
[14:23:02.451] J-Link SN: 851012345
[14:23:02.460] Target connected
[14:23:02.461] --- program ---
[14:23:02.500] progress 12% (13312 / 109760 bytes)
[14:23:08.211] flash_file OK (5.7s)
[14:23:08.220] --- reset ---
[14:23:08.330] CPU running
[14:23:08.331] --- disconnect ---
[14:23:08.450] === Done, success (7.3s) ===
```

### "复制日志" 段头

按下按钮时拷贝到剪贴板的内容前缀：
```
J-Link RTT Viewer / Flash log
App version: <APP_VERSION>
OS: <platform.platform()>
pylink-square: 1.6.0
PySide6: <PySide6.__version__>
---
<日志正文>
```

### `_safe_disconnect`（参考 CLAUDE.md `_do_disconnect`）

```python
def _safe_disconnect(self) -> None:
    if self._jlink is None:
        return
    try:
        self._jlink.close()
    except pylink.JLinkException as e:
        self.flash_log.emit("warn", f"close warn: {e}")
```

---

## 8. 测试策略

| 层 | 测试方式 | 文件 |
|---|---|---|
| `flash_file_parser` 纯函数 | pytest，3 个 fixture 固件文件 | `tests/test_flash_file_parser.py` |
| `FlashParams` dataclass | frozen + 字段类型 | （同上） |
| `FlashWorker` 流程 | mock `pylink.JLink`（仿 RTT worker 测试范式），断言信号序列：started → stage(connect) → stage(program) → progress(...) → stage(reset) → finished(True, …) | `tests/test_flash_worker.py` |
| `FlashWorker` 错误路径 | mock `flash_file` 抛异常，断言 `flash_finished(False, …)` + `_safe_disconnect` 被调 | （同上） |
| UI (FlashPage) | **不自动化**。手动测 checklist：拖文件、3 种格式各烧一次、bin 改地址、erase 切换、verify 勾选、详情展开、复制日志 | `docs/manual-test-flash.md` |

Fixture 固件文件来源：从某个开源 STM32 demo 项目（如 STM32CubeF7 的 Blink）借小型的 `.axf` / `.hex` / `.bin`，提交到 `tests/fixtures/`。

---

## 9. 实现拆分预览（后续 writing-plans 的输入）

按依赖顺序：
1. **依赖 + 偏好键**：`requirements.txt` 加 `pyelftools` / `intelhex`；`ConfigService` DEFAULTS 加 `flash_*` 字段；打包脚本加 `--include-package`。
2. **`flash_file_parser.py`** + pytest（可独立完成，无 Qt 依赖，先做先安心）。
3. **`flash_worker.py`** + pytest（mock pylink）。
4. **`flash_page.py`** UI 实现（连接参数 / 文件 / 选项 / 执行 4 个 Card + 透明 ScrollArea）。
5. **MainWindow 集成**：导航入口 + closeEvent 清理。
6. **手动测试 + 文档**：3 种格式实机烧录、CHANGELOG / README 更新。

---

## 10. 待 writing-plans 阶段确认

- pyelftools / intelhex 是否能正常被 Nuitka 打包（之前 pylink / qfluentwidgets 都需要 `--include-package-data`，这两个新依赖照同样模式加，但需在打包验证）。
- `jlink.flash_file()` 的 `on_progress` 回调签名（pylink 1.6.0 文档需对照）—— 实现期间确认。
- 烧录失败后是否真的留下"半擦除" Flash —— 实机验证一次以确保文案准确。
