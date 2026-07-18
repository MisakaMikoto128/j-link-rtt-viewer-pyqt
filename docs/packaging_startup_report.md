# Nuitka 打包启动速度实测报告

测量日期：2026-07；环境：Nuitka 4.1 + PySide6 + qfluentwidgets。相关脚本路径 `scripts/measure_launch.py`，结果数据 `scratch/measure/results.jsonl`。

## Nuitka 打包启动速度实测结论（2026-07，Nuitka 4.1 + PySide6 + qfluentwidgets）

**实测方法**：`src/main.py --startup-bench` 模式（窗口 show + 事件循环一拍后写 `%APPDATA%/JLinkRTTViewer/logs/launch_bench.txt`），`scripts/measure_launch.py` 以「进程创建 → ready 时间戳」计时，warmup 1 + 5 次取中位数。结果存 `scratch/measure/results.jsonl`。

**实测结果（中位数）**：

| 方案 | 启动时间 |
|---|---|
| 直接跑 `python src/main.py` | 2.00s |
| standalone（build_nuitka.bat） | 1.63s |
| standalone 优化（-OO/no_site/删冗余Qt DLL） | 1.69s |
| onefile 冷启动（无缓存） | 3.5~3.9s |
| onefile 缓存命中 | 1.96s（zstd）/ 1.81s（no-compression） |

**结论 / 经验**：

1. **standalone 已比直接跑 python 快**，瓶颈不在打包形态。Python import 时间才是大头：PySide6.QtCore ~115ms、qfluentwidgets ~290ms、pylink ~70ms。要继续提速只能减少 import 数量（懒加载），打包侧选项收效甚微。
2. **`-OO`/`no_site`/`no_asserts`/`no_docstrings` 对启动几乎无影响**（1.63→1.69s，噪声内）。src 内无 `assert` 依赖，`-OO` 理论上可用，但收益可忽略，默认脚本保持 `-O` 保守。
3. **删除 Qt6 DLL 要小心**：`Qt6Svg` 被 qfluentwidgets 间接依赖（IconEngine → QtSvg），删了 ImportError。可安全删的只有 `Qt6Pdf/Qt6Multimedia/Qt6Qml/Qt6Quick` 系列（dist 113M→104M），对启动无可见影响。
4. **onefile 必须配 `--onefile-tempdir-spec={CACHE_DIR}\...` 持久缓存**：冷启动解压 3.5~3.9s，缓存命中后 1.8~2.0s。`--onefile-no-compression` 让缓存命中再快 ~0.15s 但 exe 从 33M→116M，不值。
5. **MSVC CFLAGS/LDFLAGS 全局环境变量注入历史上会踩 scons AssertionError（旧 PIL 模块编译崩）**；当前依赖已不含 PIL，但 PySide6/shiboken 等第三方 C 扩展仍可能不稳，故默认 build 脚本不全局注入这些标志。`--lto=yes` 已覆盖大部分 LTCG 收益。
6. **`--msvc=latest` 实测无可见收益，已撤回**：试过显式指定最新 VS 工具链，构建耗时、产物体积、启动耗时均与默认（Nuitka 自动探测）无差异，故从 build 脚本移除，避免多余配置项。保留下来的只是低风险的清理项：`--nofollow-import-to` 排除 `*.tests/*.test/*.testing` 及 `setuptools/pip/wheel/pytest/docutils/unittest/ensurepip/distutils`（减少扫描量与产物体积）和 `--python-flag=no_site`（跳过 site 启动逻辑，收益在噪声内但零风险）。
7. **cmd 下跑 bat 文件必须是 CRLF 行尾**。Git Bash 的 Edit/Write 会写 LF，cmd 解析失败静默退出（只打印 banner 后回到提示符）。改完 bat 用 python 转 CRLF。
8. **console-disabled 的 exe 重定向 stdout 拿不到输出**（onefile 是子进程 stdout、standalone 完全丢）。启动计时之类需要进程外拿数据的场景，让 app 写文件，外部轮询。

**最终方案**：发版用 `build_nuitka.bat`（standalone，最快）；onefile 用 `build_nuitka_onefile.bat`（持久缓存）。启动时间差异主要在 onefile 冷启动的解压，缓存机制已把稳态差距压到 ~0.2s。

---

## 第二轮：编译速度 / 运行时速度 / Linux 打包（2026-07-18）

启动速度已到该技术栈地板（见上），本轮转向构建耗时、运行时性能与跨平台支持。

### 编译速度

| 脚本 | 用途 |
|---|---|
| `build_nuitka.bat` | 发版（standalone + LTO + bytecode cache） |
| `build_nuitka.sh` / `build_nuitka_onefile.sh` | Linux（见下） |

共享缓存 `.\temp\nuitka_cache_*`（ccache/clcache/downloads/dll_dependencies/bytecode）跨脚本复用，多轮构建越快越明显。

### 运行时速度

- `--lto=yes` 是唯一实测可用的全局优化（MSVC CFLAGS/LDFLAGS env 注入在 Nuitka 4.1 下触发 scons AssertionError，不可用）。
- `--python-flag=no_docstrings`（= -OO）理论上省内存、加速属性访问；**本项目未实测**（第一轮已验证 -OO 对启动无影响，运行时收益预期也在噪声内）。如需验证：把 `build_nuitka.bat` 的 `--python-flag=-O` 换成 `--python-flag=-OO`，用 `scripts/measure_launch.py` 对比 + 手动跑 RTT 高频数据流看 CPU 占用。
- 运行时热点（RTT 数据渲染、内存页 hex dump）的优化在代码层已做过（QColor 预构造、缓冲合并节流等，见 CLAUDE.md），打包层面无额外收益。

### Linux 打包（待实测）

脚本：`build_nuitka.sh`（standalone）、`build_nuitka_onefile.sh`（onefile + 持久解压缓存 `{CACHE_DIR}/JLinkRTTViewer/Cache/{VERSION}` → `~/.cache/JLinkRTTViewer/Cache/0.6.0/`）。

代码层 Linux 适配（本轮已改）：
- `core/config_service.py`：用户偏好 Windows → `%APPDATA%`，Linux → `XDG_CONFIG_HOME`（默认 `~/.config`）/JLinkRTTViewer/
- `core/logger.py`：日志目录 Windows → `%APPDATA%`，Linux → `XDG_STATE_HOME`（默认 `~/.local/state`）/JLinkRTTViewer/logs

**未验证的坑（到 Linux 机器上首先检查）**：
1. `pylink-square==1.6.0` 只内置 Windows/macOS 的 J-Link 库；Linux 需先装 SEGGER J-Link 工具包（提供 `libjlinkarm.so`），`pylink.JLink()` 才能构造成功。若 SEGGER 未装，main.py 的 DLL 致命检测会弹框退出——这是预期行为。
2. Qt 运行时库：PySide6 pip 包自带大部分，但系统侧常需 `libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1`。
3. onefile 解压缓存目录语义：`{CACHE_DIR}` 在 Linux 解析为 `$XDG_CACHE_HOME`（默认 `~/.cache`）。
4. `--windows-console-mode=disable` / `--windows-icon-from-ico` 是 Windows 专属，Linux 脚本里已去掉；GUI 模式由 ELF 本身决定，无需参数。
