# J-Link RTT Viewer PyQt 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把原 PyWebView 版的 J-Link RTT Viewer 重写为 PySide6 + QFluentWidgets 桌面应用，保留 RTT 监控和内存查看/导出两大功能，修复原项目的关闭死锁与连接重开问题。

**Architecture:** UI 主线程 + 单 `JLinkWorker` QThread 持有 pylink，命令/响应通过 Qt 信号往返。core 层（ansi_parser、memory_service、config_service、jlink_worker）UI 无关，可独立 pytest；UI 层（FluentWindow + 四个页面）只 emit 命令/connect 响应。

**Tech Stack:** Python 3.10+ · PySide6 ≥6.6 · PySide6-Fluent-Widgets ≥1.6,<2.0 · pylink-square ≥1.6.0 · pytest · Nuitka（打包）

**Spec:** `docs/superpowers/specs/2026-05-15-jlink-rtt-viewer-pyqt-design.md`

**Working directory:** `C:\Users\liuyu\Desktop\WorkPlace\J-Link RTT Viewer PyQt`

---

## 通用约定

- **Git 提交**：约定式提交 + 中文描述，每个 Task 末尾一笔提交
- **pytest 配置**：用 `pyproject.toml` 的 `[tool.pytest.ini_options]` 把 `src` 加进 `pythonpath`，测试代码放 `tests/`
- **import 规则**：测试代码 `from core.xxx import yyy`；UI 代码 `from core.xxx import yyy` 或 `from ui.xxx import yyy`（src 加入 sys.path 后顶层包就是 `core` / `ui`）
- **预存默认值**：用户偏好的所有默认值集中放在 `ConfigService.DEFAULTS`，UI 不写硬编码默认
- **`%APPDATA%` 路径**：用 `os.environ.get("APPDATA")` 取，无则回落到 `Path.home() / "AppData" / "Roaming"`
- **每个 UI 任务都有"启动 app 并人眼校验"步骤**：用 `start.bat` 或 `python src\main.py`

---

## Task 1：项目骨架与依赖

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src\__init__.py`
- Create: `src\core\__init__.py`
- Create: `src\ui\__init__.py`
- Create: `src\ui\widgets\__init__.py`
- Create: `tests\__init__.py`
- Create: `tests\conftest.py`

- [ ] **Step 1.1: 创建 `requirements.txt`**

```
PySide6>=6.6,<7
PySide6-Fluent-Widgets>=1.6,<2.0
pylink-square>=1.6.0

# dev / build
pytest>=8.0
nuitka>=2.0
```

- [ ] **Step 1.2: 创建 `pyproject.toml`**

```toml
[project]
name = "jlink-rtt-viewer"
version = "0.1.0"
description = "基于 PySide6 + QFluentWidgets 的 J-Link RTT 查看与内存导出工具"
requires-python = ">=3.10"

[tool.black]
line-length = 100
target-version = ["py310", "py311"]

