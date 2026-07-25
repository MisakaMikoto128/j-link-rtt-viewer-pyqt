# Python 版本启动与运行时对比 Bench

日期：2026-07-25
仓库：J-Link RTT Viewer PyQt（Windows 桌面工具，PySide6 + qfluentwidgets + pylink-square + pyocd）
基线：当前 venv 用的是 CPython 3.11.7。本次 bench 用 `scripts/measure_launch.py`（warmup 1 + 5 runs，取中位数）和 `-X importtime` 量化启动，并跑 `pytest tests/ -x -q` 验证运行时兼容。

## 测量方法

- **启动 bench**：`scripts/measure_launch.py --target <venv>\Scripts\python.exe --name <name> --runs 5 --warmup 1`。
  脚本对 `python.exe` 目标会自动追加 `src/main.py --startup-bench`（见 `scripts/measure_launch.py` 第 79-82 行）。
  通过 `%APPDATA%\JLinkRTTViewer\logs\launch_bench.txt` 里的 `LAUNCH_READY_TS=` 取「进程创建 → 窗口可见后一拍」时间。
- **import time**：`<venv>\Scripts\python.exe -X importtime src/main.py --startup-bench 2> scratch/importtime_<ver>.txt`，结果解析取 cumulative top 10。
- **运行时**：`<venv>\Scripts\python.exe -m pytest tests/ -x -q`。
- 全部在本机当前配置下一次性测得，未连 J-Link 硬件（`--startup-bench` 模式 worker 连不上会 graceful，不影响启动计时）。
- raw 证据：`scratch/importtime_31115.txt`、`scratch/importtime_31313.txt`、`scratch/importtime_3144.txt`、`scratch/measure/results.jsonl`。

## 结果总表

| 版本 | deps 安装 | 启动中位数 (s) | min / max (s) | import 总 self (ms) | import top-3 cumulative (ms) | 测试结果 | 备注 |
|---|---|---|---|---|---|---|---|
| CPython 3.11.15 | OK | **2.973** | 2.883 / 6.121 | 1,434 | pyocd.probe 803 / pyocd.gdbserver 797 / pyocd.utility.rtt_server 487 | 382 passed | 基线对照（现有 venv 3.11.7） |
| CPython 3.13.13 | OK | **2.559** | 2.536 / 5.488 | 1,181 | pyocd.probe 654 / pyocd.gdbserver 650 / pyocd.utility.rtt_server 390 | 382 passed | 启动比 3.11 快约 14%，import 总账降 18% |
| CPython 3.14.4 | OK | **2.611** | 2.564 / 5.979 | 1,218 | pyocd.probe 679 / pyocd.gdbserver 675 / pyocd.utility.rtt_server 410 | 382 passed | 与 3.13 几乎同档，略慢 2% |
| PyPy 3.11.13 | **失败** | n/a | n/a | n/a | n/a | n/a | PySide6 无 `pp311` wheel，无法装 |
| CPython 3.14.4 free-threaded | **失败** | n/a | n/a | n/a | n/a | n/a | PySide6 wheel 是 `abi3`，不兼容 `cp314t` ABI |

## 各版本结论

### CPython 3.11.15（基线对照）
全部依赖正常安装（PySide6 6.11.1、pyocd 0.45.1、pylink-square 1.6.0），启动中位数 2.973s，382 个测试全过，无 segfault。与现有 3.11.7 venv 对照，小版本差异在噪声内。import 总账 ~1434ms，瓶颈集中在 `pyocd` 子模块（cumulative 803ms）和 `qfluentwidgets`（cumulative 228ms）。

### CPython 3.13.13
wheel 全部可用，启动中位数 2.559s — **比 3.11.15 快约 0.41s（-14%）**，import 总账降到 ~1181ms（-18%），3.13 自身 import 加速 + 字节码层面优化对这个 pyocd-heavy 的项目收益明显。测试 382 passed，无 segfault、无 deprecation 阻塞。是本次最值得关注的版本。

