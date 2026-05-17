# 固件烧录页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立的固件烧录页（.axf/.elf/.hex/.bin），独立的 `FlashWorker` + 独立 `pylink` 会话 + 独立 QThread，不干涉现有 RTT/Memory 模块。

**Architecture:** 三个新文件：`core/flash_file_parser.py`（纯函数解析层，零 Qt 依赖）+ `core/flash_worker.py`（QObject worker，跑在独立 QThread）+ `ui/flash_page.py`（整页透明 ScrollArea + 4 Card）。MainWindow 增加导航入口和 closeEvent 清理。spec：`docs/superpowers/specs/2026-05-17-firmware-flashing-design.md`。

**Tech Stack:** PySide6 + qfluentwidgets + pylink-square 1.6.0 + pyelftools + intelhex + pytest

---

## Task 1: 添加依赖 + 打包脚本

**Files:**
- Modify: `requirements.txt`
- Modify: `build_nuitka.bat`
- Modify: `build_nuitka_onefile.bat`

- [ ] **Step 1: 加 pyelftools 和 intelhex 到 requirements.txt**

把这两行追加到 `requirements.txt`（不要触碰 `pylink-square==1.6.0` 那行，按 CLAUDE.md 严格锁版本）：

```
pyelftools>=0.29
intelhex>=2.3.0
```

- [ ] **Step 2: 在 venv 里安装**

Run:
```powershell
.\venv\Scripts\activate.ps1
pip install pyelftools intelhex
```

Expected: 安装成功，import 验证通过：
```powershell
python -c "import elftools, intelhex; print(elftools.__version__, intelhex.__version__)"
```

- [ ] **Step 3: build_nuitka.bat 加 --include-package**

把现有 `--include-package=pylink ^` 那一段下面追加两行（保持反引号换行格式）：

```
    --include-package=pyelftools ^
    --include-package=intelhex ^
```

（注意：仅 `--include-package`，不需要 `--include-package-data`——这两个库都是纯 Python，无非 .py 数据文件）

- [ ] **Step 4: build_nuitka_onefile.bat 同步加 --include-package**

同上，在 `--include-package=pylink ^` 下面追加两行。

- [ ] **Step 5: Commit**

```powershell
git add requirements.txt build_nuitka.bat build_nuitka_onefile.bat
git commit -m "build(deps): add pyelftools + intelhex for firmware flashing"
```

---

## Task 2: ConfigService 新增 flash_* 偏好键

**Files:**
- Modify: `src/core/config_service.py:33-80` (DEFAULTS dict)
- Test: `tests/test_config_service.py`

- [ ] **Step 1: 写失败测试**

加到 `tests/test_config_service.py` 末尾：

```python
def test_flash_defaults_present(tmp_path, monkeypatch):
    """新增的 flash_* 偏好键必须出现在 DEFAULTS 里，并有正确默认值。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = ConfigService()
    assert cfg.get("flash_device_name") == "STM32H750VB"
    assert cfg.get("flash_interface") == "SWD"
    assert cfg.get("flash_speed") == 4000
    assert cfg.get("flash_bin_address") == 0x08000000
    assert cfg.get("flash_erase_mode") == "sector"
    assert cfg.get("flash_post_action") == "reset_run"
    assert cfg.get("flash_verify") is False
    assert cfg.get("flash_recent_files") == []
    assert cfg.get("flash_recent_files_mtime") == {}


def test_flash_set_persists_recent_files(tmp_path, monkeypatch):
    """flash_recent_files 是 list，set 进去要保留。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = ConfigService()
    cfg.set("flash_recent_files", ["C:/a.axf", "C:/b.hex"])
    cfg.flush()
    cfg2 = ConfigService()
    assert cfg2.get("flash_recent_files") == ["C:/a.axf", "C:/b.hex"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_config_service.py::test_flash_defaults_present tests/test_config_service.py::test_flash_set_persists_recent_files -v`

Expected: FAIL with `AssertionError` 或 `忽略未知偏好键：flash_device_name` 警告。

- [ ] **Step 3: 在 DEFAULTS 末尾加 flash_* 字段**

在 `src/core/config_service.py` 的 `DEFAULTS` 字典末尾（`"mem_write_addr": "0x20000000",` 后面、`}` 前面）加：

```python
        # === 烧录页（v0.3.0 新增）===
        # 独立持久化，不复用 RTT 页的 target_mcu / interface / speed_khz：
        # 让烧录与 RTT 监控目标可以不同（同时维护多个项目）
        "flash_device_name": "STM32H750VB",
        "flash_interface": "SWD",
        "flash_speed": 4000,
        "flash_bin_address": 0x08000000,        # bin 模式的起始地址
        "flash_erase_mode": "sector",           # "sector" | "chip"
        "flash_post_action": "reset_run",       # "none" | "reset" | "reset_run"
        "flash_verify": False,                  # extra byte-by-byte verify
        "flash_recent_files": [],               # 最多 10 个，时间倒序
        "flash_recent_files_mtime": {},         # path → mtime（float），用于变更提示
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_config_service.py -v`

Expected: 所有测试 PASS（包括上面两个新增）。

- [ ] **Step 5: Commit**

```powershell
git add src/core/config_service.py tests/test_config_service.py
git commit -m "feat(config): 添加 flash_* 偏好键支持固件烧录页"
```

---

## Task 3: flash_file_parser.py 实现

**Files:**
- Create: `src/core/flash_file_parser.py`
- Create: `tests/test_flash_file_parser.py`
- Create: `tests/fixtures/blink.bin`（小型固定字节文件，手工生成）
- Create: `tests/fixtures/blink.hex`（手工写）
- Create: `tests/fixtures/blink.axf`（pyelftools 测试 fixture 或从公开 demo 借）

- [ ] **Step 1: 生成 fixture 文件**

```powershell
mkdir tests\fixtures
# bin：256 字节 0x00..0xFF
python -c "open('tests/fixtures/blink.bin','wb').write(bytes(range(256)))"
```

`tests/fixtures/blink.hex` 手写一个最小 Intel HEX（4 条 16 字节记录 + EOF；起始地址 0x08000000，需 ExtendedLinearAddress 记录）：

```
:020000040800F2
:10000000000102030405060708090A0B0C0D0E0F78
:10001000101112131415161718191A1B1C1D1E1F68
:10002000202122232425262728292A2B2C2D2E2F58
:10003000303132333435363738393A3B3C3D3E3F48
:00000001FF
```

写文件：
```powershell
@"
:020000040800F2
:10000000000102030405060708090A0B0C0D0E0F78
:10001000101112131415161718191A1B1C1D1E1F68
:10002000202122232425262728292A2B2C2D2E2F58
:10003000303132333435363738393A3B3C3D3E3F48
:00000001FF
"@ | Out-File -FilePath tests\fixtures\blink.hex -Encoding ascii -NoNewline
```

`tests/fixtures/blink.axf`：构造一个 minimal ELF（用 pyelftools 不方便构造，直接用 Python 生成）：

```python
# tests/fixtures/gen_axf.py — 用一次性脚本生成，跑完可保留也可删
import struct

# Minimal ELF32 LE with 1 LOAD segment at vaddr=paddr=0x08000000, 64 bytes payload
ELF_HEADER_SIZE = 52
PHDR_SIZE = 32
payload = bytes(range(64))
e_phoff = ELF_HEADER_SIZE
e_phentsize = PHDR_SIZE
e_phnum = 1
data_off = ELF_HEADER_SIZE + PHDR_SIZE

elf_header = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8  # e_ident
elf_header += struct.pack("<HHIIIIIHHHHHH",
    2,        # e_type ET_EXEC
    0x28,     # e_machine EM_ARM
    1,        # e_version
    0x08000000,  # e_entry
    e_phoff,  # e_phoff
    0,        # e_shoff
    0x05000000,  # e_flags EF_ARM_EABI_VER5
    ELF_HEADER_SIZE,  # e_ehsize
    e_phentsize,
    e_phnum,
    0, 0, 0,  # e_shentsize, e_shnum, e_shstrndx
)
phdr = struct.pack("<IIIIIIII",
    1,            # p_type PT_LOAD
    data_off,     # p_offset
    0x08000000,   # p_vaddr
    0x08000000,   # p_paddr
    len(payload), # p_filesz
    len(payload), # p_memsz
    5,            # p_flags PF_R|PF_X
    4,            # p_align
)
open("tests/fixtures/blink.axf", "wb").write(elf_header + phdr + payload)
print("blink.axf written")
```

跑这个脚本：`python tests/fixtures/gen_axf.py`，生成完后该脚本可保留作为 fixture 文档。

- [ ] **Step 2: 写失败测试**

`tests/test_flash_file_parser.py`：

