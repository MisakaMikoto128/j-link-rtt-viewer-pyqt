# 设置背景图片功能设计文档

日期：2026-07-18
状态：待用户批准

## 1. 背景与目标

在现有外观设置（主题、主题色、字体）之外，允许用户为应用主窗口设置一张背景图片，并调整透明度与填充方式，满足个性化需求。

## 2. 总体方案

采用 **方案 A：覆盖 `MainWindow.paintEvent` 绘制 `QPixmap`**。

- 由 `MainWindow` 统一负责背景绘制，单点控制、缩放/透明度逻辑集中。
- 当启用背景图时，关闭 FluentWindow 的 Mica 效果，避免系统材质覆盖图片。
- 现有页面已通过 `_scroll_helpers.py` 和多处 `setAttribute(Qt.WA_TranslucentBackground)` / `background: transparent` 留出透明间隙，背景图会自然显示在卡片之间；`CardWidget` 本身保持不透明，保证可读性。

## 3. 持久化

在 `ConfigService.DEFAULTS` 新增：

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `background_image_path` | `str` | `""` | 图片绝对路径，空字符串表示未启用 |
| `background_opacity` | `float` | `0.3` | 0.0 ~ 1.0 |
| `background_fill_mode` | `str` | `"cover"` | `stretch` / `cover` / `center` / `tile` |

写入 `%APPDATA%/JLinkRTTViewer/user_prefs.json`，复用现有 200 ms 节流 + `flush()` 机制。

## 4. UI 设计

在 `src/ui/settings_page.py` 的「外观」卡片内新增一行设置：

- **背景图片**：文件路径输入框（只读/可编辑）+「浏览…」按钮 +「清除」按钮
- **透明度**：`Slider` + `SpinBox` 联动，范围 0–100（显示为 %）
- **填充方式**：`ComboBox`，选项：拉伸 / 覆盖 / 居中 / 平铺

交互：
- 选择图片后即时应用并保存路径。
- 透明度/填充方式即时应用。
- 清除后恢复默认 Fluent 背景（重新启用 Mica）。

## 5. 实现要点

### 5.1 `MainWindow` 绘制

- 加载 `QPixmap(path)`，缓存避免每帧 IO。
- 路径为空或加载失败 → 调用基类 `paintEvent`，保持默认外观。
- 路径有效时：
  1. 若尚未关闭 Mica，调用 `self.setMicaEffectEnabled(False)`。
  2. 在 `paintEvent` 中先按 `background_fill_mode` 将图片绘制到整个 `self.rect()`。
  3. 设置 `QPainter` 透明度（`painter.setOpacity`）后再 drawPixmap，或先用 `QImage`/`QPainter::CompositionMode` 混合。
  4. 调用基类 `paintEvent` 让 FluentWindow 的正常背景层（半透明遮罩等）覆盖其上，确保文字可读性。

### 5.2 填充方式

- `stretch`：直接 `drawPixmap(targetRect, pixmap)` 拉伸。
- `cover`：按宽高比缩放，居中裁剪，保持图片比例填满窗口。
- `center`：原尺寸居中绘制，超出部分不显示。
- `tile`：按原尺寸平铺。

### 5.3 失败与边界

- 图片加载失败 → 记录 warning，回退默认背景，UI 不崩溃。
- 窗口 resize → 重新按当前尺寸计算绘制（不重读文件）。
- 主题切换 → 背景图应保持；透明度/填充方式不变。
- 多显示器/DPI 变化 → `QPixmap.scaled` 已处理。

## 6. i18n

新增翻译键：

- `背景图片`
- `浏览…`
- `清除`
- `透明度`
- `填充方式`
- `拉伸`
- `覆盖`
- `居中`
- `平铺`

zh_CN.json 无需加入（源文本为中文，按现有约定）。

## 7. 测试计划

委托 subagent 执行：

- `tests/test_main_window.py`（如不存在则新建）：
  - 设置有效图片路径 → `paintEvent` 不抛异常、Mica 被关闭。
  - 清空图片路径 → Mica 恢复启用。
  - 无效路径 → 回退默认、不崩溃。
- `tests/test_settings_page.py`：
  - 浏览/清除按钮存在，修改透明度/填充方式后配置正确写入。
- 全量回归：确保现有 296 项测试仍通过。

## 8. 明确不做（YAGNI）

- 不支持每页独立背景。
- 不支持背景模糊/滤镜（仅透明度）。
- 不支持 GIF/视频背景。
- 不支持网络图片 URL。
- 不做壁纸在线下载或预设图库。
