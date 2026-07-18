# 设置背景图片功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为主窗口添加可配置的背景图片，支持透明度与填充方式，并在设置页提供 UI。

**Architecture:** 在 `ConfigService` 中新增三个配置项；`MainWindow` 覆盖 `paintEvent` 统一绘制背景图；`SettingsPage` 在外观卡片新增文件选择、透明度、填充方式控件。复用现有透明页面布局让图片显示在卡片间隙。

**Tech Stack:** Python 3.11, PySide6, qfluentwidgets, pytest-qt

---

## File Map

| File | Responsibility |
|---|---|
| `src/core/config_service.py` | 新增 `background_image_path`, `background_opacity`, `background_fill_mode` 默认值与类型校验 |
| `src/ui/main_window.py` | 监听配置变化，缓存/绘制背景图，控制 Mica 开关 |
| `src/ui/settings_page.py` | 在外观卡片新增背景图片设置行 |
| `src/i18n/*.json` | 新增背景图片相关翻译键（除 zh_CN 外） |
| `tests/test_main_window.py` | 新增 MainWindow 背景图绘制与 Mica 开关测试 |
| `tests/test_settings_page.py` | 新增设置页控件与配置写入测试 |

---

## Task 1: 新增配置项

**Files:**
- Modify: `src/core/config_service.py`

- [ ] **Step 1: 打开 `src/core/config_service.py`，找到 `DEFAULTS` 字典**

大概在文件 36 行附近。当前包含 `theme`, `theme_color`, `language`, `font_family`, `font_size`, `ui_font_size`, `ui_font_family`, `mark_color`, `send_text_color`, `memory_font_size` 等。

- [ ] **Step 2: 在 `DEFAULTS` 中新增三项**

```python
    "background_image_path": "",
    "background_opacity": 0.3,
    "background_fill_mode": "cover",
```

- [ ] **Step 3: 找到 `_validate` 或类型校验逻辑，新增规则**

如果 `ConfigService` 有 `_validate(self, key, value)` 方法，确保：
- `background_image_path`: `str`
- `background_opacity`: `float` / `int`，且 0.0 <= value <= 1.0
- `background_fill_mode`: `str`，且值在 `("stretch", "cover", "center", "tile")` 中

如果没有集中校验，跳过这步，但要确保类型不会被后续代码拒绝。

- [ ] **Step 4: 运行该模块现有测试确认无回归**

```bash
uv run python -m pytest tests/test_config_service.py -x -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/config_service.py
git commit -m "feat(config): add background image settings defaults"
```

---

## Task 2: MainWindow 背景图绘制

**Files:**
- Modify: `src/ui/main_window.py`

- [ ] **Step 1: 在 `MainWindow.__init__` 末尾新增初始化代码**

```python
        self._bg_pixmap: QPixmap | None = None
        self._bg_path: str = ""
        self._bg_opacity: float = 0.0
        self._bg_fill_mode: str = "cover"
        self._load_background_image()
        self._cfg.background_image_path_changed.connect(self._on_background_image_path_changed)
        self._cfg.background_opacity_changed.connect(self._on_background_opacity_changed)
        self._cfg.background_fill_mode_changed.connect(self._on_background_fill_mode_changed)
```

> 如果 `ConfigService` 尚未有这三个信号，先在该任务内加上（见 Task 1 补充说明）。

- [ ] **Step 2: 添加加载/更新方法**

在 `MainWindow` 类中添加：

```python
    def _load_background_image(self) -> None:
        path = self._cfg.get("background_image_path")
        self._bg_path = path
        if not path:
            self._bg_pixmap = None
            self._update_mica_state()
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._bg_pixmap = None
            _logger.warning(f"背景图片加载失败：{path}")
        else:
            self._bg_pixmap = pixmap
        self._update_mica_state()

    def _update_mica_state(self) -> None:
        has_bg = self._bg_pixmap is not None
        try:
            self.setMicaEffectEnabled(not has_bg)
        except Exception:
            pass

    @Slot(str)
    def _on_background_image_path_changed(self, path: str) -> None:
        self._load_background_image()
        self.update()

    @Slot(float)
    def _on_background_opacity_changed(self, opacity: float) -> None:
        self._bg_opacity = max(0.0, min(1.0, opacity))
        self.update()

    @Slot(str)
    def _on_background_fill_mode_changed(self, mode: str) -> None:
        self._bg_fill_mode = mode
        self.update()
```

