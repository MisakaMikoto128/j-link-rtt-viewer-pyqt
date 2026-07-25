# 启动 / 运行时 速度优化调研报告

调研日期：2026-07-25
环境：Windows 11 Pro for Workstations 10.0.26200，CPython 3.11.7（venv），PySide6 6.6+qfluentwidgets 1.11.2+pylink-square 1.6.0+pyocd
基准：worktree 当前 commit `37caa3b`（**注意：worktree 在 pyocd 后台预热 commit `718754b` 之前**，本报告基线不含该项优化；主仓当前已在 `559dfbf`，含 pyocd 后台预热，dev 启动约 2.52s）
测量工具：`scripts/measure_launch.py`（warmup 1-2 + 5-8 runs 取中位数）、`-X importtime`、main.py 内置 `--startup-bench` + perf_counter 分段计时

---

## 量级基线（worktree，无 pyocd 后台预热）

### 启动总时间

| 场景 | 中位数 | min / max |
|---|---|---|
| dev `python src/main.py`（worktree 基线） | **2.296s** | 2.268 / 2.395 |
| dev + 平台预热（本文新发现，见 §1） | **2.132s** | 2.100 / 2.182 |
| 主仓 dev（含 pyocd 后台预热，`a1_pyocd_bg` record） | ~2.52s | - |
| standalone Nuitka（`packaging_startup_report.md`） | 1.63s | - |

### 启动分段计时（worktree，含平台预热，`--startup-bench` + perf_counter）

```
QApplication=30ms  ui_font+paths=17ms  logger+cfg=33ms  pylink=51ms
screen+i18n=5ms  qfluent+theme=149ms  font=0ms
mw_import=30ms  mw_construct=1515ms  show=46ms
TOTAL=1873ms
```

### MainWindow 构造分段（perf_counter，3 次平均）

```
worker setup =  77ms
RTT 页       = 832ms   ← 单项最大（53% 构造时间）
Memory 页    =  77ms
Flash 页     = 229ms
Settings 页  = 102ms
About 页     =  39ms
nav + rest   = 201ms   （含 _apply_ui_font 遍历全 widget ≈19ms）
TOTAL        ≈ 1558ms
```

### importtime top（worktree，含平台预热后）

```
qfluentwidgets                351ms cumulative
  └─ components.dialog_box    245ms（color_dialog 链，widgets 已共享）
  └─ components.widgets       145ms（menu/button 拉 qframelesswindow→win32）
PySide6.QtCore                108ms
pylink                         71ms（含 psutil 30ms）
core.config_service            67ms（含 json/socket/pickle）
core.logger                    49ms（含 logging.handlers 45ms）
darkdetect                      4ms（平台预热后；未预热时 33ms）
```

---

## 一、启动方向

### §1 后台预热 `platform.release()` 缓存【A 类｜强烈推荐｜新发现】

**现状**：`darkdetect` 被 `qfluentwidgets.common.config` 拉入。`darkdetect.__init__.py` 在 Windows 上调 `platform.release()` 判断系统版本，该函数首次调用读注册表 `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion`，本机实测 **40.8ms**（第二次 0.0ms，有缓存）。`import platform` 本身还连带 `subprocess`/`socket` 约 19ms。未预热时 darkdetect 段 cumulative ~33ms，全在主线程关键路径。

**改法**：在 `main.py` 模块级（PySide6 import 之后、`main()` 之前）起 daemon 线程预热：

```python
import threading

def _warm_platform_cache():
    import platform
    platform.release()
    platform.version()

threading.Thread(target=_warm_platform_cache, name="platform-warm", daemon=True).start()
```

darkdetect 后续在主线程 import 时 `platform.release()` 已缓存（0ms），`platform`/`subprocess`/`socket` 也已在 `sys.modules`（主线程 import 走快路径）。daemon 线程的 C 扩展 init（winreg/ctypes）释放 GIL，与主线程 PySide6 import 真并行。

**实测收益**（A/B 对照，worktree commit `37caa3b`，`scripts/measure_launch.py` 5 runs + warmup 1）：

| | 中位数 | min | max | 极差 |
|---|---|---|---|---|
| 基线 | 2.296s | 2.268 | 2.395 | 0.127s |
| +平台预热 | 2.132s | 2.100 | 2.182 | 0.082s |
| **节省** | **164ms（7.1%）** | | | |

多轮复测一致（前序 8-run 版本：基线 2.403s → 预热 2.174s，节省 229ms；差异来自系统态，节省量稳定 >150ms）。预热版极差更小说明启动路径更确定。