[tool.ruff]
line-length = 100
target-version = "py310"
extend-exclude = ["venv", ".venv", "build", "dist"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "UP", "SIM", "RUF"]
ignore = ["E501", "B008"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 1.3: 创建 `README.md`（中文，简洁）**

````markdown
# J-Link RTT Viewer (PyQt)

基于 PySide6 + QFluentWidgets 重写的 J-Link RTT 实时数据查看 / 内存导出工具。

## 功能

- **RTT 监控**：实时显示 MCU 通过 SEGGER RTT 输出的日志，支持 UTF-8 中文 / ANSI 颜色 / 0-15 通道切换 / 文本与十六进制发送 / 实时日志记录
- **内存查看**：任意地址 hex dump、固件按区间导出 `.bin`

## 开发

```bat
:: 首次创建 venv
python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt -i https://pypi.org/simple/

:: 启动
start.bat

:: 测试
pytest -q
```

## 打包

```bat
build_nuitka.bat
```

需要在系统上安装 SEGGER J-Link 驱动；`JLinkARM.dll` 由 pylink 自带，无需另置。
````

- [ ] **Step 1.4: 创建空 `__init__.py` 文件**

四个文件都写空内容：
- `src\__init__.py`
- `src\core\__init__.py`
- `src\ui\__init__.py`
- `src\ui\widgets\__init__.py`
- `tests\__init__.py`

- [ ] **Step 1.5: 创建 `tests\conftest.py`**

```python
"""共享 pytest fixtures。"""
import sys
from pathlib import Path

import pytest

# 防止某些子进程测试找不到 src
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def qapp():
    """整个测试会话共用一个 QApplication，避免多次创建。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
```

- [ ] **Step 1.6: 创建 venv 并安装依赖**

Run:
```bat
python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt -i https://pypi.org/simple/
```

Expected: 成功，无 ERROR。

- [ ] **Step 1.7: 验证可 import**

Run:
```bat
call venv\Scripts\activate.bat
python -c "import PySide6, qfluentwidgets, pylink; print(PySide6.__version__, pylink.__version__)"
```

Expected: 打印 PySide6 6.6+ 和 pylink 1.6.0+ 的版本号。

- [ ] **Step 1.8: Commit**

```bat
git add requirements.txt pyproject.toml README.md src tests
git commit -m "chore: 初始化项目骨架与依赖"
```

---

## Task 2：默认配置 `src/config.json`

**Files:**
- Create: `src\config.json`

- [ ] **Step 2.1: 写入默认配置**

```json
{
  "chip_models": [
    "STM32F103C8",
    "STM32F407VG",
    "STM32G070CB",
    "STM32G473RE",
    "STM32G474RE",
    "STM32H750VB",
    "nRF52840_xxAA",
    "CS32F103C8"
  ],
  "default_interface": "SWD",
  "default_speed_khz": 4000,
  "speed_options_khz": [
    100, 400, 1000, 2000, 4000, 8000, 9600,
    10000, 12000, 14400, 16000, 18000, 20000, 24000
  ],
  "default_font_family": "Consolas",
  "default_font_size": 13,
  "default_rtt_channel": 0
}
```

- [ ] **Step 2.2: 校验 JSON 合法**

Run:
```bat
python -c "import json; json.load(open('src/config.json', encoding='utf-8'))"
```

Expected: 无报错。

- [ ] **Step 2.3: Commit**

```bat
git add src/config.json
git commit -m "feat(config): 添加默认配置（芯片列表/速度/字体）"
```

---

## Task 3：logger 模块（TDD）

**Files:**
- Create: `src\core\logger.py`
- Create: `tests\test_logger.py`

- [ ] **Step 3.1: 写失败测试 `tests\test_logger.py`**

```python
"""logger 单例 + 文件 handler 行为。"""
import logging
import tempfile
from pathlib import Path

from core import logger as logger_mod


def test_get_logger_returns_same_instance():
    log1 = logger_mod.get_logger()
    log2 = logger_mod.get_logger()
    assert log1 is log2


def test_get_logger_has_console_and_file_handler(monkeypatch, tmp_path):
    # 重置模块状态
    monkeypatch.setattr(logger_mod, "_initialized", False)
    monkeypatch.setattr(logger_mod, "_logger", None)
    monkeypatch.setattr(logger_mod, "_log_dir_override", tmp_path)

    log = logger_mod.get_logger()
    handler_types = {type(h).__name__ for h in log.handlers}
    assert "StreamHandler" in handler_types
    assert "RotatingFileHandler" in handler_types

    log.info("hello logger")
    log_files = list(tmp_path.glob("*.log"))
    assert len(log_files) == 1
    assert "hello logger" in log_files[0].read_text(encoding="utf-8")


def test_log_dir_default_under_appdata(monkeypatch):
    monkeypatch.setattr(logger_mod, "_initialized", False)
    monkeypatch.setattr(logger_mod, "_logger", None)
    monkeypatch.setattr(logger_mod, "_log_dir_override", None)

    path = logger_mod.get_log_dir()
    assert "JLinkRTTViewer" in str(path)
    assert path.name == "logs"
```

- [ ] **Step 3.2: 运行测试，确认失败**

Run:
```bat
pytest tests/test_logger.py -v
```

Expected: ImportError 或 AttributeError，因为 `core.logger` 还不存在。

- [ ] **Step 3.3: 实现 `src\core\logger.py`**

```python
"""统一日志模块。

模块级单例：首次 get_logger() 时初始化 console + RotatingFileHandler，
后续调用返回同一 Logger。日志目录默认 %APPDATA%/JLinkRTTViewer/logs，
测试时可通过 _log_dir_override 注入。
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "jlink_rtt_viewer"
_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"

_initialized: bool = False
_logger: logging.Logger | None = None
_log_dir_override: Path | None = None  # 测试注入用


def get_log_dir() -> Path:
    """日志目录：%APPDATA%/JLinkRTTViewer/logs，缺失则 ~/AppData/Roaming/...。"""
    if _log_dir_override is not None:
        return Path(_log_dir_override)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "JLinkRTTViewer" / "logs"


def get_logger() -> logging.Logger:
    """获取应用全局 logger（单例）。"""
    global _initialized, _logger
    if _initialized and _logger is not None:
        return _logger

    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _initialized = True
    _logger = logger
    return logger
```

- [ ] **Step 3.4: 运行测试，确认通过**

Run:
```bat
pytest tests/test_logger.py -v
```

Expected: 3 个测试全部 PASS。

- [ ] **Step 3.5: Commit**

```bat
git add src/core/logger.py tests/test_logger.py
git commit -m "feat(core): 添加 logger 模块（单例 + RotatingFileHandler）"
```

---

## Task 4：ConfigService 节流写盘（TDD）

**Files:**
- Create: `src\core\config_service.py`
- Create: `tests\test_config_service.py`

- [ ] **Step 4.1: 写失败测试**

```python
"""ConfigService：默认值、节流、flush、atomic write。"""
import json
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from core.config_service import ConfigService


@pytest.fixture
def cfg(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # 把 bundled config.json 放进 tmp 临时 src
    bundled = tmp_path / "bundled_config.json"
    bundled.write_text(json.dumps({
        "chip_models": ["X", "Y"],
        "default_interface": "SWD",
        "default_speed_khz": 4000,
        "speed_options_khz": [100, 4000, 8000],
        "default_font_family": "Consolas",
        "default_font_size": 13,
        "default_rtt_channel": 0,
    }), encoding="utf-8")
    return ConfigService(bundled_config_path=bundled, throttle_ms=50)


def test_default_values(cfg):
    assert cfg.get("target_mcu") == ""
    assert cfg.get("rtt_channel") == 0
    assert cfg.get("send_history") == []
    assert cfg.get("theme") == "auto"
    assert cfg.get_chip_list() == ["X", "Y"]
    assert cfg.get_default_speeds() == [100, 4000, 8000]


def test_set_emits_signals(cfg, qapp):
    received = []
    cfg.theme_changed.connect(lambda v: received.append(("theme", v)))
    cfg.theme_color_changed.connect(lambda v: received.append(("color", v)))
    cfg.font_changed.connect(lambda f, s: received.append(("font", f, s)))

    cfg.set("theme", "dark")
    cfg.set("theme_color", "#28afe9")
    cfg.set("font_family", "Cascadia Mono")
    cfg.set("font_size", 16)

    QCoreApplication.processEvents()
    assert ("theme", "dark") in received
    assert ("color", "#28afe9") in received
    assert any(r[0] == "font" for r in received)


def test_set_throttled(cfg, qapp, tmp_path):
    user_prefs = tmp_path / "JLinkRTTViewer" / "user_prefs.json"

    cfg.set("target_mcu", "STM32G070CB")
    # 立即落盘 → 应该尚未写入
    assert not user_prefs.exists()

    # 等节流 timer + Qt 事件循环
    deadline = time.time() + 1.0
    while not user_prefs.exists() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert user_prefs.exists()
    data = json.loads(user_prefs.read_text(encoding="utf-8"))
    assert data["target_mcu"] == "STM32G070CB"


def test_flush_writes_immediately(cfg, qapp, tmp_path):
    user_prefs = tmp_path / "JLinkRTTViewer" / "user_prefs.json"

    cfg.set("rtt_channel", 5)
    cfg.flush()
    assert user_prefs.exists()
    data = json.loads(user_prefs.read_text(encoding="utf-8"))
    assert data["rtt_channel"] == 5


def test_send_history_truncated_to_50(cfg, qapp):
    long_hist = [f"cmd-{i}" for i in range(80)]
    cfg.set("send_history", long_hist)
    cfg.flush()
    assert cfg.get("send_history") == long_hist[-50:]


def test_reload_from_disk(cfg, qapp, tmp_path):
    cfg.set("theme", "dark")
    cfg.set("target_mcu", "STM32H750VB")
    cfg.flush()

    cfg2 = ConfigService(bundled_config_path=cfg._bundled_path, throttle_ms=50)
    assert cfg2.get("theme") == "dark"
    assert cfg2.get("target_mcu") == "STM32H750VB"


def test_atomic_write_on_crash(cfg, qapp, tmp_path, monkeypatch):
    """模拟写入时崩溃：tmp 文件即使存在，最终原文件不损坏。"""
    user_prefs = tmp_path / "JLinkRTTViewer" / "user_prefs.json"
    cfg.set("rtt_channel", 3)
    cfg.flush()

    original = user_prefs.read_text(encoding="utf-8")

    # mock os.replace 抛异常
    import os as _os
    original_replace = _os.replace

    def fake_replace(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr(_os, "replace", fake_replace)
    cfg.set("rtt_channel", 99)
    cfg.flush()  # 不应抛
    monkeypatch.setattr(_os, "replace", original_replace)

    # 原文件未被破坏
    assert user_prefs.read_text(encoding="utf-8") == original
```

- [ ] **Step 4.2: 运行测试，确认失败**

Run:
```bat
pytest tests/test_config_service.py -v
```

Expected: ImportError（`core.config_service` 不存在）。

- [ ] **Step 4.3: 实现 `src\core\config_service.py`**

```python
"""ConfigService：bundled config.json + 用户偏好 user_prefs.json。

设计要点：
1. set() 节流落盘（默认 200ms 单次 timer），避免高频值（窗口几何/字体大小）拖死 SSD
2. flush() 强制立即落盘，closeEvent 必须调用
3. 写入用 atomic replace（写 .tmp + os.replace）
4. theme/theme_color/font 改动 emit 信号，UI 热应用
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .logger import get_logger


class ConfigService(QObject):
    theme_changed = Signal(str)             # "light" / "dark" / "auto"
    theme_color_changed = Signal(str)       # hex e.g. "#28afe9"
    font_changed = Signal(str, int)         # (family, size)

    DEFAULTS: dict[str, Any] = {
        "target_mcu": "",
        "interface": "SWD",
        "speed_khz": 4000,
        "rtt_channel": 0,
        "send_history": [],
        "theme": "auto",            # light / dark / auto
        "theme_color": "#28afe9",
        "font_family": "Consolas",
        "font_size": 13,
        "max_display_lines": 10000,
        "rx_timeout_ms": 0,
        "log_dir": "",              # 空 → 用默认 %APPDATA%/JLinkRTTViewer/logs
        "window_geometry": "",      # base64 of QByteArray
        "hex_send_mode": False,
        "auto_scroll": True,
        "power_output": False,
        "log_recording": False,
    }

    SEND_HISTORY_MAX = 50

    def __init__(self, bundled_config_path: Path | None = None, throttle_ms: int = 200, parent=None):
        super().__init__(parent)
        self._logger = get_logger()
        self._bundled_path = bundled_config_path or (
            Path(__file__).resolve().parent.parent / "config.json"
        )
        self._user_prefs_path = self._compute_user_prefs_path()
        self._data: dict[str, Any] = dict(self.DEFAULTS)
        self._bundled: dict[str, Any] = {}
        self._load_bundled()
        self._load_user_prefs()

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(throttle_ms)
        self._flush_timer.timeout.connect(self._do_flush)
        self._dirty = False

    @staticmethod
    def _compute_user_prefs_path() -> Path:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "JLinkRTTViewer" / "user_prefs.json"

    def _load_bundled(self) -> None:
        try:
            with open(self._bundled_path, "r", encoding="utf-8") as f:
                self._bundled = json.load(f)
        except Exception as e:
            self._logger.warning(f"读取 bundled config.json 失败：{e}")
            self._bundled = {}

    def _load_user_prefs(self) -> None:
        path = self._user_prefs_path
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                disk = json.load(f)
        except Exception as e:
            self._logger.warning(f"读取 user_prefs.json 失败：{e}")
            return
        if not isinstance(disk, dict):
            return
        for key, default in self.DEFAULTS.items():
            if key in disk and isinstance(disk[key], type(default)):
                self._data[key] = disk[key]

    def get(self, key: str) -> Any:
        return self._data.get(key, self.DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        if key not in self.DEFAULTS:
            self._logger.warning(f"忽略未知偏好键：{key}")
            return
        expected = type(self.DEFAULTS[key])
        if not isinstance(value, expected):
            self._logger.warning(
                f"偏好 {key} 类型不匹配，期望 {expected.__name__}，收到 {type(value).__name__}"
            )
            return
        if key == "send_history":
            value = [str(x) for x in value][-self.SEND_HISTORY_MAX:]

        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._dirty = True
        self._flush_timer.start()

        if key == "theme":
            self.theme_changed.emit(value)
        elif key == "theme_color":
            self.theme_color_changed.emit(value)
        elif key in ("font_family", "font_size"):
            self.font_changed.emit(self._data["font_family"], self._data["font_size"])

    def flush(self) -> None:
        self._flush_timer.stop()
        self._do_flush()

    def _do_flush(self) -> None:
        if not self._dirty:
            return
        path = self._user_prefs_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            self._dirty = False
        except Exception as e:
            self._logger.error(f"保存 user_prefs.json 失败：{e}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def get_chip_list(self) -> list[str]:
        return list(self._bundled.get("chip_models", []))

    def get_default_speeds(self) -> list[int]:
        return list(self._bundled.get("speed_options_khz", []))

    def get_default_interface(self) -> str:
        return self._bundled.get("default_interface", "SWD")

    def get_default_speed(self) -> int:
        return self._bundled.get("default_speed_khz", 4000)
```

- [ ] **Step 4.4: 运行测试，确认通过**

Run:
```bat
pytest tests/test_config_service.py -v
```

Expected: 7 个测试全部 PASS。

- [ ] **Step 4.5: Commit**

```bat
git add src/core/config_service.py tests/test_config_service.py
git commit -m "feat(core): 添加 ConfigService（节流落盘 + 信号广播）"
```

---

## Task 5：ANSI 解析器（TDD）

**Files:**
- Create: `src\core\ansi_parser.py`
- Create: `tests\test_ansi_parser.py`

- [ ] **Step 5.1: 写失败测试**

```python
"""ANSI 转义序列解析为 (text, AnsiAttrs) 段。"""
from core.ansi_parser import AnsiAttrs, parse_ansi


def test_plain_text():
    assert parse_ansi("hello") == [("hello", AnsiAttrs())]


def test_single_color():
    out = parse_ansi("\x1b[31mred\x1b[0m")
    assert out == [("red", AnsiAttrs(fg="red"))]


def test_color_then_plain():
    out = parse_ansi("\x1b[31mhi\x1b[0m bye")
    assert out == [
        ("hi", AnsiAttrs(fg="red")),
        (" bye", AnsiAttrs()),
    ]


def test_multi_param():
    out = parse_ansi("\x1b[1;31;42mbold-red-bg-green\x1b[0m")
    attrs = out[0][1]
    assert attrs.bold is True
    assert attrs.fg == "red"
    assert attrs.bg == "green"


def test_nested_reset():
    out = parse_ansi("\x1b[31mA\x1b[32mB\x1b[0mC")
    assert out == [
        ("A", AnsiAttrs(fg="red")),
        ("B", AnsiAttrs(fg="green")),
        ("C", AnsiAttrs()),
    ]


def test_invalid_sequence_kept_as_literal():
    out = parse_ansi("\x1b[abcmhello")
    # 解析失败的序列应该当成字面量保留，不丢字符
    text = "".join(seg for seg, _ in out)
    assert "hello" in text


def test_unterminated_csi_at_end():
    out = parse_ansi("normal\x1b[31")
    text = "".join(seg for seg, _ in out)
    assert text.startswith("normal")


def test_bright_colors():
    out = parse_ansi("\x1b[91mbright-red\x1b[0m")
    assert out[0][1].fg == "bright_red"


def test_8bit_color_ignored_gracefully():
    out = parse_ansi("\x1b[38;5;196mfoo\x1b[0m")
    text = "".join(seg for seg, _ in out)
    assert text == "foo"
```

- [ ] **Step 5.2: 运行测试，确认失败**

Run:
```bat
pytest tests/test_ansi_parser.py -v
```

Expected: ImportError。

- [ ] **Step 5.3: 实现 `src\core\ansi_parser.py`**

```python
"""ANSI 转义序列解析。

只支持 SGR（Select Graphic Rendition）参数子集：
  0=reset, 1=bold, 22=normal,
  30-37 / 90-97 = 前景色, 40-47 / 100-107 = 背景色
更复杂的 38;5;N（256 色）/ 38;2;R;G;B（真彩）参数会被静默吞掉，但不抛错。
返回 list[(text, AnsiAttrs)]，AnsiAttrs 是纯 dataclass，不依赖 QtGui。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CSI_RE = re.compile(r"\x1b\[([0-9;]*)m")

_BASE = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
_BRIGHT = ["bright_" + c for c in _BASE]


@dataclass(frozen=True)
class AnsiAttrs:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False


@dataclass
class _MutAttrs:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False

    def freeze(self) -> AnsiAttrs:
        return AnsiAttrs(fg=self.fg, bg=self.bg, bold=self.bold)


def _apply_sgr(state: _MutAttrs, params: list[int]) -> None:
    """对一组 SGR 参数依次套用，识别不了的跳过。"""
    i = 0
    while i < len(params):
        p = params[i]
        if p == 0:
            state.fg = None
            state.bg = None
            state.bold = False
        elif p == 1:
            state.bold = True
        elif p == 22:
            state.bold = False
        elif 30 <= p <= 37:
            state.fg = _BASE[p - 30]
        elif 40 <= p <= 47:
            state.bg = _BASE[p - 40]
        elif 90 <= p <= 97:
            state.fg = _BRIGHT[p - 90]
        elif 100 <= p <= 107:
            state.bg = _BRIGHT[p - 100]
        elif p == 38 or p == 48:
            # 38;5;N or 38;2;R;G;B — skip subsequent params
            if i + 1 < len(params):
                mode = params[i + 1]
                if mode == 5 and i + 2 < len(params):
                    i += 2
                elif mode == 2 and i + 4 < len(params):
                    i += 4
                else:
                    i += 1
            # else: malformed, just consume
        # 其他参数（粗体/斜体/下划线之外）忽略
        i += 1


def parse_ansi(text: str) -> list[tuple[str, AnsiAttrs]]:
    """把含 ANSI 序列的字符串切成有色段。"""
    if not text:
        return []

    segments: list[tuple[str, AnsiAttrs]] = []
    state = _MutAttrs()
    pos = 0

    for m in CSI_RE.finditer(text):
        # 截取前一段普通文本
        if m.start() > pos:
            segments.append((text[pos:m.start()], state.freeze()))

        params_str = m.group(1)
        try:
            params = [int(p) for p in params_str.split(";") if p != ""]
            if not params:
                params = [0]
            _apply_sgr(state, params)
        except ValueError:
            # 非法参数 → 把这段 CSI 当字面量保留
            segments.append((m.group(0), state.freeze()))
        pos = m.end()

    # 剩余文本（如果末尾有未匹配的 \x1b[ 不会被 finditer 命中，会留在这里）
    if pos < len(text):
        segments.append((text[pos:], state.freeze()))

    # 合并相邻同 attrs 段，方便 UI 渲染
    if not segments:
        return []
    merged: list[tuple[str, AnsiAttrs]] = [segments[0]]
    for seg, attrs in segments[1:]:
        prev_seg, prev_attrs = merged[-1]
        if prev_attrs == attrs:
            merged[-1] = (prev_seg + seg, attrs)
        else:
            merged.append((seg, attrs))
    return merged
```

- [ ] **Step 5.4: 运行测试，确认通过**

Run:
```bat
pytest tests/test_ansi_parser.py -v
```

Expected: 9 个测试全部 PASS。

- [ ] **Step 5.5: Commit**

```bat
git add src/core/ansi_parser.py tests/test_ansi_parser.py
git commit -m "feat(core): 添加 ANSI 转义序列解析器"
```

---

## Task 6：memory_service 纯函数部分（TDD）

**Files:**
- Create: `src\core\memory_service.py`
- Create: `tests\test_memory_service.py`

- [ ] **Step 6.1: 写失败测试**

```python
"""memory_service：format_hex_dump 纯函数 + read_memory/export_firmware 用 mock jlink。"""
from unittest.mock import MagicMock

import pytest

from core import memory_service


def test_format_hex_dump_basic():
    data = bytes(range(16))
    out = memory_service.format_hex_dump(data, base_addr=0x08000000)
    assert "0x08000000" in out
    assert "00 01 02 03" in out
    assert "0F" in out


def test_format_hex_dump_multi_line():
    data = bytes(range(40))
    out = memory_service.format_hex_dump(data, base_addr=0x20000000)
    lines = out.splitlines()
    assert len(lines) == 3  # 16 + 16 + 8
    assert "0x20000000" in lines[0]
    assert "0x20000010" in lines[1]
    assert "0x20000020" in lines[2]


def test_format_hex_dump_ascii_column():
    data = b"Hello\x00World\x01\x02"
    out = memory_service.format_hex_dump(data, base_addr=0)
    assert "|Hello.World..|" in out  # 非可打印替换为 .


def test_format_hex_dump_empty():
    assert memory_service.format_hex_dump(b"", base_addr=0) == ""


def test_read_memory_returns_bytes():
    jlink = MagicMock()
    # memory_read(addr, word_count, nbits=32) 返回 list[int]（32-bit 字，小端写回）
    jlink.memory_read.return_value = [0x12345678, 0xCAFEBABE]
    result = memory_service.read_memory(jlink, addr=0x08000000, size=8)
    assert result == bytes.fromhex("78563412BEBAFECA")
    jlink.memory_read.assert_called_once_with(0x08000000, 2, nbits=32)


def test_read_memory_truncates_to_requested_size():
    jlink = MagicMock()
    jlink.memory_read.return_value = [0x11223344]
    result = memory_service.read_memory(jlink, addr=0, size=3)
    assert result == bytes.fromhex("443322")


def test_export_firmware_chunked_and_progress(tmp_path):
    jlink = MagicMock()
    # 16 KB → 4 chunks of 4 KB, each chunk 1024 words
    jlink.memory_read.side_effect = [
        [0xAAAAAAAA] * 1024,
        [0xBBBBBBBB] * 1024,
        [0xCCCCCCCC] * 1024,
        [0xDDDDDDDD] * 1024,
    ]
    progress = []
    out_file = tmp_path / "fw.bin"

    memory_service.export_firmware(
        jlink,
        save_path=str(out_file),
        start_addr=0x08000000,
        size=16 * 1024,
        progress_cb=lambda cur, total: progress.append((cur, total)),
    )

    assert out_file.stat().st_size == 16 * 1024
    blob = out_file.read_bytes()
    assert blob[:4] == bytes.fromhex("AAAAAAAA")
    assert blob[4096:4100] == bytes.fromhex("BBBBBBBB")
    assert progress[-1] == (4, 4)  # 最后一次回调是完成


def test_export_firmware_handles_partial_chunk(tmp_path):
    jlink = MagicMock()
    jlink.memory_read.side_effect = [
        [0xAAAAAAAA] * 1024,
        [0xBBBBBBBB] * 250,  # 1000 bytes
    ]
    out_file = tmp_path / "fw.bin"
    memory_service.export_firmware(
        jlink, save_path=str(out_file),
        start_addr=0, size=4096 + 1000,
        progress_cb=lambda c, t: None,
    )
    assert out_file.stat().st_size == 4096 + 1000
```

- [ ] **Step 6.2: 运行测试，确认失败**

Run:
```bat
pytest tests/test_memory_service.py -v
```

Expected: ImportError。

- [ ] **Step 6.3: 实现 `src\core\memory_service.py`**

```python
"""内存读取 / Hex 转储 / 固件导出。

调用方必须保证 jlink 已经 connected()，本模块只关心数据搬运。
read_memory / export_firmware 必须在持有 pylink 的那条线程（JLinkWorker）内调用。
format_hex_dump 是纯字符串函数，UI 端用，不需要 pylink。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

CHUNK_BYTES = 4096  # 每次 memory_read 的字节数（1024 个 32-bit 字）


def read_memory(jlink, addr: int, size: int) -> bytes:
    """读 size 字节，返回原始 bytes（小端字节序）。"""
    word_count = (size + 3) // 4
    words = jlink.memory_read(addr, word_count, nbits=32)
    out = bytearray()
    for w in words:
        out.append(w & 0xFF)
        out.append((w >> 8) & 0xFF)
        out.append((w >> 16) & 0xFF)
        out.append((w >> 24) & 0xFF)
    return bytes(out[:size])


def export_firmware(
    jlink,
    save_path: str,
    start_addr: int,
    size: int,
    progress_cb: Callable[[int, int], None],
) -> None:
    """按 4 KB 分块流式写入文件；progress_cb(current_chunk, total_chunks)。"""
    total_chunks = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for i in range(total_chunks):
            chunk_addr = start_addr + i * CHUNK_BYTES
            current_chunk_size = min(CHUNK_BYTES, size - i * CHUNK_BYTES)
            chunk = read_memory(jlink, chunk_addr, current_chunk_size)
            f.write(chunk)
            progress_cb(i + 1, total_chunks)


def format_hex_dump(data: bytes, base_addr: int = 0) -> str:
    """格式化为 ``0xAAAA...:  HH HH HH HH ... |ascii|`` 多行字符串。"""
    if not data:
        return ""
    lines: list[str] = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        addr = base_addr + offset

        hex_parts: list[str] = []
        for j in range(16):
            if j < len(chunk):
                hex_parts.append(f"{chunk[j]:02X}")
            else:
                hex_parts.append("  ")
            if j % 4 == 3:
                hex_parts.append(" ")
        hex_col = " ".join(hex_parts)

        ascii_col = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        lines.append(f"0x{addr:08X}:  {hex_col} |{ascii_col}|")
    return "\n".join(lines)
```

- [ ] **Step 6.4: 运行测试，确认通过**

Run:
```bat
pytest tests/test_memory_service.py -v
```

Expected: 7 个测试全部 PASS。

- [ ] **Step 6.5: Commit**

```bat
git add src/core/memory_service.py tests/test_memory_service.py
git commit -m "feat(core): 添加内存读取/Hex 转储/固件导出"
```

---

## Task 7：JLinkWorker 骨架与状态机（TDD with mock）

**Files:**
- Create: `src\core\jlink_worker.py`
- Create: `tests\test_jlink_worker.py`

> **测试策略**：所有 `pylink.JLink` 都用 `MagicMock` 替换；worker `run()` 启动后跑在测试主线程的另一条 QThread 里；通过 `QSignalSpy` 或捕获信号验证行为。

- [ ] **Step 7.1: 写失败测试（覆盖状态、信号、连接序列）**

```python
"""JLinkWorker：状态机、连接/断开序列、命令分发。

所有 pylink 都用 MagicMock。Worker 跑在子 QThread，主线程驱动 Qt 事件循环
处理 queued connection。
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, Qt


@pytest.fixture
def worker(qapp, monkeypatch):
    """创建 JLinkWorker 并 mock 掉 pylink.JLink。"""
    from core import jlink_worker as jw_mod

    fake_jlink_cls = MagicMock()
    fake_jlink_instance = MagicMock()
    fake_jlink_instance.opened.return_value = False
    fake_jlink_instance.connected.return_value = False
    fake_jlink_cls.return_value = fake_jlink_instance

    monkeypatch.setattr(jw_mod.pylink, "JLink", fake_jlink_cls)

    worker = jw_mod.JLinkWorker()
    worker.start()

    # 等待 worker 进入事件循环
    deadline = time.time() + 2.0
    while not worker._ready and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert worker._ready, "worker 启动超时"

    yield worker, fake_jlink_instance

    # 清理
    worker.stop_requested.emit()
    deadline = time.time() + 3.0
    while worker.isRunning() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    if worker.isRunning():
        worker.terminate()
        worker.wait(1000)


def _drain_events(timeout=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def test_initial_state_idle(worker):
    w, _ = worker
    assert w.state_name() == "IDLE"


def test_connect_sequence(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True

    states = []
    w.connection_state_changed.connect(lambda c, info: states.append((c, dict(info))))

    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(1.0)

    # 调用顺序：open() → set_tif() → set_speed() → connect() → rtt_start()
    assert jl.open.called
    assert jl.set_tif.called
    assert jl.set_speed.called
    assert jl.set_speed.call_args[0][0] == 4000
    assert jl.connect.call_args[0][0] == "STM32G070CB"
    assert jl.rtt_start.called

    assert any(c is True for c, _ in states)
    assert w.state_name() == "CONNECTED"


def test_no_double_open(worker):
    w, jl = worker
    jl.opened.return_value = True  # 已 open
    jl.connected.return_value = True

    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(1.0)

    assert jl.open.call_count == 0  # 已 open 不再 open


def test_disconnect_sequence_with_guards(worker):
    w, jl = worker
    # 先连上
    jl.opened.return_value = True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    w.disconnect_requested.emit()
    _drain_events(0.5)

    assert jl.rtt_stop.called
    assert jl.close.called
    assert w.state_name() == "IDLE"


def test_disconnect_skips_close_if_not_opened(worker):
    w, jl = worker
    # 连接然后让 opened/connected 都变 false
    jl.opened.return_value = True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    jl.opened.return_value = False
    jl.connected.return_value = False

    w.disconnect_requested.emit()
    _drain_events(0.5)

    # 守卫生效：connected() False → rtt_stop 不调；opened() False → close 不调
    jl.rtt_stop.assert_not_called()
    jl.close.assert_not_called()


def test_reconnect_after_disconnect(worker):
    """断开后立即重连：复现原项目"无法再次打开"场景。"""
    w, jl = worker

    jl.opened.return_value = True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    w.disconnect_requested.emit()
    _drain_events(0.5)
    jl.opened.return_value = False
    jl.connected.return_value = False

    # 重连
    open_calls_before = jl.open.call_count
    jl.opened.side_effect = [False, True]  # 第一次 check False → open() → 之后 True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    assert jl.open.call_count > open_calls_before
    assert w.state_name() == "CONNECTED"


def test_set_tif_swd_vs_jtag(worker):
    import pylink
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True

    w.connect_requested.emit("STM32G070CB", "JTAG", 4000, 0)
    _drain_events(0.5)
    assert jl.set_tif.call_args[0][0] == pylink.enums.JLinkInterfaces.JTAG

    w.disconnect_requested.emit()
    _drain_events(0.3)

    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)
    assert jl.set_tif.call_args[0][0] == pylink.enums.JLinkInterfaces.SWD


def test_stop_requested_quits_thread(qapp, monkeypatch):
    """stop_requested 必须 worker 自己 quit()，不能外部 quit()。"""
    from core import jlink_worker as jw_mod

    fake_jlink_cls = MagicMock()
    fake_jlink_instance = MagicMock()
    fake_jlink_instance.opened.return_value = False
    fake_jlink_instance.connected.return_value = False
    fake_jlink_cls.return_value = fake_jlink_instance
    monkeypatch.setattr(jw_mod.pylink, "JLink", fake_jlink_cls)

    w = jw_mod.JLinkWorker()
    w.start()
    deadline = time.time() + 2.0
    while not w._ready and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    w.stop_requested.emit()
    deadline = time.time() + 3.0
    while w.isRunning() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert not w.isRunning(), "stop_requested 后 worker 应已退出"
```

- [ ] **Step 7.2: 运行测试，确认失败**

Run:
```bat
pytest tests/test_jlink_worker.py -v
```

Expected: ImportError。

- [ ] **Step 7.3: 实现 `src\core\jlink_worker.py`（骨架 + 连接/断开 + stop）**

```python
"""JLinkWorker：所有 pylink 调用集中在这一条 QThread。

设计要点：
1. __init__ 在主线程；run() 内才是 worker 线程。pylink.JLink / QTimer / IncrementalDecoder
   都在 run() 内创建，确保 thread affinity 正确。
2. 输入信号用 Qt.QueuedConnection 投递到 worker 自己的事件循环。
3. stop_requested 由 worker 自己处理：清理 pylink → quit() → run() 退出；
   主线程不能外部 quit()，否则和阻塞中的 C 调用赛跑。
4. 连接时 if not opened(): open()；断开时 if connected(): rtt_stop(); if opened(): close()。
"""
from __future__ import annotations

import codecs
import os
from datetime import datetime
from pathlib import Path

import pylink
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from . import memory_service
from .logger import get_logger

_STATE_IDLE = "IDLE"
_STATE_CONNECTING = "CONNECTING"
_STATE_CONNECTED = "CONNECTED"
_STATE_DISCONNECTING = "DISCONNECTING"


class JLinkWorker(QThread):
    # ---- 输入信号 ----
    connect_requested = Signal(str, str, int, int)   # target, iface, speed, channel
    disconnect_requested = Signal()
    send_data_requested = Signal(str, bool)
    reset_target_requested = Signal()
    set_rtt_channel_requested = Signal(int)
    set_pause_receive_requested = Signal(bool)
    set_power_output_requested = Signal(bool)
    read_memory_requested = Signal(int, int)
    export_firmware_requested = Signal(str, int, int)
    start_log_recording_requested = Signal(str)
    stop_log_recording_requested = Signal()
    stop_requested = Signal()

    # ---- 输出信号 ----
    rtt_data_received = Signal(str)
    connection_state_changed = Signal(bool, dict)
    log_message = Signal(str, str)             # level, msg
    command_result = Signal(str, bool, dict)
    memory_read_finished = Signal(int, bytes)  # addr, raw bytes
    firmware_export_progress = Signal(int, int)
    firmware_export_finished = Signal(bool, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = get_logger()
        self._state: str = _STATE_IDLE
        self._channel: int = 0
        self._paused: bool = False
        self._ready: bool = False  # 测试用：worker 事件循环已就绪

        # 这些在 run() 内创建：
        self.jlink: pylink.JLink | None = None
        self._decoder: codecs.IncrementalDecoder | None = None
        self._poll_timer: QTimer | None = None
        self._log_file = None
        self._log_path: str | None = None

    # ============================================================
    # 线程入口
    # ============================================================
    def run(self) -> None:
        # 所有依赖事件循环的 QObject 在这里创建
        self.jlink = pylink.JLink()
        self._reset_utf8_decoder()
        self._poll_timer = QTimer()  # 无 parent → 归属当前线程
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self._poll_rtt)

        # 注：信号 ↔ 槽连接在主线程已经建立（信号对象在 __init__），
        # 默认 AutoConnection 会变 QueuedConnection（跨线程）
        self.connect_requested.connect(self._on_connect, type=3)         # Qt.QueuedConnection
        self.disconnect_requested.connect(self._on_disconnect, type=3)
        self.send_data_requested.connect(self._on_send_data, type=3)
        self.reset_target_requested.connect(self._on_reset_target, type=3)
        self.set_rtt_channel_requested.connect(self._on_set_channel, type=3)
        self.set_pause_receive_requested.connect(self._on_set_paused, type=3)
        self.set_power_output_requested.connect(self._on_set_power, type=3)
        self.read_memory_requested.connect(self._on_read_memory, type=3)
        self.export_firmware_requested.connect(self._on_export_firmware, type=3)
        self.start_log_recording_requested.connect(self._on_start_log, type=3)
        self.stop_log_recording_requested.connect(self._on_stop_log, type=3)
        self.stop_requested.connect(self._on_stop, type=3)

        self._ready = True
        self.exec()

    # ============================================================
    # 状态查询（仅给测试 / 调试用，必须线程安全；Python 单赋值原子，简单读 OK）
    # ============================================================
    def state_name(self) -> str:
        return self._state

    # ============================================================
    # 连接 / 断开
    # ============================================================
    @Slot(str, str, int, int)
    def _on_connect(self, target: str, iface: str, speed: int, channel: int) -> None:
        if self._state == _STATE_CONNECTED:
            self.log_message.emit("warning", "已连接，先断开再切换设备")
            return
        self._state = _STATE_CONNECTING
        self._channel = channel
        try:
            if not self.jlink.opened():
                self.jlink.open()  # 不传 serial_no, pylink 自动选第一个
            tif = pylink.enums.JLinkInterfaces.SWD if iface == "SWD" \
                else pylink.enums.JLinkInterfaces.JTAG
            self.jlink.set_tif(tif)
            self.jlink.set_speed(int(speed))
            self.jlink.connect(target)
            self.jlink.rtt_start()
            self._reset_utf8_decoder()
            self._state = _STATE_CONNECTED
            info = self._collect_device_info(target, iface, speed)
            self.connection_state_changed.emit(True, info)
            self._poll_timer.start()
        except Exception as e:
            self._logger.error(f"连接失败：{e}")
            self.log_message.emit("error", f"连接失败：{e}")
            self._transition_to_idle()

    @Slot()
    def _on_disconnect(self) -> None:
        self._do_disconnect()

    def _do_disconnect(self) -> None:
        self._state = _STATE_DISCONNECTING
        if self._poll_timer is not None and self._poll_timer.isActive():
            self._poll_timer.stop()
        self._close_log_file()

        try:
            if self.jlink is not None and self.jlink.connected():
                self.jlink.rtt_stop()
        except Exception as e:
            self._logger.warning(f"rtt_stop 失败：{e}")
        try:
            if self.jlink is not None and self.jlink.opened():
                self.jlink.close()
        except Exception as e:
            self._logger.warning(f"close 失败：{e}")

        self._state = _STATE_IDLE
        self.connection_state_changed.emit(False, {})

    def _transition_to_idle(self) -> None:
        self._do_disconnect()

    def _collect_device_info(self, target: str, iface: str, speed: int) -> dict:
        try:
            return {
                "jlink_firmware": self.jlink.firmware_version,
                "jlink_hardware": str(self.jlink.hardware_version),
                "jlink_serial": str(self.jlink.serial_number),
                "core_name": self.jlink.core_name(),
                "core_id": hex(self.jlink.core_id()),
                "core_cpu": self.jlink.core_cpu(),
                "target_device": target,
                "interface": iface,
                "speed_khz": speed,
            }
        except Exception as e:
            self._logger.warning(f"获取设备信息失败：{e}")
            return {"target_device": target, "interface": iface, "speed_khz": speed}

    # ============================================================
    # RTT 读循环
    # ============================================================
    def _reset_utf8_decoder(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def _poll_rtt(self) -> None:
        if self._state != _STATE_CONNECTED or self._paused:
            return
        try:
            data = self.jlink.rtt_read(self._channel, 4096)
        except Exception as e:
            self._logger.error(f"RTT 读异常：{e}")
            self.log_message.emit("error", f"RTT 读异常：{e}")
            self._transition_to_idle()
            return
        if not data:
            return
        decoded = self._decoder.decode(bytes(data))
        if decoded:
            self.rtt_data_received.emit(decoded)
            self._write_log_file(decoded)

    # ============================================================
    # 命令槽（占位，下一 Task 实现）
    # ============================================================
    @Slot(str, bool)
    def _on_send_data(self, data: str, is_hex: bool) -> None:
        # Task 8 实现
        pass

    @Slot()
    def _on_reset_target(self) -> None:
        pass

    @Slot(int)
    def _on_set_channel(self, channel: int) -> None:
        self._channel = channel
        self.log_message.emit("info", f"RTT 通道切换为 {channel}")

    @Slot(bool)
    def _on_set_paused(self, paused: bool) -> None:
        self._paused = paused

    @Slot(bool)
    def _on_set_power(self, enable: bool) -> None:
        pass

    @Slot(int, int)
    def _on_read_memory(self, addr: int, size: int) -> None:
        pass

    @Slot(str, int, int)
    def _on_export_firmware(self, path: str, start_addr: int, size: int) -> None:
        pass

    @Slot(str)
    def _on_start_log(self, log_dir: str) -> None:
        pass

    @Slot()
    def _on_stop_log(self) -> None:
        pass

    def _close_log_file(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
            self._log_path = None

    def _write_log_file(self, text: str) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.write(text)
            self._log_file.flush()
        except Exception as e:
            self._logger.warning(f"写日志文件失败：{e}")

    # ============================================================
    # 停止
    # ============================================================
    @Slot()
    def _on_stop(self) -> None:
        """主线程发 stop_requested → worker 自己清理 + quit。"""
        self._do_disconnect()
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self.quit()  # 让 run() 的 exec() 返回
```

- [ ] **Step 7.4: 运行测试，确认通过**

Run:
```bat
pytest tests/test_jlink_worker.py -v
```

Expected: 7 个测试全部 PASS。

- [ ] **Step 7.5: Commit**

```bat
git add src/core/jlink_worker.py tests/test_jlink_worker.py
git commit -m "feat(core): 添加 JLinkWorker 骨架与连接/断开时序"
```

---

## Task 8：JLinkWorker 命令槽实现（TDD）

**Files:**
- Modify: `src\core\jlink_worker.py`
- Modify: `tests\test_jlink_worker.py`

填充上一任务遗留的占位槽：send_data / reset_target / set_power / read_memory / export_firmware / start_log_recording。

- [ ] **Step 8.1: 追加测试**

把以下测试**追加**到 `tests\test_jlink_worker.py` 末尾：

```python
def test_send_data_text(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_write.return_value = 5
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.send_data_requested.emit("hello", False)
    _drain_events(0.3)
    jl.rtt_write.assert_called_once_with(0, b"hello")


def test_send_data_hex(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_write.return_value = 3
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.send_data_requested.emit("AA BB\nCC", True)
    _drain_events(0.3)
    jl.rtt_write.assert_called_once_with(0, bytes.fromhex("AABBCC"))


def test_send_data_when_not_connected(worker):
    w, jl = worker
    results = []
    w.command_result.connect(lambda c, ok, p: results.append((c, ok, dict(p))))

    w.send_data_requested.emit("hello", False)
    _drain_events(0.3)
    assert ("send_data", False) == (results[0][0], results[0][1])


def test_reset_target(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.reset_target_requested.emit()
    _drain_events(0.3)
    jl.reset.assert_called_once()


def test_set_channel_takes_effect(worker):
    w, jl = worker
    w.set_rtt_channel_requested.emit(5)
    _drain_events(0.2)
    assert w._channel == 5


def test_power_output_on_off(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.set_power_output_requested.emit(True)
    _drain_events(0.2)
    jl.power_on.assert_called_once()

    w.set_power_output_requested.emit(False)
    _drain_events(0.2)
    jl.power_off.assert_called_once()


def test_read_memory_emits_bytes(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.memory_read.return_value = [0x12345678]
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    received = []
    w.memory_read_finished.connect(lambda addr, raw: received.append((addr, bytes(raw))))

    w.read_memory_requested.emit(0x08000000, 4)
    _drain_events(0.5)
    assert received == [(0x08000000, bytes.fromhex("78563412"))]


def test_export_firmware_progress(worker, tmp_path):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    # 8 KB = 2 chunks, 每 chunk 1024 words
    jl.memory_read.side_effect = [[0xAA] * 1024, [0xBB] * 1024]
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    progress = []
    finished = []
    w.firmware_export_progress.connect(lambda c, t: progress.append((c, t)))
    w.firmware_export_finished.connect(lambda ok, p, err: finished.append((ok, p, err)))

    out = tmp_path / "fw.bin"
    w.export_firmware_requested.emit(str(out), 0x08000000, 8 * 1024)
    _drain_events(1.5)

    assert progress[-1] == (2, 2)
    assert finished and finished[0][0] is True
    assert out.stat().st_size == 8 * 1024


def test_log_recording_writes_file(worker, tmp_path):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.start_log_recording_requested.emit(str(tmp_path))
    _drain_events(0.2)
    assert w._log_file is not None

    # 模拟一次 RTT 输出
    w._write_log_file("hello log\n")
    w.stop_log_recording_requested.emit()
    _drain_events(0.2)

    logs = list(tmp_path.glob("*.log"))
    assert len(logs) == 1
    assert "hello log" in logs[0].read_text(encoding="utf-8")
```

- [ ] **Step 8.2: 运行测试，确认失败**

Run:
```bat
pytest tests/test_jlink_worker.py -v -k "send_data or reset_target or power_output or read_memory or export_firmware or log_recording or set_channel"
```

Expected: 9 个测试 FAIL（占位槽什么都没做）。

- [ ] **Step 8.3: 实现各槽函数**

替换 `src\core\jlink_worker.py` 中所有占位 `pass` 槽：

```python
    @Slot(str, bool)
    def _on_send_data(self, data: str, is_hex: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("send_data", False, {"error": "未连接"})
            return
        try:
            if is_hex:
                cleaned = data.replace(" ", "").replace("\n", "").replace("\r", "")
                if len(cleaned) % 2 != 0:
                    cleaned += "0"
                payload = bytes.fromhex(cleaned)
            else:
                payload = data.encode("utf-8")
            written = self.jlink.rtt_write(self._channel, payload)
            ok = written == len(payload)
            self.command_result.emit("send_data", ok, {"bytes": written})
        except Exception as e:
            self._logger.error(f"发送数据失败：{e}")
            self.command_result.emit("send_data", False, {"error": str(e)})

    @Slot()
    def _on_reset_target(self) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("reset", False, {"error": "未连接"})
            return
        try:
            self.jlink.reset(1, False)  # 正常重置，复位后运行
            self.command_result.emit("reset", True, {})
            self.log_message.emit("info", "目标设备已重置")
        except Exception as e:
            self.command_result.emit("reset", False, {"error": str(e)})

    @Slot(bool)
    def _on_set_power(self, enable: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("power_output", False, {"error": "未连接"})
            return
        try:
            if enable:
                self.jlink.power_on(default=False)
            else:
                self.jlink.power_off(default=False)
            self.command_result.emit("power_output", True, {"enabled": enable})
        except Exception as e:
            self.command_result.emit("power_output", False, {"error": str(e)})

    @Slot(int, int)
    def _on_read_memory(self, addr: int, size: int) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("read_memory", False, {"error": "未连接"})
            return
        try:
            raw = memory_service.read_memory(self.jlink, addr, size)
            self.memory_read_finished.emit(addr, bytes(raw))
        except Exception as e:
            self._logger.error(f"读内存失败：{e}")
            self.command_result.emit("read_memory", False, {"error": str(e)})

    @Slot(str, int, int)
    def _on_export_firmware(self, path: str, start_addr: int, size: int) -> None:
        if self._state != _STATE_CONNECTED:
            self.firmware_export_finished.emit(False, "", "未连接")
            return
        # 导出期间停 RTT 读循环
        was_active = self._poll_timer.isActive()
        self._poll_timer.stop()
        try:
            def cb(cur: int, total: int) -> None:
                self.firmware_export_progress.emit(cur, total)
            memory_service.export_firmware(self.jlink, path, start_addr, size, cb)
            self.firmware_export_finished.emit(True, path, "")
        except Exception as e:
            self._logger.error(f"导出固件失败：{e}")
            self.firmware_export_finished.emit(False, path, str(e))
        finally:
            if was_active and self._state == _STATE_CONNECTED:
                self._poll_timer.start()

    @Slot(str)
    def _on_start_log(self, log_dir: str) -> None:
        if self._log_file is not None:
            return
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._log_path = str(Path(log_dir) / f"rtt_{stamp}.log")
            self._log_file = open(self._log_path, "a", encoding="utf-8", buffering=1)
            self.command_result.emit("log_recording", True, {"path": self._log_path})
        except Exception as e:
            self._logger.error(f"开始日志记录失败：{e}")
            self.command_result.emit("log_recording", False, {"error": str(e)})

    @Slot()
    def _on_stop_log(self) -> None:
        self._close_log_file()
        self.command_result.emit("log_recording", True, {"stopped": True})
```

- [ ] **Step 8.4: 运行测试，确认通过**

Run:
```bat
pytest tests/test_jlink_worker.py -v
```

Expected: 全部测试通过（含 Task 7 已有的 + Task 8 新增的）。

- [ ] **Step 8.5: Commit**

```bat
git add src/core/jlink_worker.py tests/test_jlink_worker.py
git commit -m "feat(core): JLinkWorker 命令槽（发送/重置/电源/内存/导出/日志）"
```

---

## Task 9：程序入口 main.py（含 DLL 致命检测）

**Files:**
- Create: `src\main.py`

> 本任务先建一个**最小可启动**的主入口，主窗口下一任务实现。

- [ ] **Step 9.1: 实现 `src\main.py`**

```python
"""程序入口。

启动顺序：
1. 高 DPI 策略
2. QApplication
3. logger（先于业务模块）
4. pylink DLL 致命检测（失败即弹框退出）
5. ConfigService + 主题色
6. MainWindow.show()
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

# 确保 src 加入 path
SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)

    from core.logger import get_logger
    logger = get_logger()
    logger.info("应用启动")

    try:
        import pylink
        pylink.JLink()  # 触发 JLinkARM.dll 加载，构造失败立即抛
    except Exception as e:
        logger.error(f"加载 JLinkARM.dll 失败：{e}")
        QMessageBox.critical(
            None,
            "启动失败",
            f"加载 JLinkARM.dll 失败：\n\n{e}\n\n请确认已安装 SEGGER J-Link 驱动。",
        )
        return 1

    from core.config_service import ConfigService
    cfg = ConfigService()

    from qfluentwidgets import Theme, setTheme, setThemeColor
    theme_str = cfg.get("theme")
    if theme_str == "dark":
        setTheme(Theme.DARK)
    elif theme_str == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.AUTO)
    setThemeColor(cfg.get("theme_color"))

    from ui.main_window import MainWindow
    win = MainWindow(cfg)
    win.show()

    rc = app.exec()
    cfg.flush()
    logger.info(f"应用退出，rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9.2: 暂时提交（MainWindow 占位会在 Task 10 创建后才能跑）**

不运行启动 — 因为 `ui.main_window` 还不存在。下一任务才会创建。

直接 commit：
```bat
git add src/main.py
git commit -m "feat: 添加程序入口（含 DLL 致命检测）"
```

---

## Task 10：MainWindow 与导航骨架

**Files:**
- Create: `src\ui\main_window.py`
- Create: `src\ui\rtt_monitor_page.py`（占位）
- Create: `src\ui\memory_viewer_page.py`（占位）
- Create: `src\ui\settings_page.py`（占位）
- Create: `src\ui\about_page.py`（占位）

> 本任务先把"骨架 + 占位四页"建好，让 `python src/main.py` 能跑起来看到导航。各页面的实现放在后续任务。

- [ ] **Step 10.1: 创建四个页面的占位 widget**

`src\ui\rtt_monitor_page.py`：
```python
"""RTT 监控页（占位，由 Task 11/12 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class RTTMonitorPage(QWidget):
    def __init__(self, worker, cfg, parent=None):
        super().__init__(parent)
        self.setObjectName("rtt-monitor")
        self._worker = worker
        self._cfg = cfg
        layout = QVBoxLayout(self)
        label = SubtitleLabel("RTT 监控（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
```

`src\ui\memory_viewer_page.py`：
```python
"""内存查看页（占位，由 Task 13 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class MemoryViewerPage(QWidget):
    def __init__(self, worker, cfg, parent=None):
        super().__init__(parent)
        self.setObjectName("memory-viewer")
        self._worker = worker
        self._cfg = cfg
        layout = QVBoxLayout(self)
        label = SubtitleLabel("内存查看（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
```

`src\ui\settings_page.py`：
```python
"""设置页（占位，由 Task 14 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class SettingsPage(QWidget):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setObjectName("settings")
        self._cfg = cfg
        layout = QVBoxLayout(self)
        label = SubtitleLabel("设置（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
```

`src\ui\about_page.py`：
```python
"""关于页（占位，由 Task 15 实现）。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import SubtitleLabel


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        layout = QVBoxLayout(self)
        label = SubtitleLabel("关于（占位）", self)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
```

- [ ] **Step 10.2: 实现 `src\ui\main_window.py`**

```python
"""主窗口：FluentWindow + 左侧导航 + JLinkWorker 全生命周期。"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QCloseEvent
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition

from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker
from core.logger import get_logger

from .about_page import AboutPage
from .memory_viewer_page import MemoryViewerPage
from .rtt_monitor_page import RTTMonitorPage
from .settings_page import SettingsPage


class MainWindow(FluentWindow):
    def __init__(self, cfg: ConfigService) -> None:
        super().__init__()
        self._cfg = cfg
        self._logger = get_logger()

        # 1. 创建 worker 并启动
        self.worker = JLinkWorker()
        self.worker.start()

        # 2. 各页面
        self.rtt_page = RTTMonitorPage(self.worker, cfg, self)
        self.memory_page = MemoryViewerPage(self.worker, cfg, self)
        self.settings_page = SettingsPage(cfg, self)
        self.about_page = AboutPage(self)

        # 3. 导航
        self.addSubInterface(self.rtt_page, FIF.SPEED_HIGH, "RTT 监控")
        self.addSubInterface(self.memory_page, FIF.CODE, "内存查看")
        self.navigationInterface.addSeparator()
        self.addSubInterface(
            self.settings_page, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM
        )
        self.addSubInterface(
            self.about_page, FIF.INFO, "关于", NavigationItemPosition.BOTTOM
        )

        # 4. 窗口属性
        self.setWindowTitle("J-Link RTT Viewer")
        self._restore_geometry()

    def _restore_geometry(self) -> None:
        geom_b64 = self._cfg.get("window_geometry")
        if geom_b64:
            try:
                ba = QByteArray(base64.b64decode(geom_b64))
                self.restoreGeometry(ba)
                return
            except Exception as e:
                self._logger.warning(f"恢复窗口几何失败：{e}")
        self.resize(1200, 800)

    def closeEvent(self, event: QCloseEvent) -> None:
        # 保存窗口几何
        geom = self.saveGeometry()
        self._cfg.set("window_geometry", base64.b64encode(bytes(geom)).decode("ascii"))
        self._cfg.flush()

        # 关闭 worker：发停止信号 → wait → 兜底 terminate
        self.worker.stop_requested.emit()
        if not self.worker.wait(3000):
            self._logger.error("worker 退出超时，强制 terminate")
            self.worker.terminate()
            self.worker.wait(1000)

        event.accept()
```

- [ ] **Step 10.3: 启动应用，人眼校验**

Run:
```bat
call venv\Scripts\activate.bat
python src\main.py
```

Expected:
- 出现 Fluent 风格窗口，左侧 4 项导航：RTT 监控 / 内存查看 / 设置 / 关于
- 点击各项，右侧切换占位文字
- 关闭窗口干净退出（任务管理器无残留 python.exe）

- [ ] **Step 10.4: Commit**

```bat
git add src/ui/main_window.py src/ui/rtt_monitor_page.py src/ui/memory_viewer_page.py src/ui/settings_page.py src/ui/about_page.py
git commit -m "feat(ui): 主窗口与导航骨架（FluentWindow + 4 个占位页）"
```

---

## Task 11：RTT 监控页 — 控制栏 + 显示区 + 自动滚动

**Files:**
- Modify: `src\ui\rtt_monitor_page.py`

- [ ] **Step 11.1: 完整替换 `src\ui\rtt_monitor_page.py`**

```python
"""RTT 监控页：控制栏 + 显示区 + 选项栏 + 搜索栏 + 发送栏。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QTextCharFormat, QTextCursor, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    EditableComboBox,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SpinBox,
)

from core.ansi_parser import AnsiAttrs, parse_ansi
from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker


_ANSI_COLOR_MAP = {
    "black": "#000000",
    "red": "#cc0000",
    "green": "#00aa00",
    "yellow": "#cc9900",
    "blue": "#3366cc",
    "magenta": "#aa00aa",
    "cyan": "#00aaaa",
    "white": "#dddddd",
    "bright_black": "#666666",
    "bright_red": "#ff5555",
    "bright_green": "#55ff55",
    "bright_yellow": "#ffff55",
    "bright_blue": "#5599ff",
    "bright_magenta": "#ff55ff",
    "bright_cyan": "#55ffff",
    "bright_white": "#ffffff",
}


class RTTMonitorPage(QWidget):
    def __init__(self, worker: JLinkWorker, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("rtt-monitor")
        self._worker = worker
        self._cfg = cfg

        self._build_ui()
        self._wire_signals()
        self._apply_font(cfg.get("font_family"), cfg.get("font_size"))

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        # ---- 控制栏 ----
        ctrl = QHBoxLayout()
        ctrl.addWidget(BodyLabel("目标设备"))
        self.cb_target = EditableComboBox(self)
        self.cb_target.addItems(self._cfg.get_chip_list())
        last_mcu = self._cfg.get("target_mcu")
        if last_mcu:
            self.cb_target.setCurrentText(last_mcu)
        self.cb_target.setMinimumWidth(180)
        ctrl.addWidget(self.cb_target)

        ctrl.addWidget(BodyLabel("接口"))
        self.cb_iface = ComboBox(self)
        self.cb_iface.addItems(["SWD", "JTAG"])
        self.cb_iface.setCurrentText(self._cfg.get("interface"))
        ctrl.addWidget(self.cb_iface)

        ctrl.addWidget(BodyLabel("速度(kHz)"))
        self.cb_speed = ComboBox(self)
        for s in self._cfg.get_default_speeds():
            self.cb_speed.addItem(str(s))
        cur_speed = str(self._cfg.get("speed_khz"))
        if self.cb_speed.findText(cur_speed) < 0:
            self.cb_speed.addItem(cur_speed)
        self.cb_speed.setCurrentText(cur_speed)
        ctrl.addWidget(self.cb_speed)

        ctrl.addWidget(BodyLabel("RTT 通道"))
        self.sp_channel = SpinBox(self)
        self.sp_channel.setRange(0, 15)
        self.sp_channel.setValue(self._cfg.get("rtt_channel"))
        ctrl.addWidget(self.sp_channel)

        self.btn_connect = PrimaryPushButton("连接", self)
        self.btn_reset = PushButton("重置目标", self)
        self.btn_reset.setEnabled(False)
        ctrl.addWidget(self.btn_connect)
        ctrl.addWidget(self.btn_reset)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # ---- 选项栏 ----
        opt = QHBoxLayout()
        self.chk_auto_scroll = CheckBox("自动滚动")
        self.chk_auto_scroll.setChecked(self._cfg.get("auto_scroll"))
        self.chk_pause = CheckBox("暂停接收")
        self.chk_power = CheckBox("电源输出")
        self.chk_power.setEnabled(False)
        self.chk_log_rec = CheckBox("实时日志记录")
        self.btn_clear = PushButton("清除", self)
        self.btn_save = PushButton("💾 保存当前", self)
        opt.addWidget(self.chk_auto_scroll)
        opt.addWidget(self.chk_pause)
        opt.addWidget(self.chk_power)
        opt.addWidget(self.chk_log_rec)
        opt.addStretch(1)
        opt.addWidget(self.btn_clear)
        opt.addWidget(self.btn_save)
        root.addLayout(opt)

        # ---- 显示区 ----
        self.display = QPlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setMaximumBlockCount(self._cfg.get("max_display_lines"))
        self.display.setLineWrapMode(QPlainTextEdit.NoWrap)
        root.addWidget(self.display, 1)

        # ---- 发送栏 ----
        send = QHBoxLayout()
        from qfluentwidgets import LineEdit
        self.le_send = LineEdit(self)
        self.le_send.setPlaceholderText("输入要发送的数据 (Hex 模式下用 16 进制字符)")
        self.chk_hex = CheckBox("Hex")
        self.chk_hex.setChecked(self._cfg.get("hex_send_mode"))
        self.btn_send = PushButton("发送", self)
        self.btn_send.setEnabled(False)
        send.addWidget(self.le_send, 1)
        send.addWidget(self.chk_hex)
        send.addWidget(self.btn_send)
        root.addLayout(send)

    # ------------------------------------------------------------------
    # 信号接线
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_reset.clicked.connect(self._worker.reset_target_requested.emit)
        self.btn_clear.clicked.connect(self.display.clear)
        self.chk_pause.toggled.connect(self._worker.set_pause_receive_requested.emit)
        self.chk_power.toggled.connect(self._worker.set_power_output_requested.emit)
        self.sp_channel.valueChanged.connect(self._on_channel_changed)
        self.chk_auto_scroll.toggled.connect(lambda v: self._cfg.set("auto_scroll", v))
        self.chk_hex.toggled.connect(lambda v: self._cfg.set("hex_send_mode", v))
        self.btn_send.clicked.connect(self._on_send_clicked)

        self._worker.rtt_data_received.connect(self._on_rtt_data)
        self._worker.connection_state_changed.connect(self._on_state_changed)

        self._cfg.font_changed.connect(self._apply_font)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_connect_clicked(self) -> None:
        if self.btn_connect.text() == "连接":
            target = self.cb_target.currentText().strip()
            if not target:
                InfoBar.warning("提示", "请先选择目标芯片", parent=self,
                                position=InfoBarPosition.TOP, duration=2000)
                return
            iface = self.cb_iface.currentText()
            speed = int(self.cb_speed.currentText())
            channel = self.sp_channel.value()
            # 持久化用户选择
            self._cfg.set("target_mcu", target)
            self._cfg.set("interface", iface)
            self._cfg.set("speed_khz", speed)
            self._cfg.set("rtt_channel", channel)
            self._worker.connect_requested.emit(target, iface, speed, channel)
        else:
            self._worker.disconnect_requested.emit()

    def _on_channel_changed(self, ch: int) -> None:
        self._cfg.set("rtt_channel", ch)
        self._worker.set_rtt_channel_requested.emit(ch)

    def _on_send_clicked(self) -> None:
        text = self.le_send.text()
        if not text:
            return
        self._worker.send_data_requested.emit(text, self.chk_hex.isChecked())
        # 加入历史
        hist = list(self._cfg.get("send_history"))
        if text in hist:
            hist.remove(text)
        hist.append(text)
        self._cfg.set("send_history", hist)

    def _on_state_changed(self, connected: bool, _info: dict) -> None:
        self.btn_connect.setText("断开" if connected else "连接")
        self.btn_reset.setEnabled(connected)
        self.btn_send.setEnabled(connected)
        self.chk_power.setEnabled(connected)

    def _on_rtt_data(self, text: str) -> None:
        # 自动滚动判断必须在插入文本前
        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.End)
        for seg, attrs in parse_ansi(text):
            cursor.insertText(seg, self._fmt(attrs))

        if at_bottom and self.chk_auto_scroll.isChecked():
            sb.setValue(sb.maximum())

    def _fmt(self, attrs: AnsiAttrs) -> QTextCharFormat:
        fmt = QTextCharFormat()
        if attrs.fg:
            fmt.setForeground(QColor(_ANSI_COLOR_MAP.get(attrs.fg, "#dddddd")))
        if attrs.bg:
            fmt.setBackground(QColor(_ANSI_COLOR_MAP.get(attrs.bg, "#222222")))
        if attrs.bold:
            f = fmt.font()
            f.setBold(True)
            fmt.setFont(f)
        return fmt

    def _apply_font(self, family: str, size: int) -> None:
        font = QFont(family, size)
        if font.family() != family:
            # 字体回落到等宽字体
            font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            font.setPointSize(size)
        self.display.setFont(font)
```

- [ ] **Step 11.2: 启动验证**

Run:
```bat
python src\main.py
```

Expected:
- RTT 监控页显示完整控制栏 / 选项栏 / 显示区 / 发送栏
- 点击「连接」（无 J-Link 也行）：按钮文案不变（连接失败），InfoBar 无误报
- 改 RTT 通道，能在日志看到 `RTT 通道切换为 N`
- 切换其他页面再回来，控件状态保留

- [ ] **Step 11.3: Commit**

```bat
git add src/ui/rtt_monitor_page.py
git commit -m "feat(ui): 实现 RTT 监控页主体（连接/接收/发送/自动滚动）"
```

---

## Task 12：RTT 监控页 — 设备信息卡片 + 搜索栏 + 日志记录 + 保存当前

**Files:**
- Modify: `src\ui\rtt_monitor_page.py`

- [ ] **Step 12.1: 在 `_build_ui` 中追加设备信息卡片（控制栏与选项栏之间）**

在 `root.addLayout(opt)` 之后、`self.display = QPlainTextEdit(...)` 之前插入：

```python
        # ---- 设备信息折叠组（用 QGroupBox.setCheckable 实现简易折叠）----
        from PySide6.QtWidgets import QGroupBox, QGridLayout
        self.gb_info = QGroupBox("设备信息", self)
        self.gb_info.setCheckable(True)
        self.gb_info.setChecked(False)
        info_grid = QGridLayout(self.gb_info)
        self._info_labels: dict[str, QLabel] = {}
        rows = [
            ("固件版本", "jlink_firmware"),
            ("硬件版本", "jlink_hardware"),
            ("序列号", "jlink_serial"),
            ("核心名称", "core_name"),
            ("核心 ID", "core_id"),
            ("CPU 类型", "core_cpu"),
            ("目标设备", "target_device"),
            ("接口", "interface"),
            ("速度 (kHz)", "speed_khz"),
        ]
        for i, (text, key) in enumerate(rows):
            r, c = divmod(i, 3)
            info_grid.addWidget(QLabel(f"{text}:"), r, c * 2)
            lbl = QLabel("-")
            self._info_labels[key] = lbl
            info_grid.addWidget(lbl, r, c * 2 + 1)
        root.addWidget(self.gb_info)
```

- [ ] **Step 12.2: 在 `_build_ui` 显示区之后、发送栏之前插入搜索栏**

```python
        # ---- 搜索栏 ----
        from qfluentwidgets import SearchLineEdit
        srch = QHBoxLayout()
        self.le_search = SearchLineEdit(self)
        self.le_search.setPlaceholderText("搜索日志…")
        self.btn_prev = PushButton("↑", self)
        self.btn_next = PushButton("↓", self)
        self.lbl_match = QLabel("0/0")
        srch.addWidget(self.le_search, 1)
        srch.addWidget(self.btn_prev)
        srch.addWidget(self.btn_next)
        srch.addWidget(self.lbl_match)
        root.addLayout(srch)
```

- [ ] **Step 12.3: 在 `_wire_signals` 追加：**

```python
        # 日志记录
        self.chk_log_rec.toggled.connect(self._on_log_recording_toggled)
        # 保存当前
        self.btn_save.clicked.connect(self._on_save_clicked)
        # 搜索
        from PySide6.QtGui import QTextDocument
        self._search_doc = QTextDocument  # 引用一下
        self.btn_prev.clicked.connect(lambda: self._do_search(backward=True))
        self.btn_next.clicked.connect(lambda: self._do_search(backward=False))
        self.le_search.returnPressed.connect(lambda: self._do_search(backward=False))
        self.le_search.textChanged.connect(self._update_match_count)

        # 命令结果（错误提示）
        self._worker.command_result.connect(self._on_command_result)
        self._worker.log_message.connect(self._on_log_message)
```

- [ ] **Step 12.4: 在 `_on_state_changed` 中刷新设备信息卡片**

把 `_on_state_changed` 整体替换为：
```python
    def _on_state_changed(self, connected: bool, info: dict) -> None:
        self.btn_connect.setText("断开" if connected else "连接")
        self.btn_reset.setEnabled(connected)
        self.btn_send.setEnabled(connected)
        self.chk_power.setEnabled(connected)
        if connected:
            for key, lbl in self._info_labels.items():
                lbl.setText(str(info.get(key, "-")))
            self.gb_info.setChecked(True)
        else:
            for lbl in self._info_labels.values():
                lbl.setText("-")
```

- [ ] **Step 12.5: 新增方法**

在类末尾追加：
```python
    # ------------------------------------------------------------------
    # 日志记录 / 保存当前 / 搜索 / 错误提示
    # ------------------------------------------------------------------
    def _on_log_recording_toggled(self, checked: bool) -> None:
        if checked:
            from core.logger import get_log_dir
            log_dir = self._cfg.get("log_dir") or str(get_log_dir())
            self._worker.start_log_recording_requested.emit(log_dir)
        else:
            self._worker.stop_log_recording_requested.emit()

    def _on_save_clicked(self) -> None:
        from datetime import datetime
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        default_name = f"rtt_snapshot_{datetime.now():%Y%m%d_%H%M%S}.log"
        path, _ = QFileDialog.getSaveFileName(self, "保存当前显示", default_name, "Log files (*.log);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(self.display.toPlainText(), encoding="utf-8")
            InfoBar.success("已保存", path, parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
        except Exception as e:
            InfoBar.error("保存失败", str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)

    def _do_search(self, backward: bool) -> None:
        text = self.le_search.text()
        if not text:
            return
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindBackward
        if not self.display.find(text, flags):
            # 回卷
            cursor = self.display.textCursor()
            cursor.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.display.setTextCursor(cursor)
            self.display.find(text, flags)

    def _update_match_count(self, text: str) -> None:
        if not text:
            self.lbl_match.setText("0/0")
            return
        # 简单计数
        cnt = self.display.toPlainText().count(text)
        self.lbl_match.setText(f"-/{cnt}")

    def _on_command_result(self, cmd: str, ok: bool, payload: dict) -> None:
        if ok:
            return
        err = payload.get("error", "未知错误")
        InfoBar.warning(cmd, err, parent=self,
                        position=InfoBarPosition.TOP, duration=3000)

    def _on_log_message(self, level: str, msg: str) -> None:
        if level == "error":
            InfoBar.error("错误", msg, parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
```

- [ ] **Step 12.6: 启动验证**

Run:
```bat
python src\main.py
```

Expected:
- 设备信息折叠卡可展开收起
- 搜索框输入文字，能在显示区高亮（先填几行测试文字到显示区也行：编辑 `self.display.setPlainText("test\nhello\nworld")` 临时验证后回退）
- 「保存当前」弹文件选择对话框

- [ ] **Step 12.7: Commit**

```bat
git add src/ui/rtt_monitor_page.py
git commit -m "feat(ui): RTT 监控页补全（设备信息/搜索/日志记录/保存当前）"
```

---

## Task 13：内存查看页

**Files:**
- Modify: `src\ui\memory_viewer_page.py`

- [ ] **Step 13.1: 完整替换 `src\ui\memory_viewer_page.py`**

```python
"""内存查看页：地址 hex dump + 固件分块导出。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
)

from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker
from core.memory_service import format_hex_dump


_SIZE_PRESETS = [
    ("128 KB", 128 * 1024),
    ("256 KB", 256 * 1024),
    ("512 KB", 512 * 1024),
    ("1 MB", 1024 * 1024),
    ("2 MB", 2 * 1024 * 1024),
    ("自定义", -1),
]


def _parse_int(text: str) -> int:
    text = text.strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text)


class MemoryViewerPage(QWidget):
    def __init__(self, worker: JLinkWorker, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("memory-viewer")
        self._worker = worker
        self._cfg = cfg
        self._connected = False

        self._build_ui()
        self._wire_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- 读取区 ----
        read_card = CardWidget(self)
        read_lay = QHBoxLayout(read_card)
        read_lay.addWidget(BodyLabel("起始地址"))
        self.le_read_addr = LineEdit(self)
        self.le_read_addr.setText("0x08000000")
        self.le_read_addr.setMaximumWidth(140)
        read_lay.addWidget(self.le_read_addr)
        read_lay.addWidget(BodyLabel("大小 (字节)"))
        self.le_read_size = LineEdit(self)
        self.le_read_size.setText("0x100")
        self.le_read_size.setMaximumWidth(100)
        read_lay.addWidget(self.le_read_size)
        self.btn_read = PrimaryPushButton("读取", self)
        self.btn_clear = PushButton("清空", self)
        read_lay.addWidget(self.btn_read)
        read_lay.addWidget(self.btn_clear)
        read_lay.addStretch(1)
        root.addWidget(read_card)

        # ---- Hex 显示 ----
        self.display = QPlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Consolas", 12)
        self.display.setFont(font)
        root.addWidget(self.display, 1)

        # ---- 导出固件 ----
        export_card = CardWidget(self)
        ex_root = QVBoxLayout(export_card)
        ex_root.addWidget(BodyLabel("导出固件"))

        ex_row = QHBoxLayout()
        ex_row.addWidget(QLabel("起始地址"))
        self.le_ex_addr = LineEdit(self)
        self.le_ex_addr.setText("0x08000000")
        self.le_ex_addr.setMaximumWidth(140)
        ex_row.addWidget(self.le_ex_addr)
        ex_row.addWidget(QLabel("大小"))
        self.cb_ex_preset = ComboBox(self)
        for label, _ in _SIZE_PRESETS:
            self.cb_ex_preset.addItem(label)
        ex_row.addWidget(self.cb_ex_preset)
        self.le_ex_custom = LineEdit(self)
        self.le_ex_custom.setPlaceholderText("0x100000")
        self.le_ex_custom.setMaximumWidth(120)
        self.le_ex_custom.setEnabled(False)
        ex_row.addWidget(self.le_ex_custom)

        self.btn_choose = PushButton("选择保存路径", self)
        ex_row.addWidget(self.btn_choose)
        ex_row.addStretch(1)
        ex_root.addLayout(ex_row)

        self.lbl_path = QLabel("（未选择保存路径）", self)
        ex_root.addWidget(self.lbl_path)

        bottom = QHBoxLayout()
        self.btn_export = PrimaryPushButton("开始导出", self)
        self.btn_export.setEnabled(False)
        self.pb_export = QProgressBar(self)
        self.pb_export.setRange(0, 100)
        self.pb_export.setValue(0)
        bottom.addWidget(self.btn_export)
        bottom.addWidget(self.pb_export, 1)
        ex_root.addLayout(bottom)
        root.addWidget(export_card)

        self._save_path: str = ""
        self._set_enabled_by_connection(False)

    def _wire_signals(self) -> None:
        self.btn_read.clicked.connect(self._on_read_clicked)
        self.btn_clear.clicked.connect(self.display.clear)
        self.cb_ex_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.btn_choose.clicked.connect(self._on_choose_path)
        self.btn_export.clicked.connect(self._on_export_clicked)

        self._worker.connection_state_changed.connect(
            lambda c, _info: self._set_enabled_by_connection(c)
        )
        self._worker.memory_read_finished.connect(self._on_memory_read)
        self._worker.firmware_export_progress.connect(self._on_export_progress)
        self._worker.firmware_export_finished.connect(self._on_export_finished)
        self._worker.command_result.connect(self._on_command_result)

    def _set_enabled_by_connection(self, connected: bool) -> None:
        self._connected = connected
        self.btn_read.setEnabled(connected)
        self.btn_export.setEnabled(connected and bool(self._save_path))
        if not connected:
            InfoBar.warning(
                "未连接 J-Link",
                "请先到 RTT 监控页连接 J-Link，再进行内存读取或导出",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000,
            )

    def _on_read_clicked(self) -> None:
        try:
            addr = _parse_int(self.le_read_addr.text())
            size = _parse_int(self.le_read_size.text())
        except ValueError as e:
            InfoBar.warning("地址/大小格式错误", str(e), parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        if size <= 0 or size > 16 * 1024 * 1024:
            InfoBar.warning("大小越界", "1B - 16MB", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        self._worker.read_memory_requested.emit(addr, size)

    def _on_memory_read(self, addr: int, raw: bytes) -> None:
        self.display.setPlainText(format_hex_dump(raw, addr))

    def _on_preset_changed(self, idx: int) -> None:
        _, size = _SIZE_PRESETS[idx]
        self.le_ex_custom.setEnabled(size < 0)

    def _on_choose_path(self) -> None:
        from datetime import datetime
        default = f"firmware_{datetime.now():%Y%m%d_%H%M%S}.bin"
        path, _ = QFileDialog.getSaveFileName(self, "选择导出路径", default, "Binary (*.bin);;All (*)")
        if path:
            self._save_path = path
            self.lbl_path.setText(path)
            self.btn_export.setEnabled(self._connected)

    def _on_export_clicked(self) -> None:
        try:
            start = _parse_int(self.le_ex_addr.text())
        except ValueError as e:
            InfoBar.warning("地址格式错误", str(e), parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        idx = self.cb_ex_preset.currentIndex()
        _, preset_size = _SIZE_PRESETS[idx]
        if preset_size < 0:
            try:
                size = _parse_int(self.le_ex_custom.text())
            except ValueError as e:
                InfoBar.warning("大小格式错误", str(e), parent=self,
                                position=InfoBarPosition.TOP, duration=2000)
                return
        else:
            size = preset_size

        InfoBar.warning(
            "RTT 接收将暂停",
            f"导出 {size // 1024} KB 期间无法接收 RTT 数据",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )
        self.pb_export.setValue(0)
        self.btn_export.setEnabled(False)
        self._worker.export_firmware_requested.emit(self._save_path, start, size)

    def _on_export_progress(self, current: int, total: int) -> None:
        pct = int(current * 100 / total)
        self.pb_export.setValue(pct)

    def _on_export_finished(self, ok: bool, path: str, err: str) -> None:
        self.btn_export.setEnabled(self._connected)
        if ok:
            InfoBar.success("导出完成", path, parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
        else:
            InfoBar.error("导出失败", err, parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _on_command_result(self, cmd: str, ok: bool, payload: dict) -> None:
        if cmd == "read_memory" and not ok:
            InfoBar.error("读取失败", payload.get("error", ""), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
```

- [ ] **Step 13.2: 启动验证**

Run:
```bat
python src\main.py
```

Expected:
- 切到「内存查看」页，看到读取区 + Hex 显示区（空）+ 导出卡片
- 未连接时，所有按钮置灰，顶部 InfoBar 提示
- 切换大小预设到「自定义」，自定义 LineEdit 启用
- 点「选择保存路径」弹出 QFileDialog

- [ ] **Step 13.3: Commit**

```bat
git add src/ui/memory_viewer_page.py
git commit -m "feat(ui): 实现内存查看页（读取/Hex 显示/固件导出）"
```

---

## Task 14：设置页

**Files:**
- Modify: `src\ui\settings_page.py`

- [ ] **Step 14.1: 完整替换 `src\ui\settings_page.py`**

```python
"""设置页：外观（主题/字体）+ RTT 行为（最大行数/Rx Timeout/日志目录）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    PushButton,
    SpinBox,
    SubtitleLabel,
    Theme,
    setTheme,
    setThemeColor,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFontDialog

from core.config_service import ConfigService
from core.logger import get_log_dir


class _SettingRow(QWidget):
    """通用：左标题 + 右控件 的一行。"""

    def __init__(self, title: str, widget: QWidget, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.addWidget(BodyLabel(title), 1)
        lay.addWidget(widget)


class SettingsPage(QWidget):
    def __init__(self, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("settings")
        self._cfg = cfg
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # ---- 外观 ----
        appearance = CardWidget(self)
        app_lay = QVBoxLayout(appearance)
        app_lay.addWidget(SubtitleLabel("外观"))

        # 主题模式
        self.cb_theme = ComboBox(self)
        self.cb_theme.addItems(["跟随系统", "浅色", "深色"])
        theme_str = self._cfg.get("theme")
        self.cb_theme.setCurrentIndex({"auto": 0, "light": 1, "dark": 2}.get(theme_str, 0))
        self.cb_theme.currentIndexChanged.connect(self._on_theme_changed)
        app_lay.addWidget(_SettingRow("主题模式", self.cb_theme))

        # 主题色
        color_row = QHBoxLayout()
        color_row.addWidget(BodyLabel("主题色"), 1)
        self.lbl_color = QLabel(self._cfg.get("theme_color"))
        self.lbl_color.setStyleSheet(
            f"background: {self._cfg.get('theme_color')}; padding: 2px 8px; color: white; border-radius: 4px;"
        )
        color_row.addWidget(self.lbl_color)
        self.btn_color = PushButton("选择…", self)
        self.btn_color.clicked.connect(self._on_pick_color)
        color_row.addWidget(self.btn_color)
        wrap = QWidget(self)
        wrap.setLayout(color_row)
        app_lay.addWidget(wrap)

        # 显示字体
        font_row = QHBoxLayout()
        font_row.addWidget(BodyLabel("显示字体"), 1)
        self.lbl_font = QLabel(f"{self._cfg.get('font_family')} {self._cfg.get('font_size')}pt")
        font_row.addWidget(self.lbl_font)
        self.btn_font = PushButton("选择…", self)
        self.btn_font.clicked.connect(self._on_pick_font)
        font_row.addWidget(self.btn_font)
        wrap2 = QWidget(self)
        wrap2.setLayout(font_row)
        app_lay.addWidget(wrap2)

        # 字体大小（仍提供 SpinBox 快速调整）
        self.sp_font_size = SpinBox(self)
        self.sp_font_size.setRange(8, 32)
        self.sp_font_size.setValue(self._cfg.get("font_size"))
        self.sp_font_size.valueChanged.connect(self._on_font_size_changed)
        app_lay.addWidget(_SettingRow("字体大小", self.sp_font_size))

        root.addWidget(appearance)

        # ---- RTT 行为 ----
        rtt_card = CardWidget(self)
        rtt_lay = QVBoxLayout(rtt_card)
        rtt_lay.addWidget(SubtitleLabel("RTT 行为"))

        self.sp_max_lines = SpinBox(self)
        self.sp_max_lines.setRange(1000, 100000)
        self.sp_max_lines.setSingleStep(1000)
        self.sp_max_lines.setValue(self._cfg.get("max_display_lines"))
        self.sp_max_lines.valueChanged.connect(lambda v: self._cfg.set("max_display_lines", v))
        rtt_lay.addWidget(_SettingRow("显示区最大行数", self.sp_max_lines))

        self.sp_rx_to = SpinBox(self)
        self.sp_rx_to.setRange(0, 5000)
        self.sp_rx_to.setSuffix(" ms")
        self.sp_rx_to.setValue(self._cfg.get("rx_timeout_ms"))
        self.sp_rx_to.valueChanged.connect(lambda v: self._cfg.set("rx_timeout_ms", v))
        rtt_lay.addWidget(_SettingRow("Rx Timeout", self.sp_rx_to))

        log_row = QHBoxLayout()
        log_row.addWidget(BodyLabel("日志保存目录"), 1)
        self.lbl_log_dir = QLabel(self._cfg.get("log_dir") or str(get_log_dir()))
        log_row.addWidget(self.lbl_log_dir)
        self.btn_log_dir = PushButton("选择…", self)
        self.btn_log_dir.clicked.connect(self._on_pick_log_dir)
        log_row.addWidget(self.btn_log_dir)
        self.btn_open_log = PushButton("打开日志目录", self)
        self.btn_open_log.clicked.connect(self._on_open_log_dir)
        log_row.addWidget(self.btn_open_log)
        wrap3 = QWidget(self)
        wrap3.setLayout(log_row)
        rtt_lay.addWidget(wrap3)

        root.addWidget(rtt_card)
        root.addStretch(1)

    def _on_theme_changed(self, idx: int) -> None:
        mapping = ["auto", "light", "dark"]
        theme_str = mapping[idx]
        self._cfg.set("theme", theme_str)
        if theme_str == "dark":
            setTheme(Theme.DARK)
        elif theme_str == "light":
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.AUTO)

    def _on_pick_color(self) -> None:
        from qfluentwidgets import ColorDialog
        cur = QColor(self._cfg.get("theme_color"))
        dlg = ColorDialog(cur, "选择主题色", self, enableAlpha=False)
        dlg.colorChanged.connect(self._apply_color)
        dlg.exec()

    def _apply_color(self, color: QColor) -> None:
        hex_str = color.name()
        self._cfg.set("theme_color", hex_str)
        setThemeColor(hex_str)
        self.lbl_color.setText(hex_str)
        self.lbl_color.setStyleSheet(
            f"background: {hex_str}; padding: 2px 8px; color: white; border-radius: 4px;"
        )

    def _on_pick_font(self) -> None:
        cur = QFont(self._cfg.get("font_family"), self._cfg.get("font_size"))
        ok, font = QFontDialog.getFont(cur, self, "选择字体")
        if not ok:
            return
        self._cfg.set("font_family", font.family())
        self._cfg.set("font_size", font.pointSize())
        self.sp_font_size.setValue(font.pointSize())
        self.lbl_font.setText(f"{font.family()} {font.pointSize()}pt")

    def _on_font_size_changed(self, v: int) -> None:
        self._cfg.set("font_size", v)
        self.lbl_font.setText(f"{self._cfg.get('font_family')} {v}pt")

    def _on_pick_log_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择日志目录", self.lbl_log_dir.text())
        if path:
            self._cfg.set("log_dir", path)
            self.lbl_log_dir.setText(path)

    def _on_open_log_dir(self) -> None:
        path = self._cfg.get("log_dir") or str(get_log_dir())
        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            InfoBar.error("打开失败", str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
```

- [ ] **Step 14.2: 启动验证**

Run:
```bat
python src\main.py
```

Expected:
- 切到「设置」页，看到外观/RTT 行为两张卡
- 切换主题模式（深色/浅色/跟随系统）立即生效
- 点击「选择主题色」弹出颜色对话框，选色后主窗口主题色立即变化
- 「打开日志目录」打开 explorer 到 `%APPDATA%\JLinkRTTViewer\logs`

- [ ] **Step 14.3: Commit**

```bat
git add src/ui/settings_page.py
git commit -m "feat(ui): 实现设置页（主题/字体/RTT 行为）"
```

---

## Task 15：关于页

**Files:**
- Modify: `src\ui\about_page.py`

- [ ] **Step 15.1: 完整替换 `src\ui\about_page.py`**

```python
"""关于页：应用信息 + 功能介绍 + 第三方致谢。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    HyperlinkButton,
    SubtitleLabel,
    TitleLabel,
)

APP_VERSION = "0.1.0"
AUTHOR_NAME = "待定"
AUTHOR_GITHUB = "https://github.com/"


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(20)

        # 标题
        title = TitleLabel("J-Link RTT Viewer", self)
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        ver = BodyLabel(f"版本 {APP_VERSION}", self)
        ver.setAlignment(Qt.AlignCenter)
        root.addWidget(ver)

        # 功能介绍
        feat_row = QHBoxLayout()
        feat_row.addWidget(self._feature_card(
            "📊 RTT 监控",
            "实时显示 MCU 通过 SEGGER RTT 输出的日志，支持 UTF-8 中文与 ANSI 颜色，"
            "16 个通道任意切换，可向 MCU 发送文本/十六进制数据。"
        ), 1)
        feat_row.addWidget(self._feature_card(
            "🔍 内存查看",
            "读取目标设备任意地址内存并以 Hex Dump 形式展示，"
            "支持按区间将固件分块导出为 .bin 文件。"
        ), 1)
        root.addLayout(feat_row)

        # 作者
        author = CardWidget(self)
        a_lay = QVBoxLayout(author)
        a_lay.addWidget(SubtitleLabel("作者", self))
        a_lay.addWidget(BodyLabel(AUTHOR_NAME, self))
        a_lay.addWidget(HyperlinkButton(AUTHOR_GITHUB, f"GitHub: {AUTHOR_NAME}"))
        root.addWidget(author)

        # 致谢
        ack = CardWidget(self)
        ack_lay = QVBoxLayout(ack)
        ack_lay.addWidget(SubtitleLabel("第三方依赖致谢", self))
        ack_lay.addWidget(BodyLabel("• pylink-square — SEGGER J-Link Python 封装", self))
        ack_lay.addWidget(BodyLabel("• PySide6 / Qt — Qt for Python", self))
        ack_lay.addWidget(BodyLabel("• PyQt-Fluent-Widgets — Fluent 设计组件库", self))
        root.addWidget(ack)

        root.addStretch(1)

    def _feature_card(self, title: str, desc: str) -> CardWidget:
        card = CardWidget(self)
        lay = QVBoxLayout(card)
        lay.addWidget(SubtitleLabel(title, self))
        body = BodyLabel(desc, self)
        body.setWordWrap(True)
        lay.addWidget(body)
        return card
```

- [ ] **Step 15.2: 启动验证**

Run:
```bat
python src\main.py
```

Expected:
- 切到「关于」页，显示标题、版本、两张功能卡（并排）、作者卡、致谢卡

- [ ] **Step 15.3: Commit**

```bat
git add src/ui/about_page.py
git commit -m "feat(ui): 实现关于页"
```

---

## Task 16：CLAUDE.md + start.bat + build_nuitka.bat

**Files:**
- Create: `CLAUDE.md`
- Create: `start.bat`
- Create: `build_nuitka.bat`

- [ ] **Step 16.1: 创建 `start.bat`**

```bat
@echo off
chcp 65001 >nul
call venv\Scripts\activate.bat
python src\main.py
```

- [ ] **Step 16.2: 创建 `build_nuitka.bat`**

```bat
@echo off
chcp 65001 >nul
call venv\Scripts\activate.bat
python -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --include-package=qfluentwidgets ^
    --include-package-data=qfluentwidgets ^
    --include-package=pylink ^
    --include-package-data=pylink ^
    --include-data-files=src\config.json=config.json ^
    --include-data-dir=img=img ^
    --output-dir=build ^
    --output-filename=JLinkRTTViewer.exe ^
    src\main.py

echo.
echo Build complete. Output: build\main.dist\JLinkRTTViewer.exe
```

> 注：`pylink` 包数据里包含 `JLinkARM.dll`（pylink-square 自带），无需另置。如果用户机器没装 SEGGER 驱动，运行时会被 main.py 的致命检测捕获并弹框。

- [ ] **Step 16.3: 创建 `CLAUDE.md`（中文，"现象/原因/处理"三段式）**

```markdown
# 项目经验笔记

为后续维护积累的实际踩坑经验。每条都带 **现象 / 原因 / 处理** 三段。

---

## pylink `close()` 在未连接时抛 `JLinkException`

**现象**：调用 `jlink.close()` 抛 `pylink.errors.JLinkException: There is no connected JLink.`

**原因**：pylink 把"没 open 过"和"open 过但已 close"都当成"无连接"，再次 close() 直接抛异常。原 PyWebView 项目里没有守卫，断开成功后稍微再点一次断开就报错。

**处理**：close 前必须 `if jlink.opened():`；同理 rtt_stop 前必须 `if jlink.connected():`。守卫语句应该把整个 close/rtt_stop 块各自包一层 try/except，except 内 `log_message.emit('warning', ...)`，不要让单一次清理失败阻断退出路径。

参考：`src/core/jlink_worker.py` `_do_disconnect`

---

## 不要做 "open → 取 serial → close → 再 open" 双开

**现象**：原项目 `jlink_service.connect()` 里有这样的代码：
```python
self.jlink.open()
ser_num = self.jlink.serial_number
self.jlink.close()
self.jlink.open(str(ser_num))
```

**原因**：本意可能是想"显式按序列号打开"，但 pylink 1.6.0 的 `open()` 不传 serial_no 时本来就会选第一个可用 J-Link。多余的 close 引入了线程时序窗口（read thread 可能还在用 jlink 句柄），是后续 close 死锁的根因之一。

**处理**：直接一次 `if not jlink.opened(): jlink.open()` 即可，不再传 serial_no 也不再双开。如果未来要支持多 J-Link 选择，加一个"选择序列号"对话框，把选中的 serial 传给 `open(serial_no=...)`，而不是先 open 再 close 再 open。

---

## `set_tif(SWD | JTAG)` 是错的

**现象**：把 `pylink.enums.JLinkInterfaces.SWD | pylink.enums.JLinkInterfaces.JTAG` 传 set_tif，pylink 报"Invalid interface"。

**原因**：SWD 和 JTAG 是互斥枚举值，不是 bit flag。原项目代码是按 if/else 二选一调用的，这里只是提醒——重构时不要"图省事"把两个值 OR 起来。

**处理**：按用户选择二选一调用，参考：
```python
tif = JLinkInterfaces.SWD if iface == "SWD" else JLinkInterfaces.JTAG
self.jlink.set_tif(tif)
```

---

## QThread 子类陷阱：`__init__` 跑在主线程

**现象**：在 `JLinkWorker.__init__` 里 `self._timer = QTimer(self)` 之后，无论怎么 connect 都不触发 timeout。

**原因**：QThread 子类的 `__init__` 是在创建者线程（主线程）执行的，`run()` 才是新线程。如果 QTimer / pylink.JLink / IncrementalDecoder 在 `__init__` 里创建，它们的 thread affinity 仍归主线程，timer 事件会被发到主线程的事件循环；同时 cross-thread queued connection 也会错乱。

**处理**：所有依赖事件循环 / 跨线程访问的对象 **必须在 `run()` 内创建**。`__init__` 只保存配置 + 创建 Signal 对象（Signal 本身没有 thread affinity）。

参考：`src/core/jlink_worker.py` `run()`

---

## worker 退出必须 worker 自己 `quit()`

**现象**：原项目用 `os._exit(0)` 兜底窗口关闭，否则进程会卡 2 秒以上。

**原因**：如果主线程直接调用 `worker.quit()`，Qt 只会把 quit 事件放到 worker 事件循环里——而此刻 worker 可能正阻塞在 `rtt_read()` 或 `close()` 的 C 扩展调用中，没机会处理事件。结果是 `worker.wait()` 超时，要么死等要么强 terminate（不安全）。

**处理**：定义 `stop_requested` 信号，槽里**worker 自己**清理 pylink → `self.quit()`。主线程只 emit 信号 + wait。这样 quit 不会和阻塞的 C 调用赛跑——清理完才 quit。

参考：`src/core/jlink_worker.py` `_on_stop`、`src/ui/main_window.py` `closeEvent`

---

## `IncrementalDecoder` 自管半字节缓冲

**现象**：原项目里：
```python
self.byte_buffer.extend(data_bytes)
decoded = self.decoder.decode(bytes(self.byte_buffer))
if decoded:
    self.byte_buffer = bytearray(self.decoder.getstate()[1])
```
看似在合理地保留未处理字节。

**原因**：`IncrementalDecoder.getstate()` 返回 `(buffer_state, additional_state)` 其中 `additional_state` 是 **整数**（标记位），不是剩余字节。`bytearray(int)` 会创建一个该长度的零字节数组——等于每次清空 buffer 但加几个零字节进去。这是抄来的 bug，但因为 UTF-8 跨行半字节场景很少触发，长期没被发现。

**处理**：直接 `decoded = decoder.decode(bytes(data))` 即可，半字节缓冲在 decoder 内部维护。不要在外层叠一层 byte_buffer。每次重连前 `_reset_utf8_decoder()` 重建 decoder，避免上次掉线残留污染。

参考：`src/core/jlink_worker.py` `_poll_rtt` / `_reset_utf8_decoder`

---

## `QTextEdit` 没有 `setMaximumBlockCount`

**现象**：把 `QTextEdit.setMaximumBlockCount(10000)` 写到代码里，运行时报 `AttributeError`。

**原因**：`setMaximumBlockCount` 是 `QPlainTextEdit` 独有的 API，`QTextEdit` 没有。RTT 显示区如果用 QTextEdit + 富文本，长时间运行会无上限增长，最后 GUI 卡死。

**处理**：RTT 显示区一律用 `QPlainTextEdit`；ANSI 着色通过 `QTextCharFormat` + `QTextCursor.insertText(seg, fmt)` 实现——`QPlainTextEdit` 也支持富文本字符格式，只是不支持完整 HTML。

---

## 自动滚动判断时机

**现象**：插入文本后判断 `at_bottom = sb.value() >= sb.maximum()`，结果几乎永远 True，自动滚动总在生效，用户即使滚到中间也会被拉回底部。

**原因**：插入文本会立即更新 `verticalScrollBar()` 的 maximum；插入后再判断时，光标可能因为 insert 自动跟到了新的最大值附近。判断需要在插入前。

**处理**：
```python
sb = self.display.verticalScrollBar()
at_bottom = sb.value() >= sb.maximum() - 4   # 留 4 像素余量
# ... 插入文本 ...
if at_bottom and self.chk_auto_scroll.isChecked():
    sb.setValue(sb.maximum())
```

参考：`src/ui/rtt_monitor_page.py` `_on_rtt_data`

---

## `user_prefs.json` 放 `%APPDATA%`

**现象**：把 user_prefs.json 放在 `src/` 目录下，打包成 Nuitka 后写入失败（`Program Files` 权限）。

**原因**：Windows 应用安装到 Program Files 后，应用目录默认只读。用户偏好必须放可写位置。

**处理**：`%APPDATA%/JLinkRTTViewer/user_prefs.json`；开发期不回落到 `src/`，避免与打包后行为不一致。

参考：`src/core/config_service.py` `_compute_user_prefs_path`

---

## `ConfigService.set()` 高频值要节流

**现象**：用户拖动窗口/调整字体大小 SpinBox 时，每帧都会 `cfg.set("window_geometry", ...)` 或 `cfg.set("font_size", N)`，每次都 atomic replace 写盘，明显卡顿。

**原因**：拖动 / SpinBox 每秒能触发几十次 setter；每次 set → 写 .tmp → fsync → os.replace 是 ms 级 syscall，叠加起来阻塞 UI 事件循环。

**处理**：`set()` 只标 dirty 并 `_flush_timer.start()`（默认 200 ms 单次 timer）。timer 触发时统一落盘。`closeEvent` 必须调 `cfg.flush()` 强制冲刷。

参考：`src/core/config_service.py` `set` / `flush` / `_do_flush`

---

## `closeEvent` 必须 `cfg.flush()`

**现象**：用户改了主题色然后立即关窗口；下次启动主题色没保存。

**原因**：节流策略下，最后一次 `set()` 后到 timer 触发前的 200 ms 内如果窗口关闭，落盘没赶上。

**处理**：`MainWindow.closeEvent` 第一行：保存窗口几何 → `self._cfg.flush()` 强制落盘 → 再启动 worker 清理。

参考：`src/ui/main_window.py` `closeEvent`

---

## Nuitka 打包 qfluentwidgets / pylink 资源

**现象**：Nuitka 打包后运行，qfluentwidgets qss 找不到、pylink 找不到 JLinkARM.dll。

**原因**：Nuitka 默认只打包 .py 源码，包内的 .qss / .dll / 图片资源不会跟着进。

**处理**：`build_nuitka.bat` 加：
```
--include-package=qfluentwidgets
--include-package-data=qfluentwidgets
--include-package=pylink
--include-package-data=pylink
```

参考：`build_nuitka.bat`

---
```

- [ ] **Step 16.4: 启动 start.bat 验证**

Run:
```bat
start.bat
```

Expected: 应用启动，等同 `python src/main.py`。

- [ ] **Step 16.5: Commit**

```bat
git add CLAUDE.md start.bat build_nuitka.bat
git commit -m "docs: 添加 CLAUDE.md 踩坑笔记与构建脚本"
```

---

## Task 17：测试全跑 + 单元测试覆盖回顾

**Files:**
- No file changes (验收任务)

- [ ] **Step 17.1: 全量测试**

Run:
```bat
pytest -v
```

Expected: 全部 PASS。如果有任何失败，回到对应 Task 修复。

- [ ] **Step 17.2: 简单覆盖统计**

```bat
pytest --co -q
```

Expected: 至少 30 个测试用例（logger 3 + config 7 + ansi 9 + memory 7 + jlink_worker 7+9 ≈ 42）。

- [ ] **Step 17.3: Commit（如有微调）**

如本步无代码改动，跳过。否则：
```bat
git add ...
git commit -m "test: 修复测试 ..."
```

---

## Task 18：真机回归（用户手动执行）

**Files:**
- No file changes (验收清单)

> 此任务**不修改任何代码**，只是按 spec §11 验收清单跑一遍真机。如发现问题，在新 commit 中修复并把现象/原因/处理追加到 CLAUDE.md。

- [ ] **Step 18.1: 走完以下清单**

| # | 验收项 | 预期 |
|---|---|---|
| 1 | `start.bat` 启动 | Fluent 窗口、四项导航无报错 |
| 2 | RTT 监控页选 STM32G070CB（或实际型号）+ SWD + 4000 kHz，点连接 | 设备信息卡片刷新，显示区无报错 |
| 3 | MCU 端 `SEGGER_RTT_printf` 中文+英文+ANSI 颜色 | 显示区正常显示三者，颜色生效 |
| 4 | 断开 → 再连接（重复 3 次） | 每次都成功，无死锁、无报错 |
| 5 | 文本/Hex 发送字符串到 MCU | MCU 端正确收到 |
| 6 | 切换 RTT 通道 0 → 5 → 0 | 切换日志正常，对应通道收到数据 |
| 7 | 实时日志记录 → 收到 100+ 行 → 停止 | 日志目录有 `rtt_*.log`，内容完整 |
| 8 | 「保存当前」对话框 → 保存 | 文件存在，与显示区一致 |
| 9 | 切到内存查看，读取 0x08000000 + 0x100 | hex dump 正确 |
| 10 | 导出 128 KB 固件 | 进度条流畅，.bin 大小正确 |
| 11 | 设置页改主题色（蓝→绿） | 立即生效，重启仍保留 |
| 12 | 设置页改主题（深色 ↔ 浅色） | 立即生效 |
| 13 | 改字体（Consolas → Cascadia Code） | RTT 显示区字体立即变化 |
| 14 | 拖动窗口大小 → 关闭 → 重启 | 窗口几何恢复 |
| 15 | 关闭窗口 | 任务管理器无残留 python.exe 进程 |
| 16 | `build_nuitka.bat` | 生成 `build/main.dist/JLinkRTTViewer.exe`，双击可运行 |

- [ ] **Step 18.2: 任何失败项的修复**

每条失败：
1. 修复代码
2. 把现象/原因/处理追加到 `CLAUDE.md`
3. `git commit -m "fix: <模块>: <一句话>"` 

- [ ] **Step 18.3: 最终 commit（如所有项 PASS）**

```bat
git log --oneline
echo 真机回归完成
```

---

## 自检

完成所有 Task 后回头扫一遍 spec：

- [x] §2.2 不双开 / 守卫断开 → Task 7（测试 `test_no_double_open` / `test_disconnect_skips_close_if_not_opened`）
- [x] §4.1 worker 信号清单 → Task 7-8 全部实现
- [x] §4.2 ANSI 解析 → Task 5
- [x] §4.3 内存服务 → Task 6, 8
- [x] §4.4 config 节流 → Task 4
- [x] §4.5 logger → Task 3
- [x] §4.6 main.py → Task 9
- [x] §5.1-5.5 各页面 → Task 10-15
- [x] §7 错误处理 → 各任务中 InfoBar/InfoBar.error 调用
- [x] §8 git 提交规范 → 每 Task 末尾约定式中文提交
- [x] §9 CLAUDE.md 12 条 → Task 16 全部预置
- [x] §10 构建脚本 → Task 16
- [x] §11 验收清单 → Task 18
- [x] §12 单元测试 → Tasks 3, 4, 5, 6, 7, 8（pytest）