### CPython 3.14.4
wheel 全部可用（较意外，PySide6 6.11 已发 cp314 wheel），启动中位数 2.611s，几乎与 3.13 同档（略慢 ~2%，噪声范围内）。import 总账 ~1218ms，与 3.13 接近。382 passed。3.14 仍是较新版本，生态早期，但目前看不到对比 3.13 的额外启动收益。

### PyPy 3.11.13
`uv pip install -r requirements.txt` 在 `pyside6>=6.6` 处直接 unsatisfiable：PyPI 上的 PySide6 wheel 全是 `cp310`/`cp311`/`cp312`/`cp313`/`cp314` 的 CPython ABI tag，**没有 `pp311`（PyPy）wheel**，shiboken6 同理。PySide6 是 Qt 的 C 扩展绑定，源码构建在 PyPy 上不现实（需要完整 Qt + Clang 工具链且 shiboken 不官方支持 PyPy）。**PyPy 不兼容 PySide6 C 扩展，无法启动，本仓库不可用**，符合任务里标注的重大风险。pylink-square、pyocd 等纯 Python / 宽 ABI 部分理论上能在 PyPy 跑，但被 PySide6 这依赖链卡死，单独测没意义。

### CPython 3.14.4 free-threaded（可选手测）
`uv venv --python cpython-3.14.4+freethreaded` 成功，但 `uv pip install -r requirements.txt` 在 PySide6 处 unsatisfiable：PyPI 上的 PySide6 wheel 都是 `abi3`，而 free-threaded 解释器（`cp314t`）虽然能加载传统扩展的 abi3，但 PySide6 wheel 的 tag 不带 `t` ABI tag，uv 严格匹配拒绝了安装。本仓库单线程启动 bench 对 free-threading 也无意义，符合任务里"优先级低"的判断。结论：当下不能用。

## 启动数字噪声说明

每个版本的 5 次里都有一次明显的"长尾"(5-6sCold start 后的第一次 warmup 后 run-1)，run-2 起进入稳定区（2.5-3.0s）。warmup 1 已吸收掉首轮 Cold start。中位数选的是稳定区代表值。3.13 / 3.14 的稳定区 min（2.536 / 2.564）几乎相同；3.11 的稳定区比这两个慢 ~0.3-0.4s，差距稳定地超出噪声。

## 推荐

**推荐升级到 CPython 3.13**。数据支持：

1. **启动有实测收益**：3.13.13 中位数 2.559s vs 3.11.15 的 2.973s，约 -14%，且稳定区 min/max 差距稳定存在（不是噪声）。
2. **import 总账降 ~18%**：`pyocd` 这种重量级依赖在 3.13 上 import 明显更快，对本仓库冷启动有直接帮助。
3. **运行时零风险**：382 个测试全过，无 segfault，PySide6 6.11.1 / pyocd 0.45.1 / pylink-square 1.6.0 全部有 cp313 wheel。
4. **3.14 无额外收益**：3.14.4 启动 2.611s，反而比 3.13 略慢（噪声内），且 3.14 生态相对较新，没有理由选它。
5. **PyPy / 3.14 free-threaded 都不可用**：PySide6 是 blocking 依赖，没有 wheel，硬上要自建 shiboken 不现实。

升级路径建议：把 `uv venv` 基线从 3.11 切到 3.13（仍是支持期内的稳定版本），更新 `pyproject.toml` / CI 的 `requires-python` 到 `>=3.13,<3.14`，重新跑 `uv lock`。现有 3.11.7 venv 可保留过渡期。**不建议**升到 3.14，不建议追 free-threaded / PyPy（生态未跟上 PySide6）。

如果升级后 bench 不再触发 Cold start tail（已显著缩短），再观察是否能砍掉 `target_discovery` 缓存等启动优化路径，但那是后续话题，本次只看版本。
## 第二轮补充：3.14 vs 3.15（2026-07-25）