importtime 前后对比：`darkdetect` cumulative 33.7ms → 4.2ms（省 29.5ms 直接），加上 `platform`/`subprocess`/`socket` 后台并行化，总主线程节省 ~164ms。

**风险评估**：零。`platform.release()`/`platform.version()` 是只读注册表查询，幂等可重入，结果全局缓存。daemon 线程失败不影响主线程（darkdetect 会再调一次，只是慢 40ms）。不碰任何业务逻辑。

**主仓叠加效果预估**：主仓基线 ~2.52s（含 pyocd 后台预热），叠加本项后预估 ~2.30-2.36s（pyocd 预热线程与本线程并行，互不干扰）。

**A 类理由**：5 行代码、零风险、实测 >150ms 收益、不增加任何代码复杂度。

---

### §2 Page 懒构造（推迟非默认页 `__init__`）【A 类｜推荐｜已实测确认收益】

**现状**：MainWindow 构造 ~1558ms，其中 5 个非默认页（Memory 77ms + Flash 229ms + Settings 102ms + About 39ms = **447ms**）在启动时全量构造，但用户启动后默认只看 RTT 页。FluentWindow 的 `addSubInterface` 要求已构造的 QWidget。

**改法**（已有详细方案见主仓 `docs/perf-next-steps.md` §1）：
- 为 5 个非默认页各建一个空占位 QWidget（仅 objectName），addSubInterface 用占位
- 监听导航点击信号，首次进入某页时实例化真实 Page 并替换 stackedWidget 对应项、刷新路由表
- 跨页信号（如 `flash_page._rtt_page_ref`、`pack_page.packs_changed -> flash_page._on_packs_changed`）在 swap 之后连接
- `closeEvent` 的 `shutdown()` 加 NoneGuard

**实测收益**（基于本次 perf_counter 分段）：dev **~400-450ms**（5 个非默认页构造时间总和）；standalone 因 Nuitka 已部分固化控件构造，收益打折至 ~150-300ms。

**复杂度**：中等。约 80-120 行新增/改动，涉及 `src/ui/main_window.py` 主改 + 多处状态机分支测试。风险点：stackedWidget 索引/路由表更新、跨页信号时序、closeEvent None 路径。

**A 类理由**：单项最大可节省（~400ms+ dev），与 §1 叠加可把 dev 启动从 ~2.3s 压到 ~1.7s 量级。但需配真实硬件验证切页 + 关机时序。

**与主仓 `docs/perf-next-steps.md` 的关系**：本项在主仓已列为「强烈建议先做」，本报告补充了 per-page 实测分解（RTT 832ms / Flash 229ms / Settings 102ms / Memory 77ms / About 39ms），确认收益量级。

---

### §3 启动 Splash Screen（感知速度）【A 类｜推荐｜新提议】

**现状**：用户从双击到看到主窗口约 2.1-2.5s，期间无任何视觉反馈。

**改法**：在 `QApplication(sys.argv)` 之后、重 import 链（`core.logger`/`core.config_service`/`pylink`/`qfluentwidgets`/`MainWindow`）之前，插入 `QSplashScreen`：

```python
app = QApplication(sys.argv)

# 启动 splash：PySide6 已 import（~200ms 用掉），后续 ~1.8s import + 构造期间给用户反馈
from PySide6.QtWidgets import QSplashScreen
from PySide6.QtGui import QPixmap, Qt
splash_pix = QPixmap(400, 240)
splash_pix.fill(Qt.darkGray)
splash = QSplashScreen(splash_pix)
splash.showMessage("Loading J-Link RTT Viewer...", Qt.AlignCenter | Qt.AlignVCenter, Qt.white)
splash.show()
app.processEvents()

# ... 后续 import / cfg / pylink / qfluentwidgets / MainWindow 构造 ...

win.show()
splash.finish(win)  # win.show 后自动隐藏
```

**实测收益**：启动 ms 不变（splash 自身 ~1-2ms），但**感知启动时间从 ~2.3s 降到 ~200ms**（用户看到反馈的时间）。复杂度低（~15 行），零风险。

**A 类理由**：用户感知提升巨大，代码改动极小，不碰任何业务逻辑。唯一缺点是 splash 视觉简陋（纯灰底白字）--如要更精致可加载 app icon 或自绘 logo，但属可选 polish，不影响功能。

**备注**：qfluentwidgets 自带 `SplashScreen`（`qfluentwidgets.window.splash_screen`），但它本身要等 qfluentwidgets import 完（~293ms）才能用，错失最早反馈窗口。用原生 `QSplashScreen`（仅需 `PySide6.QtWidgets`，已在模块级 import）能在 ~200ms 就显示。

