# 主页面滚动 + 可拖动 display 高度 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RTT 监控页加 `QScrollArea` 外包 + 内嵌垂直 `QSplitter`，让用户能拖动改变 display 与底部控件（搜索 + 发送 + 状态栏）的高度比例，状态持久化到 user_prefs。

**Architecture:** ConfigService 已加 2 个 str DEFAULTS key（`rtt_splitter_state` / `memory_splitter_state`，前者用于本计划；后者保留备用）。新建 `src/ui/_splitter_persist.py` 工具模块抽出"接 splitterMoved + 从 cfg 恢复"两个函数。RTT 页的 `_build_ui` 把原 `QVBoxLayout(self)` 改为先建内容 widget，再用 `QScrollArea.setWidget(content)`；在内容布局内把 display 和底栏（search + send + status）放进 `QSplitter(Qt.Vertical)`。

**Tech Stack:** PySide6 (QScrollArea / QSplitter / QByteArray) + 现有 ConfigService 节流写盘机制。

**Spec:** `docs/superpowers/specs/2026-05-16-page-scroll-and-resizable-displays-design.md`

---

## 计划变更说明（2026-05-16 实施期间）

实施前用户独立 commit 了 `072af10`（4 个 UI 特性，含状态栏 + 写内存）和 `ac439e9`（内存页加 ScrollArea + 连接按钮换 PLAY/PAUSE 图标）。其中：

1. **内存页 ScrollArea 已经在 `ac439e9` 实施**——原 Task 4 (内存页 ScrollArea + 垂直 splitter) **取消**。横向 splitter 加了 `setMinimumHeight(320)`，配合 ScrollArea 整页可滚，效果等价于"hex 区高度可控 + 不被压扁"。用户确认不再加垂直 splitter（避免和新加的写内存卡片堆叠造成复杂度）。
2. **RTT 页新增了底部状态栏**（line 291+）显示连接状态/速率/累计/编码。Task 3 的 splitter 底栏现在装 search + send + **status**（不再只是 search + send）。
3. **`memory_splitter_state` config key 保留**——已经 commit 了，删除是 churn；空字符串默认值零成本，备用。

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| ~~`src/core/config_service.py`~~ | ✅ Task 1 done (`6febfa2`) | 2 DEFAULTS key 已加 |
| `src/ui/_splitter_persist.py` | Create | `wire(splitter, cfg, key)` + `restore(splitter, cfg, key, logger)` 两个函数 |
| `src/ui/rtt_monitor_page.py` | Modify | `_build_ui()` 重组：内容 widget 包进 QScrollArea，display + (search + send + status) 进垂直 QSplitter；`__init__` 末尾调用 `_splitter_persist` 的 wire/restore |
| ~~`src/ui/memory_viewer_page.py`~~ | ✅ Skipped per 用户决定 | ScrollArea 已在 `ac439e9` 加，不再加 vertical splitter |

---

## Task 1: ConfigService 加 splitter state keys ✅ DONE

Commit `6febfa2`. 加了 `rtt_splitter_state` / `memory_splitter_state` 到 DEFAULTS + 一个 unit test。68/68 测试通过。

---

## Task 2: 创建 splitter 持久化工具模块

**Files:**
- Create: `src/ui/_splitter_persist.py`

- [ ] **Step 1: 创建文件**

把下面内容完整写入 `src/ui/_splitter_persist.py`：

```python
"""QSplitter 状态持久化到 ConfigService。

为什么独立模块：RTT 页（未来可能内存页也用）需要"接 splitterMoved → cfg.set
base64 编码的 saveState"以及"启动时从 cfg.get → restoreState"两段逻辑。
抽出来调用方写 ``_splitter_persist.wire(self.splitter, self._cfg, "rtt_splitter_state")``
和 ``_splitter_persist.restore(self.splitter, self._cfg, "rtt_splitter_state", self._logger)``
即可，对齐 ``_infobar.py`` 的模块化风格。

注意：cfg.set 已有 200ms 节流（CLAUDE.md "ConfigService.set 高频值要节流"
经验），splitterMoved 拖动期间高频触发也不会拖死 SSD。
"""
from __future__ import annotations

import base64

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QSplitter


def wire(splitter: QSplitter, cfg, key: str) -> None:
    """接 splitterMoved 信号 → 把 saveState() base64 编码后存进 cfg[key]。"""
    def _save(*_args) -> None:
        cfg.set(key, base64.b64encode(bytes(splitter.saveState())).decode("ascii"))
    splitter.splitterMoved.connect(_save)


def restore(splitter: QSplitter, cfg, key: str, logger) -> None:
    """从 cfg[key] 取 base64 → 解码 → restoreState。

    异常路径（跨版本不兼容 / user_prefs 损坏）catch + warning，回落代码默认
    setStretchFactor 比例（即不调 restoreState）。
    """
    state_b64 = cfg.get(key)
    if not state_b64:
        return
    try:
        splitter.restoreState(QByteArray(base64.b64decode(state_b64)))
    except Exception as e:
        logger.warning(f"恢复 splitter state 失败 ({key})：{e}")
```

