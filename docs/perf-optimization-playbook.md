# Python 桌面应用启动 / 运行时性能优化 Playbook

> 通用方法论，与具体项目无关。任何 Python GUI / CLI / 服务应用都可借鉴。
> 末尾附本项目（PySide6 + qfluentwidgets + pylink + pyocd，Nuitka 打包）实测效果作示例。
>
> **⚠️ AI 测试可能不准确**：所有标「实测」的数字由 AI 在特定环境（Windows 11、CPython 3.11.7/3.13、特定硬件）测得，受 OS cache / 后台进程 / 测量方法影响，**复测可能不同**。优化前必须自己在目标环境 A/B 对照验证，不要直接套用数字。

---

## 一、核心原则

1. **先测后优**：用 `-X importtime` / `perf_counter` 分段 / `py-spy` 找瓶颈，不要猜。80% 的启动时间通常在 20% 的代码里。
2. **A/B 对照**：每改一项，warmup 1 + N 次取中位数对比，min/max 极差要小于收益才算有效。单次测量不可信。
3. **感知 vs 实测**：splash screen / 渐进显示不改 ms，改用户感知；适合「实测已到地板但用户仍觉得慢」的场景。但本项目实测 splash 反而 +1.36s（见 §3.4），不一定有效。
4. **简化优先**：「复杂度高 + 收益边际」的不做；「能简化代码又提速」的优先做。
5. **零风险优先**：后台预热 / 懒构造这类不改业务逻辑的先做；改时序 / 状态机的后做并配真机验证。

---

## 二、启动优化方法

### §2.1 import 后台预热【高频通用】

**问题**：某些模块 import 时有副作用（读注册表 / 读文件 / 扫描 plugin），首次 import 几十~几百 ms，全在主线程阻塞。

**方法**：在主线程早期起 daemon `threading.Thread` 跑该 import，与主线程后续工作并行；主线程后续真正 import 时走 `sys.modules` 缓存（0ms）。

```python
import threading

def _warm():
    import some_heavy_module  # 副作用在子线程完成
threading.Thread(target=_warm, daemon=True).start()
```

**适用条件**：
- 模块 import 副作用是只读查询（注册表 / 文件 / 环境变量），幂等可重入
- 副作用不依赖主线程状态（不碰 GUI、不依赖 QApplication 已创建）
- 子线程 import 不会与主线程 import 竞争 import lock（不同模块链）

**不适用**：
- 副作用是建 GUI 对象（必须主线程）
- 副作用与主线程 import 链共享模块（import lock 竞争反而变慢）

### §2.2 平台缓存预热【Windows 专用】

**问题**：`platform.release()` / `platform.version()` 在 Windows 首调读注册表 `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion`，~40ms。`darkdetect` / 某些主题库会调它。

**方法**：daemon 线程预调 `platform.release()` + `platform.version()`，结果全局缓存，后续主线程调 0ms。连带 `import platform` / `subprocess` / `socket` 也并行化。

```python
def _warm_platform():
    import platform
    platform.release()
    platform.version()
threading.Thread(target=_warm_platform, daemon=True).start()
```

### §2.3 Page / Tab 懒构造【GUI 通用】

**问题**：多页应用启动时全量构造所有页，但用户默认只看第一页。每页构造含 qfluentwidgets / Qt 控件实例化，几十~几百 ms / 页。

**方法**：默认页立即建（快捷键 / 跨页依赖需要），其余页用 Wrapper 推迟到首次 `showEvent` 才 import + 构造。

```python
class _LazyPageWrapper(QWidget):
    built = Signal()  # 首次构建完成，用于补应用全局状态（字体/主题等）

    def __init__(self, object_name, factory, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)  # nav 路由用，构造时设好
        self._factory = factory
        self._page = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._layout = layout

    def page(self):
        if self._page is None:
            self._page = self._factory()
            self._layout.addWidget(self._page)
            self.built.emit()
        return self._page

    def showEvent(self, event):
        self.page()  # 首次 show 触发构建
        super().showEvent(event)

    def shutdown(self):
        if self._page is not None and hasattr(self._page, "shutdown"):
            self._page.shutdown()  # NoneGuard
```

**关键点**：
- `objectName` 在构造时设好，nav 路由不依赖真实 Page 构建时机
- `built` Signal 用于补应用全局状态（启动时遍历 `allWidgets()` 漏掉未构建懒页的 widget，懒页构建后必须补一次 setFont / 主题应用）
- 跨页依赖（page A 持 page B 引用、page A 信号 -> page B 槽）用工厂方法 + dirty 中转：
  - page A 工厂内设 `A._ref = B`（B 是已立即建的默认页）
  - page B 信号连到 main_window 中转槽，槽内 `if A.is_built: A.page().handle() else: dirty=True`；A 工厂内 `if dirty: A.page().handle(); dirty=False`
