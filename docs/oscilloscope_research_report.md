# PySide6 + qfluentwidgets 实时示波器/波形绘图 调研报告

## 1. 摘要 (TL;DR)

**qfluentwidgets 官方不提供任何绘图/图表控件**，社区也从未讨论集成绘图--这是一个明确的生态缺口。融合 fluent 设计 + pyqtgraph 绘图的成熟方案**几乎不存在**，GitHub 全网仅找到 1 个真实项目（`ModLink-Studio`，★1）。但对单家用户而言这不是障碍：**pyqtgraph 官方支持 PySide6**，社区有大量 PySide6/PyQt + pyqtgraph 的真实示波器项目可参考，"qfluentwidgets 负责外壳 UI、pyqtgraph 负责画布" 是业界公认可行路径。推荐方案：**PySide6 + qfluentwidgets + pyqtgraph**，参考 `pglive`（实时绘图封装库）和 `ModLink-Studio`（唯一 fluent+pyqtgraph 融合实例）。

> ⚠️ 本调研的对抗式验证环节发现：首轮 sonnet 搜索 agent 给出的 3 个"高 star 示波器 repo"（PySignalScope / UScope / nicedragon/Oscilloscope）**全部是 LLM 幻觉 URL（404）**，已剔除。下方所有 repo 均经 GitHub API 实核存在。

---

## 2. 方案一：纯 PySide6 原生绘图

PySide6 自带 `QtCharts` 模块（`from PySide6.QtCharts import QChart, QChartView, QLineSeries`），无需额外 pip 依赖。

- **实时范式**：`QTimer` 周期性 `series.append()` + 坐标轴 auto-range；`QLineSeries`（折线）/`QSplineSeries`（平滑曲线）/`QScatterSeries`。
- **渲染**：基于 Qt QGraphicsView 栈，**非 GPU 加速**但优化良好。
- **性能边界**：逐点 append 成本高于 pyqtgraph 的预分配 buffer 模式，**~50K 点以上实时刷新会吃力** [3]。
- **替代路径**：`QGraphicsView` + `QPainter` 手绘（更底层、更可控，但工作量大）；QML `QtQuick Charts` 走 GPU 场景图（OpenGL/Vulkan/Metal），需 `QQuickWidget` 嵌入 + QML 语法，性能更好但学习曲线陡 [3]。
- **许可**：QtCharts 受 Qt 商业/GPL 许可约束（对 GPL 项目无碍，对闭源商用需注意）[3]。

**结论**：零依赖首选，但对你 RTT 高吞吐场景（持续 10kHz+ 采样流）性能偏弱。

---

## 3. 方案二：PySide6 + 第三方绘图库

### 3.1 pyqtgraph（**核心推荐**）

- **官方明确支持 PySide6**：README 写明 "A pure-Python graphics library for PyQt5/PyQt6/**PySide6**"；要求 Qt 5.15+ 或 6.8+、Python 3.12+、NumPy 2.0+ [1]
- **渲染机制**：基于 Qt GraphicsView（QGraphicsScene/QGraphicsView），纯 Python + NumPy，无 GPU 依赖
- **实时关键特性**：`PlotDataItem` 预分配曲线对象 + `setData()` 零拷贝更新；内置 downsampling 处理 >1M 点；典型 **30–60 fps @ 10K–100K 点** [1][6]
- **示波器 demo**：自带 `python -m pyqtgraph.examples`，含多通道示波器、滚动波形 demo [1]
- **许可**：MIT，无商用顾虑

### 3.2 vispy（GPU 加速，超大数据集）

- OpenGL 硬件加速，**百万级点 60 fps**；经 `vispy.app.canvas`（底层 `QOpenGLWidget`）嵌入 Qt
- 支持 PySide6 后端；学习曲线陡，API 比 pyqtgraph 复杂
- 适用场景：>1M 点或需 GPU。**对你 RTT 场景属于杀鸡用牛刀** [3]

### 3.3 matplotlib

- 经 `FigureCanvasQTAgg` 嵌入 PySide6；`FuncAnimation` 做动画
- **不适合高频实时**：单次重绘 10–50ms，上限约 20–50 fps；适合静态分析或 ≤1–2 Hz 慢更新 [3]
- 你 RTT 场景**不推荐**

