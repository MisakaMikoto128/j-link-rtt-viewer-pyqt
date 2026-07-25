# 启动 / 运行时 进一步可提速手段调研

调研日期：2026-07-25。dev 模式实测 venv（Python 3.13.13 + 同环境），通过 `-X importtime`
扫描 `ui.main_window` 全链路；standalone 已有基线见 `docs/packaging_startup_report.md`。

## 量级基线（dev venv 3.13 复测）

```
import qfluentwidgets                      648 ms  *
  ├─ color_dialog cumulative 依赖         ~200 ms（连带 numpy ~440ms）
  ├─ darkdetect                             ~90 ms
  └─ components 各 widget 子包合计         ~130 ms
import ui.main_window                     638 ms  *（含上面 qfluentwidgets，下为子项）
  ├─ core.jlink_worker                      58 ms  *（pulls pylink+psutil）
  │   └─ psutil                             65 ms
  ├─ ui.flash_page                          37 ms
  │   ├─ core.flash_worker                  16 ms
  │   └─ ui.firmware_analysis_view           7 ms
  ├─ ui.rtt_monitor_page                   14 ms
  ├─ ui.memory_viewer_page                  3 ms
  ├─ ui.about_page                          2 ms
  ├─ ui.pack_manager_page                   1 ms
  └─ ui.settings_page                       1 ms
(b1_pylink_lazy 实验已测：pylink 拖到首次 connect，常驻版本启动中位 1.78s vs baseline 1.79s)
```

`*` 标的是有改动空间的大头。其余几条都在 5 ms 内，已不值当。

---

## 启动方向

### 1. Page 懒构造（推迟非默认页 `__init__`）
**结论：可行（中风险 / 中收益 / 改动量较大）**

理由：` MW_inst 1968ms` 当中绝大部分是 6 个 page 构造时的 qfluentwidgets 控件实例化。
RTT 页是默认可见页，必须构造；flash / memory / pack / settings / about 5 个推到首次点击
再建，理论上能把大头约 800-1200 ms 推迟到首次切页。

但 FluentWindow 的 `addSubInterface(interface: QWidget, ...)`（见
`.venv/Lib/site-packages/qfluentwidgets/window/fluent_window.py:276`）要求 widget 已
建好、有 objectName、且把它装进 stackedWidget。**懒 import 子模块**收益有限——测得
`ui.flash_page` 模块顶部 import 仅 5ms（推到懒也只省这点），*真正的大头是构造控件*。
所以要走「占位 QWidget + 首次点击事件 swap」：先塞一个空 QWidget（仅有 objectName），
连导航点击信号，第一次访问时新建真实 page 并替换 stackedWidget 第 N 项、刷新路由表。
风险点：(a) stackedWidget 索引 / qrouter 路由表更新、(b) `flash_page._rtt_page_ref`
+ `pack_page.packs_changed -> flash_page._on_packs_changed` 的跨页信号在主窗口构造期
就要连——懒构造后需要先连到「占位 holder」上的转发信号或在 swap 之后再连接、(c)
closeEvent 里 `flash_page.shutdown()/pack_page.shutdown()` 在懒且未访问过时 None 进兜
异常路径要处理、(d) 收益在 standalone 下被 Nuitka 部分摊薄（编译期已固化控件构造）。

**估计收益**：dev ~500-900 ms（最大单项）；standalone ~150-400 ms（Nuitka 已优化控件
构造，但 Qt 实例化仍占相当份额）。**未实测**。

**落地动手点**：
- `src/ui/main_window.py:32-37`，page 模块顶部 import 改后置（在 `_ensure_page(name)`
  lazy helper 内 `import ui.flash_page as _fp; return _fp.FlashPage(...)`）。
- `src/ui/main_window.py:62-67`，把 5 个非默认 page 实例化改为占位 + holder。
- 改 `addSubInterface` 调用（`src/ui/main_window.py:74-80`），传占位 widget。
- 新增 `MainWindow._ensure_page(route_key) -> None`，在
  `navigationInterface` 的「item clicked」信号里首次进入时 swap。