- [ ] **Step 2: 验证模块可导入**

Run: `python -c "import sys; sys.path.insert(0, 'src'); from ui._splitter_persist import wire, restore; print('OK')"`

Expected: 输出 `OK`

- [ ] **Step 3: 提交**

```bash
git add src/ui/_splitter_persist.py
git commit -m "$(cat <<'EOF'
feat(ui): 加 _splitter_persist 工具模块

抽出 splitterMoved → cfg.set / cfg.get → restoreState 的样板，
RTT 页（未来可能内存页也用）共用。对齐 _infobar.py 模块化风格。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: RTT 监控页重构 — QScrollArea + 垂直 QSplitter

**Files:**
- Modify: `src/ui/rtt_monitor_page.py` 主要是 `_build_ui()` 和 `__init__`

> **背景**：原 `_build_ui()` 在 `QVBoxLayout(self)` 上挂 ctrl/info/opt/display(stretch=1)/search/send/status 七层（status 是 `072af10` 新加的状态栏）。改动后 ctrl/info/opt 不变；display + search + send + **status** 三段→四段进 vertical QSplitter 下半（status 跟着进 splitter 底栏，因为它和 search/send 一样属于 display 之外的"底部控件"）；最外层套 QScrollArea。

- [ ] **Step 1: 加 imports**

在 `src/ui/rtt_monitor_page.py` 顶部 imports 区域：

把 `from PySide6.QtWidgets import (...)` 修改为加入 `QFrame`、`QScrollArea`、`QSplitter`：

```python
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
```

紧跟在 `from . import _infobar` 下面加：

```python
from . import _splitter_persist
```

- [ ] **Step 2: 重构 `_build_ui()` 顶部 — 把内容 widget 包进 ScrollArea**

找到 `_build_ui` 开头（约 105 行）：

```python
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
```

替换为：

```python
    def _build_ui(self) -> None:
        # 最外层：ScrollArea；窗口高度 < 内容自然高度时出现垂直滚动条。
        # 同 ac439e9 给内存页加的套路，保持两页一致。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer.addWidget(self._scroll)
        inner = QWidget()
        self._scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
```

**注意**：原代码所有后续 `root.add...` 调用不变——`root` 现在挂在 `inner` 而不是 `self` 上。

- [ ] **Step 3: 重构 `_build_ui()` 尾部 — display + 底栏（search+send+status）进垂直 splitter**

找到原代码这段（约 243-307 行，从 `# ---- 显示区` 到 `root.addLayout(status)` 结束）。具体识别：以 `# ---- 显示区（qfluentwidgets PlainTextEdit 自动适应主题）----` 开始，以 `root.addLayout(status)` 结束。

替换为下面的完整内容（**注意：原代码该范围的所有 widget 构造逻辑保留，只把 `root.addWidget(self.display, 1)` / `root.addLayout(srch)` / `root.addLayout(send)` / `root.addLayout(status)` 四处 root 装配改为放进 vertical splitter 的两半**）：

