# 替代 Python 打包技术调研（启动速度视角）

本项目是 Windows 桌面应用（PySide6 + qfluentwidgets + pylink-square 1.6.0），当前发版用 Nuitka standalone 打包，实测启动 1.63s。本调研回答一个问题：**是否有比 Nuitka standalone 启动更快的 Python 打包方案？结论是 Nuitka 已最优，无更优替代。**

> 本文档从 `docs/packaging_startup_report.md` 的同名小节抽取并扩写成独立成文的分析，不依赖原报告上下文也能读懂。

## 背景与基线

### 实测启动基线

测量方法：`src/main.py --startup-bench` 模式（窗口 show + 事件循环一拍后写 `%APPDATA%/JLinkRTTViewer/logs/launch_bench.txt`），`scripts/measure_launch.py` 以「进程创建 -> ready 时间戳」计时，warmup 1 + 5 次取中位数，结果数据存 `scratch/measure/results.jsonl`。环境：Nuitka 4.1 + PySide6 + qfluentwidgets，2026-07 实测。

| 方案 | 启动时间 |
|---|---|
| 直接跑 `python src/main.py` | 2.00s |
| standalone（`build_nuitka.bat`） | **1.63s** |
| standalone 优化（-OO/no_site/删冗余 Qt DLL） | 1.69s |
| onefile 缓存命中 | 1.96s（zstd）/ 1.81s（no-compression） |
| onefile 冷启动（无缓存） | 3.5~3.9s |

### 启动瓶颈：Python import

第一性原理分析：启动瓶颈是 Python import 本身。三大硬开销：

| import 模块 | 耗时 |
|---|---|
| PySide6.QtCore | ~115ms |
| qfluentwidgets | ~290ms |
| pylink | ~70ms |
| **合计** | **≈ 0.47s** |

这 0.47s 是任何打包方案都跑不掉的硬开销（除非把 Python 代码编进 C 省掉 src 自身的解析/字节码加载）。Nuitka 之所以 1.63s < 直接 `python main.py` 2.0s，正是因为它把 `src/*.py` 编进 C，省了 src 自身的解析/字节码加载；其他打包器要么走标准解释器（吃满 import），要么工程风险高。

## 项目硬约束

调研替代方案时必须考虑本项目的硬约束（6 条）：

1. **PySide6 含 Qt C++ 扩展 + DLL + 插件**：Qt 运行时由 Shiboken/QtCore/QtGui 等 `.pyd` C 扩展 + 一批 `Qt6*.dll` + `platforms/iconengines/imageformats` 等插件构成。打包器必须能正确收集与定位这些原生依赖，否则运行期 ImportError 或 platforms 找不到。Nuitka 自带 PySide6 hook 自动处理；其他打包器需验证 hook 完整性。
2. **qfluentwidgets 纯 Python + 资源**：qss/图标/字体/翻译已由 Qt Resource Compiler 编进 `qfluentwidgets/_rc/resource.py`（纯 Python，~3.2MB），由 `__init__.py` 静态 `from ._rc import resource` 引入，全库零动态 import，不存在运行时从文件系统读 `.qss` 的路径。打包器只需把整个包按静态 import 链收进 dist 即可（详见 CLAUDE.md「Nuitka 打包 qfluentwidgets 资源（已订正）」）。
3. **pylink-square==1.6.0 锁版本**：2.x 有 `rtt_start`/`rtt_read` breaking change（RTT 通道永远读不到数据，详见 CLAUDE.md「pylink 必须用 1.6.0，2.x 不工作」），必须锁 1.6.0。`JLinkARM.dll` 由用户装 SEGGER J-Link 工具包提供，pylink 包内不捆绑任何二进制 data，所以 `--include-package-data=pylink` 实际是 no-op。
4. **pyelftools / intelhex**：ELF/HEX 固件解析依赖。`elftools.elf.elffile` 在模块级别硬 `from ..dwarf.dwarfinfo import ...`，不能靠 `--nofollow-import-to=elftools.dwarf` 排除（dwarf ~244KB 是 elftools 体积大头，只能靠 fork 改 lazy import 才能砍，不能靠打包参数排除）；intelhex 在 `flash_file_parser` 内是函数级 lazy `from intelhex import IntelHex`。打包器需正确处理这两种 import 形态。
5. **Windows 首要平台 + PE 元数据**：发版首要 Windows，产物需带 Windows PE 元数据（版本号 / 图标 / 文件描述 / 公司等）。Nuitka 自带 `--windows-icon-from-ico` + `--product-version` 等 PE 资源写入能力；其他打包器如无 PE 元数据写入能力需另配 `rcedit` / `verpatch` 等工具补。
6. **--startup-bench 测量机制**：`src/main.py --startup-bench` 在窗口 show + 事件循环一拍后写 `%APPDATA%/JLinkRTTViewer/logs/launch_bench.txt`；`scripts/measure_launch.py` 以「进程创建 -> ready 时间戳」计时，warmup 1 + 5 次取中位数。任何替代方案必须能在同一测量口径下对比。注意：console-disabled 的 exe 重定向 stdout 拿不到输出（onefile 是子进程 stdout、standalone 完全丢），启动计时必须走「app 写文件 + 外部轮询」路径，不能依赖 stdout 重定向。