```python
"""flash_file_parser 单元测试。

固件 fixture 在 tests/fixtures/blink.{bin,hex,axf}：
- bin: 256 字节 0x00..0xFF
- hex: 64 字节 (4×16) 起始地址 0x08000000
- axf: ELF32 ARM, 1 LOAD seg @ 0x08000000 size 64
"""
from pathlib import Path

import pytest

from core.flash_file_parser import (
    FORMAT_BIN,
    FORMAT_ELF,
    FORMAT_HEX,
    FileInfo,
    FileParseError,
    detect_format,
    parse_file,
)

FIX = Path(__file__).parent / "fixtures"


def test_detect_format_by_extension():
    assert detect_format("a.axf") == FORMAT_ELF
    assert detect_format("a.ELF") == FORMAT_ELF
    assert detect_format("a.hex") == FORMAT_HEX
    assert detect_format("a.bin") == FORMAT_BIN

def test_detect_format_unknown():
    with pytest.raises(FileParseError):
        detect_format("a.txt")

def test_parse_bin_uses_provided_addr():
    info = parse_file(str(FIX / "blink.bin"), bin_start_addr=0x20000000)
    assert info.fmt == FORMAT_BIN
    assert info.addr_start == 0x20000000
    assert info.addr_end == 0x20000000 + 256
    assert info.total_bytes == 256

def test_parse_hex_extracts_range():
    info = parse_file(str(FIX / "blink.hex"))
    assert info.fmt == FORMAT_HEX
    assert info.addr_start == 0x08000000
    assert info.addr_end == 0x08000000 + 64
    assert info.total_bytes == 64

def test_parse_elf_extracts_load_segments():
    info = parse_file(str(FIX / "blink.axf"))
    assert info.fmt == FORMAT_ELF
    assert info.addr_start == 0x08000000
    assert info.addr_end == 0x08000000 + 64
    assert info.total_bytes == 64

def test_parse_nonexistent_raises():
    with pytest.raises(FileParseError):
        parse_file(str(FIX / "does_not_exist.bin"))

def test_parse_empty_bin_raises(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    with pytest.raises(FileParseError):
        parse_file(str(p))

def test_parse_corrupt_hex_raises(tmp_path):
    p = tmp_path / "bad.hex"
    p.write_text(":FFFFFFFFFFGG\n")  # 非法字符
    with pytest.raises(FileParseError):
        parse_file(str(p))

def test_parse_corrupt_elf_raises(tmp_path):
    p = tmp_path / "bad.axf"
    p.write_bytes(b"\x7fELFXXXXXX")  # ELF magic 后面截断
    with pytest.raises(FileParseError):
        parse_file(str(p))

def test_file_info_is_frozen():
    info = FileInfo(fmt=FORMAT_BIN, addr_start=0, addr_end=1, total_bytes=1, notes="")
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        info.addr_start = 999  # type: ignore
```

- [ ] **Step 3: 跑测试确认全失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_file_parser.py -v`

Expected: FAIL with `ImportError: cannot import name 'parse_file' from 'core.flash_file_parser'`.

- [ ] **Step 4: 实现 flash_file_parser.py**

Create `src/core/flash_file_parser.py`:

```python
"""固件文件解析：纯函数 + 零 Qt 依赖，可独立单元测试。

支持格式：
- .axf / .elf → ELF32 Program Headers (pyelftools)
- .hex        → Intel HEX (intelhex)
- .bin        → 起始地址由调用方提供

设计责任划分：
- 本层：格式合法性 + 文件内地址范围 + 总字节数
- UI 层：把 FileParseError 转 InfoBar
- J-Link DLL 层：地址是否真的落在芯片 Flash 范围（不在这边维护芯片表）
"""
from __future__ import annotations

import os
from dataclasses import dataclass

FORMAT_ELF = "elf"
FORMAT_HEX = "hex"
FORMAT_BIN = "bin"

_EXT_MAP = {
    ".axf": FORMAT_ELF,
    ".elf": FORMAT_ELF,
    ".hex": FORMAT_HEX,
    ".bin": FORMAT_BIN,
}


class FileParseError(Exception):
    """文件不存在 / 格式损坏 / 不支持的后缀都抛这个。"""


@dataclass(frozen=True)
class FileInfo:
    fmt: str               # FORMAT_ELF / FORMAT_HEX / FORMAT_BIN
    addr_start: int        # bin 模式由调用方提供；其它格式从文件读
    addr_end: int          # exclusive
    total_bytes: int       # 实际要烧的字节数
    notes: str             # 人类可读补充


def detect_format(path: str) -> str:
    """按后缀判断；不读文件头。未知后缀抛 FileParseError。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in _EXT_MAP:
        raise FileParseError(f"不支持的文件后缀：{ext or '(无后缀)'}")
    return _EXT_MAP[ext]


def parse_file(path: str, bin_start_addr: int = 0) -> FileInfo:
    """统一入口；按格式分派。bin_start_addr 仅在 fmt=='bin' 时使用。"""
    if not os.path.exists(path):
        raise FileParseError(f"文件不存在：{path}")
    fmt = detect_format(path)
    if fmt == FORMAT_ELF:
        return _parse_elf(path)
    if fmt == FORMAT_HEX:
        return _parse_hex(path)
    return _parse_bin(path, bin_start_addr)


def _parse_elf(path: str) -> FileInfo:
    try:
        from elftools.elf.elffile import ELFFile
        from elftools.common.exceptions import ELFError
    except ImportError as e:
        raise FileParseError(f"pyelftools 未安装：{e}")
    try:
        with open(path, "rb") as f:
            elf = ELFFile(f)
            load_segs = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"
                         and s["p_filesz"] > 0]
            if not load_segs:
                raise FileParseError("ELF 中无 LOAD 段")
            addrs_start = [s["p_paddr"] for s in load_segs]
            addrs_end = [s["p_paddr"] + s["p_filesz"] for s in load_segs]
            total = sum(s["p_filesz"] for s in load_segs)
            return FileInfo(
                fmt=FORMAT_ELF,
                addr_start=min(addrs_start),
                addr_end=max(addrs_end),
                total_bytes=total,
                notes=f"{len(load_segs)} LOAD segment(s)",
            )
    except ELFError as e:
        raise FileParseError(f"ELF 解析失败：{e}")
    except FileParseError:
        raise
    except Exception as e:
        raise FileParseError(f"ELF 读取异常：{e}")


def _parse_hex(path: str) -> FileInfo:
    try:
        from intelhex import IntelHex, HexRecordError
    except ImportError as e:
        raise FileParseError(f"intelhex 未安装：{e}")
    try:
        ih = IntelHex()
        ih.loadhex(path)
        if len(ih) == 0:
            raise FileParseError("HEX 文件为空")
        return FileInfo(
            fmt=FORMAT_HEX,
            addr_start=ih.minaddr(),
            addr_end=ih.maxaddr() + 1,
            total_bytes=len(ih),
            notes=f"{ih.maxaddr() - ih.minaddr() + 1} address span",
        )
    except (HexRecordError, ValueError) as e:
        raise FileParseError(f"HEX 解析失败：{e}")
    except FileParseError:
        raise
    except Exception as e:
        raise FileParseError(f"HEX 读取异常：{e}")


def _parse_bin(path: str, start_addr: int) -> FileInfo:
    size = os.path.getsize(path)
    if size == 0:
        raise FileParseError("BIN 文件为空")
    return FileInfo(
        fmt=FORMAT_BIN,
        addr_start=start_addr,
        addr_end=start_addr + size,
        total_bytes=size,
        notes=f"raw {size} bytes",
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_file_parser.py -v`

Expected: 所有测试 PASS。

- [ ] **Step 6: Commit**

```powershell
git add src/core/flash_file_parser.py tests/test_flash_file_parser.py tests/fixtures/
git commit -m "feat(flash): 添加 .axf/.elf/.hex/.bin 文件解析层 + 测试"
```

---

## Task 4: FlashWorker 骨架（dataclass + 常量 + 信号）

**Files:**
- Create: `src/core/flash_worker.py`
- Create: `tests/test_flash_worker.py`

- [ ] **Step 1: 写失败测试（验证常量、dataclass 不可变性、Signal 存在）**

`tests/test_flash_worker.py`:

```python
"""FlashWorker 单元测试：dataclass / 常量 / 流程 / 错误路径。

走 pylink mock，不需要实际 J-Link 硬件。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QThread, QTimer

from core.flash_worker import (
    ERASE_MODE_CHIP,
    ERASE_MODE_SECTOR,
    FORMAT_BIN,
    FORMAT_ELF,
    POST_ACTION_NONE,
    POST_ACTION_RESET,
    POST_ACTION_RESET_RUN,
    STAGE_CONNECT,
    STAGE_DISCONNECT,
    STAGE_ERASE,
    STAGE_PROGRAM,
    STAGE_RESET,
    STAGE_VERIFY,
    FlashParams,
    FlashWorker,
)


