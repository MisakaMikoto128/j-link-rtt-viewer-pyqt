# 代码规范

本项目遵循 **PEP 8 / PEP 257 / PEP 484** + **Google Python Style Guide** 的 docstring 风格，并针对 PySide6 + qfluentwidgets 桌面应用补充约定。改 worker / 线程 / 配置相关代码前，先看 [CLAUDE.md](../CLAUDE.md) 的踩坑笔记。

## 1. 工具链

| 工具 | 配置 | 作用 |
|---|---|---|
| [ruff](https://docs.astral.sh/ruff/) | `pyproject.toml [tool.ruff]` | lint，规则集 `E,W,F,I,B,UP,SIM,RUF` |
| [black](https://black.readthedocs.io/) | `pyproject.toml [tool.black]` | 格式化，`line-length=100` |

```bash
# 检查
venv/Scripts/ruff.exe check src tests
# 自动修可修的 + 格式化
venv/Scripts/ruff.exe check src tests --fix
venv/Scripts/black.exe src tests
# 测试
venv/Scripts/python.exe -m pytest -q
```

**中文项目特例**：`RUF001/RUF002/RUF003`（ambiguous-unicode-character）对中文全角标点是纯噪声，已在 `ignore` 中排除。不要为了"消除警告"把中文标点改成 ASCII。

`E501`（行太长）由 black 统一处理，ruff 不重复报。`B008`（函数默认值实例化对象）是 PySide6 `Signal()` 定义必需，误报已排除。

## 2. 命名

| 对象 | 规则 | 示例 |
|---|---|---|
| 模块/文件 | `snake_case` | `jlink_worker.py` |
| 类 | `PascalCase` | `JLinkWorker`、`ConfigService` |
| 函数/方法/变量 | `snake_case` | `encode_send_payload`、`_do_connect` |
| 常量 | `UPPER_SNAKE` | `RESET_MODE_NORMAL`、`CHANNEL_ALL` |
| 私有 | `_` 前缀 | `_logger`、`_on_connect` |
| 信号 | 过去式 / 意图动词 | `connection_state_changed`、`connect_requested` |
| Slot 方法 | `_on_<信号名>` | `_on_connect`、`_on_reset` |
| 模式枚举字符串 | 模块级常量，不散落字面值 | `RESET_MODE_AUTO_RECONNECT = "auto_reconnect"` |

**禁止**：用按钮 `text()` 当状态判断（`if btn.text() == "连接"`）--维护真实 `_is_connected` 字段。详见 CLAUDE.md「UI 控件文本不是 state enum」。

## 3. 类型注解

- **强制** `from __future__ import annotations`（延迟求值，避免 `Optional`/`Union` 导入）
- 所有函数签名带返回类型，无返回值用 `-> None`
- 用 PEP 585/604 新语法：`dict[str, Any]`（非 `Dict`）、`list[int]`（非 `List`）、`X | None`（非 `Optional[X]`）

```python
def encode_send_payload(data: str, is_hex: bool) -> bytes:
    ...

def _on_connect(self, target: str, iface: str, speed: int, channel: int, serial: str) -> None:
    ...
```

## 4. docstring（PEP 257 + Google style）

- **模块级**：说明职责 + 设计要点 + 线程模型（参考 `src/core/jlink_worker.py`、`src/core/config_service.py` 顶部）
- **类级**：说明职责 + 使用约束（如"必须 moveToThread 后再用"）
- **函数级**：公开 API 必须有；私有 helper 可只写一行，但非平凡逻辑要说明 why
- 三引号 `"""`，首行简明概述，空行后展开

```python
class JLinkWorker(QObject):
    """J-Link 后台业务对象。**必须 moveToThread 到一个 QThread 后再用**。"""

    def _reset_and_halt(self) -> None:
        """halt 模式：reset 后 CPU 停在复位状态（halt=True），不运行、不断开。

        与「仅重置」不同--这里 reset 第二参 halt=True，CPU 复位后停在复位向量、
        不执行启动代码，可用于上电瞬间状态调试。
        """
```

**踩坑记录**不写进 docstring，写进 CLAUDE.md（带 现象/原因/处理 三段）。

## 5. PySide6 专项

### 线程模型
- **不继承 QThread**。worker 类继承 `QObject`，调用方创建 `QThread` + `worker.moveToThread(thread)` + `thread.started.connect(worker.initialize)`。详见 CLAUDE.md「QThread 必须独立于业务对象」。
- worker 在 `initialize()` 槽内创建 `QTimer` / `pylink.JLink` / `IncrementalDecoder`（此槽在 worker 线程触发，对象 thread affinity 正确）。
- worker 退出：`_on_stop` 槽内清理 pylink -> 显式 `stop()` + `deleteLater()` 所有 worker 线程内 QObject -> `self.thread().quit()`。主线程只 `thread.wait()`。

### 信号
- 跨线程信号**不传 dict / list / 自定义 PyObject**（PySide6 marshalling 会触发 setParent cross-thread 警告并卡线程）。只传 `bool/int/str/bytes`。复杂结构改用 `get_xxx()` 同步方法 + lock，让 UI 主动取。详见 CLAUDE.md「PySide6 跨线程 Signal 不要传 dict 参数」。
- `@Slot()` 装饰器标注槽方法，参数类型显式声明。
- 信号命名：输入用 `xxx_requested`（`connect_requested`），输出用 `xxx_changed`（`connection_state_changed`）或完成态（`target_infos_ready`）。

### 类属性常量
mutable 默认值用 `ClassVar` 或 tuple，不用裸 `dict`/`list`（RUF012）：

```python
from typing import Any, ClassVar

class ConfigService(QObject):
    DEFAULTS: ClassVar[dict[str, Any]] = {...}  # 共享配置默认值

class FirmwareAnalysisView(QWidget):
    _PIVOT_ITEMS = (  # 不可变常量映射用 tuple
        ("symbols", "符号 Symbols"),
        ...
    )
```

### native threading.Thread + Qt Signal
读循环等独立 pthread **不直接 emit Qt 信号**，只跟 `threading.Lock` + `list` 打交道；worker 线程内 `QTimer` 定时 drain 缓冲，从 worker 线程 context emit。详见 CLAUDE.md「native threading.Thread 不要直接 emit Qt signal」。

## 6. 注释原则

- **解释 why，不解释 what**。代码自解释 what，注释写约束/踩坑/非直觉选择。
- 踩坑进 CLAUDE.md（带 现象/原因/处理 + 参考代码位置），不散落代码注释。
- `TODO` 带上下文：`# TODO(名字): 发版前替换 AUTHOR_NAME`。
- 中文注释全角标点正常用（`。，：；`），不要为 ruff 改 ASCII。

## 7. 文件组织

- `from __future__ import annotations` 放第一行（模块 docstring 之后）。
- import 分组 + 组间空行：`future` -> `stdlib` -> `third party`（PySide6/qfluentwidgets/pylink）-> `local`（`from core...` / `from .`）。ruff `I001` 自动排序。
- 常量集中模块顶部（大写），类属性常量紧跟类声明。
- 单文件超 ~800 行考虑拆分。**拆分原则**：只拆「低耦合辅助组件 + 独立逻辑」（独立 QWidget/QObject + 信号通信，不持有主类状态），不拆「主类状态机」（`_is_connected` / `_channel_history` 等全局状态强耦合）。
- UI 页面辅助组件拆到 `widgets/`（如 `v_resize_handle` / `color_picker` / `remote_probe`），页面专用常量/逻辑拆到 `_<page>_colors.py` / `_<page>_search.py`，主页面状态机留主文件。参考 `rtt_monitor_page.py` 的拆分（3083 -> 2535，详见 CLAUDE.md「UI 模块拆分」）。
- 拆分主类状态机（如 `_CommandPanel` / `_DisplayArea`）属高风险，需先补集成测试覆盖 UI 交互，另开任务。

## 8. 测试

- 测试函数 `test_xxx`，fixture 小写（`worker`、`cfg`）。
- **UI 级测试不碰真实 DLL**：`conftest.py` 的 `stub_make_backend` autouse fixture 把 `core.flash_worker.make_backend` 替换成 `MagicMock()`。直接测 `_run_flash` 的单测用 `@pytest.mark.real_make_backend` 退出 stub，自行注入 mock pylink/pyOCD。
- 信号 spy 用 `QObject` + bound-method 槽，不用裸 lambda（跨线程信号 owner 在 worker 线程时裸 lambda 不触发）。详见 CLAUDE.md「PySide6 测试信号 spy」。
- 解包 fixture 未用的变量用 `_` 前缀：`w, _jl = worker`（RUF059）。

## 9. 异常处理

- `except ... as e:` 块内 `raise X(...)` 加 `from e`（B904），保留异常链。
- `try/except: pass` 改 `with contextlib.suppress(具体异常):`（SIM105），更清晰。
- `open()` 持有到函数结束/返回句柄给调用方的，加 `# noqa: SIM115` + 注释说明，不强行 `with`。
- 「open + 立刻 parse」类 helper 自带 catch + close，统一抛领域异常（如 `FileParseError`）。详见 CLAUDE.md「`_open_elf` 必须自己 catch ELFError」。

## 10. 配置 / 持久化

- `ConfigService.set()` 高频值已节流（200ms），调用方直接 `cfg.set` 即可。
- `closeEvent` 必须 `cfg.flush()` 强制落盘。
- 用户偏好放 `%APPDATA%/JLinkRTTViewer/`，不放应用目录（Program Files 只读）。

---

## 不在本规范覆盖的

- **大文件拆分**（`rtt_monitor_page.py` 3083 行等）：高风险，需先补集成测试，单独立项。
- **性能优化**：热路径 QColor 预构造、QPlainTextEdit 限行等已沉淀在 CLAUDE.md，本规范只管风格。
- **打包**（Nuitka）：见 `build_nuitka.bat` + CLAUDE.md 打包条目。
