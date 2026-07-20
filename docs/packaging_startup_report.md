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

### 踩坑：onefile 的 `--show-progress` 是打包管道杀手（2026-07-18）

**现象**：`Build + package (full)`（`scripts/package_release.ps1` 菜单选项 0）跑完，`build/dist/<basename>/` 里**只有 onefile 的 `.exe`，standalone 同名目录和 `.zip` 缺席**。

**根因**：`build_nuitka_onefile.bat` 曾带 `--show-progress`，每次 onefile 构建喷 ~4000 行 `Nuitka-Progress:`（~400KB）到 stdout。打包脚本 `[1/4]` 用 `cmd /c .\build_nuitka_onefile.bat` 跑构建——在任何**非纯 TTY**调用路径下（`Release to GitHub...` 菜单选项内部用 `Invoke-Expression` 重新拉一个 PowerShell 子进程跑同一脚本、CI、后台、`*>` 重定向），这巨量输出**撑爆管道缓冲区，Nuitka 进程被 OS 在 scons 链接阶段中途杀掉**，`$LASTEXITCODE -ne 0` → `throw` → 打包脚本停。打包阶段顺序是 `[3/4] onefile copy` → `[4/4] standalone dir+zip`，脚本在被杀前如果 onefile 已落盘、[3/4] 已 copy，就出现「dist 里只剩 onefile」的现象。standalone 的 `build_nuitka.bat` 从来没这个 flag（输出 46 行安静），所以它本身不发死，but 被 onefile 卡死连累整条流程。

**处理**：删掉 `build_nuitka_onefile.bat` 的 `--show-progress`，让 onefile 与 standalone 一样安静。`--show-progress` 在交互终端也只是一直刷屏、15 分钟里没人盯着看，删掉零损失。

**实测验证**：删后非交互模式（`-Version 0.6.0 -Detail verify`）跑完整流程 EXITCODE=0，四阶段 `[1/4]`→`[4/4]` 全到达，dist 三件套齐全（onefile `.exe` 30.9MB + standalone dir 43 文件 + `.zip` 43.9MB），日志里 `Nuitka-Progress` 行数 = 0（之前 4000+）。约 13 分钟。

**判别**：凡是「打包/CI 跑完 dist 缺 artifact、但手动单跑构建 OK」的现象，先怀疑某个构建脚本在被管道调用时 stdout 喷得太猛撑爆 buffer。Nuitka 的 `--show-progress` 是典型元凶，别加回来。

### 编译速度

| 脚本 | 用途 |
|---|---|
| `build_nuitka.bat` | 发版（standalone + LTO + bytecode cache） |
| `build_nuitka.sh` / `build_nuitka_onefile.sh` | Linux（见下） |

共享缓存 `.\temp\nuitka_cache_*`（ccache/clcache/downloads/dll_dependencies/bytecode）跨脚本复用，多轮构建越快越明显。

### 加快打包速度：`--nofollow-import-to` 排未用模块（第三轮，2026-07-18 实测）

**这是目前发现对打包速度（+ 发行体积）收益最大的方向**，远胜任何编译标志。本轮把"项目代码 + qfluentwidgets/pylink import 链都没真正用到的模块"全列出来排除。

调研方法：分别派 subagent 扫（a）`src/` 实际 import 全景、（b）qfluentwidgets 间接拉入的 PySide6 子模块链、（c）pylink/elftools/intelhex 子模块使用面，三者交叉验证"保留集"。

**保留的 PySide6 子模块（仅 6 个）**：`QtCore / QtGui / QtWidgets`（src 直用）+ `QtSvg / QtSvgWidgets / QtXml`（qfluentwidgets `common/icon.py` 用 `QSvgRenderer`+`QDomDocument` 渲染 FluentIcon，间接硬依赖，不能删——删了 IconEngine/图标全崩）。

**排除的 PySide6 子模块（~50 个）**：3D 全家桶、Bluetooth、Charts、Concurrent、DataVisualization、DBus、Designer、Graphs/GraphsWidgets、Help、HttpServer、Location、Multimedia/MultimediaWidgets、Network、NetworkAuth、Nfc、OpenGL/OpenGLWidgets、Pdf/PdfWidgets、Positioning、PrintSupport、Qml、Quick/Quick3D/QuickControls2/QuickTemplates2/QuickWidgets、RemoteObjects、Scxml、Sensors、SerialBus、SerialPort、ShaderTools、SpatialAudio、Sql、StateMachine、Test、TextToSpeech、UiTools、WebChannel、WebEngine 全家、WebSockets、WebView、AxContainer。

