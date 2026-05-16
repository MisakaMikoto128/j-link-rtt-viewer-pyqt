# 设计：主页面滚动 + 可拖动的显示区高度

**日期**：2026-05-16
**作者**：协作设计（用户 + Claude Opus 4.7）
**状态**：已批准设计，待 implementation plan

## 背景

当前 RTT 监控页和内存查看页都是 `QVBoxLayout` 拼装，display 控件（`PlainTextEdit`）用 `stretch=1` 占用剩余空间。两个问题：

1. **窗口高度不够时底部控件被任务栏遮挡**。CLAUDE.md 有现成的 `setMinimumSize(900, 540)` + `showEvent` clamp 缓解，但治标不治本——如果用户拖小窗口到 540 以下，仍会触发。
2. **用户没办法控制 display 高度**。display 占满剩余空间，用户既不能调大也不能调小，更不能跨会话保留偏好。

## 目标

- 给 RTT 页和 Memory 页加 **垂直 QSplitter**：用户可以拖动改变 display 和底部控件的高度比例。
- 给两个页面包 **QScrollArea**：窗口高度 < 最小内容高度时出现垂直滚动条，永不显示水平滚动条。
- Splitter 位置 **持久化**：关窗口再启动，比例保持上次拖动的位置。
- 既不动 Settings 页也不动 About 页（控件少，不需要）。
- 既不加"重置 splitter"按钮，也不加设置页 SpinBox 联动（YAGNI）。

## 非目标

- 不重写整页 layout，只把 display 和底栏部分塞进 splitter。
- 不引入新的 widget 库依赖。
- 不做横向 splitter 持久化（内存页现有的横向 splitter 不动）——只新增竖向 splitter 状态。

## 架构

### RTT 监控页（`src/ui/rtt_monitor_page.py`）

**改动前**：
```
QVBoxLayout
├── ctrl bar
├── gb_info (HeaderCardWidget)
├── opt bar
├── display (PlainTextEdit, stretch=1)
├── search bar
└── send bar
```

**改动后**：
```
QScrollArea (vertical-only, setWidgetResizable=True)
└── 内容 widget (QVBoxLayout)
    ├── ctrl bar
    ├── gb_info
    ├── opt bar
    └── QSplitter(Vertical, stretch=1)
        ├── display
        └── 底栏 QWidget
            └── QVBoxLayout
                ├── search bar
                └── send bar
```

### 内存查看页（`src/ui/memory_viewer_page.py`）

**改动前**：
```
QVBoxLayout
├── read_card
├── QSplitter(Horizontal, stretch=1) [display | side panel]
└── export_card
```

**改动后**：
```
QScrollArea
└── 内容 widget (QVBoxLayout)
    ├── read_card
    └── QSplitter(Vertical, stretch=1)
        ├── QSplitter(Horizontal) [display | side panel]   ← 原有
        └── export_card
```

### `QScrollArea` 配置

```python
scroll = QScrollArea(self)
scroll.setWidgetResizable(True)
scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
scroll.setFrameShape(QFrame.NoFrame)  # 去掉默认边框，融入 fluent 主题
```

QWidget 内容包一层，原 `QVBoxLayout(self)` 改成 `QVBoxLayout(content_widget)`，再 `scroll.setWidget(content_widget)`。最外层 widget 只放 scroll：
```python
outer = QVBoxLayout(self)
outer.setContentsMargins(0, 0, 0, 0)
outer.addWidget(scroll)
```

### `QSplitter` 配置

```python
splitter = QSplitter(Qt.Vertical, self)
splitter.addWidget(display)
splitter.addWidget(bottom_container)
splitter.setStretchFactor(0, 3)  # display 默认占 3/4
splitter.setStretchFactor(1, 1)
splitter.setChildrenCollapsible(False)  # 防止误拖到 0 高度
```

`setChildrenCollapsible(False)` 保证用户不会把 display 或底栏拖到完全消失。

## 持久化

### ConfigService 新增 keys

```python
DEFAULTS = {
    ...
    "rtt_splitter_state": "",      # base64(QByteArray) of QSplitter.saveState()
    "memory_splitter_state": "",   # 同上
}
```

不需要 `Signal` ——splitter state 是单向持久化，只在页面构造时读、splitterMoved 时写，没有跨页面同步的需求。

### 保存

```python
def _wire_splitter_persistence(self, splitter: QSplitter, key: str) -> None:
    splitter.splitterMoved.connect(
        lambda *_: self._cfg.set(key, base64.b64encode(bytes(splitter.saveState())).decode("ascii"))
    )
```