- `closeEvent` 的 `shutdown()` 加 NoneGuard

**代价**：用户首次切到懒页有 ~40-230ms 构造延迟（page 构造时间不变，只是挪到首次点击）。

### §2.4 wait 兜底移到真正使用处【有并发约束的场景】

**问题**：启动时为了防并发竞态，必须等某个预热完成才能启动 worker。但 wait 在启动关键路径阻塞。

**方法**：分析 wait 的真正约束--如果 worker A 不碰该资源，wait 移到真正碰该资源的 worker B 启动前。worker A 启动不等，启动快；worker B 启动（用户操作触发，通常远晚于启动）时 wait 兜底，此时预热早完成，wait 立即返回。

**适用条件**：
- 资源有并发安全约束（如 C 扩展 DLL 全局句柄不线程安全）
- 启动时预热仍需早期跑（防竞态），但 wait 可以推迟
- worker B 启动是用户操作触发（不是启动即跑）

**风险**：极端情况（启动后立即触发 worker B）wait 阻塞 ~预热剩余时间。需评估是否可接受。

> **⚠️ 本项目踩坑（已回退）**：曾把 pyocd 预热的 wait 从「RTT worker 启动前」移到「FlashWorker 启动前」，误判「RTT worker 不碰 pyocd，不需等」。**实测崩**：`0xc000001d` + `access violation reading 0x...`。
>
> **错在哪**：pyocd 预热**线程本身**在 `import pyocd.probe.aggregator` 扫描时调 `JLinkProbePlugin.should_load -> _get_jlink -> pylink.JLink()`，碰 JLinkARM DLL 全局句柄。RTT worker 启动后跑 `connected_emulators()` 也碰同一 DLL。wait 移走后预热线程扫描与 RTT worker 的 `connected_emulators()` 并发 -> DLL 全局句柄竞态 -> 崩。
>
> **教训**：wait 移真正使用处前，必须确认**所有碰该资源的 worker**（包括预热线程本身的副作用）都在该 wait 之后启动。C 扩展 DLL 全局句柄场景，预热线程的 import 副作用（plugin 扫描建对象）也会碰 DLL，与任何已启动 worker 并发。**这类场景 wait 必须在所有 worker 启动前，不能移**。本项目已回退（commit `37bda17`），启动回到 1.915s。

### §2.5 Python 版本升级【零代码改动】

**方法**：CPython 每个版本都有启动 / import 优化。实测对比（用 `uv venv --python <ver>` 建多版本）。

**通用经验**（本项目实测，你的可能不同）：
- 3.11 -> 3.13：启动 -14%、import -18%（3.13 faster startup CPython 进展）
- 3.13 -> 3.14：噪声内（无额外收益）
- 3.14 -> 3.15 alpha：噪声内（PEP 810 lazy import 需改源码显式开启，默认无收益）
- PyPy：C 扩展重依赖（PySide6 / shiboken6）无 wheel，不可用
- free-threaded（cp314t）：PySide6 wheel 是 abi3 不带 t tag，uv 严格匹配拒绝

**注意**：升级前跑全量测试套验证兼容（C 扩展 wheel 可用性是主要风险）。

### §2.6 打包优化【Nuitka 专用，但思路通用】

| 方法 | 收益 | 风险 |
|---|---|---|
| `--standalone`（多文件） | import 编进二进制，启动最快 | 体积大 |
| `--onefile` + `--onefile-tempdir-spec={CACHE_DIR}\...` | 单文件便携 | 冷启动解压慢，缓存命中后接近 standalone |
| `--onefile-no-compression` | 缓存命中再快 ~0.15s | exe 体积 33M->116M，通常不值 |
| `--lto=yes` | LTCG 收益 | 构建慢 |
| `--nofollow-import-to=*.tests,setuptools,pip,...` | 减少扫描量 + 体积 | 零 |
| `--python-flag=no_site` | 跳过 site 启动 | 收益噪声内但零风险 |
| 删冗余第三方 DLL（Qt6Pdf/Multimedia/Qml/Quick） | 体积 -10% | 启动无可见影响；**小心间接依赖**（Qt6Svg 被 IconEngine 依赖，删了 ImportError） |
| `--msvc=latest` | 无 | Nuitka 自动探测已够 |
| `--show-progress`（onefile） | 无 | **坑**：非 TTY 调用下喷 4000 行撑爆管道缓冲，Nuitka 被 OS 杀，**别加** |

**通用经验**：
- standalone 启动 < onefile 缓存 < onefile 冷启动
- onefile 必须配持久缓存（`{CACHE_DIR}`），否则冷启动每次解压
- `-OO` 对依赖函数注解的库（如 singledispatchmethod）会崩，慎用