**排除的第三方子包**：`qfluentwidgets.multimedia`（零引用）、`intelhex.__main__/test/bench`、`pylink.__main__`（CLI/测试 stub）。

**实测结论**：
1. 50 个 PySide6 排除项编译期**零 WARNING** —— Nuitka 静态追踪压根没发现任何代码路径 import 它们，说明排除完全安全，不只是"砍了但运行时靠回退"。
2. 最初为 intelhex/pylink 的 CLI/test stub（`intelhex.__main__/test/bench`、`pylink.__main__`）也加了 `--nofollow-import-to`，构建时打 4 条 `Not allowed to include module` WARNING——这些是排除生效的确认，无害。但**根因是脚本里多了冗余的 `--include-package=intelhex` / `--include-package=pylink`**：这两条强制扫整包，stub 被纳入意向再被 nofollow 拦下 → 既占 WARNING 又是死循环。**修法**：standalone 模式 `--follow-imports` 默认开，本就会自动跟随 src 的 import（含 `flash_file_parser.py` 函数内 lazy 的 `from elftools.../from intelhex import IntelHex` + 模块级 `import pylink`），不需要 `--include-package`。实验（subagent 去 `--show-modules` 验证）：去掉这两条后 Nuitka 仍自动把 `intelhex/intelhex.compat/intelhex.getsizeof` 和 pylink 全套子模块打进 dist，**4 条 WARNING 消失**，stub nofollow 也随之删掉（再无扫描源触达它们）。**结论：`--include-package=intelhex/pylink` 是多余且有害的，已删**。
3. standalone 构建成功，`--startup-bench` smoke 启动到 `launch_bench.txt` 写出（窗口 show + 事件循环一拍都跑过），exit 0。
4. 最终构建只剩 **1 条 WARNING**：`qfluentwidgets.multimedia` —— 这条当时无法消除（`--include-package=qfluentwidgets` 必须保留以保动态加载的 widget 子模块/资源，见 CLAUDE.md 踩坑；multimedia 被它扫到，只能靠 nofollow 拦）。是的，1 条无害 WARNING 换来 81 KB 的干净排除，值。
5. **后续把这最后 1 条也清零了（2026-07-20）**：深查发现 `qfluentwidgets.multimedia` 被 Nuitka 纳入意向**不是 import 链拉的**（`qfluentwidgets/__init__.py` 只 `from .components/.common/.window/._rc import`，全包零引用 multimedia），而是 `--include-package=qfluentwidgets` 主动扫磁盘发现它 → 再被 `--nofollow-import-to` 拦 → WARNING。根因在 `--include-package=qfluentwidgets` 本身多余。查实：(a) qfluentwidgets 1.11.2 的 qss/图标/字体/翻译全嵌进 `_rc/resource.py`（纯 Python，`__init__.py` 静态 `from ._rc import resource`），不存在运行时从文件系统读 `.qss` 的路径；(b) qfluentwidgets 全库**零动态 import**（`importlib/__import__/pkgutil/getattr-import` 全零命中），所有 widget 子模块走静态 `from .xxx import` 可达。所以删 `--include-package=qfluentwidgets` 改靠 standalone 默认 `--follow-imports` 自动跟随静态 import 链，qss 不会丢、multimedia 不会被扫到、WARNING 消失。同时删配套的 `--nofollow-import-to=qfluentwidgets.multimedia`（无扫描源则不需要拦）。**实测：构建零 WARNING，standalone + onefile 深度 smoke（窗口存活、FluentWindow 渲染、app.log 无 ImportError/qss 报错）全通过**。保留 `--include-package-data=qfluentwidgets` 作零成本保险。CLAUDE.md 的 qss 踩坑条目已订正。**最终全部构建零 WARNING。**

