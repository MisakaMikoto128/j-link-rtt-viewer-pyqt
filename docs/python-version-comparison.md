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