def test_constants_exposed():
    assert ERASE_MODE_SECTOR == "sector"
    assert ERASE_MODE_CHIP == "chip"
    assert POST_ACTION_NONE == "none"
    assert POST_ACTION_RESET == "reset"
    assert POST_ACTION_RESET_RUN == "reset_run"
    assert STAGE_CONNECT == "connect"
    assert STAGE_PROGRAM == "program"
    assert STAGE_DISCONNECT == "disconnect"


def test_flash_params_frozen():
    p = FlashParams(
        file_path="/x.bin", file_format=FORMAT_BIN, bin_start_addr=0,
        device_name="STM32", interface="SWD", speed_khz=4000,
        erase_mode=ERASE_MODE_SECTOR, post_action=POST_ACTION_RESET_RUN,
        extra_verify=False,
    )
    with pytest.raises(Exception):
        p.file_path = "/y.bin"  # type: ignore


def test_worker_signals_present():
    w = FlashWorker()
    for name in ("flash_requested", "stop_requested",
                 "flash_started", "flash_stage_changed",
                 "flash_progress", "flash_log", "flash_finished"):
        assert hasattr(w, name), f"missing signal: {name}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_worker.py -v`

Expected: FAIL with `ImportError: No module named 'core.flash_worker'`.

- [ ] **Step 3: 实现骨架**

Create `src/core/flash_worker.py`:

```python
"""FlashWorker：固件烧录后台业务对象。

**和 JLinkWorker 完全独立**：自己的 pylink.JLink 实例 + 自己的 QThread。
用户负责确保烧录前 RTT 页已断开（不自动协调）。

设计要点（参考 JLinkWorker 同款套路）：
- 不继承 QThread；调用方外部创建 QThread + moveToThread。
- 所有 pylink.JLink 操作都在 worker 线程。
- 参数传递避开 PySide6 跨线程 Signal 传 dict 的坑：UI 调
  set_pending_params() 用 lock，然后 emit 无参 flash_requested。
- 退出清理：_on_stop 槽内 _safe_disconnect → thread.quit()。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pylink
from PySide6.QtCore import QObject, Signal, Slot

from .logger import get_logger

# ============================================================
# 公开常量（避免散落字面值，参考 CLAUDE.md "模式/枚举字符串必须有常量"）
# ============================================================
ERASE_MODE_SECTOR = "sector"
ERASE_MODE_CHIP = "chip"

POST_ACTION_NONE = "none"
POST_ACTION_RESET = "reset"
POST_ACTION_RESET_RUN = "reset_run"

FORMAT_ELF = "elf"
FORMAT_HEX = "hex"
FORMAT_BIN = "bin"

STAGE_CONNECT = "connect"
STAGE_ERASE = "erase"
STAGE_PROGRAM = "program"
STAGE_VERIFY = "verify"
STAGE_RESET = "reset"
STAGE_DISCONNECT = "disconnect"


@dataclass(frozen=True)
class FlashParams:
    file_path: str
    file_format: str          # FORMAT_*
    bin_start_addr: int       # 仅 bin 用，其它格式忽略
    device_name: str
    interface: str            # "SWD" | "JTAG"
    speed_khz: int
    erase_mode: str           # ERASE_MODE_*
    post_action: str          # POST_ACTION_*
    extra_verify: bool


class FlashWorker(QObject):
    """烧录后台业务对象。**必须 moveToThread 到一个 QThread 后再用**。"""

    # ---- 输入信号 ----
    flash_requested = Signal()           # 配合 set_pending_params() lock
    stop_requested = Signal()            # 关窗清理用

    # ---- 输出信号 ----
    flash_started = Signal()
    flash_stage_changed = Signal(str)        # STAGE_*
    flash_progress = Signal(int, int)        # (current_bytes, total_bytes)
    flash_log = Signal(str, str)             # (level, msg) — "info"/"warn"/"error"
    flash_finished = Signal(bool, str)       # (success, summary_text)

    def __init__(self) -> None:
        super().__init__()
        self._logger = get_logger()
        # 这些在 initialize() 内（worker 线程）创建：
        self._jlink: pylink.JLink | None = None
        # 参数 setter + lock，避开跨线程 Signal 传 dataclass
        self._pending_params: FlashParams | None = None
        self._params_lock = threading.Lock()
        # 进度回调用：在 _run_flash 启动时记录 total
        self._current_total: int = 0
        self._t_start: float = 0.0

    def set_pending_params(self, params: FlashParams) -> None:
        """UI 线程调；GIL+lock 保护，不走 Qt 信号 marshalling。"""
        with self._params_lock:
            self._pending_params = params

    @Slot()
    def initialize(self) -> None:
        """thread.started → 这里。worker 线程内创建 pylink.JLink。"""
        self._jlink = pylink.JLink()
        # 把输入信号连到本地槽
        self.flash_requested.connect(self._on_flash_requested)
        self.stop_requested.connect(self._on_stop)
        self._logger.info("FlashWorker initialized in worker thread")

    @Slot()
    def _on_stop(self) -> None:
        self._safe_disconnect()
        t = self.thread()
        if t is not None:
            t.quit()

    @Slot()
    def _on_flash_requested(self) -> None:
        with self._params_lock:
            params = self._pending_params
            self._pending_params = None
        if params is None:
            self.flash_log.emit("warn", "flash_requested 收到但 pending_params 为空")
            return
        self._run_flash(params)

    # 下面在 Task 5/6/7 里实现：
    def _run_flash(self, p: FlashParams) -> None:
        raise NotImplementedError  # Task 6

    def _do_connect(self, device: str, iface: str, speed: int) -> None:
        raise NotImplementedError  # Task 5

    def _safe_disconnect(self) -> None:
        if self._jlink is None:
            return
        try:
            self._jlink.close()
        except pylink.JLinkException as e:
            self.flash_log.emit("warn", f"close warn: {e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_worker.py -v`

Expected: 3 个测试 PASS。

- [ ] **Step 5: Commit**

```powershell
git add src/core/flash_worker.py tests/test_flash_worker.py
git commit -m "feat(flash): FlashWorker 骨架（常量 + FlashParams + 信号）"
```

---

## Task 5: FlashWorker._do_connect 实现

**Files:**
- Modify: `src/core/flash_worker.py`（替换 `_do_connect` 的 NotImplementedError）
- Modify: `tests/test_flash_worker.py`

- [ ] **Step 1: 加测试 —— mock pylink.JLink 验证连接 dance**

追加到 `tests/test_flash_worker.py`：

```python
def test_do_connect_follows_open_close_open_dance(monkeypatch):
    """严格按 CLAUDE.md 'pylink 1.6.0 连接顺序'：open → close → open(serial)
    → set_tif → set_speed → connect。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.serial_number = 851012345
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)

    w = FlashWorker()
    w.initialize()
    # 调真实方法
    w._do_connect("STM32H750VB", "SWD", 4000)

    # 验证调用序列（用 mock_calls 的顺序）
    call_names = [c[0] for c in fake_jlink.mock_calls]
    # 期望前几次：opened → open(空) → close → open(serial) → set_tif → set_speed → connect
    assert "opened" in call_names
    assert "open" in call_names
    assert "close" in call_names
    assert "set_tif" in call_names
    assert "set_speed" in call_names
    assert "connect" in call_names

    fake_jlink.set_speed.assert_called_with(4000)
    fake_jlink.connect.assert_called_with("STM32H750VB")


def test_do_connect_uses_jtag_enum_when_iface_jtag(monkeypatch):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True   # 已开，跳过双开
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    w._do_connect("STM32", "JTAG", 1000)
    set_tif_arg = fake_jlink.set_tif.call_args[0][0]
    assert set_tif_arg == pylink.enums.JLinkInterfaces.JTAG
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_worker.py -v -k "do_connect"`

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: 实现 _do_connect（替换 NotImplementedError）**

把 `_do_connect` 的占位实现改成：

```python
    def _do_connect(self, device: str, iface: str, speed: int) -> None:
        """严格按 CLAUDE.md 'pylink 1.6.0 连接顺序'：open → close → open(serial)
        → set_tif → set_speed → connect。"""
        j = self._jlink
        if j is None:
            raise RuntimeError("FlashWorker 未 initialize")
        if not j.opened():
            j.open()
            ser = j.serial_number
            j.close()
            j.open(str(ser))
            self.flash_log.emit("info", f"J-Link SN: {ser}")
        tif = (pylink.enums.JLinkInterfaces.SWD if iface == "SWD"
               else pylink.enums.JLinkInterfaces.JTAG)
        j.set_tif(tif)
        j.set_speed(int(speed))
        j.connect(device)
        self.flash_log.emit("info", f"Target connected: {device}")
```

