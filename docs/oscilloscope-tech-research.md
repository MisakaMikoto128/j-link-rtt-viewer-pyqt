# PySide6 示波器技术方案调研报告

> **调研日期**: 2026-07-20
> **目的**: 为 J-Link RTT Viewer 新增示波器功能选型
> **当前技术栈**: PySide6 + qfluentwidgets

---

## 1. 核心结论：推荐方案

```
qfluentwidgets (UI 容器) + pyqtgraph (绘图引擎) + QThread (数据采集)
```

- **qfluentwidgets**：提供 CardWidget 容器和主题系统
- **pyqtgraph**：嵌入 PlotWidget 作为画布，kHz 级实时绘图无压力
- **主题联动**：`isDarkTheme()` + `qconfig.themeChanged` 信号联动深色模式

---

## 2. 四大方向调研结果

### 2.1 qfluentwidgets 官方绘图支持

| 维度 | 结论 |
|------|------|
| 开源版 | **不提供**任何图表/绘图组件 |
| Pro 版 | 提供 `ChartWidget`（封装 ECharts）+ `AudiowaveformWidget` |
| Pro 版局限 | ECharts 是 Web 渲染，不适合高频实时示波器场景；且需商业授权 |

**社区实践**：用 qfluentwidgets 的 `CardWidget` / `SimpleCardWidget` 作为容器，嵌入第三方绘图组件。这是官方认可的标准做法。

**主题联动 API**：
```python
from qfluentwidgets import qconfig, isDarkTheme, themeColor

# 深色模式判断
if isDarkTheme():
    plot.setBackground('k')
else:
    plot.setBackground('w')

# 主题切换信号
qconfig.themeChanged.connect(on_theme_changed)

# 主题主色
curve_color = themeColor()  # 用于曲线主色
```

### 2.2 pyqtgraph——实时绘图的事实标准

pyqtgraph 是 Qt 生态中最专业的实时数据可视化库（GitHub 4.3k star），基于 Qt GraphicsView + NumPy + OpenGL 三重加速。

**与 PySide6 兼容性**：官方 CI 测试矩阵明确覆盖 PySide6，原生兼容。

**关键性能特性**：
- 内置降采样（Downsampling）——百万数据点不卡顿
- `setData()` 增量更新——只刷新变化的数据范围
- OpenGL 加速——可选 `pg.GraphicsLayoutWidget(enableOpenGL=True)`
- 自带示波器 demo：`python -m pyqtgraph.examples`

### 2.3 重点参考项目

#### A. 最直接参考——同为 J-Link RTT 场景

| 项目 | 地址 | 技术栈 | 亮点 |
|------|------|--------|------|
| **Ccccbj/RTT_TOOL** | https://gitee.com/baicai_code/rtt_-tool | PySide6 + pyqtgraph | **同为 J-Link RTT Viewer**，16 通道 30fps，CSV 录制，`$PLT`/`$CH`/`$LOG` 协议解析 |

#### B. 工程化最佳——线程安全实时绘图封装

| 项目 | 地址 | Star | 亮点 |
|------|------|------|------|
| **pglive** | https://github.com/domarm-comat/pglive | 111 | 线程安全的 pyqtgraph 封装，`DataConnector` 管理 update_rate/max_points，支持 PySide6 |

#### C. 场景最匹配——嵌入式调试串口绘图

| 项目 | 地址 | Star | 亮点 |
|------|------|------|------|
| **tk_uart** | https://github.com/zllzh8083/tk_uart | 1 | **性能优化到位**：可视区抽点 + 最大点数限制 + 更新节流，协议格式与 RTT 天然兼容 |
| **luisabel_serial_plotter** | https://github.com/raquenaengineering/luisabel_serial_plotter | 45 | 已上 PyPI，串口/Socket 双源，工程化成熟 |
| **PyQTGraph_Real_Time_Plotter** | https://github.com/pierreallier/PyQTGraph_Real_Time_Plotter | - | 明确 PySide6，兼容 Arduino Serial Plotter 语法 |

#### D. 高级功能参考

| 项目 | 地址 | 亮点 |
|------|------|------|
| **wavebin** | https://github.com/sam210723/wavebin | Keysight/Rigol 示波器波形查看器，OpenGL 加速 + >50k 点自动降采样 |
| **stm32-upper-computer** | https://gitee.com/byl729729/stm32-upper-computer | PySide6 + pyqtgraph + numpy，含实时 FFT 频谱分析（Hann 窗） |
| **SoftwareOscilloscope** | https://github.com/suyashb95/SoftwareOscilloscope | 149 star，多通道通用示波器，架构可借鉴 |