`splitterMoved` 在拖动期间高频触发（鼠标 move 级别）。`ConfigService.set` 已有 200ms 节流（dirty flag + `_flush_timer.start()`），落盘频率上限就是 5 次/秒，符合 CLAUDE.md "高频值要节流" 经验。

### 恢复

页面 `__init__` 末尾、构造完 splitter 之后：

```python
state_b64 = self._cfg.get(key)
if state_b64:
    try:
        splitter.restoreState(QByteArray(base64.b64decode(state_b64)))
    except Exception as e:
        self._logger.warning(f"恢复 splitter state 失败 ({key})：{e}")
# 空字符串 → 保留 setStretchFactor 默认 3:1
```

`try/except` 必要：如果用户跨版本升级，旧的 splitter state 结构可能 incompatible，restoreState 抛错就 fallback 到默认。

## 数据流

```
用户拖动 splitter handle
    ↓ splitterMoved(pos, index)
lambda 槽 → cfg.set("..._splitter_state", base64_b64encode(saveState()))
    ↓ (200ms 节流)
_do_flush → atomic write user_prefs.json

下次启动：
__init__ → cfg.get("..._splitter_state")
    ↓ 非空
splitter.restoreState(QByteArray(b64decode(...)))
```

## 错误处理

- **`restoreState` 抛异常**（跨版本不兼容）：catch + `_logger.warning`，回落默认 sizes
- **base64 decode 失败**（user_prefs 被外部编辑损坏）：同上 catch 处理
- **`saveState` 返回空 QByteArray**（splitter 未初始化）：不会发生——`splitterMoved` 信号只在用户实际拖动后才 emit
- **ScrollArea + Splitter 嵌套时 sizeHint 计算异常**：CLAUDE.md 已经吃过 FluentWindow 子页 sizeHint 累加的亏（`showEvent` clamp）。此次重构需要在窗口最大化和最小化两种情形下手动确认垂直滚动条出现/隐藏正确，不要依赖 Qt 默认行为。

## 测试

### 自动化

现有 67 个测试覆盖 `core/`，不涉及 UI 布局。本次改动不引入新的 core 逻辑，不需要新增单测。改动后跑 `pytest tests/` 确认无回归即可（应仍为 67 通过）。

### 手动验证清单

1. **拖动 splitter 改变 display 高度** → 关窗口 → 重启 → splitter 位置保持上次拖动值
2. **首次启动**（删 user_prefs.json）→ splitter 按 3:1 默认比例显示
3. **窗口缩到 540×360** → RTT 页和 Memory 页都出现垂直滚动条，可滚到底部看到 send bar / export card
4. **窗口最大化** → 滚动条消失，splitter 撑满
5. **拖动 splitter 把 display 拖到最小** → 不能拖到 0（`setChildrenCollapsible(False)` 生效）
6. **修改 user_prefs.json 把 `rtt_splitter_state` 改成乱码** → 重启不崩溃，回落默认
7. **横向 splitter（内存页 display vs side panel）** → 拖动正常，不受改动影响（不持久化是有意为之）

## 影响面

| 文件 | 改动类型 | 行数估算 |
|---|---|---|
| `src/core/config_service.py` | +2 DEFAULTS key | +2 |
| `src/ui/rtt_monitor_page.py` | `_build_ui()` 重组末尾 + 加 splitter persistence helper | ~30 |
| `src/ui/memory_viewer_page.py` | 同上 | ~25 |
| `tests/` | 无 | 0 |
| `CLAUDE.md` | 可能加一条「QScrollArea + QSplitter 嵌套注意点」（实施时按踩坑情况决定） | 0-10 |

## 已知风险

1. **QScrollArea 内的 QSplitter 行为**：Qt 上 splitter 默认想"填满"，scroll 想"自然 sizeHint"。两者在 `setWidgetResizable(True)` 下一般能共存，但需要实施时手动验证清单第 3、4 项。如果出现 splitter 高度计算异常，可能需要给内容 widget 设 `setMinimumHeight` 兜底。
2. **`splitterMoved` 高频信号 + cfg.set 节流**：200ms 节流是已验证的方案（参考 CLAUDE.md "ConfigService.set 高频值要节流"），但确认 splitter 拖动结束后最后一个值有落盘——`closeEvent` 已经调 `cfg.flush()` 强制落盘，逻辑闭环。
3. **首次启动布局抖动**：构造完 splitter 立即 restoreState，可能在 show 之前布局算两次。可接受——只是启动 100ms 的视觉细节。

## 不做的事

- 不加"重置 splitter"按钮
- 不加 SpinBox 数字输入 splitter 比例
- 不持久化横向 splitter（内存页 display vs side panel）
- 不动 Settings / About 页
- 不引入新依赖