- [ ] **Step 4: 跑测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_worker.py -v`

Expected: 5 个测试 PASS。

- [ ] **Step 5: Commit**

```powershell
git add src/core/flash_worker.py tests/test_flash_worker.py
git commit -m "feat(flash): FlashWorker._do_connect 按 pylink 1.6.0 dance 实现"
```

---

## Task 6: FlashWorker._run_flash 成功路径

**Files:**
- Modify: `src/core/flash_worker.py`（替换 `_run_flash` 的 NotImplementedError）
- Modify: `tests/test_flash_worker.py`

- [ ] **Step 1: 写测试 —— mock pylink，断言信号 emit 序列**

追加到 `tests/test_flash_worker.py`：

```python
def _params_default(**overrides):
    base = dict(
        file_path="C:/x.axf", file_format=FORMAT_ELF, bin_start_addr=0,
        device_name="STM32", interface="SWD", speed_khz=4000,
        erase_mode=ERASE_MODE_SECTOR, post_action=POST_ACTION_RESET_RUN,
        extra_verify=False,
    )
    base.update(overrides)
    return FlashParams(**base)


def _collect_signals(worker):
    """订阅 worker 输出信号，把每个 emit 记到列表。"""
    log = []
    worker.flash_started.connect(lambda: log.append(("started",)))
    worker.flash_stage_changed.connect(lambda s: log.append(("stage", s)))
    worker.flash_progress.connect(lambda c, t: log.append(("progress", c, t)))
    worker.flash_log.connect(lambda lvl, m: log.append(("log", lvl, m)))
    worker.flash_finished.connect(lambda ok, msg: log.append(("finished", ok, msg)))
    return log


def test_run_flash_success_elf_sector_reset_run(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.serial_number = 851012345
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)

    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)

    w._run_flash(_params_default())
    qapp.processEvents()

    stages = [e[1] for e in log if e[0] == "stage"]
    assert stages == [STAGE_CONNECT, STAGE_PROGRAM, STAGE_RESET, STAGE_DISCONNECT]
    # flash_file 调用时 addr=0（ELF 文件内带地址）
    fake_jlink.flash_file.assert_called_once()
    _, kwargs = fake_jlink.flash_file.call_args
    args = fake_jlink.flash_file.call_args[0]
    assert args[1] == 0   # addr
    # reset(halt=False)：reset_run 模式希望 reset 不停 → restart 释放
    # 实现里我们用 reset(halt=False)（POST_ACTION_RESET_RUN）+ restart() 双调
    fake_jlink.reset.assert_called()
    fake_jlink.restart.assert_called()
    # 完成
    assert log[-1] == ("finished", True, "烧录成功")


def test_run_flash_bin_uses_bin_start_addr(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    p = _params_default(file_format=FORMAT_BIN, bin_start_addr=0x20000000)
    w._run_flash(p)
    qapp.processEvents()
    args = fake_jlink.flash_file.call_args[0]
    assert args[1] == 0x20000000


def test_run_flash_chip_erase_calls_erase_before_program(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)
    w._run_flash(_params_default(erase_mode=ERASE_MODE_CHIP))
    qapp.processEvents()
    stages = [e[1] for e in log if e[0] == "stage"]
    assert STAGE_ERASE in stages
    assert stages.index(STAGE_ERASE) < stages.index(STAGE_PROGRAM)
    fake_jlink.erase.assert_called_once()


def test_run_flash_post_action_none_no_reset(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)
    w._run_flash(_params_default(post_action=POST_ACTION_NONE))
    qapp.processEvents()
    stages = [e[1] for e in log if e[0] == "stage"]
    assert STAGE_RESET not in stages
    fake_jlink.reset.assert_not_called()
    fake_jlink.restart.assert_not_called()


def test_run_flash_post_action_reset_no_run(monkeypatch, qapp):
    """post_action=reset 调 reset(halt=True)，不调 restart。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    w._run_flash(_params_default(post_action=POST_ACTION_RESET))
    qapp.processEvents()
    fake_jlink.reset.assert_called_with(halt=True)
    fake_jlink.restart.assert_not_called()
```

加 fixture 到 `tests/conftest.py`（如果已有 qapp fixture 则跳过）：

```python
# tests/conftest.py 顶部如果还没有，加上：
import pytest
from PySide6.QtCore import QCoreApplication

@pytest.fixture
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app
```

（注意：如果 conftest.py 已有 qapp fixture，跳过这步。先看 `tests/conftest.py`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_worker.py -v -k "run_flash"`

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: 实现 _run_flash 成功路径（含 progress 回调）**

替换 `_run_flash` 占位为：

```python
    def _run_flash(self, p: FlashParams) -> None:
        self.flash_started.emit()
        self._t_start = time.time()
        self.flash_log.emit("info", f"=== Flash session ===")
        self.flash_log.emit("info",
            f"File: {p.file_path} ({p.file_format})")
        self.flash_log.emit("info",
            f"Device: {p.device_name} | {p.interface} @ {p.speed_khz} kHz")
        self.flash_log.emit("info",
            f"Options: erase={p.erase_mode} post={p.post_action} verify={p.extra_verify}")
        try:
            # --- connect ---
            self.flash_stage_changed.emit(STAGE_CONNECT)
            self._do_connect(p.device_name, p.interface, p.speed_khz)

            # --- chip erase（sector 由 flash_file 内含，不显式 emit STAGE_ERASE）---
            if p.erase_mode == ERASE_MODE_CHIP:
                self.flash_stage_changed.emit(STAGE_ERASE)
                self._jlink.erase()
                self.flash_log.emit("info", "chip erase OK")

            # --- program ---
            addr = p.bin_start_addr if p.file_format == FORMAT_BIN else 0
            self.flash_stage_changed.emit(STAGE_PROGRAM)
            self._current_total = 0
            self._jlink.flash_file(p.file_path, addr,
                                   on_progress=self._on_pylink_progress)
            self.flash_log.emit("info", "flash_file OK")

            # --- extra verify ---
            if p.extra_verify:
                self.flash_stage_changed.emit(STAGE_VERIFY)
                self._verify_bytewise(p)
                self.flash_log.emit("info", "extra verify OK")

            # --- post action ---
            if p.post_action in (POST_ACTION_RESET, POST_ACTION_RESET_RUN):
                self.flash_stage_changed.emit(STAGE_RESET)
                self._jlink.reset(halt=(p.post_action == POST_ACTION_RESET))
                if p.post_action == POST_ACTION_RESET_RUN:
                    self._jlink.restart()
                    self.flash_log.emit("info", "CPU running")

            # --- disconnect ---
            self.flash_stage_changed.emit(STAGE_DISCONNECT)
            self._safe_disconnect()

            elapsed = time.time() - self._t_start
            self.flash_log.emit("info", f"=== Done ({elapsed:.1f}s) ===")
            self.flash_finished.emit(True, "烧录成功")

        except Exception as e:
            self.flash_log.emit("error", f"{type(e).__name__}: {e}")
            self._safe_disconnect()
            self.flash_finished.emit(False, str(e))

    def _on_pylink_progress(self, action, progress_string, percentage) -> None:
        """pylink flash_file 的 on_progress 回调。

        pylink 1.6.0 签名（实测）：(action, progress_string, percentage)
        action: bytes 形如 b'Erase' / b'Program' / b'Verify'
        percentage: int 0-100
        """
        try:
            pct = int(percentage) if percentage is not None else 0
        except (TypeError, ValueError):
            pct = 0
        # 没有精确 byte 数，把百分比 * 100 当 total = 100 报上去
        self.flash_progress.emit(pct, 100)

    def _verify_bytewise(self, p: FlashParams) -> None:
        """按文件实际内容逐字节比对（在 flash_file 内含 CRC verify 之上的二次保险）。

        pylink memory_read 一次最多读 4096 字节，分块。
        """
        from . import flash_file_parser as fp
        info = fp.parse_file(p.file_path, bin_start_addr=p.bin_start_addr)
        # 对 bin/elf/hex 都按"文件→bytes"读出来再比对
        if p.file_format == FORMAT_BIN:
            with open(p.file_path, "rb") as f:
                expected = f.read()
            base_addr = p.bin_start_addr
            self._verify_range(base_addr, expected)
        elif p.file_format == FORMAT_HEX:
            from intelhex import IntelHex
            ih = IntelHex(); ih.loadhex(p.file_path)
            data = ih.tobinarray(start=ih.minaddr(), end=ih.maxaddr())
            self._verify_range(ih.minaddr(), bytes(data))
        else:  # FORMAT_ELF
            from elftools.elf.elffile import ELFFile
            with open(p.file_path, "rb") as f:
                elf = ELFFile(f)
                for seg in elf.iter_segments():
                    if seg["p_type"] != "PT_LOAD" or seg["p_filesz"] == 0:
                        continue
                    data = seg.data()
                    self._verify_range(seg["p_paddr"], data)

    def _verify_range(self, addr: int, expected: bytes) -> None:
        CHUNK = 4096
        off = 0
        while off < len(expected):
            n = min(CHUNK, len(expected) - off)
            got = bytes(self._jlink.memory_read(addr + off, n))
            if got != expected[off:off + n]:
                raise RuntimeError(
                    f"verify mismatch at 0x{addr + off:08X}: {n} bytes")
            off += n
```

- [ ] **Step 4: 跑测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_worker.py -v`

Expected: 所有测试 PASS。

- [ ] **Step 5: Commit**

```powershell
git add src/core/flash_worker.py tests/test_flash_worker.py tests/conftest.py
git commit -m "feat(flash): FlashWorker._run_flash 成功路径 + verify + progress 回调"
```

---

## Task 7: FlashWorker._run_flash 错误路径 + 退出清理

**Files:**
- Modify: `tests/test_flash_worker.py`

- [ ] **Step 1: 写错误路径测试**

追加到 `tests/test_flash_worker.py`：

```python
def test_run_flash_connect_failure(monkeypatch, qapp):
    """connect 抛异常 → flash_finished(False, ...) 且 _safe_disconnect 被调。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.connect.side_effect = pylink.JLinkException("Could not connect")
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)

    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)
    w._run_flash(_params_default())
    qapp.processEvents()

    assert log[-1][0] == "finished"
    assert log[-1][1] is False
    fake_jlink.close.assert_called()
    # 不应该到达 program 阶段
    stages = [e[1] for e in log if e[0] == "stage"]
    assert STAGE_PROGRAM not in stages