- 跨页信号 `pack_page.packs_changed` 改成在 `_ensure_page("flash")` swap 后再连。
- `shutdown` 两处加 NoneGuard。

涉及：`src/ui/main_window.py`（主改，~80-120 行新增/改）。**风险：中**——swap 路径
多个状态机分支要测；建议配真实硬件验证切页 + 关机时序 + 信号断开后重连。

---

### 2. qfluentwidgets 延迟 import 子模块
**结论：不推荐（嵌套 __init__ 强制子模块导入，实测几乎无收益）**

理由：实测 `from qfluentwidgets import FluentIcon` 触发 `qfluentwidgets/__init__.py`
的 `from .components import * / from .common import * …` 数百毫秒；即使 `from
qfluentwidgets.common.config import isDarkTheme` 也要先跑父包 `__init__`。
**改用直接 `from qfluentwidgets.common.icon import FluentIcon` 也救不了**——父包
`__init__` 必跑，连带 color_dialog / numpy 一并拖入（实测 457ms vs 完全版本 431ms，
差异在噪声内）。所以「页面顶部 import 改方法内 import」这条路在该库结构下无效。

唯一能省的是某个 page 专有的 widget 子模块不在 qfluentwidgets 默认 `__init__` 里
的——但 `components/widgets/__init__.py` 几乎 `from .xxx import *` 全量拉入，无可省。
settings_page 已用的 lazy `ColorDialog` 延迟到点击触发（`src/ui/settings_page.py:555,
572`），但 color_dialog 这个**模块**本身因为 qfluentwidgets 父 `__init__` 早就被加载，
没救到。**结论：因为 color_dialog 是被父 `__init__` 拖入的而非 settings_page 拖入，
settings_page 改 lazy 不省 qfluentwidgets 任何开销（仅省点击触发时的 ColorDialog 实例化
CPU，省不到启动 import）**。

**估计收益**：~0 ms（dev 实测）。

落地：不改。

---

### 3. darkdetect / psutil / numpy 拖累
**结论：已知无效（numpy/darkdetect 经 qfluentwidgets 强制 import）+ 部分可行（psutil
可救）**

理由（numpy / darkdetect）：实测 qfluentwidgets 父 `__init__` 必经
`from .common import *` → `common/__init__.py` 加载 `.icon` `.style_sheet`
`.translator` `.smooth_scroll` …，**只要这些子模块之一被父 `__init__` 走过就拉
`.config`、拉 `darkdetect`**（90 ms）；`qfluentwidgets/__init__.py` 的 `from
.components import *` 又必拉 `components/dialog_box/__init__.py` → `color_dialog`
（200 ms），color_dialog 自身拉 numpy（440 ms dev，standalone 由于 numpy C 扩展已编
进 exe、import 体感较小但仍存在）。**只要用 FluentWindow 就逃不掉父 `__init__`。**

理论「shim sys.modules 拦 color_dialog」实测会立即 `ModuleNotFoundError`（`__init__.py`
显式 `from .color_dialog import ColorDialog`），需要装个伪造 module 才能瞒过——属「魔改
qfluentwidgets」级别，对新版本升级不稳健，**不推荐**。

理由（darkdetect）：darkdetect 只为系统主题嗅探。避免方法：固定 `Theme.LIGHT/DARK`
不走 `setTheme(Theme.AUTO)`。但 `qfluentwidgets.common.theme_listener` 仍会被
`__init__` 拖入，且 darkdetect 已在 config import 时加载，**固定主题也省不掉 darkdetect
import 本身**。只能省 `darkdetect.listener()` 启动的 PolestarRegNotify 后台线程（无关
启动 ms，仅一点点内存/CPU）。**不推荐为启动改这块**。