#### E. qfluentwidgets 内嵌波形案例

| 项目 | 地址 | 亮点 |
|------|------|------|
| **AiNiee** | https://gitee.com | PyQt5 + qfluentwidgets，`WaveformCard.py` 在 `CardWidget` 里用 QPainter 自绘波形——证明嵌入可行 |
| **infypower-tools** | https://github.com/MisakaMikoto128/infypower-tools | qfluentwidgets + QPainter，双 Y 轴实时图，有主题联动代码 |

### 2.4 其他绘图库对比

| 绘图库 | 实时性能 | PySide6 兼容 | 示波器项目数 | 结论 |
|--------|----------|-------------|-------------|------|
| **pyqtgraph** | ★★★★★ | 原生 | 大量 | **首选** |
| **matplotlib** | ★★ | 通过 `FigureCanvasQTAgg` | 极少 | >60Hz 即卡顿，高频场景放弃 |
| **QtCharts** | ★★★ | PySide6 自带 | 极少 | 点数过万不如 pyqtgraph，中低频可用 |
| **QCustomPlot** | ★★★★ | 需 pybind 绑定 | 无 Python 项目 | C++ 库，Python 生态不成熟 |
| **vispy** | ★★★★★ | 兼容 | 极少 | GPU 加速但学习成本高，过度复杂 |

---

## 3. 推荐架构设计

```
┌─────────────────────────────────────────┐
│  qfluentwidgets.CardWidget (UI 容器)     │
│  ┌───────────────────────────────────┐   │
│  │  pyqtgraph.PlotWidget (绘图画布)   │   │
│  │  - PlotCurveItem × N (曲线)       │   │
│  │  - ViewBox (坐标系统)              │   │
│  │  - TextItem (光标数值)             │   │
│  └───────────────────────────────────┘   │
│  工具栏: 开始/停止、通道选择、导出 CSV      │
└─────────────────────────────────────────┘
          ▲ pyqtSignal(data)
┌─────────────────────┐
│  DataCollectorThread │  ← QThread
│  - 从 RTT 解析数据    │
│  - 降采样/缓冲        │
│  - 线程安全 emit       │
└─────────────────────┘
```

**主题联动流程**：
1. 初始化时调用 `isDarkTheme()` 设置 pyqtgraph 背景/网格/文字颜色
2. 监听 `qconfig.themeChanged`，主题切换时重新设置绘图样式
3. 曲线颜色使用 `themeColor()` 作为默认主色

---

## 4. 实施建议

1. **先做原型验证**：用 pyqtgraph 的 `PlotWidget` 嵌入一个 CardWidget，写死 3 条正弦波测试刷新率和 CPU 占用
2. **参考 pglive 的 DataConnector 模式**：线程安全地管理数据流，避免跨线程 pyqtgraph 对象访问问题
3. **性能三步优化**（按优先级）：
   - 可视区外数据不渲染（pyqtgraph 内置）
   - 降采样（>50k 点自动触发，参考 wavebin）
   - 如有需要开启 OpenGL 加速
4. **协议层面**：参考 Ccccbj/RTT_TOOL 的 `$PLT`/`$CH` 格式，定义我们自己的 RTT 绘图数据协议
5. **先做成插件/子页面**，不侵入现有 RTT Monitor 页面

---

## 5. 风险与注意

| 风险 | 说明 | 应对 |
|------|------|------|
| pyqtgraph 与 qfluentwidgets 主题色冲突 | pyqtgraph 用自己的配色，不自动跟随 | 手动用 `isDarkTheme()` + `qconfig.themeChanged` 联动 |
| pylink 1.6.0 兼容性 | pyqtgraph 和 pylink 的 Python 版本要求 | 已验证 pylink-square==1.6.0 与 PySide6 兼容 |
| GPU 依赖 | OpenGL 加速可选，但某些 PC 可能没有 GPU 驱动 | 默认关闭 OpenGL，仅 CPU 渲染也足够 RTT 场景 |

---

*报告结束。下一步：原型验证（CardWidget + PlotWidget 正弦波测试）。*