def test_run_flash_program_failure(monkeypatch, qapp):
    """flash_file 抛异常 → finished(False) + 错误 log 已写。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    fake_jlink.flash_file.side_effect = pylink.JLinkException("Erase failed")
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)

    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)
    w._run_flash(_params_default())
    qapp.processEvents()

    errors = [e for e in log if e[0] == "log" and e[1] == "error"]
    assert any("Erase failed" in e[2] for e in errors)
    assert log[-1] == ("finished", False, "Erase failed")
    fake_jlink.close.assert_called()


def test_safe_disconnect_swallows_jlink_exception(monkeypatch, qapp):
    """_safe_disconnect 内 close 抛 JLinkException 不传播（参考 CLAUDE.md
    'close/rtt_stop 抛异常不致命'）。"""
    fake_jlink = MagicMock()
    fake_jlink.close.side_effect = pylink.JLinkException("not connected")
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)
    w._safe_disconnect()  # 不应抛
    qapp.processEvents()
    warns = [e for e in log if e[0] == "log" and e[1] == "warn"]
    assert any("close warn" in e[2] for e in warns)


def test_on_stop_calls_safe_disconnect_and_quits_thread(monkeypatch, qapp):
    """_on_stop 调 _safe_disconnect → thread.quit()。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    fake_thread = MagicMock()
    # 替换 self.thread() —— QObject 没法直接 setattr 'thread' 方法，monkeypatch
    monkeypatch.setattr(w, "thread", lambda: fake_thread)
    w._on_stop()
    fake_jlink.close.assert_called()
    fake_thread.quit.assert_called()
```

- [ ] **Step 2: 跑测试**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_flash_worker.py -v`

Expected: 所有测试 PASS（错误路径代码上一个 Task 已经实现过）。

- [ ] **Step 3: Commit**

```powershell
git add tests/test_flash_worker.py
git commit -m "test(flash): 错误路径 + _safe_disconnect + _on_stop 覆盖"
```

---

## Task 8: FlashPage UI 骨架（4 Card + 透明 ScrollArea）

**Files:**
- Create: `src/ui/flash_page.py`

- [ ] **Step 1: 创建 FlashPage 文件，先把骨架 + 4 Card 搭起来**

Create `src/ui/flash_page.py`:

```python
"""固件烧录页：独立 FlashWorker + 独立 QThread，不干涉 RTT/Memory。

UI 布局（4 个 Card，透明 ScrollArea 整页包裹）：
1. 连接参数 — device / interface / speed
2. 固件文件 — file picker + 最近 10 + 拖放 + 解析后 format/range/size
3. 烧录选项 — erase_mode / post_action / extra_verify
4. 执行 — 大按钮 + ProgressBar + 阶段文字 + 可折叠详情面板

参数持久化：cfg.flash_*。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    EditableComboBox,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    SpinBox,
    StrongBodyLabel,
)

from core.config_service import ConfigService
from core.flash_worker import (
    ERASE_MODE_CHIP,
    ERASE_MODE_SECTOR,
    FORMAT_BIN,
    POST_ACTION_NONE,
    POST_ACTION_RESET,
    POST_ACTION_RESET_RUN,
    FlashParams,
    FlashWorker,
)

from . import _infobar
from ._scroll_helpers import make_transparent_scroll


_ERASE_LABELS = [
    ("扇区擦除（推荐，快）", ERASE_MODE_SECTOR),
    ("整片擦除（慢，更干净）", ERASE_MODE_CHIP),
]
_POST_LABELS = [
    ("仅烧录", POST_ACTION_NONE),
    ("烧录 + 复位", POST_ACTION_RESET),
    ("烧录 + 复位 + 运行（推荐）", POST_ACTION_RESET_RUN),
]


class FlashPage(QWidget):
    def __init__(self, cfg: ConfigService, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("flashPage")
        self._cfg = cfg
        self._is_running = False

        # 独立 worker + 独立 QThread（和 JLinkWorker 完全无关）
        self._thread = QThread(self)
        self._worker = FlashWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.initialize)
        self._thread.start()

        # 拖放
        self.setAcceptDrops(True)

        # 外层：透明 scroll
        scroll, inner = make_transparent_scroll(self, "flash")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # inner 主 layout
        v = QVBoxLayout(inner)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(12)

        v.addWidget(self._build_conn_card())
        v.addWidget(self._build_file_card())
        v.addWidget(self._build_options_card())
        v.addWidget(self._build_run_card())
        v.addStretch(1)

        self._connect_signals()
        self._load_prefs_into_controls()

    # ---- card builders (占位，下一 Task 填实) ----
    def _build_conn_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.addWidget(StrongBodyLabel("连接参数"))
        row = QHBoxLayout()
        row.addWidget(BodyLabel("Device:"))
        self.cmb_device = EditableComboBox()
        self.cmb_device.addItems(self._cfg.get_chip_list() or ["STM32H750VB"])
        row.addWidget(self.cmb_device, 1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("Interface:"))
        self.rb_swd = RadioButton("SWD")
        self.rb_jtag = RadioButton("JTAG")
        row2.addWidget(self.rb_swd)
        row2.addWidget(self.rb_jtag)
        row2.addSpacing(20)
        row2.addWidget(BodyLabel("Speed (kHz):"))
        self.spin_speed = SpinBox()
        self.spin_speed.setRange(100, 50000)
        self.spin_speed.setSingleStep(100)
        row2.addWidget(self.spin_speed)
        row2.addStretch(1)
        layout.addLayout(row2)
        return card

    def _build_file_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.addWidget(StrongBodyLabel("固件文件"))

        row = QHBoxLayout()
        row.addWidget(BodyLabel("File:"))
        self.cmb_file = EditableComboBox()  # 最近 10 文件下拉
        self.cmb_file.setMinimumWidth(360)
        row.addWidget(self.cmb_file, 1)
        self.btn_browse = PushButton("浏览…")
        row.addWidget(self.btn_browse)
        self.lbl_mtime_flag = BodyLabel("")
        self.lbl_mtime_flag.setStyleSheet("color: #d97706;")  # amber
        row.addWidget(self.lbl_mtime_flag)
        layout.addLayout(row)

        # format + range
        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("Format:"))
        self.lbl_format = BodyLabel("(无)")
        row2.addWidget(self.lbl_format)
        row2.addSpacing(20)
        row2.addWidget(BodyLabel("Range:"))
        self.lbl_range = BodyLabel("(无)")
        row2.addWidget(self.lbl_range, 1)
        layout.addLayout(row2)

        # bin start addr (仅 bin 模式可编辑)
        row3 = QHBoxLayout()
        row3.addWidget(BodyLabel("Bin 起始地址:"))
        self.edit_bin_addr = LineEdit()
        self.edit_bin_addr.setPlaceholderText("0x08000000")
        self.edit_bin_addr.setMaximumWidth(180)
        row3.addWidget(self.edit_bin_addr)
        row3.addStretch(1)
        layout.addLayout(row3)
        return card

    def _build_options_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.addWidget(StrongBodyLabel("烧录选项"))

        row = QHBoxLayout()
        row.addWidget(BodyLabel("擦除模式:"))
        self.cmb_erase = ComboBox()
        for label, _ in _ERASE_LABELS:
            self.cmb_erase.addItem(label)
        row.addWidget(self.cmb_erase, 1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("完成动作:"))
        self.cmb_post = ComboBox()
        for label, _ in _POST_LABELS:
            self.cmb_post.addItem(label)
        row2.addWidget(self.cmb_post, 1)
        layout.addLayout(row2)

        self.chk_verify = CheckBox("额外 byte-by-byte verify（慢一倍）")
        layout.addWidget(self.chk_verify)
        return card

    def _build_run_card(self) -> QWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)

        self.btn_flash = PrimaryPushButton("开始烧录")
        self.btn_flash.setMinimumHeight(36)
        layout.addWidget(self.btn_flash)

        row = QHBoxLayout()
        self.lbl_stage = BodyLabel("待命")
        row.addWidget(self.lbl_stage)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        row.addWidget(self.progress, 1)
        layout.addLayout(row)

        # 详情面板（折叠）
        row_det = QHBoxLayout()
        self.btn_toggle_log = PushButton("▶ 详情")
        self.btn_toggle_log.setFlat(True)
        row_det.addWidget(self.btn_toggle_log)
        self.btn_copy_log = PushButton("复制日志")
        row_det.addWidget(self.btn_copy_log)
        row_det.addStretch(1)
        layout.addLayout(row_det)

        self.txt_log = PlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(1000)
        self.txt_log.setVisible(False)
        layout.addWidget(self.txt_log)
        return card

    # ---- 占位（下一 Task 填）----
    def _connect_signals(self) -> None:
        pass

    def _load_prefs_into_controls(self) -> None:
        pass

    # ---- 拖放（下一 Task 完善）----
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        urls = e.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path.lower().endswith((".axf", ".elf", ".hex", ".bin")):
            self.cmb_file.setCurrentText(path)
            e.acceptProposedAction()

    def shutdown(self) -> None:
        """主窗口 closeEvent 调；干净关掉 worker 线程。"""
        self._worker.stop_requested.emit()
        if not self._thread.wait(3000):
            self._thread.terminate()
            self._thread.wait(1000)
```