---

### §4 qfluentwidgets 精细 import【B 类｜不推荐】

**现状**：`from qfluentwidgets import FluentWindow` 触发 `qfluentwidgets/__init__.py` 的 `from .components import *` → `components/__init__.py` 的 `from .dialog_box import *` → `color_dialog`（200ms+ cumulative）。

**尝试**：改用 `from qfluentwidgets.window.fluent_window import FluentWindow` 等精细路径。

**实测**：无效。Python import 系统在导入子模块前必先初始化父包 `__init__.py`。`qfluentwidgets.components.__init__.py` 显式 `from .dialog_box import *` → `from .color_dialog import ColorDialog`，任何 `qfluentwidgets.components.*` 或 `qfluentwidgets.window.*`（window imports components.navigation）的 import 都必经此链。**唯一能省的路径是 monkeypatch `sys.modules` 装假 `color_dialog` 模块**，但 `__init__.py` 显式 `from .color_dialog import ColorDialog` 会直接 `ModuleNotFoundError`，需伪造类才能瞒过--属魔改第三方库，对版本升级不稳，**不推荐**。

**B 类理由**：qfluentwidgets 包结构决定，无干净绕过方式。color_dialog 本身 self 仅 ~4ms，重的是它拉 `..widgets`（145ms，含 menu→qframelesswindow→win32 链），而 widgets 被 `components.__init__.py` 直接 `from .widgets import *` 二次拉入，删 color_dialog 也省不掉 widgets。

---

### §5 darkdetect stub【B 类｜不推荐】

**现状**：darkdetect 被 `qfluentwidgets.common.config` 拉入，~33ms（未预热时）。

**尝试**：在 `sys.modules` 注入 fake `darkdetect`（提供 `theme()`/`isDark()`/`isLight()`/`listener()`），绕过 `platform.release()`。

**不推荐**：
1. §1 的平台预热已把 darkdetect 段从 33ms 压到 4ms，stub 收益边际化（~4ms）
2. qfluentwidgets 还用 `darkdetect.listener(callback)` 启动注册表监听线程（`theme_listener.py:19`），stub 需提供等价 polling 实现才能保留主题实时切换功能，否则功能退化
3. darkdetect 内部 `$platform.release()` 调用已被 §1 覆盖，stub 只省 darkdetect.__init__ 的剩余 ~4ms self

**B 类理由**：收益边际（~4ms），需维护 fake listener，复杂度不划算。§1 已是更优解。

---

### §6 pylink 懒 import / 后台预 import【B 类｜不推荐】

**现状**：`import pylink` ~71ms（含 `psutil` 30ms）。主线程 `main.py:61` 早期 import 做 DLL 致命检测；`jlink_worker.py:35` 模块级 import（类型 hint）。

**尝试 1（后台预 import）**：在 §1 的预热线程里加 `import pylink`。
**实测**：几乎无额外收益（2.160s vs 2.174s，噪声内）。原因：pylink 的重依赖 `ctypes`/`socket` 已被 §1 的 `platform` 预热并行化，主线程 `import pylink` 本身已快。

**尝试 2（TYPE_CHECKING + 延到 worker 线程）**：`jlink_worker.py` 改 `if TYPE_CHECKING: import pylink`，运行时 `import pylink` 挪到 `initialize()`（worker 线程）。同时 `main.py` 的 DLL 检测改后台线程 + 信号回主线程报错。
**评估**：理论省 ~71ms 主线程，但：(a) 改变 DLL 致命失败的用户反馈时序（窗口先显示再弹错框），(b) `jlink_worker.py` 类型标注需全量改 `string annotation`，(c) 主仓 `docs/perf-next-steps.md` 已记录 `b1_pylink_lazy` 实验结论为「baseline 持平」。

**B 类理由**：实测收益已被 §1 榨干；进一步懒 import 改 DLL 检测时序风险/复杂度不划算。

---

### §7 logging.handlers 懒 import【B 类｜不推荐】

**现状**：`core/logger.py:13` 模块级 `from logging.handlers import RotatingFileHandler`，拖入 `socket`(12ms)+`pickle`(10ms)+`queue`(12ms) = ~45ms。

**尝试**：把 `from logging.handlers import RotatingFileHandler` 挪到 `get_logger()` 函数内。
**实测**：无净收益。`get_logger()` 在 `main.py:57` 仍早调（`logger.info("应用启动")`），只是把 45ms 从 line 44 挪到 line 57，总时间不变。