- [ ] **Step 3: 在 `__init__` 中读取当前 opacity 和 fill_mode**

```python
        self._bg_opacity = max(0.0, min(1.0, self._cfg.get("background_opacity")))
        self._bg_fill_mode = self._cfg.get("background_fill_mode")
```

- [ ] **Step 4: 覆盖 `paintEvent`**

在 `MainWindow` 类中添加：

```python
    def paintEvent(self, event) -> None:
        if self._bg_pixmap is None or self._bg_pixmap.isNull():
            super().paintEvent(event)
            return

        painter = QPainter(self)
        if not painter.isActive():
            super().paintEvent(event)
            return

        target_rect = self.rect()
        pixmap = self._bg_pixmap
        mode = self._bg_fill_mode

        if mode == "stretch":
            scaled = pixmap.scaled(target_rect.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.setOpacity(self._bg_opacity)
            painter.drawPixmap(target_rect, scaled)
        elif mode == "cover":
            scaled = pixmap.scaled(target_rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (target_rect.width() - scaled.width()) // 2
            y = (target_rect.height() - scaled.height()) // 2
            painter.setOpacity(self._bg_opacity)
            painter.drawPixmap(x, y, scaled)
        elif mode == "center":
            x = (target_rect.width() - pixmap.width()) // 2
            y = (target_rect.height() - pixmap.height()) // 2
            painter.setOpacity(self._bg_opacity)
            painter.drawPixmap(x, y, pixmap)
        elif mode == "tile":
            painter.setOpacity(self._bg_opacity)
            for x in range(0, target_rect.width(), pixmap.width()):
                for y in range(0, target_rect.height(), pixmap.height()):
                    painter.drawPixmap(x, y, pixmap)
        else:
            painter.setOpacity(self._bg_opacity)
            scaled = pixmap.scaled(target_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (target_rect.width() - scaled.width()) // 2
            y = (target_rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)

        painter.end()
        super().paintEvent(event)
```

- [ ] **Step 5: 确保导入了 `QPainter`, `QPixmap`, `Qt`**

```python
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtCore import Qt, Slot
```

- [ ] **Step 6: Commit**

```bash
git add src/ui/main_window.py
git commit -m "feat(main_window): paint background image with opacity and fill mode"
```

---

## Task 3: ConfigService 新增信号

**Files:**
- Modify: `src/core/config_service.py`

> 如果 Task 1 中该文件已有这三个信号，跳过此任务。

- [ ] **Step 1: 在 `ConfigService` 信号定义区新增**

```python
    background_image_path_changed = Signal(str)
    background_opacity_changed = Signal(float)
    background_fill_mode_changed = Signal(str)
```

- [ ] **Step 2: 在 `set()` 方法中，当 key 为新增三项时 emit 对应信号**

查找 `set(self, key, value)` 方法，在写入和 dirty 标记后添加：

```python
        if key == "background_image_path":
            self.background_image_path_changed.emit(value)
        elif key == "background_opacity":
            self.background_opacity_changed.emit(float(value))
        elif key == "background_fill_mode":
            self.background_fill_mode_changed.emit(value)
```

- [ ] **Step 3: 运行 config 测试**

```bash
uv run python -m pytest tests/test_config_service.py -x -q
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/core/config_service.py
git commit -m "feat(config): emit signals for background image settings"
```

---

## Task 4: SettingsPage UI

**Files:**
- Modify: `src/ui/settings_page.py`

- [ ] **Step 1: 在外观卡片 `_build_appearance_card` 内新增控件**

当前外观卡片有 theme、language、theme_color、ui_font_size、ui_font_family、rtt_font。在其末尾新增一组垂直排列的设置：