```python
        # ---- 显示区（qfluentwidgets PlainTextEdit 自动适应主题）----
        self.display = PlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setMaximumBlockCount(self._cfg.get("max_display_lines"))
        # 固定宽度按窗口宽度换行（超过窗宽自动 wrap，便于阅读长行日志）
        self.display.setLineWrapMode(PlainTextEdit.WidgetWidth)
        # display 最小高度 80px：避免子控件 sizeHint 累积导致主窗口 mintrack 过大
        # （Windows 最大化时底部被任务栏遮挡 → 搜索栏/发送栏看不见）
        self.display.setMinimumHeight(80)

        # ---- 搜索栏 ----
        try:
            from qfluentwidgets import SearchLineEdit
            self.le_search = SearchLineEdit(self)
        except (ImportError, AttributeError):
            from PySide6.QtWidgets import QLineEdit
            self.le_search = QLineEdit(self)
        srch = QHBoxLayout()
        self.le_search.setPlaceholderText("搜索日志…")
        self.btn_prev = PushButton("↑", self)
        self.btn_next = PushButton("↓", self)
        self.lbl_match = QLabel("0/0")
        srch.addWidget(self.le_search, 1)
        srch.addWidget(self.btn_prev)
        srch.addWidget(self.btn_next)
        srch.addWidget(self.lbl_match)

        # ---- 发送栏 ----
        send = QHBoxLayout()
        # EditableComboBox 复用 cfg.send_history（最近 50 条）下拉快速重发
        self.le_send = EditableComboBox(self)
        self.le_send.setPlaceholderText("输入要发送的数据 (Hex 模式下用 16 进制字符)")
        # 加载历史（倒序：最新在最前）
        history = list(self._cfg.get("send_history") or [])
        if history:
            self.le_send.addItems(list(reversed(history)))
            self.le_send.setCurrentText("")  # 不预选任何项
        self.chk_hex = CheckBox("Hex")
        self.chk_hex.setChecked(self._cfg.get("hex_send_mode"))
        self.btn_send = PushButton(FluentIcon.SEND, "发送", self)
        self.btn_send.setEnabled(False)
        send.addWidget(self.le_send, 1)
        send.addWidget(self.chk_hex)
        send.addWidget(self.btn_send)

        # ---- 底部状态栏 ----
        status = QHBoxLayout()
        status.setContentsMargins(0, 0, 0, 0)
        self.lbl_status_state = BodyLabel("● 未连接")
        self.lbl_status_state.setStyleSheet("color: #888888;")
        self.lbl_status_state.setMinimumWidth(120)
        self.lbl_status_rate = BodyLabel("")
        self.lbl_status_rate.setMinimumWidth(160)
        self.lbl_status_total = BodyLabel("")
        self.lbl_status_total.setMinimumWidth(200)
        self.lbl_status_encoding = BodyLabel("")
        status.addWidget(self.lbl_status_state)
        status.addWidget(self.lbl_status_rate)
        status.addWidget(self.lbl_status_total)
        status.addStretch(1)
        status.addWidget(self.lbl_status_encoding)

        # 底栏 container：搜索 + 发送 + 状态栏 一起进 splitter 下半。
        # 用 QWidget 包，便于设 minimumHeight 防止误拖到 0。
        bottom = QWidget()
        bottom_lay = QVBoxLayout(bottom)
        bottom_lay.setContentsMargins(0, 0, 0, 0)
        bottom_lay.setSpacing(8)
        bottom_lay.addLayout(srch)
        bottom_lay.addLayout(send)
        bottom_lay.addLayout(status)
        # 三行控件 + 间距：合计约 120px 起跳，给 20px 余量
        bottom.setMinimumHeight(120)

        # 垂直 splitter：display | 底栏。拖动 handle 调比例，状态进 user_prefs。
        self.splitter = QSplitter(Qt.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.display)
        self.splitter.addWidget(bottom)
        self.splitter.setStretchFactor(0, 3)  # display 默认占 3/4
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, 1)
```

- [ ] **Step 4: `__init__` 末尾接 splitter 持久化**

找到 `__init__`（约 72-100 行）末尾：

```python
        # 初始化编码状态显示
        self._update_encoding_label(cfg.get("rtt_encoding") or "utf-8")
        cfg.rtt_encoding_changed.connect(self._update_encoding_label)
```

紧跟在最后一行下面加：

```python

        # 恢复并连接 splitter 状态持久化（必须在 _build_ui 之后；
        # _build_ui 已 setStretchFactor 默认 3:1，restoreState 空字符串就 no-op）
        from core.logger import get_logger
        _splitter_persist.restore(self.splitter, self._cfg, "rtt_splitter_state", get_logger())
        _splitter_persist.wire(self.splitter, self._cfg, "rtt_splitter_state")
```

- [ ] **Step 5: 跑全量测试**

Run: `python -m pytest tests/ -v`

Expected: 68 通过

- [ ] **Step 6: 手动启动验证**

Run: `python src/main.py`

验证：
- [ ] RTT 监控页打开正常，七层控件全部可见
- [ ] display 和搜索栏之间出现细的可拖动 splitter handle
- [ ] 鼠标拖动 handle 上下 → display 高度可变
- [ ] 拖到极端位置 → display 不会变成 0 高度（`setChildrenCollapsible(False)` 生效），底栏（搜索 + 发送 + 状态）也不会消失（`bottom.setMinimumHeight(120)`）
- [ ] 拖动后关窗口 → 重启 → splitter 位置保持上次值
- [ ] 缩窗口高度到 540 以下 → 出现垂直滚动条，可滚到底部看到状态栏
- [ ] 最大化窗口 → 滚动条消失，splitter 撑满
- [ ] 状态栏的 4 段（连接状态/速率/累计/编码）显示正常，1s 一次刷新