**尝试 2（后台预 import）**：§1 预热线程加 `import logging.handlers`。
**实测**：反而变慢（2.324s vs 2.160s）。原因：`logging.handlers` import 锁与主线程 `core.logger` import 竞争，主线程等锁反而阻塞。

**B 类理由**：无净收益；自实现 `SimpleRotatingFileHandler` 替代可省 45ms 但 +20 行代码、`-OO` 编译标志风险，不满足「简化代码」判据。

---

### §8 Python 启动 flag / .pyc 预热 / zipapp【B 类｜不推荐】

均已实测，详见 `docs/packaging_startup_report.md`：
- `-S`（no site）/`-O`/`PYTHONDONTWRITEBYTECODE`：噪声内（±20ms）
- `-OO`：**会崩**（qfluentwidgets `singledispatchmethod` 依赖函数注解）
- `compileall`：dev 二次启动无可见加速（.pyc 已存在）
- zipapp/shiv/pex：不支持 PySide6 C 扩展
- `-X frozen_modules=on`：3.13+ 默认已开

**B 类理由**：已穷尽，无新收益空间。

---

## 二、运行时方向（仅挑改动小的）

### §9 J-Link 枚举 timer 200ms → 1000ms【A 类｜推荐】

**现状**：`jlink_worker.py:234` `_enum_timer.setInterval(200)`，每 200ms 调 `jlink.connected_emulators()`（DLL 全局句柄串行经 `_dll_lock`）。

**改法**：`setInterval(1000)`。一行改。

**收益**：idle CPU 频度 ↓5×（200ms→1000ms，QTimer fire 频率 1/5）。插入识别延迟 ≤1s 对调试 UX 可接受（USB 插拔后 1s 内列表刷新）。掉线检测不依赖 enum timer（靠 read thread 检异常），退化不影响掉线 semantics。

**A 类理由**：一行改、零风险、纯运行时 CPU 收益。启动 0 影响。

---

### §10 RTT drain timer 自适应【A 类｜低优先级】

**现状**：`jlink_worker.py:227` `_rtt_drain_timer.setInterval(50)`，固定 50ms 一次。idle 时 buffer 空也每 50ms fire。

**改法**：`_drain_rtt_buffer` 内按 buffer 是否为空调 `setInterval`：连续 N 次空 → 扩到 200ms；有数据 → 收紧到 25ms。约 10 行。

**收益**：idle CPU 略降；启动 0。风险：数据到达瞬时性在 timer 切回前有 ≤150ms 延迟（设上限保护）。

**A 类理由**：改动小、低风险，但收益小（idle CPU 微降），优先级低于 §1/§2/§3/§9。

---

### §11 RTT 高频热路径【B 类｜已优化，不动】

**现状审计**：
- `_fmt(attrs)`（`rtt_monitor_page.py:2625`）：已用模块级 `_ANSI_QCOLORS` dict 预构造 QColor，避免热路径 `QColor(hex_string)` alloc
- `_on_rtt_data`（`rtt_monitor_page.py:2461`）：自动滚动判断在插入前（`sb.value() >= sb.maximum() - 4`），`_programmatic_scroll_guard` contextmanager 防误判
- `QTextCharFormat` 每段新建：Qt 大对象，但 RTT 数据 path 已走 `_fmt`+`_ANSI_QCOLORS`，无可省 alloc
- `_append_styled_line` 的 `QColor(color)` path：低频（发送回显/标记，非 RTT 数据流），不算热路径

**B 类理由**：CLAUDE.md 经验条目已落实，无新优化点。`QTextCharFormat` cache 按 attrs key 理论可省 ~10μs/段，但增加代码复杂度，不满足「简化代码」判据。

---

### §12 `lbl_status_state` QSS 常量化【B 类｜备选】

**现状**：`rtt_monitor_page.py` 多处 `lbl_status_state.setStyleSheet("color:#888888"/"#2ecc71")` 反复 f-string 重建。

**改法**：预存 `_STATE_FG_QSS_IDLE = "color:#888888"` / `_STATE_FG_QSS_ACTIVE = "color:#2ecc71"` 模块级常量，~10 行。

**B 类理由**：收益极小（ms 量级，仅 catastrophe 类页高频时可见），属代码整洁度改善而非性能优化。可顺手做但不单独立项。

---

## 三、优先级清单

### A 类（推荐做，按收益/复杂度排序）