```python
        # 背景图片
        self.lbl_bg_image = BodyLabel(self.tr("背景图片"))
        self.le_bg_image = LineEdit()
        self.le_bg_image.setReadOnly(True)
        self.le_bg_image.setPlaceholderText(self.tr("未选择"))
        self.btn_bg_browse = PushButton(self.tr("浏览…"))
        self.btn_bg_clear = PushButton(self.tr("清除"))

        self.lbl_bg_opacity = BodyLabel(self.tr("透明度"))
        self.slider_bg_opacity = Slider(Qt.Horizontal)
        self.slider_bg_opacity.setRange(0, 100)
        self.spin_bg_opacity = SpinBox()
        self.spin_bg_opacity.setRange(0, 100)
        self.spin_bg_opacity.setSuffix("%")

        self.lbl_bg_fill = BodyLabel(self.tr("填充方式"))
        self.cmb_bg_fill = ComboBox()
        self.cmb_bg_fill.addItems([
            self.tr("拉伸"),
            self.tr("覆盖"),
            self.tr("居中"),
            self.tr("平铺"),
        ])
```

- [ ] **Step 2: 布局添加到外观卡片**

将上述控件按现有卡片模式加入布局。例如：

```python
        row_bg = QHBoxLayout()
        row_bg.addWidget(self.le_bg_image, 1)
        row_bg.addWidget(self.btn_bg_browse)
        row_bg.addWidget(self.btn_bg_clear)

        row_opacity = QHBoxLayout()
        row_opacity.addWidget(self.lbl_bg_opacity)
        row_opacity.addWidget(self.slider_bg_opacity, 1)
        row_opacity.addWidget(self.spin_bg_opacity)

        row_fill = QHBoxLayout()
        row_fill.addWidget(self.lbl_bg_fill)
        row_fill.addWidget(self.cmb_bg_fill, 1)

        layout.addWidget(self.lbl_bg_image)
        layout.addLayout(row_bg)
        layout.addLayout(row_opacity)
        layout.addLayout(row_fill)
```

- [ ] **Step 3: 在 `_load_settings` 中初始化值**

```python
        self.le_bg_image.setText(self._cfg.get("background_image_path"))
        opacity = int(round(self._cfg.get("background_opacity") * 100))
        self.slider_bg_opacity.setValue(opacity)
        self.spin_bg_opacity.setValue(opacity)
        mode = self._cfg.get("background_fill_mode")
        mode_index = ["stretch", "cover", "center", "tile"].index(mode)
        self.cmb_bg_fill.setCurrentIndex(mode_index)
```

- [ ] **Step 4: 连接信号**

新增方法或内联连接：

```python
    def _on_bg_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("选择背景图片"), "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.le_bg_image.setText(path)
            self._cfg.set("background_image_path", path)

    def _on_bg_clear(self) -> None:
        self.le_bg_image.clear()
        self._cfg.set("background_image_path", "")

    def _on_bg_opacity_changed(self, value: int) -> None:
        self.slider_bg_opacity.blockSignals(True)
        self.spin_bg_opacity.blockSignals(True)
        self.slider_bg_opacity.setValue(value)
        self.spin_bg_opacity.setValue(value)
        self.slider_bg_opacity.blockSignals(False)
        self.spin_bg_opacity.blockSignals(False)
        self._cfg.set("background_opacity", value / 100.0)

    def _on_bg_fill_changed(self, index: int) -> None:
        modes = ["stretch", "cover", "center", "tile"]
        self._cfg.set("background_fill_mode", modes[index])
```

在 `_connect_signals`（或等价的连接区域）中：

```python
        self.btn_bg_browse.clicked.connect(self._on_bg_browse)
        self.btn_bg_clear.clicked.connect(self._on_bg_clear)
        self.slider_bg_opacity.valueChanged.connect(self._on_bg_opacity_changed)
        self.spin_bg_opacity.valueChanged.connect(self._on_bg_opacity_changed)
        self.cmb_bg_fill.currentIndexChanged.connect(self._on_bg_fill_changed)
```

- [ ] **Step 5: 在 `_retranslate_ui` 中更新文本**

确保语言切换时这些控件的文本被重设：