**关键不能排的（踩坑提醒）**：
- `elftools.dwarf` / `elftools.ehabi` 看似没用到（项目只用 `elftools.elf.elffile` + `elftools.common.exceptions`），但 `elffile.py` 在**模块级别** `from ..dwarf.dwarfinfo import ...` 硬导入。`--nofollow-import-to=elftools.dwarf` 会让 `from elftools.elf.elffile import ELFFile` 运行时直接 `ModuleNotFoundError` ——**致命，绝不能排**。dwarf（~244KB）是 elftools 体积大头，但只能靠 fork 改 lazy import 才能砍，不能靠 nofollow。`--include-package=elftools` 仍保留（elftools 的 lazy import 历史上不稳，保留扫全包更保险；它不产生我们关心的 stub WARNING）。
- qfluentwidgets `components/__init__.py` 和 `widgets/__init__.py` 对子模块有显式 `from .xxx import *`，单文件级排除（如 `components.widgets.tree_view`）会炸包初始化。`date_time` 子包同理（`from .date_time import *`）。**只排 `qfluentwidgets.multimedia`（顶层无 `from .multimedia import *` 拉入）；其余 qfluentwidgets 子包都别碰**。
- `--nofollow-import-to` 的语义是"运行时被 import 会 `ModuleNotFoundError`"（不是"保留字节码可回退"），所以只能排**确认零引用**的模块；对包内 `__init__` 用 `import *` 硬拉入的子包，排了等于自杀。
- **冗余 `--include-package` 会催生 stub nofollow 的死循环 WARNING**：如果一个包靠 `--include-package` 全包扫，又对它的 `__main__/test/...` stub 加 nofollow，必然 WARNING。正解是**去掉那个 `--include-package`，改靠 default `--follow-imports` 自动跟随**（standalone 模式默认开），stub 不再被扫到，nofollow 也无需加。`--include-package` 只对"Nuitka 静态追踪追不到、但又确实在运行时用"的包保留（如_clr 的 ext、动态 `__import__`）。

### 运行时速度

- `--lto=yes` 是唯一实测可用的全局编译优化（MSVC CFLAGS/LDFLAGS env 注入在 Nuitka 4.1 下触发 scons AssertionError，不可用）。
- 运行时热点（RTT 数据渲染、内存页 hex dump）由 Qt C++ 渲染主导；Python 侧热点已被 Nuitka 以 C+LTO 编进 exe。**Nuitka 编译标志不影响 Qt C++ 运行时**，所以打包层面已无额外运行时收益空间——见下「第四轮：激进优化已穷尽」。

### 第四轮：激进编译优化已穷尽（2026-07-20 受控实验）

为找比现状更激进的运行时提速手段，在独立 `temp/` 目录做了对照实验（不碰主产物）。逐项实测结论：

| 选项 | 运行时收益 | 风险 | 4.1 支持 | 实测 |
|---|---|---|---|---|
| `--python-flag=-OO`（no_docstrings） | **会崩** | 高 | 是 | qfluentwidgets `singledispatchmethod` 依赖函数注解，`-OO` 破坏注解 → import 期崩。**发版只能用 `-O`，绝不能升 `-OO`** |
| `--python-flag=no_annotations` | 零 | 低 | 是 | 只改 sys.flags，不动 AST，零运行时影响 |
| `--deployment` | 零 | 低 | 是 | 只删诊断 loader stub（excluded-module 标记/`-c` 自启守卫），零运行时影响 |
| `--experimental=iterator-optimization` | 崩 | 高 | 有 bug | 优化阶段 AttributeError |
| `--experimental=optimize-dual-int` | 崩 | 高 | 有 bug | elftools 触发 C2440 |
| `--experimental=standalone-imports` | 崩 | 高 | 有 bug | 漏 Shiboken.pyd，运行期崩 |
| `--experimental=assume-type-complete / del_optimization / eliminate-backports` | 零微 | 低 | 是 | 纯 import 期或冷路径，无热点收益 |
| `--pgo-c` | 理论 | 不可用 | 否 | 官方明说"not working with standalone modes yet" |

**结论**：维持现状（`--lto=yes` + `--python-flag=-O / no_warnings / no_site` + 全量 nofollow 清理）。`-OO` 是唯一诱人但实测崩 qfluentwidgets 的项，别踩。剩下的提速空间只在代码层（减少 import 深度、懒加载少见 qfluentwidgets 子包），不在编译标志层。详见 CLAUDE.md「Nuitka 激进编译优化已穷尽」。

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
