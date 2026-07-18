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
5. **MSVC CFLAGS/LDFLAGS(/Ox /GL /LTCG）环境变量注入会踩 scons AssertionError**(PIL 模块编译崩），Nuitka 4.1 下不可用。`--lto=yes` 已带 LTCG。
6. **cmd 下跑 bat 文件必须是 CRLF 行尾**。Git Bash 的 Edit/Write 会写 LF，cmd 解析失败静默退出（只打印 banner 后回到提示符）。改完 bat 用 python 转 CRLF。
7. **console-disabled 的 exe 重定向 stdout 拿不到输出**（onefile 是子进程 stdout、standalone 完全丢）。启动计时之类需要进程外拿数据的场景，让 app 写文件，外部轮询。

**最终方案**：发版用 `build_nuitka.bat`（standalone，最快）；onefile 用 `build_nuitka_onefile_opt.bat`（持久缓存）。启动时间差异主要在 onefile 冷启动的解压，缓存机制已把稳态差距压到 ~0.2s。