```python
        self.lbl_bg_image.setText(self.tr("背景图片"))
        self.btn_bg_browse.setText(self.tr("浏览…"))
        self.btn_bg_clear.setText(self.tr("清除"))
        self.lbl_bg_opacity.setText(self.tr("透明度"))
        self.lbl_bg_fill.setText(self.tr("填充方式"))
        # 重新填充填充方式下拉，避免已选项错位
        current_mode = self._cfg.get("background_fill_mode")
        self.cmb_bg_fill.blockSignals(True)
        self.cmb_bg_fill.clear()
        self.cmb_bg_fill.addItems([
            self.tr("拉伸"),
            self.tr("覆盖"),
            self.tr("居中"),
            self.tr("平铺"),
        ])
        mode_index = ["stretch", "cover", "center", "tile"].index(current_mode)
        self.cmb_bg_fill.setCurrentIndex(mode_index)
        self.cmb_bg_fill.blockSignals(False)
```

- [ ] **Step 6: 确保导入了 `QFileDialog`**

```python
from PySide6.QtWidgets import QFileDialog
```

- [ ] **Step 7: Commit**

```bash
git add src/ui/settings_page.py
git commit -m "feat(settings): add background image controls"
```

---

## Task 5: i18n 翻译

**Files:**
- Modify: `src/i18n/en.json`, `src/i18n/zh_TW.json`, `src/i18n/ja.json`, `src/i18n/ko.json`, `src/i18n/fr.json`
- 不修改 `src/i18n/zh_CN.json`（源文本为中文，按约定只放英文 source → 中文映射）

- [ ] **Step 1: 在每个非中文 JSON 中加入以下键值**

```json
    "背景图片": "Background image",
    "浏览…": "Browse…",
    "清除": "Clear",
    "透明度": "Opacity",
    "填充方式": "Fill mode",
    "拉伸": "Stretch",
    "覆盖": "Cover",
    "居中": "Center",
    "平铺": "Tile",
    "选择背景图片": "Select background image",
    "未选择": "Not selected"
```

请按各语言实际翻译。示例为英文，其他语言请保持与项目其他翻译一致的风格。

- [ ] **Step 2: Commit**

```bash
git add src/i18n/en.json src/i18n/zh_TW.json src/i18n/ja.json src/i18n/ko.json src/i18n/fr.json
git commit -m "i18n: add background image translations"
```

---

## Task 6: 测试 MainWindow 背景图

**Files:**
- Create: `tests/test_main_window.py`

- [ ] **Step 1: 创建测试文件并写入基础 fixture**

```python
import os
import tempfile
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from main import main as app_main
from ui.main_window import MainWindow


@pytest.fixture
def main_window(qtbot, tmp_path, monkeypatch):
    from core.config_service import ConfigService
    cfg_path = tmp_path / "prefs.json"
    cfg = ConfigService(str(cfg_path))
    # 阻止真实 worker 启动，避免硬件依赖
    win = MainWindow(cfg)
    qtbot.addWidget(win)
    win.show()
    qtbot.wait_for_window_shown(win)
    yield win
    win.close()


def test_main_window_defaults_to_no_background(main_window):
    assert main_window._bg_pixmap is None
    assert main_window._bg_path == ""


def test_main_window_loads_valid_background_image(main_window, tmp_path, qtbot):
    path = str(tmp_path / "bg.png")
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.red)
    assert pixmap.save(path)

    main_window._cfg.set("background_image_path", path)
    qtbot.wait(100)

    assert main_window._bg_pixmap is not None
    assert not main_window._bg_pixmap.isNull()


def test_main_window_handles_invalid_background_path(main_window, qtbot):
    main_window._cfg.set("background_image_path", "/nonexistent/image.png")
    qtbot.wait(100)
    assert main_window._bg_pixmap is None


def test_main_window_opacity_is_clamped(main_window, qtbot):
    main_window._cfg.set("background_opacity", 1.5)
    assert main_window._bg_opacity == 1.0

    main_window._cfg.set("background_opacity", -0.5)
    assert main_window._bg_opacity == 0.0


def test_main_window_fill_mode_change_updates_state(main_window, qtbot):
    main_window._cfg.set("background_fill_mode", "tile")
    assert main_window._bg_fill_mode == "tile"
```

- [ ] **Step 2: 运行测试**

```bash
uv run python -m pytest tests/test_main_window.py -x -v
```

Expected: PASS（至少这些新测试通过；若 Fixture 因真实 worker 启动失败，需用 monkeypatch 进一步隔离 `_start_worker` / `JLinkWorker`）

- [ ] **Step 3: Commit**

```bash
git add tests/test_main_window.py
git commit -m "test(main_window): add background image tests"
```