---

## 三、不推荐 / 慎用的方法

### §3.1 精细 import 第三方库

`from pkg.sub import X` 不能绕过 `pkg/__init__.py` 的全量加载。Python import 系统必先初始化父包 `__init__.py`。若父包 `__init__.py` 显式 `from .heavy import *`，任何子模块 import 都必经此链。唯一绕过是 monkeypatch `sys.modules` 装假模块，对版本升级不稳，**不推荐**。

### §3.2 stub 重依赖模块

在 `sys.modules` 注入 fake 模块（如 darkdetect）绕过其副作用。如果该模块有运行时回调（如 darkdetect.listener 主题监听线程），stub 需提供等价实现，否则功能退化。且后台预热（§2.1/§2.2）通常已把副作用压到接近 0，stub 收益边际化。**不推荐**，除非副作用无法后台预热。

### §3.3 后台预 import 与主线程 import lock 竞争

某些模块（如 `logging.handlers`）后台预 import 反而变慢：子线程 import 持 import lock，主线程同时 import 别的模块要等锁。**先实测**，无净收益就放弃。

### §3.4 Splash Screen

`QSplashScreen.show() + app.processEvents()` 理论 1-2ms，但**本项目实测 +1.36s**：提前触发 Qt GUI 子系统完整初始化（QFontDatabase / 渲染管线），与主线程后续 import 竞争 CPU。**先实测**，不一定有效。如果要用，考虑最轻实现（不 processEvents、纯 show）或推迟到首个 import 完成后。

### §3.5 Python flag / .pyc / zipapp

- `-S`（no site）/`-O`/`PYTHONDONTWRITEBYTECODE`：噪声内（±20ms）
- `-OO`：可能崩（依赖函数注解的库）
- `compileall`：.pyc 已存在，二次启动无可见加速
- zipapp/shiv/pex：不支持 PySide6 等 C 扩展
- `-X frozen_modules=on`：3.13+ 默认已开

**已穷尽，无新空间**。

### §3.6 体验换 CPU 的运行时优化

如「枚举定时器 200ms -> 1000ms」降 idle CPU，但 USB 插拔响应延迟从 200ms 变 1s。**体验降级换 CPU 不算「简化又提速」**，除非 idle CPU 是核心痛点，否则不做。

---

## 四、运行时优化方法（仅挑改动小的）

### §4.1 高频热路径避免 alloc

- 预构造常量对象放模块级（如 `QColor` hex 字符串 -> 模块级 dict `_ANSI_QCOLORS`）
- 自动滚动判断在**插入前**（`sb.value() >= sb.maximum() - 4`），插入后判断永远 True
- `IncrementalDecoder` 自管半字节缓冲，别外层叠 `byte_buffer`

### §4.2 定时器频度权衡

- 枚举 / 轮询定时器频度按 UX 可接受延迟上限设（200ms 还是 1000ms 取决于业务）
- idle 自适应定时器（连续 N 次空 -> 扩 interval，有数据 -> 收紧）~10 行，idle CPU 微降，低优先级

### §4.3 跨线程信号参数

PySide6 跨线程 Signal 不传 dict（序列化问题），改 str / 同步方法 + lock 取信息。

---

## 五、测量方法

### §5.1 import 瓶颈

```bash
python -X importtime src/main.py 2>importtime.txt
# 解析取 top cumulative：
python -c "
import re
rows=[]
for line in open('importtime.txt',encoding='utf-8',errors='replace'):
    m=re.search(r'import time:\s+(\d+)\s+\|\s+(\d+)\s+\|(.*)', line)
    if m: rows.append((int(m.group(2)), m.group(3).rstrip()))
rows.sort(reverse=True)
for cum,name in rows[:15]: print(f'{cum:8d}  {name}')
"
```

**注意**：`importtime` 不含模块 import 时的运行时副作用（如 plugin 扫描）。副作用要用 `perf_counter` 分段测。

### §5.2 启动分段计时

```python
import time
t0 = time.perf_counter()
# ... 每段后
t1 = time.perf_counter()
print(f'segment: {(t1-t0)*1000:.1f}ms')
```

### §5.3 进程外启动计时（含 Python 解释器启动）

```python
# app 内：--startup-bench flag，窗口 show + 一拍后写 timestamp 到文件
# 进程外：spawn app，读 timestamp 文件，差值即启动时间
```

**关键**：warmup 1 + 5 次取中位数，min/max 极差要小于收益。warmup 吸收首轮 cold start（OS cache 冷）。

### §5.4 A/B 对照