如有 sizeHint 异常（splitter 高度算不对），第一时间排查 `bottom.setMinimumHeight(120)` 是否过小或过大；必要时改为更稳的值。

- [ ] **Step 7: 提交**

```bash
git add src/ui/rtt_monitor_page.py
git commit -m "$(cat <<'EOF'
feat(rtt): 加 ScrollArea + 垂直 Splitter，display 高度可拖可记

display 和 (搜索栏+发送栏+状态栏) 放进垂直 QSplitter，用户拖
handle 调比例。外层包 QScrollArea，窗口高度不够时出现垂直
滚动条。splitter 状态用 _splitter_persist 持久化进 user_prefs。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: ~~内存页 ScrollArea + 垂直 Splitter~~ — DROPPED

已在 commit `ac439e9` 实施 ScrollArea + h_splitter setMinimumHeight(320)。用户确认不再加 vertical splitter（避免和新增的写内存卡片堆叠的复杂度）。

`memory_splitter_state` config key 保留（已在 Task 1 加进 DEFAULTS，删除是 churn；空字符串默认值零成本，备用）。

---

## Task 5: 兜底验证

**Files:**
- 无新改动；纯验证

- [ ] **Step 1: 跑全量测试**

Run: `python -m pytest tests/ -v`

Expected: 68 通过

- [ ] **Step 2: 验证 user_prefs 损坏不崩**

```bash
python -c "import sys; sys.path.insert(0,'src'); from core.config_service import ConfigService; print(ConfigService._compute_user_prefs_path())"
```

记下路径（一般 `%APPDATA%/JLinkRTTViewer/user_prefs.json`）。手动编辑它，把 `rtt_splitter_state` 改成 base64 无效串（例如 `"!@#$%"`）。

Run: `python src/main.py`

Expected: 应用正常启动，不崩；日志（控制台或 `%APPDATA%/JLinkRTTViewer/logs/`）出现 "恢复 splitter state 失败" warning；splitter 按默认 3:1 比例显示。

- [ ] **Step 3: 恢复 user_prefs**

把 Step 2 损坏的字段改回正常值（直接清空 → `""`），或者删整个 user_prefs.json 让 cfg 走默认。

- [ ] **Step 4: 终结验证 — 拖动 + 关窗 + 重启**

Run: `python src/main.py`

- [ ] 进 RTT 页拖动 splitter handle 改变 display 高度（例如改到屏幕一半）
- [ ] 切到内存页确认 ScrollArea 正常（不动新加的 splitter，因为这次不加）
- [ ] **不要点连接**，直接关窗
- [ ] 重新启动 → RTT 页 splitter 在之前位置

无需 commit。

---

## 总览

实施完后预计 3 个新 commit（Task 1 已完成）：
1. ~~`feat(config): 加 rtt_splitter_state / memory_splitter_state 持久化 key`~~ ✅ Task 1 已完成 (`6febfa2`)
2. `feat(ui): 加 _splitter_persist 工具模块` (Task 2)
3. `feat(rtt): 加 ScrollArea + 垂直 Splitter，display 高度可拖可记` (Task 3)

回归：68 测试全部通过。

## 已知风险与排查锚点

| 风险 | 触发条件 | 排查方向 |
|---|---|---|
| ScrollArea 内 splitter 高度算错 | 窗口尺寸切换时 splitter 不撑满 / 撑过头 | display.setMinimumHeight 和 bottom.setMinimumHeight 数值；必要时给 inner widget 加 setMinimumSize |
| splitterMoved 触发频率高拖累 cfg.set 节流 | 应不会——ConfigService 已 200ms 节流。如果观察到落盘超过 5 次/秒，检查 `_flush_timer` 配置 | tests/test_config_service.py::test_set_throttled |
| 跨版本 saveState 不兼容 | 旧版 user_prefs.json + 新版 splitter widget 结构变化 | `_splitter_persist.restore` 已 try/except + warning，回落默认 |
| 状态栏被拖进 splitter 后视觉位置改变 | 状态栏跟着 splitter 上下移动，不再"贴底" | 实际效果：状态栏在 splitter 下半的底部，splitter handle 拖到顶时状态栏也会在屏幕底部附近——可接受。如果用户反馈 status 想常驻 absolute bottom，需要把它从 splitter 拿出来放 outer 外层（架构改动较大） |