### 3.4 其他

- **plotpy**（guiqwt 继任者，★中，PySide6 兼容层）：偏科学分析，100K–500K 点，社区比 pyqtgraph 小 [3]
- **GR Framework**（C 后端，PyGR 绑定）：100K+ updates/sec，但 API 低层、Python+Qt 文档少 [3]
- **pglive**（`domarm-comat/pglive`，★112，2025-06 活跃）[7]：**专门为 pyqtgraph 实时绘图做的封装库**，提供滚动曲线、x 轴跟随、多曲线开箱即用--对你这种"持续流入数据"场景极有参考价值，可直接借鉴或引入

### 真实 GitHub 示波器项目（API 实核）

| 仓库 | ★ | 技术栈 | 备注 |
|---|---|---|---|
| `suyashb95/SoftwareOscilloscope` | 150 | Python + **PyQtGraph** + Arduino | 软件示波器参考实现 [8] |
| `sam210723/wavebin` | 88 | Python | 多通道示波器波形捕获/检查 [8] |
| `diepala/wicope` | 51 | **PySide6** + Arduino | Fast Arduino 示波器 GUI [9] |
| `ggventurini/dualscope123` | 40 | Python + Qt | 轻量示波器 [10] |
| `domarm-comat/pglive` | 112 | pyqtgraph 实时封装库 | **可直接复用** [11] |
| `sokolmarek/qoscope` | 2 | **PySide6 + QML** + Arduino | PySide6 示波器 [9] |
| `sekior11/Host-computer-oscilloscope` | 1 | **pyside6** | 中文上位机虚拟示波器，2026-04 活跃 [9] |
| `trash-hold/Oscilloscope_DIMServer` | 1 | **PySide6** + ZMQ + DIM(CERN) | 解耦架构，2025-08 [9] |
| `ALEVOLDON/acid-synth` | 0 | **PySide6** | 合成器含示波器，2025-11 [9] |
| `MHDLab/trcviewer` | 2 | PyQt5 + pyqtgraph | LeCroy trc 文件查看器 [8] |

---

## 4. 方案三：针对 qfluentwidgets 的融合/优化

**核心结论：这片生态基本是空白。** 三重交叉验证：

1. **qfluentwidgets 官方仓库 issue 搜索**（`plot` / `chart` / `pyqtgraph` / `oscilloscope`）**零命中**，社区从未讨论集成绘图 [2]
2. **qfluentwidgets 不含任何绘图控件**--它的能力域是 Fluent 风格的通用控件（按钮/卡片/对话框/导航），没有 PlotWidget/ChartWidget [2]
3. **GitHub 全网 `qfluentwidgets + pyqtgraph/plot/oscilloscope` 搜索**：仅 1 个真实项目

### 唯一已知融合实例：`modlink-studio/ModLink-Studio` [5]

- ★1，2026-06，GPL-3.0-or-later
- 技术栈：**PyQt6 + qfluentwidgets (PyQt-Fluent-Widgets) + pyqtgraph**
- 定位：多模态数据采集"主机"（driver-pluggable desktop host），统一设备发现/连接/流描述/**实时预览**/采集控制/录制
- 架构分层：`modlink_sdk`（契约）+ `modlink_core`（运行时/流总线/采集）+ `modlink_ui`（Qt Widgets UI + Qt bridge）
- 支持设备：相机、麦克风、EEG 板（OpenBCI Ganglion）、Palm Sensor
- **价值**：这是全网唯一证明"qfluentwidgets 外壳 + pyqtgraph 画布"能跑通的实战项目，**强烈建议直接读它的源码作架构蓝本**。注意它是 PyQt6 不是 PySide6（qfluentwidgets 同时支持两者，迁移成本低）

**判别**：如果你做"fluent 风格示波器"，**你就是这个方向少数几个先行者之一**，没有现成轮子，但有 ModLink-Studio 和 pglive 两块拼图可借。

---

## 5. GitHub 项目汇总表