```bash
# 改前
git stash
python scripts/measure_launch.py --target <python> --name baseline --runs 5 --warmup 1
git stash pop
# 改后
python scripts/measure_launch.py --target <python> --name optimized --runs 5 --warmup 1
```

---

## 六、本项目实测效果汇总

> **⚠️ AI 测试可能不准确**：以下数字由 AI 在 Windows 11 + CPython 3.11.7 venv 测得（warmup 1 + 5 次中位数），复测可能不同。仅作量级参考。

环境：PySide6 6.6 + qfluentwidgets 1.11 + pylink-square 1.6.0 + pyocd 0.36，Nuitka 4.1 打包，Windows 11。

### 启动优化累计（dev `python src/main.py` 模式）

| 优化项 | 方法 | 中位数 | 增量 |
|---|---|---|---|
| (基线) | - | 2.972s | - |
| pyocd 预热后台化 | §2.1 import 后台预热 | 2.523s | -0.45s |
| 平台缓存预热 | §2.2 平台缓存预热 | 2.458s | -0.06s |
| 5 页懒构造 | §2.3 Page 懒构造 | 1.915s | -0.54s |
| pyocd wait 移 FlashPage | §2.4 wait 移真正使用处 | ~~1.620s~~ **已回退** | ~~-0.30s~~ 崩 |
| **累计（回退后）** | | **1.915s** | **-1.06s（-35.6%）** |

### Python 版本对比（独立维度，与上叠加）

| 版本 | 启动中位数 | import 总账 | 测试 |
|---|---|---|---|
| 3.11.15 | 2.973s | 1434ms | 382 passed |
| 3.13.13 | 2.559s（-14%） | 1181ms | 382 passed |
| 3.14.4 | 2.611s | 1218ms | 382 passed |
| 3.15.0a8 | 2.294s（噪声内） | 1126ms | 375 passed + 1 hid DLL 失败 |
| PyPy 3.11 | n/a | n/a | PySide6 无 wheel |
| 3.14 free-threaded | n/a | n/a | PySide6 abi3 不带 t tag |

### Nuitka 打包对比

| 方案 | 启动中位数 |
|---|---|
| 直接 `python src/main.py` | 2.0s（旧基线）/ 1.62s（优化后） |
| standalone | 1.63s |
| onefile 冷启动 | 3.5~3.9s |
| onefile 缓存命中 | 1.96s |
| onefile no-compression 缓存 | 1.81s |

### 不做的方法（B 类）

| 方法 | 不做理由 |
|---|---|
| qfluentwidgets 精细 import | 父包 `__init__.py` 全量加载，无干净绕过 |
| darkdetect stub | §2.2 已把 darkdetect 段压到 4ms，stub 边际 |
| pylink 懒 import | §2.2 已榨干（ctypes/socket 并行化） |
| logging.handlers 懒 import | 无净收益，后台预 import 反而 import lock 竞争变慢 |
| Python flag / .pyc / zipapp | 已穷尽实测，无新空间 |
| Splash Screen | 实测 +1.36s（Qt GUI 子系统提前完整初始化） |
| 枚举定时器 200->1000ms | idle CPU 换 USB 插拔响应延迟，体验降级 |
| RTT drain timer 自适应 | 收益小（idle CPU 微降），低优先级 |

---

## 七、优化决策流程

1. **测**：`-X importtime` 找 import 大头 + `perf_counter` 分段找运行时大头
2. **分类**：import 副作用 / 控件构造 / DLL 加载 / 业务逻辑
3. **筛**：按「收益 / 复杂度 / 风险」排序，零风险高收益先做
4. **A/B 对照**：每项改完实测，收益 < 极差的不算有效
5. **验证**：全量测试套 + 真机验证（GUI / 硬件交互的改动）
6. **记录**：每项优化 commit message 写实测数字 + 方法 + 风险，便于回溯

---

## 八、复用清单（按收益/风险排序）

**零风险高收益（先做）**：
- [ ] import 后台预热（§2.1）
- [ ] 平台缓存预热（§2.2，Windows）
- [ ] Page / Tab 懒构造（§2.3，GUI 多页）
- [ ] Python 版本升级（§2.5，3.11->3.13）

**中风险需验证**：
- [ ] Nuitka 打包选项（§2.6，发版前）

**慎用 / 本项目已回退**：
- [ ] wait 兜底移真正使用处（§2.4）-- 本项目实测 DLL 竞态崩，已回退。C 扩展 DLL 全局句柄场景**不要移 wait**，详见 §2.4 踩坑段

**不推荐**：
- [ ] 精细 import 第三方库（§3.1）
- [ ] stub 重依赖模块（§3.2）
- [ ] Splash Screen（§3.4，实测可能负优化）
- [ ] Python flag / .pyc / zipapp（§3.5，已穷尽）