理由（psutil）：psutil 是 **pylink** 拉的（`core.jlink_worker.py: import pylink`）。
pylink 用 psutil 做进程列举（可能用于多 J-Link 实例检测 / DLL 路径解析）。实测 `import
pylink` 累计 258ms，其中 psutil 占 65ms。

**估计收益（仅做 pylink 推迟）**：dev ~65 ms（psutil）+ 5-15 ms（pylink 其他）。
但 pylink 在 `main.py:79` 已早期 import（用于触发 DLL 致命检测），拆这类移到 worker 线
程需要重排 DLL 检测时机——风险（DLL 致命失败不能早抛到主线程 QMessageBox）。
**已有 b1_pylink_lazy 实验**: 1.78s vs 1.79s，dev/standalone 的 median 几乎无差异。
（因 main.py 仍要在启动早期触发 pylink 触发 DLL，移不走。）

**结论**：numpy/darkdetect/psutil 拖累**已知无效**或**收益已被实测为噪声**，不动。

---

### 4. dev 模式 .pyc 预热（compileall）
**结论：已知无效（dev 二次启动无可见加速，实测噪声内）**

理由：实测 `rm -rf src/__pycache__; measure cold` vs `python -m compileall src;
measure warm`，`import ui.main_window` cumulative 590 ms vs 585 ms（差异 < 1%，在
噪声内）。Python 对 src/ 项目代码本身就是「首次触发编 .pyc，后续读」，第 2 次本身就有
.pyc。手动 compileall 仅省首次启动（开发环境 seldom cold）；Nuitka standalone 无此
问题（已编进 C）。

**估计收益**：dev 冷启动 ~50 ms（首次），warm 启动 0 ms。**不推荐**单独立项。

落地：不动。

---

### 5. FluentWindow 字体 / QSS 资源预加载
**结论：可行但收益小（定性建议 / 不深挖）**

理由：FluentWindow 构造会加载 qss (经 `FluentStyleSheet.loadStyleSheet` 读嵌入
`_rc/resource.py` 内的 qss)，无磁盘 read。字体 (`setFontFamilies`) 走
`QFontDatabase.addApplicationFont`，若用户配自定义字体会有 read。这些基本都在
`qfluentwidgets.__init__` 阶段（已经计入 648 ms 内）。预加载（启动早期 dunk 它
们）最多让 UI 构造更连贯，但 dev/standalone 都不会有可见 ms 收益。

**估计收益**：未实测，预期 < 20 ms。

落地：不动。

---

### 6. 冻结 importlib bootstrap (`-X frozen_modules=on`)
**结论：已知无效**

CPython 3.13+ 默认已开（`frozen_modules=on`）。实测在 3.13 venv 下未同比关掉重测，
但官方默认值即为 on。**无需任何动作**。

---

## 运行时方向（仅挑改动小的）

### 7. RTT 高刷新 QColor 分配审计
**结论：已做（无遗漏）**

理由：`src/ui/_rtt_colors.py` 已预构造 `ANSI_QCOLORS: dict[str, QColor]`、
`DEFAULT_FG_QCOLOR`、`DEFAULT_BG_QCOLOR` 模块级常量；`src/ui/_display_area.py:343-357`
的 `_fmt(attrs)` 注释明示「QColor 必须从预构造表查，不要 QColor(hex_string)」，且
`fmt.setForeground(ANSI_QCOLORS.get(attrs.fg, DEFAULT_FG_QCOLOR))` 已落实。
唯一遗留：`append_styled_line` (`src/ui/_display_area.py:292`) 仍 `QColor(color)`
path——这是「非 RTT 数据」染色（如发送回显、标记），低频路径（每秒 N 次，N≪RTT 流），
不在高吞吐热路径中，**不算遗漏**。

**估计收益**：0。维持现状。

落地：不动。

---

### 8. RTT drain timer（50ms）自适应
**结论：可行（低风险 / 小 CPU 收益 / 零启动收益）**