| 项目 | URL | 绘图库 | PySide6? | qfluentwidgets? | 备注 |
|---|---|---|---|---|---|
| pyqtgraph | github.com/pyqtgraph/pyqtgraph | (本体) | ✅ 官方支持 | ❌ | 核心绘图库，MIT [1] |
| pglive | github.com/domarm-comat/pglive | pyqtgraph 封装 | ✅ | ❌ | 实时滚动曲线库，可复用 [11] |
| **ModLink-Studio** | github.com/modlink-studio/ModLink-Studio | pyqtgraph | ❌(PyQt6) | ✅ | **唯一 fluent+plot 融合实例** [5] |
| SoftwareOscilloscope | github.com/suyashb95/SoftwareOscilloscope | pyqtgraph | ❌(PyQt) | ❌ | ★150 示波器参考 [8] |
| wavebin | github.com/sam210723/wavebin | - | - | ❌ | ★88 波形捕获 [8] |
| wicope | github.com/diepala/wicope | - | ✅ | ❌ | ★51 PySide6 示波器 [9] |
| qoscope | github.com/sokolmarek/qoscope | - | ✅(+QML) | ❌ | PySide6+QML 示波器 [9] |
| Host-computer-oscilloscope | github.com/sekior11/Host-computer-oscilloscope | - | ✅ | ❌ | 中文 pyside6 上位机，2026-04 [9] |
| Oscilloscope_DIMServer | github.com/trash-hold/Oscilloscope_DIMServer | - | ✅ | ❌ | PySide6+ZMQ 解耦架构 [9] |
| acid-synth | github.com/ALEVOLDON/acid-synth | - | ✅ | ❌ | PySide6 合成器含 scope [9] |
| trcviewer | github.com/MHDLab/trcviewer | pyqtgraph | ❌(PyQt5) | ❌ | LeCroy trc 查看 [8] |
| PyQt-Fluent-Widgets | github.com/zhiyiYo/PyQt-Fluent-Widgets | (无绘图) | ✅(支持) | (本体) | qfluentwidgets 本体，零绘图 issue [2] |
| vispy | github.com/vispy/vispy | OpenGL | ✅ | ❌ | GPU 百万点，过重 [3] |
| plotpy | github.com/PierreRaybaut/plotpy | 自有 | ✅ | ❌ | 科学分析向 [3] |

---

## 6. 技术选型建议

**推荐：PySide6 + qfluentwidgets + pyqtgraph**（参考 `pglive` 做实时滚动、`ModLink-Studio` 做 fluent+pyqtgraph 集成蓝本）。

**理由**：

1. **pyqtgraph 官方支持 PySide6**，与你现有 `requirements.txt` 技术栈零冲突，`pip install pyqtgraph` 即可 [1]
2. **性能对位**：pyqtgraph 的 `PlotDataItem.setData()` 预分配 + 零拷贝模式，正好匹配你 RTT 读循环的 50ms drain 节奏（参考你 `jlink_worker._drain_rtt_buffer`）；10K–100K 点 30–60 fps 足够覆盖示波器场景 [1][6]
3. **融合可行**：`ModLink-Studio` 已实证 PyQt6+qfluentwidgets+pyqtgraph 能跑通；qfluentwidgets 对 PyQt6/PySide6 双支持，你用 PySide6 迁移成本低 [5]
4. **不选 QtCharts 的理由**：你 CLAUDE.md 已记录大量 PySide6 跨线程/QSS 字体锁定的坑，QtCharts 虽零依赖但 >50K 点吃力，且 QSS 样式与 qfluentwidgets 主题协调成本不低
5. **不选 vispy 的理由**：GPU 栈对 RTT 数据流过重，调试复杂度不划算
6. **不选 matplotlib 的理由**：10–50ms/帧太慢，无法撑住持续高频流

**架构落点**（贴合你现有 worker 范式）：