## 方案对照表

| 方案 | PySide6 支持 | 预期启动 | 判定 | 备注 |
|---|---|---|---|---|
| **Nuitka standalone**（当前） | 成熟 | 1.63s | **基线** | 把 src 编进 C，唯一已实测最快 |
| **PyInstaller** (onedir) | 成熟（有 hook） | ~2.0s+ | 更慢 | 仍走标准字节码 import，自带的导入包装层比直接 python 还略慢；onefile 解压更慢 |
| **PyInstaller** (onefile) | 成熟 | ~3s+ | 更慢 | 解压开销 |
| **cx_Freeze** | 有坑（Qt 插件/platforms 路径需手动配） | ~2.0s+ | 更慢 | 走标准解释器 |
| **Briefcase** (BeeWare) | 偏 Toga，PySide6 支持弱 | ~2.0s | 更慢 | 走标准解释器，启动 ≈ 直接 python |
| **PyOxidizer** | PySide6/Qt 扩展历史不友好 | 理论可能 ~1.4-1.5s | 不可用/高风险 | oxidized importer（memory-mapped module index）理论可加速 import 是唯一可能破基线的机制；但项目半停维护（2022 后 release 缓慢），Qt C 扩展 + DLL 资源映射配置工程量大，PySide6 成功案例少 |
| **shiv/pex/zipapp** | 不支持 | N/A | 不可用 | zipimport 对 Windows `.pyd` C 扩展有限制，PySide6 跑不起来 |
| **不打包 + venv 复制**（embedded Python 部署） | N/A | ~2.0s | 更慢但简单 | 启动 ≈ 直接 python；免去编译，适工控机部署对照 |

## 核心判别

任何**走标准 Python 解释器**的打包器（PyInstaller / cx_Freeze / Briefcase / embedded 部署），启动起步就 ≈ 直接 python（2.0s），**不可能**比 Nuitka standalone 1.63s 快。它们都没有把 Python 代码编进 C 的能力，省不掉 src 的解析开销。

要破 1.63s 基线只有两条理论路径：

1. **把 Python 编进 C**：Nuitka 是唯一成熟方案。PyPy 对 PySide6 支持差、Cython 工程量巨大，都不现实。
2. **加速 import 机制本身**：PyOxidizer 的 oxidized importer（memory-mapped module index）理论可加速 import，是唯一可能破基线的机制。但项目半停维护（2022 后 release 缓慢）+ PySide6 支持差，ROI 低。

## 最终结论

**Nuitka standalone 已是该技术栈启动速度最优解，无更优替代。**

剩余提速空间只在**代码层**：减少 import 深度、对少见 qfluentwidgets 子包做懒加载（如把某些 widget 的 import 从模块级挪到首次使用处），把 qfluentwidgets 那 290ms 的 import 进一步压缩。这是唯一可能再省 50-150ms 的方向，但属代码重构，不是打包技术。

## 方法论说明

本节为基于工具知识 + 实测基线的分析判断，**未在本机实测 PyInstaller / cx_Freeze / PyOxidizer**（避免装环境耗时）。

如未来要坐实，最小验证路径：

1. 装一个 PyInstaller（`pip install pyinstaller`）。
2. 跑 `pyinstaller --onedir src/main.py`（带 PySide6 hook 自动收集 Qt 依赖）。
3. 用 `scripts/measure_launch.py` 测 onedir 产物 5 次启动中位数，对比 Nuitka standalone 的 1.63s。
4. 预期：持平或更慢（PyInstaller onedir 走标准字节码 import，自带导入包装层比直接 python 还略慢；如确实持平，可作为「免编译的简化部署备选」，但启动速度维度不破基线）。

PyOxidizer 因半停维护 + PySide6 成功案例少，不推荐投入实测配置成本。

## 相关文档

- [`packaging_startup_report.md`](./packaging_startup_report.md)：Nuitka 打包启动速度实测主报告（含四轮优化、`--nofollow-import-to` 清理、激进编译优化已穷尽等），本文档的源出处。
- [`build_nuitka.bat`](../build_nuitka.bat)：发版构建脚本（standalone，最快启动 1.63s）。
- [`build_nuitka_onefile.bat`](../build_nuitka_onefile.bat)：onefile 构建脚本（持久解压缓存，稳态 1.81-1.96s）。
- [`scripts/measure_launch.py`](../scripts/measure_launch.py)：启动耗时测量脚本（warmup 1 + 5 次中位数）。
- [`CLAUDE.md`](../CLAUDE.md)：项目经验笔记（含 pylink 1.6.0 锁版本踩坑、qfluentwidgets 资源打包订正等）。