---

## Task 7: 测试 SettingsPage 控件

**Files:**
- Create/Modify: `tests/test_settings_page.py`

- [ ] **Step 1: 打开或创建测试文件，写入 fixture**

```python
import pytest
from ui.settings_page import SettingsPage
from core.config_service import ConfigService


@pytest.fixture
def settings_page(qtbot, tmp_path):
    cfg_path = tmp_path / "prefs.json"
    cfg = ConfigService(str(cfg_path))
    page = SettingsPage(cfg)
    qtbot.addWidget(page)
    page.show()
    qtbot.wait_for_window_shown(page)
    yield page
    page.close()
```

- [ ] **Step 2: 写入控件与配置测试**

```python
def test_settings_page_has_background_controls(settings_page):
    assert settings_page.lbl_bg_image is not None
    assert settings_page.le_bg_image is not None
    assert settings_page.btn_bg_browse is not None
    assert settings_page.btn_bg_clear is not None
    assert settings_page.slider_bg_opacity is not None
    assert settings_page.spin_bg_opacity is not None
    assert settings_page.cmb_bg_fill is not None


def test_settings_page_opacity_syncs_slider_and_spin(settings_page, qtbot):
    settings_page.slider_bg_opacity.setValue(75)
    assert settings_page.spin_bg_opacity.value() == 75
    assert abs(settings_page._cfg.get("background_opacity") - 0.75) < 0.01


def test_settings_page_clear_background(settings_page, qtbot):
    settings_page._cfg.set("background_image_path", "/tmp/fake.png")
    settings_page.btn_bg_clear.click()
    assert settings_page._cfg.get("background_image_path") == ""
    assert settings_page.le_bg_image.text() == ""


def test_settings_page_fill_mode_persists(settings_page, qtbot):
    settings_page.cmb_bg_fill.setCurrentIndex(2)  # 居中
    assert settings_page._cfg.get("background_fill_mode") == "center"
```

- [ ] **Step 3: 运行测试**

```bash
uv run python -m pytest tests/test_settings_page.py -x -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_settings_page.py
git commit -m "test(settings): add background image controls tests"
```

---

## Task 8: 全量回归测试

- [ ] **Step 1: 运行全部测试**

```bash
uv run python -m pytest tests/ -x -q
```

Expected: PASS（目标保持 296+）

- [ ] **Step 2: 手动/冒烟验证**

启动应用：

```bash
uv run python src/main.py
```

1. 进入「设置」→「外观」。
2. 选择一张图片，观察主窗口背景是否即时变化。
3. 调整透明度 0/50/100%，观察深浅变化。
4. 切换填充方式拉伸/覆盖/居中/平铺，观察布局。
5. 点击「清除」，背景恢复默认 Fluent 外观。
6. 切换语言，确认所有新控件文本正确翻译。

- [ ] **Step 3: Commit 最终结果（如需）**

如果冒烟验证有修复，单独 commit；无修复则结束。

---

## 补充说明

### ConfigService 信号

如果 `ConfigService` 当前只有少量信号（如 `theme_changed`），需按 Task 3 新增三个信号。如果它使用通用信号（如 `value_changed(key, value)`），则 MainWindow 可连接该通用信号并分支处理，不必新增三个独立信号；但为与现有 `theme_changed` 等模式一致，推荐新增独立信号。

### 背景图不显示的可能原因

1. Mica 未关闭：必须调用 `setMicaEffectEnabled(False)`。
2. 页面/卡片未透明：检查 `_scroll_helpers.py` 是否已让页面内容透明；必要时在 `paintEvent` 绘制后基类 `super().paintEvent(event)` 之前确保 painter 已 end。
3. 图片路径含非 ASCII 字符：Qt `QPixmap(path)` 在 Windows 上可直接处理中文路径，无需额外转码。
4. `FluentWindowBase.paintEvent` 内部重新填充了背景：本方案先画图片再调用基类 paintEvent，基类可能再用半透明背景色覆盖，透明度/效果需微调。

### 不修改的约定

- 不修改 `zh_CN.json`（源文本为中文）。
- 不改变本地 USB / 远程 J-Link 逻辑。
- 不改动 RTT 显示区、烧录页、内存页等其他页面。