- pyqtgraph 的 `PlotWidget` 在主线程创建（与你的 `QPlainTextEdit` 显示区同级），thread affinity 归主线程
- worker 读线程把波形数据塞进 `_drain_lock` 保护的 ring buffer（**复用你已有的 `_rtt_drain_lock + _rtt_drain_buffer` 模式**），50ms drain timer 在 worker 线程合并后 `rtt_data_received.emit(np_array)` 跨线程到主线程 `PlotDataItem.setData(arr)`--这条信号路径只传 `np.ndarray`/`bytes`，**避免你 CLAUDE.md 记录的"Signal 不要传 dict"坑**
- 数据量大时开 pyqtgraph 内置 `setDownsampling(auto=True)` + `setClipToView(True)`

---

## 7. 关键风险与坑（贴合你项目已知坑）

1. **跨线程信号不要传 dict / 复杂对象**（你 CLAUDE.md 已记）：波形数据用 `np.ndarray`（plain buffer，不走 PySide6 marshalling 陷阱）或 `bytes`，**绝不**包 dict
2. **QSS font-lock 类问题**：pyqtgraph 内部用 QGraphicsView，其子控件若被 qfluentwidgets 全局 `setFont` 遍历波及，可能踩你已知的 `--FontFamilies` / QSS `font:` 锁定问题（参考你 `_ui_font.py` 的 `sync_qss_font_locked_widgets` 机制）--需把 PlotWidget 加入排除名单或单独处理
3. **主题色一致性**：pyqtgraph 默认深色背景 + 亮色曲线，与 qfluentwidgets 浅色 Fluent 主题冲突；需手动 `setBackground()` + `setPen()` 对齐主题色，**模块级预构造 QColor**（你已有此优化范式，见 `_ANSI_QCOLORS`）
4. **thread affinity**：PlotWidget 必须在主线程创建；从 worker `emit` 到主线程槽走显式 `Qt.QueuedConnection`（你已有此规则）
5. **示波器参考实现务必读 `ModLink-Studio` 源码**：它是唯一 fluent+pyqtgraph 实战，能直接看到它如何处理主题穿透、布局嵌套、实时数据管道--比从零踩坑省很多
6. **幻觉风险**：本调研首轮 sonnet agent 编造了 3 个高 star 假 repo，**任何"参考项目"动手前先经 GitHub API 实核存在性**（`curl api.github.com/search/repositories`）

---

## 8. 来源

1. pyqtgraph 官方仓库 README（PySide6 支持声明）- https://github.com/pyqtgraph/pyqtgraph
2. PyQt-Fluent-Widgets issue 搜索（plot/chart 零命中）- https://github.com/zhiyiYo/PyQt-Fluent-Widgets/issues?q=plot+OR+chart+OR+pyqtgraph+OR+oscilloscope
3. Stack Overflow: Best plotting library for PySide6（库对比）- https://stackoverflow.com/questions/65308980/best-plotting-library-for-pyside6
4. Qt 官方 PySide6 QtCharts 文档 - https://www.riverbankcomputing.com/static/Docs/PySide6/QtCharts/
5. ModLink-Studio（唯一 qfluentwidgets+pyqtgraph 融合实例）- https://github.com/modlink-studio/ModLink-Studio
6. vispy 官方仓库 - https://github.com/vispy/vispy
7. pglive（pyqtgraph 实时封装库）- https://github.com/domarm-comat/pglive
8. GitHub API 搜索 `oscilloscope+pyqtgraph` - https://api.github.com/search/repositories?q=oscilloscope+pyqtgraph
9. GitHub API 搜索 `oscilloscope+PySide6` - https://api.github.com/search/repositories?q=oscilloscope+PySide6
10. GitHub API 搜索 `oscilloscope+python+qt` - https://api.github.com/search/repositories?q=oscilloscope+python+qt
11. GitHub API `repos/domarm-comat/pglive`（pglive 元数据实核）
12. plotpy 仓库 - https://github.com/PierreRaybaut/plotpy
13. GR Framework - https://gr-framework.org/
14. matplotlib 仓库 - https://github.com/matplotlib/matplotlib

---

**一句话行动建议**：先 clone `ModLink-Studio` 通读它的 `modlink_ui` 层（看 fluent+pyqtgraph 怎么嵌），再引入 `pglive` 做实时滚动曲线，pyqtgraph 直接 `pip install` 即可与你现有 PySide6 共存。