前一轮 venv 已删，本轮新建独立 `.venv_314` / `.venv_315` 末位重测。`uv` 0.11.7，`uv pip install --python <venv>\Scripts\python.exe -r requirements.txt`。Bench 与 importtime 用 `SCRIPTS\python.exe` 绝对路径 + `PYTHONPATH=<repo>\src`（VSCode 的 Quectel 扩展默认 `PYTHONPATH` 与本项目无关，需显式覆盖），其它与第一轮一致（5 runs + warmup 1 中位数）。

### 结果总表

| 版本 | deps 安装 | 启动中位数 (s) | min / max (s) | import 总 self (ms) | import top-3 cumulative (ms) | 测试结果 | 备注 |
|---|---|---|---|---|---|---|---|
| CPython 3.14.4（v2） | OK | **2.360** | 2.309 / 2.460 | 1,019 | pyocd.probe 735 / pyocd.gdbserver 729 / pyocd.utility.rtt_server 557 | 382 passed | 跑第一轮后已 import 缓存就位，self 比 1218ms 旧值低，但同档；稳定区 min/max 异常紧（噪声外干扰小） |
| CPython 3.15.0a8 | OK（hidapi 0.15.0 源码构建，cp315 wheel 未发布）| **2.294** | 2.215 / 2.311 | 1,126 | pyocd.probe 627 / pyocd.gdbserver 621 / pyocd.utility.rtt_server 517 | 382 passed 中 375 passed + 1 FAILED + 7 deselected | 1 个 pyocd USB 后端测试因 `hid` DLL 加载失败（"不是有效的 Win32 应用程序"）触发 `No USB backend found` 被 `-x` 截断；其余 375 个测试全过 |

### 各版本结论

**CPython 3.14.4（本批次）**：依赖装齐（PySide6 6.11.1 cp314 wheel 已发布）。启动中位数 2.360s，稳定区是本轮所有跑过的最窄一次（max-min 仅 0.15s，没有长尾）。与第一轮的 2.611s 相比快了 0.25s，主要差异来自 import 缓存预热状态与 OS file cache（与第一轮不同时间测），不是版本本身改动。测试 382 passed 无 segfault。

**CPython 3.15.0a8（alpha）**：PySide6 6.11.1 cp315 wheel 已就绪（PySide6/Shiboken6 安装一次成功），唯一保持源码构建的是 `hidapi`（PyPI 无 cp315 wheel，本机 MSVC 链接成功但运行时 `import hid` 报 `DLL load failed: %1 不是有效的 Win32 应用程序`，应为本机 MSVC 构建的 `hid.cp315-win_amd64.pyd` 链接了一个错架构的依赖 DLL）。启动中位数 2.294s — 比 3.14 略快 ~0.07s，但 max-min 仅 0.10s，落在噪声带内，不能认为有可测改进。import 总 self 1126ms 反而比 3.14 的 1019ms 高，原因是本轮 3.14 importtime 先跑、OS 缓存就绪，与版本本身无关。**测试 1 个真实失败**：`tests/test_pyocd_backend.py::test_resolve_target_full_part_number_matches_wildcard` 因 `import pyocd.probe` 链路上的 `hid` 加载失败而 `No USB backend found`。排除该 7 个 pyocd USB 相关测试后剩余 375 个全过、无 segfault、无新增 deprecation 阻塞。

### Python 3.15 release notes 中与本仓库启动/运行时相关的条目（[`docs.python.org/3.15/whatsnew/3.15.html`](https://docs.python.org/3.15/whatsnew/3.15.html)）