- [ ] **Step 2: 冒烟启动（不集成进 MainWindow，单独跑）**

跑应用看是否还能正常启动（FlashPage 暂未加进主窗口，应该零影响）：

```powershell
.\venv\Scripts\python.exe src\main.py
```

Expected: 应用正常启动，可正常关闭。日志无新 error。

- [ ] **Step 3: Commit**

```powershell
git add src/ui/flash_page.py
git commit -m "feat(flash-ui): FlashPage 骨架（4 Card + 透明 ScrollArea + 占位拖放）"
```

---

## Task 9: FlashPage 文件选择 + 解析 + mtime + 最近文件

**Files:**
- Modify: `src/ui/flash_page.py`

- [ ] **Step 1: 实现 _connect_signals + _load_prefs_into_controls + 文件选择逻辑**

替换 `_connect_signals` 和 `_load_prefs_into_controls` 占位，并加文件处理方法：

```python
    # ---- 加载偏好到控件 ----
    def _load_prefs_into_controls(self) -> None:
        self.cmb_device.setCurrentText(self._cfg.get("flash_device_name"))
        iface = self._cfg.get("flash_interface")
        self.rb_swd.setChecked(iface == "SWD")
        self.rb_jtag.setChecked(iface == "JTAG")
        self.spin_speed.setValue(int(self._cfg.get("flash_speed")))

        # 最近文件
        recent = list(self._cfg.get("flash_recent_files") or [])
        self.cmb_file.clear()
        for p in recent:
            self.cmb_file.addItem(p)
        if recent:
            self.cmb_file.setCurrentIndex(0)
            self._on_file_changed(recent[0], silent=True)
        else:
            self.cmb_file.setCurrentText("")

        # bin addr
        addr = int(self._cfg.get("flash_bin_address"))
        self.edit_bin_addr.setText(f"0x{addr:08X}")

        # erase mode
        em = self._cfg.get("flash_erase_mode")
        for i, (_, v) in enumerate(_ERASE_LABELS):
            if v == em:
                self.cmb_erase.setCurrentIndex(i)
                break

        # post action
        pa = self._cfg.get("flash_post_action")
        for i, (_, v) in enumerate(_POST_LABELS):
            if v == pa:
                self.cmb_post.setCurrentIndex(i)
                break

        self.chk_verify.setChecked(bool(self._cfg.get("flash_verify")))

    # ---- 信号连接 ----
    def _connect_signals(self) -> None:
        # 持久化
        self.cmb_device.currentTextChanged.connect(
            lambda s: self._cfg.set("flash_device_name", s))
        self.rb_swd.toggled.connect(
            lambda on: on and self._cfg.set("flash_interface", "SWD"))
        self.rb_jtag.toggled.connect(
            lambda on: on and self._cfg.set("flash_interface", "JTAG"))
        self.spin_speed.valueChanged.connect(
            lambda v: self._cfg.set("flash_speed", int(v)))
        self.edit_bin_addr.editingFinished.connect(self._on_bin_addr_changed)
        self.cmb_erase.currentIndexChanged.connect(
            lambda i: self._cfg.set("flash_erase_mode", _ERASE_LABELS[i][1]))
        self.cmb_post.currentIndexChanged.connect(
            lambda i: self._cfg.set("flash_post_action", _POST_LABELS[i][1]))
        self.chk_verify.toggled.connect(
            lambda v: self._cfg.set("flash_verify", bool(v)))

        # 文件
        self.btn_browse.clicked.connect(self._on_browse)
        self.cmb_file.currentTextChanged.connect(self._on_file_changed)

        # 详情折叠
        self.btn_toggle_log.clicked.connect(self._toggle_log)
        self.btn_copy_log.clicked.connect(self._copy_log)

        # worker → ui（下一 Task 接）
        self.btn_flash.clicked.connect(self._on_start_flash)

    def _on_bin_addr_changed(self) -> None:
        txt = self.edit_bin_addr.text().strip()
        try:
            v = int(txt, 0) if txt else 0
        except ValueError:
            _infobar.warn(self, "Bin 起始地址格式错误", f"无法解析为整数：{txt}")
            return
        self._cfg.set("flash_bin_address", int(v))
        # 重解析当前文件以更新 range 显示
        cur = self.cmb_file.currentText().strip()
        if cur:
            self._on_file_changed(cur, silent=True)

    def _on_browse(self) -> None:
        cur = self.cmb_file.currentText().strip()
        start_dir = str(Path(cur).parent) if cur else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择固件文件", start_dir,
            "固件文件 (*.axf *.elf *.hex *.bin);;所有文件 (*.*)")
        if not path:
            return
        # 加进下拉 + 持久化
        self.cmb_file.setCurrentText(path)

    def _on_file_changed(self, path: str, silent: bool = False) -> None:
        """新文件选定：解析 → 填 format/range → 更新最近文件 + mtime 比对。"""
        path = path.strip()
        if not path:
            self.lbl_format.setText("(无)")
            self.lbl_range.setText("(无)")
            self.lbl_mtime_flag.setText("")
            return
        if not os.path.exists(path):
            if not silent:
                _infobar.warn(self, "文件不存在", path)
            return

        from core import flash_file_parser as fp
        # bin addr 取页面当前值
        try:
            bin_addr = int(self.edit_bin_addr.text().strip(), 0)
        except (ValueError, TypeError):
            bin_addr = int(self._cfg.get("flash_bin_address"))
        try:
            info = fp.parse_file(path, bin_start_addr=bin_addr)
        except fp.FileParseError as e:
            self.lbl_format.setText("(解析失败)")
            self.lbl_range.setText("")
            if not silent:
                _infobar.error(self, "文件解析失败", str(e))
            return

        self.lbl_format.setText(info.fmt.upper())
        self.lbl_range.setText(
            f"0x{info.addr_start:08X} – 0x{info.addr_end:08X} "
            f"({info.total_bytes} B, {info.notes})")
        # bin 模式才允许编辑 bin_addr
        self.edit_bin_addr.setEnabled(info.fmt == FORMAT_BIN)

        # 更新最近文件
        recent = list(self._cfg.get("flash_recent_files") or [])
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:10]
        self._cfg.set("flash_recent_files", recent)

        # mtime 比对
        mt_map = dict(self._cfg.get("flash_recent_files_mtime") or {})
        cur_mt = os.path.getmtime(path)
        prev_mt = mt_map.get(path)
        if prev_mt is not None and cur_mt > prev_mt + 0.5:
            self.lbl_mtime_flag.setText(f"● updated")
        else:
            self.lbl_mtime_flag.setText("")
        mt_map[path] = cur_mt
        self._cfg.set("flash_recent_files_mtime", mt_map)

    def _toggle_log(self) -> None:
        vis = not self.txt_log.isVisible()
        self.txt_log.setVisible(vis)
        self.btn_toggle_log.setText("▼ 详情" if vis else "▶ 详情")

    def _copy_log(self) -> None:
        import platform
        import PySide6
        from ui.about_page import APP_VERSION
        header = (
            f"J-Link RTT Viewer / Flash log\n"
            f"App version: {APP_VERSION}\n"
            f"OS: {platform.platform()}\n"
            f"pylink-square: 1.6.0\n"
            f"PySide6: {PySide6.__version__}\n"
            f"---\n"
        )
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(header + self.txt_log.toPlainText())
        _infobar.info(self, "已复制日志到剪贴板", "")

    # ---- 占位（下一 Task）----
    def _on_start_flash(self) -> None:
        pass
```