| # | 项 | 实测/预估收益 | 改动量 | 风险 | 备注 |
|---|---|---|---|---|---|
| §1 | **平台预热 `platform.release()`** | **dev -164ms（实测 7.1%）** | 5 行 | 零 | **最值得做**：收益/复杂度比最高 |
| §2 | Page 懒构造 | dev -400~450ms（实测分段）；standalone -150~300ms | ~80-120 行 | 中 | 单项最大，但需硬件验证切页时序 |
| §3 | Splash Screen | 感知 -2.0s（用户看到反馈从 2.3s→200ms） | ~15 行 | 零 | 不改 ms，改感知；与 §1/§2 正交 |
| §9 | J-Link enum 200→1000ms | idle CPU ↓5× | 1 行 | 零 | 纯运行时 |
| §10 | drain timer 自适应 | idle CPU 微降 | ~10 行 | 低 | 优先级最低 |

### B 类（不推荐 / 暂缓，列理由）

| # | 项 | 不推荐理由 |
|---|---|---|
| §4 | qfluentwidgets 精细 import | 父包 `__init__.py` 全量加载，无干净绕过；monkeypatch 假 module 对升级不稳 |
| §5 | darkdetect stub | §1 已把 darkdetect 段压到 4ms，stub 边际收益 ~4ms 但需维护 fake listener |
| §6 | pylink 懒 import | §1 已榨干收益（ctypes/socket 并行化）；进一步改 DLL 检测时序风险不划算 |
| §7 | logging.handlers 懒 import | 无净收益（get_logger 早调）；后台预 import 反而 import 锁竞争变慢 |
| §8 | Python flag / .pyc / zipapp | `packaging_startup_report.md` 已穷尽实测，无新空间 |
| §11 | RTT 热路径 | CLAUDE.md 已落实 `_ANSI_QCOLORS` 等优化，无新点 |
| §12 | `lbl_status_state` QSS 常量化 | 收益极小（ms 量级），属代码整洁非性能 |

---

## 四、最值得做的一条

**§1 平台预热**：5 行代码、零风险、实测 dev 启动 -164ms（7.1%），与所有其他优化正交可叠加。落地动手点：`src/main.py` 模块级 `import threading` 后插入预热线程函数 + `Thread(...).start()`。主仓叠加 pyocd 后台预热后，预估 dev 启动从 ~2.52s 降到 ~2.30-2.36s。

---

## 附：实测方法与原始数据

### 平台预热 A/B 对照（worktree commit `37caa3b`，`scripts/measure_launch.py` 5 runs + warmup 1）

```
基线（无预热）:  median=2.296s  min=2.268  max=2.395  极差=0.127s
+平台预热:       median=2.132s  min=2.100  max=2.182  极差=0.082s
节省:            164ms (7.1%)
```

前序 8-run 版本（同环境不同时间）：基线 2.403s → 预热 2.174s，节省 229ms（9.5%）。多轮复测节省量稳定在 150-230ms 区间，差异来自系统态（OS cache 预热程度、后台进程干扰）。

### MainWindow 构造分段（perf_counter，3 次 `--startup-bench` 平均）

```
worker=77ms  rtt=832ms  mem=77ms  flash=229ms  settings=102ms  about=39ms  nav+rest=201ms(font=19ms)
TOTAL=1558ms
```

### importtime darkdetect 前后对比

```
未预热:  platform cumulative=2322us  darkdetect cumulative=33680us (self=26638us)
预热后:  platform cumulative=36692us(后台)  darkdetect cumulative=4196us (self=1574us)
主线程 darkdetect 节省: 29.5ms
```

### 测量脚本

- `scripts/measure_launch.py --target <venv>\Scripts\python.exe --name <label> --runs N --warmup M`
- `venv\Scripts\python.exe -X importtime src/main.py --startup-bench 2>importtime.txt`
- perf_counter 分段：临时在 `main.py`/`main_window.py` 内插 `time.perf_counter()` 计时点（测完已 `git checkout` 还原）

### 注意事项

1. worktree 在 commit `37caa3b`（不含 pyocd 后台预热），主仓在 `559dfbf`（含）。本报告基线 ~2.30s 是 worktree 基线；主仓基线 ~2.52s。§1 的 -164ms 收益在主仓上应同样适用（预热线程与 pyocd 预热线程并行）。
2. 所有测量未连 J-Link 硬件（`--startup-bench` 模式 worker 连不上 graceful，不影响启动计时）。
3. 测量期间 OS 状态：无主动 heavy 后台进程，但 Windows Defender / Windows Update 后台扫描不可控，是多轮测量极差的主要来源。