理由：`src/core/jlink_worker.py:234-237` 的 `_rtt_drain_timer` 固定 50ms 一次，调
`_drain_rtt_buffer` 把 `_rtt_drain_lock` 内累积 emit 给 UI。读线程 `_poll_interval`
100ms。**idle 时 drain 没 buffer 也每 50ms fire**，主线程 QTimer 都为空跑。
改自适应：连续 N 次空 buffer 后扩到 100-200ms，buffer 有数据时收紧到 16-25ms（接近
60fps display）。

代码量：`jlink_worker.py:234-237` 加 `_drain_idle_count` 状态，在 `_drain_rtt_buffer`
结尾按 buffer 是否为空调 `_rtt_drain_timer.setInterval()`。**worker 线程内自调自己
QTimer没问题**（thread affinity 一致）。

**估计收益**：idle CPU 降（无数据时 50ms→200ms，QTimer fire 频率 1/4）未实测体感；
**启动 +0**。改 ~10 行。**风险：低**（数据到达瞬时性 / drain timer 切回前 buffer 高位
水线时延 ≤ ~150 ms，影响 RTT 显示流畅度）——建议同时设上限保护。

落地：`src/core/jlink_worker.py:234-237`、`src/core/jlink_worker.py:714-723` 加
`_drain_idle_count` + `setInterval` 切换。

---

### 9. J-Link 枚举定时器 200ms
**结论：可行（理论判断 / 无硬件无法实测 / 中等收益）**

理由：`src/core/jlink_worker.py:252-255` 的 `_enum_timer.setInterval(200)`，每 200ms
调 `jlink.connected_emulators()`（DLL 同全局句柄串行经 `_dll_lock`）。竞态风险已被
main.py 早期 `prepare_pyocd_for_flash(background=True)` 隔离（commit `718754b`），
所以剩下纯粒度问题。每 200ms 一次 DLL call 在没硬件插入操作的时间和设备 plugged-in
感知延迟之间权衡：
- 200ms：用户插拔 J-Link 后 ≤ 200ms 发现已足 bare interaction；裸 DLL 调用 1-5 ms 占
  0.5-2.5% CPU。
- **拉到 1000ms**：CPU 降至 0.1-0.5%，插入识别延迟 ≤ 1s 对调试 UX 可接受。
- 事件驱动（WM_DEVICECHANGE / QWinEventNotifier）：理论最优，windows USB 通知 callback
  间隔不定，无 racing 收益翻倍。但实现涉及 win32 API + worker 线程兜底（USB unplug 不
  发事件的情况要 timer 配合），复杂度高。

**建议**：两阶段——
1. **先单点把 200 → 1000**（一行改 `_enum_timer.setInterval(1000)`，1 行）。如用户
   抱怨插入识别慢再退 500ms。
2. 事件驱动列为「未来」（不实施）。

`_drain_rtt_buffer` 看到的 USB 枚举结果用来刷新下拉，掉线感知靠 read thread 检
异常（不靠 enum timer），所以 enum timer 主要管「插入识别 + 列表刷新」，对掉线 semantics
零影响。退化到 1000ms 不会破坏掉线检测路径。

**估计收益**：idle CPU 频度 ↓ 5×，未实测 ms 量；**启动 0**。

落地：`src/core/jlink_worker.py:253` 改 `setInterval(1000)`。改 1 行。**风险：低**。

---

### 10. 运行时简化候选（仅列举，不深入）

扫 `src/` 选 2-3 处「明显能简化 / 反复设 styleSheet」：

(a) **`src/ui/rtt_monitor_page.py:824/1652/1684/1957/1960` 等多处
`lbl_status_state.setStyleSheet("color:#888888"/"#2ecc71")`**：状态切换热路径反复设
固定 stylesheet。可将两个颜色预存为 module-level `QColor`，调色时只改 palette 或改
`lbl_status_state.setStyleSheet(_STATE_FG_QSS_IDLE/_ACTIVE)` 常量字符串。改动 ~10 行。
收益：解除 f-string 重建（ms 量级，仅 catastrophe 类页高频时可见）。**风险：低**。