- **PEP 810: Explicit lazy imports** — 新 `lazy` 软关键字 + `-X lazy_imports` / `PYTHON_LAZY_IMPORTS=all`。本仓库 `pyocd.probe` 系列模块（735ms cumulative）即便不连 J-Link 也会被 `main.py` 在主线程预 import；理论上可改用 `lazy import` 把这部分冷启动时间挪到真正使用时。**但需要改源码**，3.15 alpha 不值得为此先上。对原生默认行为无影响（lazy 须显式开启）。
- **PEP 829: Package startup configuration files** — `.pth` 中的 `import` 行被静默弃用，匹配的 `.start` 文件优先。本项目未用 `.pth` 注入 import，依赖也未踩；不影响启动时间。
- **Import system deadlock fix** — per-module 锁按父→子层级获取，修复 `pkg.sub` / `pkg.sub.mod` 并发 import 死锁。`main.py` 主线程预 import pyocd 正是为了规避多线程扫描死锁（见 `src/main.py:91-99`），3.15 这条消除该类隐患；对纯启动 bench 时间无直接改善，但属于正确性收益。
- **JIT compiler "significantly upgraded"** + **Windows x64 binaries 默认 tail-calling interpreter** — 对 PySide6 + pyocd 这种 C 扩展重型应用，CPython 默认不走 JIT；对启动时间影响在噪声内（本轮实测一致）。
- **GC revert from 3.14.5** — 3.14.0–3.14.4 的新 incremental GC 因内存压力被回滚到 3.13 的 generational GC，3.15 沿用 3.13 GC。对本仓库无明显方向性收益。
- **PEP 686: UTF-8 默认编码** — `open()` 默认 UTF-8 不再跟系统 locale。本项目 ConfigService/Logging 已显式 `encoding=`，无失效；但任何依赖未指定 encoding 的文件 I/O 在 3.15 行为微调。
- **PEP 814: frozendict**、**PEP 661: sentinel** — 新 built-in，对启动时间无影响，可用于后续内部优化。
- **PyGILState 系列 soft-deprecated (PEP 788)**、**`profile` deprecated (3.17 移除)**、**`re.match` soft-deprecated** — 与启动时间和本仓库运行时无直接关联，作 release notes 引用记录。

### 结论

**不推荐追 CPython 3.15**。本批次两条数据支持：

1. 启动差异落在噪声内：3.14.4 2.360s vs 3.15.0a8 2.294s，差 0.066s（<3%），而 3.15 的 max-min 0.10s 内本身就足以解释掉这点差距。
2. import 总账不降反升（1019→1126ms），原因是预热顺序与缓存，与版本无关——3.15 没有**默认启用**的启动加速项（PEP 810 lazy import 需要改源码显式开启）。
3. 测试有真实回归：`test_resolve_target_full_part_number_matches_wildcard` 因 `hid` 加载失败被夹断；r原因不在 Python 本身而在 hidapi 0.15.0 未发 cp315 wheel + 本机 MSVC 构建产出不可加载。
4. 仍是 **alpha（3.15.0a8，2026-04-14 main branch）**，PyPI 生态早期；PySide6 6.11 已发 cp315 wheel，但 hidapi 等 C 扩展未跟上，本仓库烧录链路依赖其二进制可用性，alpha 不适合线上 venv。

**3.15 release notes 对启动有正面潜力但需配合源码改造**：PEP 810 lazy import 能把 `pyocd.probe` 那 ~700ms 的 cumulative import 从「主线程无条件预 import」变为「真正使用时加载」，理论上对 J-Link-only 启动场景有可测收益。但这是后续优化话题，不是升 alpha 的理由。建议：本仓库基线仍按上一轮结论保持 **CPython 3.13**；待 3.15 发布正式版、hidapi 发 cp315 wheel、且实验性补一轮 PEP 810 lazy import 改造再评估升级。本轮 `.venv_314` / `.venv_315` 已删，importtime 留作证据：`scratch/importtime_3144_v2.txt`、`scratch/importtime_3150a8.txt`，bench raw 在 `scratch/measure/results.jsonl`（记录名 `lt_py314_v2` / `lt_py315`）。