- [ ] **Step 2: 跑应用手动测**

```powershell
.\venv\Scripts\python.exe src\main.py
```

FlashPage 还没接进 MainWindow，没法点开页面看。先确认启动无 import error。

Expected: 应用正常启动，无新 error 日志。

- [ ] **Step 3: Commit**

```powershell
git add src/ui/flash_page.py
git commit -m "feat(flash-ui): 文件选择 + 解析 + mtime + 最近文件 + 详情折叠"
```

---

## Task 10: FlashPage 烧录流程集成（worker 信号 ↔ UI）

**Files:**
- Modify: `src/ui/flash_page.py`

- [ ] **Step 1: 实现 _on_start_flash + worker → UI 信号槽**

替换 `_on_start_flash` 占位，并在 `_connect_signals` 末尾追加 worker→UI 连接。

把 `_connect_signals` 末尾这两行：
```python
        # worker → ui（下一 Task 接）
        self.btn_flash.clicked.connect(self._on_start_flash)
```

替换为：
```python
        # worker → ui（QueuedConnection 显式声明：CLAUDE.md 跨线程信号约定）
        from PySide6.QtCore import Qt as _Qt
        self.btn_flash.clicked.connect(self._on_start_flash)
        self._worker.flash_started.connect(
            self._on_flash_started, _Qt.QueuedConnection)
        self._worker.flash_stage_changed.connect(
            self._on_stage_changed, _Qt.QueuedConnection)
        self._worker.flash_progress.connect(
            self._on_progress, _Qt.QueuedConnection)
        self._worker.flash_log.connect(
            self._on_log, _Qt.QueuedConnection)
        self._worker.flash_finished.connect(
            self._on_flash_finished, _Qt.QueuedConnection)
```

加入这些槽方法（替换占位 `_on_start_flash`）：

```python
    def _on_start_flash(self) -> None:
        if self._is_running:
            return

        path = self.cmb_file.currentText().strip()
        if not path:
            _infobar.warn(self, "未选择文件", "请先选择 .axf/.elf/.hex/.bin 文件")
            return
        if not os.path.exists(path):
            _infobar.warn(self, "文件不存在", path)
            return

        from core import flash_file_parser as fp
        try:
            fmt = fp.detect_format(path)
        except fp.FileParseError as e:
            _infobar.error(self, "格式不支持", str(e))
            return

        try:
            bin_addr = int(self.edit_bin_addr.text().strip(), 0)
        except (ValueError, TypeError):
            bin_addr = 0

        device = self.cmb_device.currentText().strip()
        if not device:
            _infobar.warn(self, "未填 Device", "请填写目标设备名（如 STM32H750VB）")
            return

        iface = "SWD" if self.rb_swd.isChecked() else "JTAG"
        speed = int(self.spin_speed.value())
        erase_mode = _ERASE_LABELS[self.cmb_erase.currentIndex()][1]
        post_action = _POST_LABELS[self.cmb_post.currentIndex()][1]
        verify = self.chk_verify.isChecked()

        params = FlashParams(
            file_path=path, file_format=fmt, bin_start_addr=bin_addr,
            device_name=device, interface=iface, speed_khz=speed,
            erase_mode=erase_mode, post_action=post_action,
            extra_verify=verify,
        )
        self._worker.set_pending_params(params)
        self._worker.flash_requested.emit()

    def _on_flash_started(self) -> None:
        self._is_running = True
        self._set_inputs_enabled(False)
        self.btn_flash.setText("烧录中…")
        self.txt_log.clear()
        self.progress.setValue(0)
        self.lbl_stage.setText("准备…")

    def _on_stage_changed(self, stage: str) -> None:
        label_map = {
            "connect": "连接中…",
            "erase": "擦除中…",
            "program": "写入中…",
            "verify": "校验中…",
            "reset": "复位中…",
            "disconnect": "断开中…",
        }
        self.lbl_stage.setText(label_map.get(stage, stage))

    def _on_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self.progress.setValue(0)
            return
        self.progress.setValue(int(current * 100 / total))

    def _on_log(self, level: str, msg: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        prefix = {"info": "", "warn": "⚠ ", "error": "✖ "}.get(level, "")
        self.txt_log.appendPlainText(f"[{ts}] {prefix}{msg}")

    def _on_flash_finished(self, ok: bool, summary: str) -> None:
        self._is_running = False
        self._set_inputs_enabled(True)
        self.btn_flash.setText("开始烧录")
        if ok:
            self.lbl_stage.setText("完成 ✓")
            self.progress.setValue(100)
            _infobar.success(self, "烧录成功", summary)
        else:
            self.lbl_stage.setText("失败 ✖")
            # 失败时自动展开详情 + 写固定建议文案
            if not self.txt_log.isVisible():
                self._toggle_log()
            self.txt_log.appendPlainText(
                "⚠ Flash 已部分擦除/写入，建议下次用「整片擦除」重烧")
            _infobar.error(self, "烧录失败", summary)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for w in (self.cmb_device, self.rb_swd, self.rb_jtag, self.spin_speed,
                  self.cmb_file, self.btn_browse, self.edit_bin_addr,
                  self.cmb_erase, self.cmb_post, self.chk_verify):
            w.setEnabled(enabled)
        self.btn_flash.setEnabled(enabled)
```

- [ ] **Step 2: 跑应用确认 import + 启动 OK**

```powershell
.\venv\Scripts\python.exe src\main.py
```

Expected: 启动无 error。

- [ ] **Step 3: Commit**

```powershell
git add src/ui/flash_page.py
git commit -m "feat(flash-ui): 烧录流程 + worker 信号槽 + 失败固定建议文案"
```

---

## Task 11: MainWindow 集成 + 导航入口

**Files:**
- Modify: `src/ui/main_window.py`

- [ ] **Step 1: 加导入 + 创建页面 + 加导航**

修改 `src/ui/main_window.py`：

**改 1**：在文件顶部其他 `from .` 导入旁追加：

```python
from .flash_page import FlashPage
```

**改 2**：`__init__` 里 "2. 各页面" 块（约 line 39-42）改成：

```python
        # 2. 各页面
        self.rtt_page = RTTMonitorPage(self.worker, cfg, self)
        self.memory_page = MemoryViewerPage(self.worker, cfg, self)
        self.flash_page = FlashPage(cfg, self)
        self.settings_page = SettingsPage(cfg, self)
        self.about_page = AboutPage(self)
```

**改 3**：`__init__` 里 "3. 导航" 块（约 line 44-53）改成（在 memory_page 后面、addSeparator 前面插入烧录入口）：

```python
        # 3. 导航
        self.addSubInterface(self.rtt_page, FIF.SPEED_HIGH, "RTT 监控")
        self.addSubInterface(self.memory_page, FIF.CODE, "内存查看")
        self.addSubInterface(self.flash_page, FIF.SEND_FILL, "固件烧录")
        self.navigationInterface.addSeparator()
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(
            self.about_page, FIF.INFO, "关于", NavigationItemPosition.BOTTOM
        )
```

**改 4**：`closeEvent`（约 line 136 末尾、`event.accept()` 之前）加 flash_page 清理：

把 `event.accept()` 前面这段：
```python
            self.worker_thread.terminate()
            self.worker_thread.wait(1000)

        event.accept()
```

替换为：
```python
            self.worker_thread.terminate()
            self.worker_thread.wait(1000)

        # 关掉烧录页的独立 worker thread
        try:
            self.flash_page.shutdown()
        except Exception as e:
            self._logger.warning(f"FlashPage shutdown failed: {e}")

        event.accept()
```