(b) **`src/ui/_display_area.py: append_styled_line` 每次调 `fmt = QTextCharFormat()`**
（line 291 / 347）：QTextCharFormat 是 Qt 大对象。可考虑 cache（按 attrs key）；但
RTT 热路径已用 `_fmt` + `ANSI_QCOLORS`，QTextCharFormat 重建仍有~10μs。频率高时可
能可观，但仅在「非 RTT 数据染色」走（line 291 `_general` path），低频。**不建议**。
仅在「QTextCharFormat 重建于真正 RTT 数据热路径」才是值得优化——但 RTT 实际走
`_fmt` + `ANSI_QCOLORS`，**RTT 数据 path 已不需再优化**。结论：**不动**。

(c) **`src/ui/_send_bar.py:202-206` 的 `setStyleSheet(orig_ss + _crc_css)`**：CRC 切
换的 stylesheet 拼接 not a hot path。**不动**。

---

## 推荐优先级（按 收益/改动量 排序）

| # | 项 | 结论 | 估计收益 | 改动量 | 优先级 |
|---|---|---|---|---|---|
| 1 | Page 懒构造 | 可行（中风险） | dev ~500-900ms；standalone ~150-400ms | ~80-120 行 | **强烈建议先做** |
| 9 | J-Link 枚举 200→1000 ms | 可行（低风险） | idle CPU ↓5× | 1 行 | **建议紧跟** |
| 8 | drain timer 自适应 | 可行（低风险） | idle CPU 略降 | ~10 行 | 建议第 3 |
| 10a | lbl_status_state QSS 常量化 | 可行（低风险） | 极小 | ~10 行 | 备选 |
| 2/3/4/5/6 | qfluentwidgets / darkdetect / numpy / psutil / compileall / frozen_mod | 已知无效或不推荐 | 0-噪声 | — | 不做 |

### 强烈建议先做：**Page 懒构造 (#1)**

这是 dev 启动节省大头唯一一档 >几百 ms 的项，且 standalone 也有 ~150-400 ms 量级。
代价是改动量中（~100 行）+ 中风险（swap 路径多个状态机分支要测）。落地动手点见
§1 末段。

### 次优建议：**J-Link 枚举 timer 200→1000 ms (#9)**

单行修改、低风险、纯运行时收益（不影响启动 ms）。建议 #1 完成后顺手做。

### 不建议碰的（实测/判断已无效）

- numpy / color_dialog 经 qfluentwidgets 父 `__init__` 强制拖入。除非 monkeypatch
  sys.modules 装假 module，否则逃不掉。pylink + psutil 因 main.py 早期 DLL 致命检测
  推不走，已 b1_pylink_lazy 实验 baseline 持平。
- dev 模式 compileall 二次启动无可见加速。
- FluentIcon / FluentWindow 等直接子模块 import 在 qfluentwidgets 当前结构下仍踩父
  `__init__.py` 的全量加载，**单点延迟 import 无收益**。

---

## 备查：实测 importtime 复测（dev venv 3.13）

```
import qfluentwidgets                       648 ms
import ui.main_window                       638 ms  (含 qfluentwidgets)
  ├─ core.jlink_worker                       58 ms
  ├─ ui.flash_page                           37 ms
  ├─ ui.rtt_monitor_page                     14 ms
  ├─ core.flash_worker                       16 ms
  └─ 其它 page 各 ≤ 3 ms

import qfluentwidgets 直接子模块（去除父 __init__ 影响几乎不可能）：
  from qfluentwidgets.common.config import isDarkTheme   471 ms
  from qfluentwidgets.common.smooth_scroll import S...   426 ms
  => 父 __init__.py 全量加载行为，无法单点延迟 import

color_dialog cumulative 354–411 ms，numpy ~440 ms（dev），darkdetect ~90 ms，
psutil ~65 ms（pylink）。
```