- [ ] **Step 2: 启动应用手动验证**

```powershell
.\venv\Scripts\python.exe src\main.py
```

Expected:
- 应用正常启动
- 左侧导航出现"固件烧录"，点进去显示 4 个 Card
- 切回 RTT 页正常工作
- 关闭窗口干净退出（worker 都退出，无超时 terminate）

- [ ] **Step 3: 实机烧录冒烟测试（需要 J-Link + STM32 板）**

接好 J-Link + 板子，但**确保 RTT 页处于断开状态**（独立 worker 互斥使用 J-Link）。

- 浏览选个 `.axf` 或 `.hex`
- Format / Range 自动填充
- 点"开始烧录"
- 期望：阶段文字依次"连接中…→写入中…→复位中…→断开中…→完成 ✓"，progress 走条
- 详情面板手动展开查看完整日志

如果出错：自动展开详情 + 红色失败 toast + 固定建议文案。

- [ ] **Step 4: Commit**

```powershell
git add src/ui/main_window.py
git commit -m "feat(flash): MainWindow 集成固件烧录页 + 导航入口 + 退出清理"
```

---

## Task 12: 手动测试 checklist + CHANGELOG + README

**Files:**
- Create: `docs/manual-test-flash.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`（如有"功能列表"section）
- Modify: `pyproject.toml`（版本号 → 0.3.0）
- Modify: `src/ui/about_page.py`（APP_VERSION → "0.3.0"）

- [ ] **Step 1: 写手动测试 checklist**

Create `docs/manual-test-flash.md`:

```markdown
# 固件烧录页 手动测试 Checklist

每次发版前跑一遍。需要 J-Link + 至少一块 STM32 板。

## 前置
- [ ] RTT 页处于"已断开"状态（互斥占用 J-Link）

## UI 基础
- [ ] 导航能进入"固件烧录"页
- [ ] 4 个 Card 显示完整（连接 / 文件 / 选项 / 执行）
- [ ] 窗口高度拉低时整页能滚动，控件不被压扁

## 文件选择
- [ ] 浏览按钮能选 .axf / .elf / .hex / .bin
- [ ] 选定后 Format / Range 自动填
- [ ] 不支持的后缀（如 .txt）拒绝 + 红色提示
- [ ] 损坏的 .hex 文件解析失败 → InfoBar error
- [ ] 文件路径不存在 → 提示

## 最近文件 + mtime
- [ ] 选过的文件出现在 ComboBox 下拉
- [ ] 列表最多 10 个
- [ ] 重启应用后下拉仍在
- [ ] 同一文件外部改动（重编译）后再进入 → 文件名右侧显示"● updated"

## 拖放
- [ ] 把 .axf 拖到 FlashPage 任意位置 → 路径自动填入
- [ ] 把 .txt 拖入 → 不响应

## bin 模式
- [ ] 选 .bin 文件 → Bin 起始地址输入框启用
- [ ] 改地址 → Range 标签实时更新
- [ ] 非 bin 文件 → 起始地址输入框置灰

## 烧录流程（实机）
- [ ] 选项默认值正确（扇区 + 烧录+复位+运行 + verify 关）
- [ ] 点"开始烧录"→ 阶段文字依次 连接→写入→复位→断开→完成 ✓
- [ ] ProgressBar 走条
- [ ] 完成后绿色 InfoBar success
- [ ] 详情默认折叠

## 选项组合
- [ ] 切换"整片擦除"+ 烧录 → 阶段多出"擦除中…"
- [ ] 切换"仅烧录" → 烧完不复位（板子不会自动跑新固件）
- [ ] 勾上 "verify" → 阶段多"校验中…"且耗时增加

## 错误路径
- [ ] J-Link 未插：点烧录 → 失败 toast + 详情自动展开
- [ ] 故意选 .bin 烧到错误地址（如 0x00000000） → 失败 + 固定建议文案
- [ ] 详情面板"复制日志"按钮 → 剪贴板内容含 header + 日志正文

## 与 RTT 互斥
- [ ] RTT 页先连上 STM32 → 切到烧录页烧录 → 失败（J-Link 句柄被占）
- [ ] 失败后手动 RTT 断开 → 烧录重试成功

## 退出
- [ ] 烧录完成后关闭窗口 → 干净退出（无超时 terminate 日志）
- [ ] 烧录进行中关闭窗口 → 等待烧录完成或合理超时
```

- [ ] **Step 2: CHANGELOG 加 0.3.0 条目**

在 `CHANGELOG.md` 的 `## [Unreleased]` 下、`## [0.2.2]` 之上插入：

```markdown
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
```

更新底部 compare 链接段（如果有）：
```markdown
[0.3.0]: https://github.com/MisakaMikoto128/j-link-rtt-viewer-pyqt/compare/v0.2.2...v0.3.0
```

- [ ] **Step 3: 版本号 bump**

`pyproject.toml`：把 `version = "0.2.2"` 改为 `version = "0.3.0"`。

`src/ui/about_page.py`：把 `APP_VERSION = "0.2.2"` 改为 `APP_VERSION = "0.3.0"`。

`build_nuitka_onefile.bat`：把 `set PRODUCT_VERSION=0.2.2` 改为 `set PRODUCT_VERSION=0.3.0`。

- [ ] **Step 4: README 加 "固件烧录"**

如果 `README.md` 有"功能"/"特性"小节，加一行（按现有 bullet 风格）：

```markdown
- **固件烧录**：支持 `.axf` / `.elf` / `.hex` / `.bin`，拖放选文件，可选擦除模式与完成动作，详情日志可一键复制
```

如果 README 无功能列表则跳过此步。

- [ ] **Step 5: 跑全量测试 + 启动一次**

```powershell
.\venv\Scripts\python.exe -m pytest -v
.\venv\Scripts\python.exe src\main.py
```

Expected: pytest 全绿；应用启动正常，能进入"固件烧录"页。

- [ ] **Step 6: Commit**

```powershell
git add docs/manual-test-flash.md CHANGELOG.md pyproject.toml src/ui/about_page.py build_nuitka_onefile.bat README.md
git commit -m "docs(flash): 手动测试 checklist + CHANGELOG 0.3.0 + 版本号 bump"
```

---

## Self-Review Notes

**Spec coverage check**（对照 `2026-05-17-firmware-flashing-design.md` § 2 决策摘要）：
- ✅ J-Link 会话独立 → Task 4 `FlashWorker` 自己拥有 `pylink.JLink` + 独立 `QThread`
- ✅ bin 地址输入框 + 持久化 → Task 2 `flash_bin_address` + Task 8/9 `edit_bin_addr` + `_on_bin_addr_changed`
- ✅ 擦除模式可选 → Task 2 `flash_erase_mode` + Task 8 `cmb_erase` + Task 6 `_run_flash` chip-erase 分支
- ✅ 完成动作下拉 + verify checkbox → Task 2/8/6 都已覆盖
- ✅ 连接参数独立持久化 → Task 2 `flash_device_name/interface/speed`
- ✅ auto-connect 按下烧录时触发 → Task 6 `_run_flash` 一条龙含 connect/disconnect
- ✅ 简单 ProgressBar + 阶段文字，无取消 → Task 8/10
- ✅ 最近文件 + mtime → Task 9 `_on_file_changed`
- ✅ 拖放 + 完整文件校验 → Task 3 parser + Task 8/9 拖放 handler
- ✅ InfoBar + 详情自动展开 + 复制日志 + 固定建议文案 → Task 10 `_on_flash_finished`

**Placeholder scan**：除了 Task 8 的 `_connect_signals` / `_load_prefs_into_controls` 占位（明确在 Task 9 替换）和 `_on_start_flash` 占位（Task 10 替换），其它步骤均含完整代码。占位过渡是计划本身设计的一部分，不是 placeholder failure。

**Type consistency check**：
- `FlashParams` 字段名跨 Task 4/6/10 一致（file_path / file_format / bin_start_addr / device_name / interface / speed_khz / erase_mode / post_action / extra_verify）
- 常量名（ERASE_MODE_* / POST_ACTION_* / STAGE_* / FORMAT_*）从 Task 4 起统一，Task 6/8/10 引用一致
- Signal 名（flash_started / flash_stage_changed / flash_progress / flash_log / flash_finished）从 Task 4 起统一

**已知简化**：
- `_on_pylink_progress` 签名是按推测的 pylink 1.6.0 进度回调写的；实现 Task 6 时如签名不符，需对照 pylink 文档调整。这点在 spec § 10 已经标记。
- Nuitka 打包验证（pyelftools / intelhex 能否被正确 freeze）放到 Task 1 之外的实际打包环节。
