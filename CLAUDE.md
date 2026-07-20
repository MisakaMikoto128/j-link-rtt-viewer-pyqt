# 项目经验笔记

为后续维护积累的实际踩坑经验。每条都带 **现象 / 原因 / 处理** 三段。

---

## pylink `close()` / `rtt_stop()` 抛 `JLinkException` 不致命

**现象**：调用 `jlink.close()` 或 `jlink.rtt_stop()` 抛 `pylink.errors.JLinkException`（如 "There is no connected JLink."）。

**原因**：pylink 在某些内部状态下（如已经 close、或 rtt 未 start）调清理方法会抛异常。

**处理**：用 try/except 包裹每个清理调用，except 内 `_logger.warning(...)` 即可，不要让单次清理失败阻断整个退出路径。**不要**用 `jlink.opened()` / `jlink.connected()` 做守卫——pylink 1.6.0 下这类守卫会因内部状态时序问题误判，反而可能跳过必要的清理。直接调 + try/except 更健壮。

参考：`src/core/jlink_worker.py` `_do_disconnect`

---

## `set_tif(SWD | JTAG)` 是错的

**现象**：把 `pylink.enums.JLinkInterfaces.SWD | pylink.enums.JLinkInterfaces.JTAG` 传 set_tif，pylink 报"Invalid interface"。

**原因**：SWD 和 JTAG 是互斥枚举值，不是 bit flag。原项目代码是按 if/else 二选一调用的，这里只是提醒——重构时不要"图省事"把两个值 OR 起来。

**处理**：按用户选择二选一调用，参考：
```python
tif = JLinkInterfaces.SWD if iface == "SWD" else JLinkInterfaces.JTAG
self.jlink.set_tif(tif)
```

---

## QThread 子类陷阱：`__init__` 跑在主线程

**现象**：在 `JLinkWorker.__init__` 里 `self._timer = QTimer(self)` 之后，无论怎么 connect 都不触发 timeout。

**原因**：QThread 子类的 `__init__` 是在创建者线程（主线程）执行的，`run()` 才是新线程。如果 QTimer / pylink.JLink / IncrementalDecoder 在 `__init__` 里创建，它们的 thread affinity 仍归主线程，timer 事件会被发到主线程的事件循环；同时 cross-thread queued connection 也会错乱。

**处理**：所有依赖事件循环 / 跨线程访问的对象 **必须在 `run()` 内创建**。`__init__` 只保存配置 + 创建 Signal 对象（Signal 本身没有 thread affinity）。

参考：`src/core/jlink_worker.py` `run()`

---

## worker 退出必须 worker 自己 `quit()`

**现象**：原项目用 `os._exit(0)` 兜底窗口关闭，否则进程会卡 2 秒以上。

**原因**：如果主线程直接调用 `worker.quit()`，Qt 只会把 quit 事件放到 worker 事件循环里——而此刻 worker 可能正阻塞在 `rtt_read()` 或 `close()` 的 C 扩展调用中，没机会处理事件。结果是 `worker.wait()` 超时，要么死等要么强 terminate（不安全）。

**处理**：定义 `stop_requested` 信号，槽里**worker 自己**清理 pylink → `self.quit()`。主线程只 emit 信号 + wait。这样 quit 不会和阻塞的 C 调用赛跑——清理完才 quit。

参考：`src/core/jlink_worker.py` `_on_stop`、`src/ui/main_window.py` `closeEvent`

---

## `IncrementalDecoder` 自管半字节缓冲

**现象**：原项目里：
```python
self.byte_buffer.extend(data_bytes)
decoded = self.decoder.decode(bytes(self.byte_buffer))
if decoded:
    self.byte_buffer = bytearray(self.decoder.getstate()[1])
```
看似在合理地保留未处理字节。

**原因**：`IncrementalDecoder.getstate()` 返回 `(buffer_state, additional_state)` 其中 `additional_state` 是 **整数**（标记位），不是剩余字节。`bytearray(int)` 会创建一个该长度的零字节数组——等于每次清空 buffer 但加几个零字节进去。这是抄来的 bug，但因为 UTF-8 跨行半字节场景很少触发，长期没被发现。

**处理**：直接 `decoded = decoder.decode(bytes(data))` 即可，半字节缓冲在 decoder 内部维护。不要在外层叠一层 byte_buffer。每次重连前 `_reset_decoder()` 重建 decoder，避免上次掉线残留污染。

参考：`src/core/jlink_worker.py` `_poll_rtt` / `_reset_decoder`

---

## `QTextEdit` 没有 `setMaximumBlockCount`

**现象**：把 `QTextEdit.setMaximumBlockCount(10000)` 写到代码里，运行时报 `AttributeError`。

**原因**：`setMaximumBlockCount` 是 `QPlainTextEdit` 独有的 API，`QTextEdit` 没有。RTT 显示区如果用 QTextEdit + 富文本，长时间运行会无上限增长，最后 GUI 卡死。

**处理**：RTT 显示区一律用 `QPlainTextEdit`；ANSI 着色通过 `QTextCharFormat` + `QTextCursor.insertText(seg, fmt)` 实现——`QPlainTextEdit` 也支持富文本字符格式，只是不支持完整 HTML。

---

## 自动滚动判断时机

**现象**：插入文本后判断 `at_bottom = sb.value() >= sb.maximum()`，结果几乎永远 True，自动滚动总在生效，用户即使滚到中间也会被拉回底部。

**原因**：插入文本会立即更新 `verticalScrollBar()` 的 maximum；插入后再判断时，光标可能因为 insert 自动跟到了新的最大值附近。判断需要在插入前。

**处理**：
```python
sb = self.display.verticalScrollBar()
at_bottom = sb.value() >= sb.maximum() - 4   # 留 4 像素余量
# ... 插入文本 ...
if at_bottom and self.chk_auto_scroll.isChecked():
    sb.setValue(sb.maximum())
```

参考：`src/ui/rtt_monitor_page.py` `_on_rtt_data`

---

## `user_prefs.json` 放 `%APPDATA%`

**现象**：把 user_prefs.json 放在 `src/` 目录下，打包成 Nuitka 后写入失败（`Program Files` 权限）。

**原因**：Windows 应用安装到 Program Files 后，应用目录默认只读。用户偏好必须放可写位置。

**处理**：`%APPDATA%/JLinkRTTViewer/user_prefs.json`；开发期也不回落到 `src/`，避免与打包后行为不一致。

参考：`src/core/config_service.py` `_compute_user_prefs_path`

---

## `ConfigService.set()` 高频值要节流

**现象**：用户拖动窗口/调整字体大小 SpinBox 时，每帧都会 `cfg.set("window_geometry", ...)` 或 `cfg.set("font_size", N)`，每次都 atomic replace 写盘，明显卡顿。

**原因**：拖动 / SpinBox 每秒能触发几十次 setter；每次 set → 写 .tmp → fsync → os.replace 是 ms 级 syscall，叠加起来阻塞 UI 事件循环。

**处理**：`set()` 只标 dirty 并 `_flush_timer.start()`（默认 200 ms 单次 timer）。timer 触发时统一落盘。`closeEvent` 必须调 `cfg.flush()` 强制冲刷。

参考：`src/core/config_service.py` `set` / `flush` / `_do_flush`

---

## `closeEvent` 必须 `cfg.flush()`

**现象**：用户改了主题色然后立即关窗口；下次启动主题色没保存。

**原因**：节流策略下，最后一次 `set()` 后到 timer 触发前的 200 ms 内如果窗口关闭，落盘没赶上。

**处理**：`MainWindow.closeEvent` 第一行：保存窗口几何 → `self._cfg.flush()` 强制落盘 → 再启动 worker 清理。

参考：`src/ui/main_window.py` `closeEvent`

---

## Nuitka 打包 qfluentwidgets / pylink 资源（已订正）

**现象**：早期 Nuitka 打包后运行，qfluentwidgets qss 找不到、pylink 找不到 JLinkARM.dll。

**原因（订正）**：这条是历史踩坑，但根因要分清楚：
- **pylink 的 DLL**：pylink-square 1.6.0 的 `library.py` 在**运行时**从 SEGGER 系统安装目录（Win: `C:\Program Files\SEGGER\JLink\`）查找 `JLinkARM.dll`，包内不捆绑任何二进制 data —— 所以 `--include-package-data=pylink` 实际上 no-op，pylink 的 DLL 问题靠"用户装 SEGGER JLink 工具包"解决，不靠打包参数。
- **qfluentwidgets 的 qss/图标/字体/翻译**：现代版本（项目用的 1.11.2）全部由 Qt Resource Compiler 编进 `qfluentwidgets/_rc/resource.py`（纯 Python，~3.2MB），由 `__init__.py` **静态** `from ._rc import resource` 引入。**不存在从文件系统读 `.qss` 的路径**。早期"qss 找不到"的踩坑是旧版本党或当时缺 `--include-package-data`，现在已不是独立文件问题。

**处理（订正，2026-07-20 实测）**：
```
--include-package-data=qfluentwidgets      # 保留（保险，对无 data 文件的包扫一遍是零成本 no-op）
```
**`--include-package=qfluentwidgets` 已删除** —— 实测发现它**多余且有害**：
- 它强制 Nuitka 扫整个 qfluentwidgets 磁盘目录，把项目用不到的子包（如 `qfluentwidgets.multimedia`）也纳入"有意包含"意向；再配 `--nofollow-import-to=qfluentwidgets.multimedia` 拦下会打一条 `Nuitka-Inclusion:WARNING`。这条 WARNING 就是它催生的。
- 删掉后改靠 standalone 默认的 `--follow-imports` 自动跟随 `src → qfluentwidgets` 的**静态 import 链**。qfluentwidgets 全库零动态 import（实测 `importlib/__import__/pkgutil/getattr-import` 全零命中），所有 widget 子模块走 `from .xxx import` 静态可达，`--follow-imports` 必然追到；`_rc/resource.py` 也是静态 `from ._rc import resource`，qss/资源不会丢。
- 实测：两个 bat 都删 `--include-package=qfluentwidgets` + 对应 `--nofollow-import-to=qfluentwidgets.multimedia` 后，构建**零 WARNING**，standalone + onefile 深度 smoke（窗口存活 12s/15s、FluentWindow 渲染、app.log 无 ImportError/qss 报错）全通过。

**判别**：凡一个第三方库的资源已嵌进 Python（`_rc/resource.py`）+ 全库静态 import，`--include-package` 就是多余的磁盘扫描，删掉改靠 `--follow-imports`；只有库在运行时从文件系统读 `.qss`/`.dll`/图片（即包内真有 data 文件、且代码运行时读 `__file__` 同级路径）时才需要 `--include-package-data`，且不需要 `--include-package`。

参考：`build_nuitka.bat` / `build_nuitka_onefile.bat`、`docs/packaging_startup_report.md` 第三轮 nofollow 调研

---

## Nuitka 激进编译优化已穷尽，维持现状（2026-07-20 实测）

**现象**：想找比 `--lto=yes`/`-O`/`no_site`/大砍 nofollow 更激进的 Nuitka 编译优化提速运行时（RTT 高频 insertText、hex dump、symbol 表刷新这些热点）。

**原因（实测结论）**：热路径由 Qt C++ 渲染主导（QPlainTextEdit.insertText、QTextCursor、QTableWidget 刷新），Nuitka 编译标志**不影响 Qt C++ 运行时**；Python 侧热点代码本就被 Nuitka 以 C+LTO 编进 exe。逐一实测后的可用性结论：

1. **`--python-flag=-OO`（no_docstrings）会崩 qfluentwidgets**：qfluentwidgets 用 `singledispatchmethod` 做重载，依赖 **函数注解**，`-OO` 在剥 docstrings 的同时破坏注解路径 → 运行时 import 期就崩。**所以发版脚本只能用 `-O`（no_asserts），绝不能升 `-OO`**。这条是已有 `--python-flag=-O` 选择背后的非平凡原因，别动。

2. **`--deployment`**、`--python-flag=no_annotations` 安全但**零运行时收益**（只删诊断 loader stub / 改 sys.flags，不动 AST）。不纳入（无收益）。

3. **`--experimental` 系列**：`iterator-optimization` / `optimize-dual-int` / `standalone-imports` 在本项目直接崩（AttributeError / C2440 / 漏 Shiboken.pyd）；`assume-type-complete` / `del_optimization` / `eliminate-backports` 零收益或纯 import 期。**全不纳入**。

4. **`--pgo-c` 官方明说不支持 standalone**。

**处理**：维持 `build_nuitka.bat` / `build_nuitka_onefile.bat` 现状（`--lto=yes` + `--python-flag=-O / no_warnings / no_site` + 全量 nofollow 清理）。剩下的提速空间**只在代码层**（减少 import 深度、懒加载少见 qfluentwidgets 子包），不在编译标志层。

**判别**：凡"Nuitka 还有什么激进 flag 能加速运行时"的念头，先记住——热路径在 Qt C++，Nuitka flags 不会动它；能动的 Python 热点已被 LTO 编译。不再重复试。`-OO` 是唯一看起来诱人但实测会崩 qfluentwidgets 的项，别踩。

参考：`docs/packaging_startup_report.md` 第四轮激进优化实测

---

## TODO：发版前敲定作者信息

`src/ui/about_page.py` 顶部的 `AUTHOR_NAME = "待定"` 和 `AUTHOR_GITHUB = "https://github.com/"` 在 0.1.0 发版前需要替换为真实信息。

---

## RTT 通道选错时显示区无内容

**现象**：J-Link 连接成功、设备信息填充正确，但 RTT 显示区一直空白。

**原因**：UI 的 RTT 通道（SpinBox 0-15）和 MCU 端 `SEGGER_RTT_printf(N, "...")` 必须一致。MCU 默认在通道 0 发送，UI 默认通道也是 0，但 user_prefs.json 会保存用户上次选择——切到 1 后重启仍保持 1。

**处理**：连接前确认 SpinBox 通道与 MCU 端代码一致。如果不确定，先切到 0 试试。如需重置偏好，删除 `%APPDATA%\JLinkRTTViewer\user_prefs.json`。

---

## QThread 必须独立于业务对象：永远不要继承 QThread

**现象**：worker 跑得"半生不熟"——slot 看似在 worker 线程跑（mock 测试也能通过），但实际跑在主线程；poll timer 在 worker 线程但 start() 失效；日志反复出现 `QObject::startTimer: Timers cannot be started from another thread` 或 `QObject::setParent: Cannot set parent, new parent is in a different thread`；J-Link 连上但 RTT 显示区永远空白；关闭窗口不响应。

**原因**：
1. **直接继承 QThread + override run() 是 Qt 官方反复警告的反模式**。QThread 对象的 thread affinity 永远是创建它的线程（通常是主线程），不是 `run()` 内执行的新线程。结果：所有 `Qt.QueuedConnection` 信号 slot 实际投递到主线程的事件循环。
2. **"backend + QThread 子类瘦壳 + __getattr__ 转发"也是 hack**。`moveToThread(self)` 把 backend 移到一个 QObject（QThread 本身），但 QThread 自己的 thread affinity 还在主线程。PySide6 的 signal-slot 元对象系统在做 QueuedConnection 内部分发时，会触发隐藏的 setParent 调用——跨线程，warning。

**处理**：
- worker 类**直接继承 QObject**，**不**继承 QThread；所有信号/槽/状态都在这里。
- 调用方（MainWindow / 测试 fixture）外部创建独立的 `QThread`，调 `worker.moveToThread(thread)` + `thread.started.connect(worker.initialize)` + `thread.start()`。worker 的 thread affinity 才真正落到 worker 线程。
- worker 在 `initialize()` 槽内创建 `pylink.JLink / QTimer / IncrementalDecoder`——此槽由 `thread.started` 在 worker 线程触发，所以这些对象的 thread affinity 也是 worker 线程，timer 操作不再 cross-thread。
- 关闭：worker `_on_stop` 槽内调 `self.thread().quit()` 退出 thread 事件循环；主线程只 `thread.wait()`。
- 不要用 `__getattr__` 转发签名——会让 PySide6 元对象系统出现 cross-thread 参与对象。

参考：`src/core/jlink_worker.py`、`src/ui/main_window.py`、`tests/test_jlink_worker.py` fixture。

---

## pylink 必须用 1.6.0，2.x 不工作

**现象**：J-Link 连接成功（`connected()` 返回 True，设备信息正常回填），但 `rtt_read(channel, 4096)` 永远返回空，RTT 显示区一直空白。

**原因**：pylink-square 在 2.0.0 有 breaking API change（rtt_start/rtt_read 内部行为变化）。我们之前 pip install 时没锁版本，装了 2.0.1，结果 RTT 通道不工作。参考项目 `Charging_Pile/RTT_Viewer/RTT-T` 用 1.6.0 稳定运行。

**处理**：`requirements.txt` 锁定 `pylink-square==1.6.0`。如果之前已经装了 2.x，强制降级：`pip install pylink-square==1.6.0`。

参考：`requirements.txt`、`src/core/jlink_worker.py`

---

## RTT 读循环用 threading.Thread 而不是 QTimer

**现象**：UI 主线程被高频 `rtt_data_received` 信号 + `cursor.insertText` 渲染占满，用户点"断开"按钮事件排在队列末尾，体感"UI 卡死"几秒钟。

**原因**：用 `QTimer` 在 worker 线程跑 RTT 轮询时，每次 timeout 都通过 Qt 事件队列调度 `rtt_read` + emit `rtt_data_received`。signal-to-main-thread 排队 + 主线程逐条 ANSI 解析 + insertText → 主线程任务队列被填满。

**处理**：
1. 读循环用 `threading.Thread + time.sleep(0.1)` 模式（参考项目方案）。这样读线程完全独立于 Qt 事件循环，emit 信号只是 post 到主线程队列。
2. disconnect 时先 `_stop_read = True` + `read_thread.join(timeout=2.0)`，确保读线程退出后才调 `rtt_stop/close`——避免 close 时 jlink 句柄被读线程持有。
3. UI 侧做节流：`rtt_data_received` 只入缓冲，每 50ms `_flush_rtt_buffer` 合并所有数据一次性 insertText。极大减少 layout 重算次数。
4. 点击连接/断开按钮**立即**给 UI 反馈（setEnabled(False) + 改文字"连接中…/断开中…"），不等 worker 的 connection_state_changed 信号回来。

参考：`src/core/jlink_worker.py` `_read_loop` / `_do_disconnect`、`src/ui/rtt_monitor_page.py` `_flush_rtt_buffer`、参考项目 `RTT-T/src/services/jlink_service.py` `read_rtt_data` / `disconnect`

---

## native threading.Thread 不要直接 emit Qt signal

**现象**：连接 STM32 收数据 → 点断开 → UI 整体卡死（不只是按钮不变，鼠标点其他位置也不响应）。worker 日志显示 `_do_disconnect()` 完整跑完（"已断开 J-Link" 打印了），但主线程不动。控制台必带一条 `QObject::setParent: Cannot set parent, new parent is in a different thread`。复现 100%。

**原因**：之前的实现里，RTT 读循环跑在 `threading.Thread(target=self._read_loop, daemon=True)`（**native pthread，不是 QThread**），循环内直接 `self.rtt_data_received.emit(decoded)` emit 跨线程 Qt 信号。PySide6 在「从非 QThread 创建的 pthread emit Signal 跨线程到主线程槽」这个场景下行为不可靠——会偶发产生 setParent cross-thread 警告，并污染主线程事件循环，最终表现为主线程卡住。Qt 文档对此场景措辞模糊：能 emit，但内部 sender thread / QObject affinity / QMetaCallEvent 创建路径都有 edge case。

**处理**：read_thread **永远不直接碰 Qt signal**，只跟 Python `threading.Lock` + `list` 打交道：

```python
# read_thread 里
with self._rtt_drain_lock:
    self._rtt_drain_buffer.append(decoded)

# worker 线程在 initialize() 内建 QTimer 50ms drain：
self._rtt_drain_timer = QTimer()  # 无 parent，affinity 跟 worker_thread
self._rtt_drain_timer.setInterval(50)
self._rtt_drain_timer.timeout.connect(self._drain_rtt_buffer)
self._rtt_drain_timer.start()

@Slot()
def _drain_rtt_buffer(self):
    with self._rtt_drain_lock:
        if not self._rtt_drain_buffer: return
        merged = "".join(self._rtt_drain_buffer)
        self._rtt_drain_buffer.clear()
    self.rtt_data_received.emit(merged)  # 从 worker_thread context emit，安全
```

同时：read_thread 异常路径**也不要** emit `log_message`，只 `_logger.error()` 写文件日志。错误从日志看就够，不值得为它再开一条跨线程信号路径。

顺带：worker 已经 50ms 合并好一次推给 UI，UI 侧不需要再加一层节流 timer/buffer——直接 `_on_rtt_data` 里 `cursor.insertText` 即可。

参考：`src/core/jlink_worker.py` `_read_loop` / `_drain_rtt_buffer` / `initialize`，`src/ui/rtt_monitor_page.py` `_on_rtt_data`

---

## worker 线程内的 QTimer/QObject 退出前必须自己 stop + deleteLater

**现象**：应用正常退出（连接 → 断开 → 关窗口），所有功能都跑完了 `rc=0`，但控制台尾巴上跟两条警告：
```
QObject::killTimer: Timers cannot be stopped from another thread
QObject::~QObject: Timers cannot be stopped from another thread
```

**原因**：worker 用 `moveToThread` 范式后，在 `initialize()` 内创建的 `QTimer()`（无 parent）thread affinity 跟 worker_thread。退出流程：
1. 主线程 `closeEvent` emit `stop_requested`
2. worker `_on_stop` 在 worker 线程跑：`_do_disconnect` → `thread.quit()`
3. 主线程 `thread.wait()` 返回 — worker_thread 已结束
4. **Python GC 在主线程回收 `JLinkWorker` 实例**，间接析构 `_rtt_drain_timer`
5. QTimer 析构 → `killTimer()` → 当前是主线程，但 timer.thread() = 已结束的 worker_thread → 警告

只是噪音，功能没受影响，但应该处理掉。

**处理**：worker `_on_stop` 内在 `thread().quit()` **之前**显式 stop + deleteLater 所有 worker 线程内创建的 QObject（QTimer 等）：

```python
@Slot()
def _on_stop(self) -> None:
    self._do_disconnect()
    if self._rtt_drain_timer is not None:
        self._rtt_drain_timer.stop()
        self._rtt_drain_timer.deleteLater()
        self._rtt_drain_timer = None
    t = self.thread()
    if t is not None:
        t.quit()
```

`deleteLater()` 把删除事件 post 到 worker_thread 的事件队列。`quit()` 之后 worker_thread 的事件循环在退出前会处理这个 deleteLater event，timer 在自己的线程内被销毁。等主线程 GC 回收 worker 时 timer 已经是 None，不再触发 cross-thread killTimer。

**不要尝试给 timer 设 `QTimer(self)` 解决**：worker 已经在 worker_thread，timer parent=self 看似让父子链一起销毁，但 worker 实例的 Python 引用还是从主线程释放，C++ 析构链最终还是在主线程跑，警告不消。**只有"worker 线程内主动 deleteLater"** 才真正干净。

参考：`src/core/jlink_worker.py` `_on_stop`

---

## PySide6 跨线程 Signal 不要传 dict 参数

**现象**：worker 在 `_do_disconnect` 末尾 `self.connection_state_changed.emit(False, {})` —— 紧接着的 `self._logger.info("disconnect: connection_state_changed 已 emit")` **没有**打印到日志，同时控制台输出 `QObject::setParent: Cannot set parent, new parent is in a different thread` 警告，主线程随后卡死。`Signal(bool, dict)` 这种带 dict 参数的信号，**在 worker 线程跨线程 emit 给主线程槽时**触发 PySide6 内部 dict marshalling 路径，该路径会做一次跨线程 setParent 操作（推测是把 dict 封进 QVariant 时连带的某个临时 QObject 被 reparent），**整个 emit 调用阻塞 worker 线程，并污染主线程事件循环**。

参数全部是 `bool/int/str/bytes` 的信号不受影响——只有 `dict`（和大概率 `list` / 自定义 PyObject）会踩。

**处理**：worker → UI 的跨线程 Signal **一律不传 dict**。两种替代：

1. **改 str（推荐复杂结构）**：要传的内容序列化成 str（或拆 plain 字段）。
   ```python
   # 原
   command_result = Signal(str, bool, dict)
   self.command_result.emit("send_data", False, {"error": str(e)})
   # 改
   command_result = Signal(str, bool, str)
   self.command_result.emit("send_data", False, str(e))
   ```

2. **不通过 Signal 传，改同步方法 + lock**：UI 在收到信号后主动调 worker 的同步方法取信息。
   ```python
   # worker
   connection_state_changed = Signal(bool)  # 只传 bool
   self._device_info: dict = {}
   self._info_lock = threading.Lock()
   def get_device_info(self) -> dict:
       with self._info_lock:
           return dict(self._device_info)
   # worker 连接成功时
   with self._info_lock:
       self._device_info = info
   self.connection_state_changed.emit(True)
   # UI
   def _on_state_changed(self, connected: bool):
       if connected:
           info = self._worker.get_device_info()
           self._set_connected_ui(info)
   ```
   主线程跨线程读 worker 的 attribute 由 GIL + lock 保证安全，**不走 Qt 信号 marshalling**。

附带规则：worker → UI 跨线程信号一律显式 `Qt.QueuedConnection`，避免 PySide6 在 native thread emit 场景下误判 sender thread。

参考：`src/core/jlink_worker.py` `connection_state_changed` / `command_result` / `get_device_info`，`src/ui/rtt_monitor_page.py` `_on_state_changed` / `_on_command_result`

---

## pylink 1.6.0 连接顺序：必须 open → close → open(serial) → rtt_start → set_tif → set_speed → connect

**现象**：单次 `open()` 后直接 `set_tif → set_speed → connect → rtt_start` 这个看起来更简洁的顺序，在 pylink 1.6.0 上会导致 RTT 永远没数据（虽然 `connected()` 返回 True）。

**原因**：pylink 1.6.0 的 `rtt_start()` 必须在某个特定时机调用——参考项目的实践是双开后立即 `rtt_start()`，然后才设接口/速度/目标。这是 pylink 1.6.0 + J-Link DLL 内部状态机的硬性要求。

**处理**：连接序列严格按参考项目：
```python
if not jlink.opened():
    jlink.open()
    ser = jlink.serial_number
    jlink.close()
    jlink.open(str(ser))
    jlink.rtt_start()  # 必须这里
jlink.set_tif(SWD or JTAG)
jlink.set_speed(int(speed))
jlink.connect(target)
# 这之后 rtt_read 才真正能收到数据
```

参考：`src/core/jlink_worker.py` `_on_connect`、参考项目 `RTT-T/src/services/jlink_service.py` `connect`

---

## 设计原则：一次用户操作的编排，归属一个模块；不要跨 UI ↔ worker 用 flag 串

**现象**：reset 按钮 auto_reconnect 模式最初实现是 UI 编排：
```python
# UI _on_reset_clicked
self._pending_auto_reconnect = True              # ← 跨方法状态
self._worker.reset_only_requested.emit()
self._worker.disconnect_requested.emit()

# UI _on_state_changed —— 另一个方法里
if self._pending_auto_reconnect:
    self._pending_auto_reconnect = False
    QTimer.singleShot(300, self._reconnect_with_saved_params)  # ← reconnect 藏在这
```
读 `_on_reset_clicked` 只能看到一半（reset + disconnect，没有 reconnect）；reconnect 藏在 `_on_state_changed` 的 QTimer 里。三方法 + 一标志拼出来的隐式状态机。

**原因**：UI 端拥有 reset_mode 配置和上次连接参数，就以为自己应该编排整个流程。实际 worker 才是知道 J-Link / pylink / 读线程的，让 UI 编排等于把内部细节倒推到 UI 层。

**处理**：让"知道细节的那一层"全包：
- UI 只发**意图**：`worker.reset_requested.emit(mode)`（一行）
- worker 收到 `_on_reset(mode)` 派发到 `_reset_in_place` / `_reset_with_reconnect`
- 后者内部线性写：reset → disconnect → sleep → reconnect，四步顺序读
- worker 自己存 `_last_connect_params`（在 `_on_connect` 成功时落），重连不再需要 UI 回传

跨方法 flag (`_pending_auto_reconnect`) 删掉。UI 端 `_on_state_changed` 回到只做 UI 反馈、不做状态机。

参考：`src/core/jlink_worker.py` `_on_reset` / `_reset_with_reconnect`、commit `1bc917c`。

---

## 设计原则：信号参数不要靠 bool 反向区分模式

**现象**：`reset_target_requested = Signal(bool)`，arg 叫 `reattach_rtt`：
- `emit(True)` = normal 模式
- `emit(False)` = auto_reconnect 模式

UI 端：
```python
if self._cfg.get("reset_mode") == "auto_reconnect":
    self._worker.reset_target_requested.emit(False)   # ← 心算翻转
else:
    self._worker.reset_target_requested.emit(True)
```
读起来要在脑子里翻一次："auto_reconnect 怎么是 False？"

**原因**：bool 是从 worker 视角起的名（"要不要重新挂接 RTT"），UI 端调用方不知道这个内部细节，必须做语义反向映射。

**处理**：传**意图**而非 worker 内部的实现细节标志。两种做法都可以：

1. **传枚举字符串**（推荐，简单）：
   ```python
   reset_requested = Signal(str)  # mode: "normal" / "auto_reconnect"
   ```
   UI: `worker.reset_requested.emit(cfg.get("reset_mode"))`，零翻译。

2. **拆成两个语义独立的信号**：
   ```python
   reset_target_requested = Signal()
   reset_only_requested = Signal()
   ```

任何 `Signal(bool)` 出现都该问一句"True/False 对应什么"，如果调用方需要 if/else 才能决定，就用枚举或拆信号。

参考：commit `1bc917c` 把两步重构（`Signal(bool)` → 两信号 → `Signal(str)`）压成最终设计。

---

## 设计原则：UI 控件文本不是 state enum，不要用 `text() == "连接"` 当状态判断

**现象**：
```python
def _on_connect_clicked(self):
    if self.btn_connect.text() == "连接":
        # 走连接路径
    else:
        # 走断开路径
```

**原因**：按钮文本是**呈现**，不是**状态**。一旦改文案 / 加 i18n / 临时改成"连接中…"被读到，分支就走错。

**处理**：维护一个真实的 `_is_connected: bool` 字段（在 `_on_state_changed` 里更新），或从 worker 同步取状态。按钮文本只负责显示。

参考：`src/ui/rtt_monitor_page.py` `_on_connect_clicked` / `on_shortcut_connect` / `on_shortcut_disconnect`，维护 `self._is_connected` 真状态由 `_set_connected_ui` / `_set_disconnected_ui` 更新。

---

## 设计原则：模式 / 枚举字符串必须有常量，不能字面值散落

**现象**：`"auto_reconnect"` 这个字符串散落在 4 个文件：worker `_on_reset` 派发、config DEFAULTS、settings combo handler、UI 按钮文字逻辑。改名要 grep + 心算每处别打错。

**原因**：`Signal(str)` / `cfg.set("reset_mode", str)` 接口本身没强制类型，字面值就近写起来快，但散落后任意一处 typo 静默走默认分支。

**处理**：在拥有权威定义的模块（这里是 worker）定义常量：
```python
RESET_MODE_NORMAL = "normal"
RESET_MODE_AUTO_RECONNECT = "auto_reconnect"
```
其他模块 `from core.jlink_worker import RESET_MODE_AUTO_RECONNECT`。combo 用数据驱动列表 `_RESET_MODE_LABELS: list[tuple[str, str]]` 一次性生成 items + handler 查 index。

参考：`src/core/jlink_worker.py` 顶部常量、`src/ui/settings_page.py` `_RESET_MODE_LABELS`、commit `<本轮>`。

---

## 设计原则：helper 抽了就在所有同形态处用，不要"抽了一半"

**现象**：`_pause_read_thread` / `_restart_read_thread` 抽出后，`_do_disconnect` 仍内联同一段 `_stop_read=True + join + =None` 5 行；`_on_export_firmware` 还用 `_paused` 标志位假装锁（其实是抢句柄的 race）。

**原因**：抽 helper 时只改了"想到的"调用点，其他历史路径忘了同步。下一个 bug 修复要在两份代码里都改一遍。

**处理**：抽出 helper 后立刻 grep 同形态代码全部替换。`_do_disconnect` 改用 `_pause_read_thread`（仍保留 disconnect 自己的高层日志）；`_on_export_firmware` 改用 `_pause_read_thread + _restart_read_thread`（finally 保证恢复），删 `_paused` 标志位的 hack（`_paused` 字段仅留给用户主动按"暂停接收"按钮用）。

参考：`src/core/jlink_worker.py` `_do_disconnect` / `_on_export_firmware` / `_reset_in_place` 三处都用同一对 helper。

---

## 设计原则：slot 作为方法被直接调用要明示，抽 non-slot 私有 helper

**现象**：`_reset_with_reconnect` 内 `self._on_connect(*params)` 把 `@Slot()` 装饰过的方法当普通函数同步调。能跑（同线程同步），但读 `_on_connect` 的人看见装饰器会以为它只通过信号队列触发，那条同步路径完全不可见。

**原因**：slot 是约定"只通过信号路径调"的契约，直接 `.method()` 调突破契约，产生隐藏路径。

**处理**：抽 non-slot 私有 helper `_do_connect`，slot 退化成 1 行 wrapper：
```python
@Slot(str, str, int, int)
def _on_connect(self, ...):
    self._do_connect(...)

def _do_connect(self, ...):
    # 真正的实现，可被同模块内任意路径同步调
```
现在 `_reset_with_reconnect` 调 `_do_connect`，意图清晰，约定也没破。同时连接失败路径 `self._do_disconnect()`（不再叫 `_transition_to_idle` 这个无意义别名），UI 收到 `connection_state_changed(False)` 自动回正，不需要在 `_on_log_message` 兜底。

参考：`src/core/jlink_worker.py` `_on_connect` / `_do_connect` / `_reset_with_reconnect`、commit `<本轮>`。

---

## 设计原则：跨方法 setter/reset 的 boilerplate 必须用 context manager 包

**现象**：3 个不同方法 (`_on_rtt_data`, `_on_auto_scroll_toggled`, `_insert_mark_text`) 都重复写：
```python
self._programmatic_scroll = True
sb.setValue(sb.maximum())
self._programmatic_scroll = False
```
某次新加调用点忘了 reset = 自动滚动死亡。

**处理**：用 `@contextmanager` 包：
```python
@contextmanager
def _programmatic_scroll_guard(self):
    self._programmatic_scroll = True
    try:
        yield
    finally:
        self._programmatic_scroll = False
```
调用点变成：
```python
with self._programmatic_scroll_guard():
    sb.setValue(sb.maximum())
```
`finally` 保证异常路径也能 reset，少一类潜在 bug。

参考：`src/ui/rtt_monitor_page.py` `_programmatic_scroll_guard`。

---

## 设计原则：状态恢复逻辑放回状态机本身，不要塞在不相关的 handler 兜底

**现象**：`_on_log_message` 收到 `error` 日志时检查 `if btn_connect.text() == "连接中…": self._set_disconnected_ui()` —— 状态机的"卡 connecting 强制回滚"藏在日志 toast 处理器里，读 `_on_connect_clicked` 的人完全看不到 connecting → disconnected 还有这条恢复路径。

**原因**：连接失败时如果只 emit `log_message("error", ...)` 没 emit `connection_state_changed(False)`，按钮就卡在"连接中…"。当年加这个兜底是因为不确定 worker 是否一定 emit state(False)。

**处理**：保证 worker 在连接失败的**所有路径**上都 emit 状态变化（`_do_connect` 异常 / connected() 返回 False → 都走 `_do_disconnect`，里面有 `if was_active: emit(False)`，CONNECTING 算 active）。源头修好之后删兜底。

恢复逻辑就该在产生状态变化的地方写，不能塞在日志 / 错误 toast 这类完全不相关的 handler。

参考：`src/core/jlink_worker.py` `_do_connect` 失败路径走 `_do_disconnect`、`src/ui/rtt_monitor_page.py` `_on_log_message` 删 fallback。

---

## qfluentwidgets `EditableComboBox.setCurrentText(text)` 对「不在 items 里的文本」是 no-op

**现象**：FlashPage 浏览/拖放选完固件文件 → `cmb_file.setCurrentText(path)` 后界面像没选中：lineEdit 不显示路径（重启后才出现）、下拉历史列表为空、点「开始烧录」提示「未选择文件」。Format/Range 标签**反而更新了**（因为当时额外显式调了解析函数并把 path 当参数传进去），更迷惑。

**原因（之前记错过，这是订正）**：`EditableComboBox` 并**没有**覆盖 `setCurrentText`——它继承自 `ComboBoxBase.setCurrentText`（`qfluentwidgets/components/widgets/combo_box.py:166`）：
```python
def setCurrentText(self, text):
    if text == self.currentText(): return
    index = self.findText(text)          # 在 self.items 里找
    if index >= 0:
        self.setCurrentIndex(index)
    # else: 什么都不做——既不设 lineEdit 文本，也不 addItem
```
新选的文件路径根本不在 `items` 里 → `findText` 返回 -1 → 整个调用是 **no-op**。lineEdit 没更新（重启后才显示是因为 `_load_prefs` 从 `recent_files` 重建了 items），`currentText()`（= `self.text()`）仍是空 → 烧录读到空路径。**注意不是「不发信号」那么简单，是连文本都没设上。**

**处理**：不要用 `setCurrentText` 设任意路径。新文件统一走「更新 recent → 重建下拉 items → 用 index 选中」：
```python
def _rebuild_file_combo(self, recent, select_index=0):
    self.cmb_file.blockSignals(True)        # 重建期间别误触 currentTextChanged
    try:
        self.cmb_file.clear()
        for p in recent: self.cmb_file.addItem(p)
        if recent and 0 <= select_index < len(recent):
            self.cmb_file.setCurrentIndex(select_index)   # index 选中才真正设 lineEdit
    finally:
        self.cmb_file.blockSignals(False)
```
`_select_file(path)`（浏览/拖放共用）：置顶 recent → `_rebuild_file_combo` → 解析显示。`currentTextChanged` 只接「用户从下拉选 / 手动输入」的纯解析处理器，不在里面再改 recent 顺序（避免递归）。

附带：`recent_files` 是 cfg 与 combo items 两份状态，必须由同一个重建函数同步，否则 cfg 更新了但下拉列表要重启才刷新。

CLAUDE.md 另有相关条：「`EditableComboBox` 无 `clearEditText` AttributeError」。

参考：`src/ui/flash_page.py` `_select_file` / `_rebuild_file_combo` / `_on_file_text_changed` / `_parse_and_show`

---

## 设计原则：派生公式必须有单点真源，行 / 列 / 格式映射不允许多处重复

**现象**：内存页 hex byte → 列位置的公式 `_HEX_START_COL + col_in_row * 3 + (col_in_row // 4)` 在 `_highlight_diff` 和 `_select_buffer_range` 两处独立写。如果格式改动（如改成 `0xHHHH:` 起始列变 15），改一处漏一处。

**处理**：单点抽 `_byte_start_col(col_in_row) -> int` 静态方法，两处都调。同模块里和反向函数 `_byte_offset_at` 放一起，方便看正反映射的对称性。

参考：`src/ui/memory_viewer_page.py` `_byte_start_col` + 用法。

---

## `QTableWidget` 排序默认按字符串，数值列要用自定义 item

**现象**：符号表「地址」「大小」列点列头排序，`0x08000000` 和 `0x20000000` 看着对，但 `100` 排在 `20` 后面、`0x9` 排在 `0x10` 前面——按 ASCII 字符串比的，不是数值。

**原因**：`QTableWidgetItem` 的 `<` 默认比 `DisplayRole` 文本（字符串）。十六进制/十进制显示文本的字典序 ≠ 数值序。

**处理**：自定义 `_NumericItem(QTableWidgetItem)` override `__lt__`，比存进 `Qt.ItemDataRole.UserRole` 的真实数值；显示文本仍是格式化后的 `0x…` / 十进制。重填表格时先 `setSortingEnabled(False)` 再逐行插入、插完再 `True`，避免边插边排导致行错位。

参考：`src/ui/symbol_table_view.py` `_NumericItem` / `_apply_filter`。

---

## `_open_elf` 必须自己 catch `ELFError`，不能依赖调用方包 try

**现象**：用户在烧录页选了一个名字叫 `xxx.axf` 但实际不是 ELF 的文件（误改名、损坏、半截下载），`SymbolTableView.load()` 直接抛 `elftools.common.exceptions.ELFError: Magic number does not match`，UI 端崩出红色弹窗，体验差。

**原因**：`flash_file_parser._open_elf` 的代码长这样：
```python
f = open(path, "rb")
return f, ELFFile(f)              # ← ELFError 在这里
```
调用方（`read_sections` / `read_memory_summary` / `read_elf_meta`）虽然在外层套了 `try ... except ELFError → FileParseError`，但 ELFError 是在 `_open_elf` 内部抛的，那里只 catch 了 `ImportError`，**异常会直接逃出 `_open_elf`**，从外层 try 的「调用 `_open_elf`」这一行就已经穿过去了——外层的 except 根本接不到。同时 `f` 还被开着没 close。

**处理**：所有「打开+解析」一体的 helper（这里是 `_open_elf`）必须**自己**包 try/except + 失败时 close 文件，再统一抛领域内的 `FileParseError`：
```python
f = open(path, "rb")
try:
    return f, ELFFile(f)
except ELFError as e:
    f.close()
    raise FileParseError(f"ELF 解析失败：{e}")
```

**通用规则**：调用方的 try/except 只对「**直接由调用方代码抛出**」的异常有效；helper 内构造对象时已经抛了的异常，与外层 try 的语义上「同一行」，但实际栈帧是 helper 内的——只有 helper 自己能管。**「open + 立刻 parse」类 helper 一律自带 catch + close**。

参考：`src/core/flash_file_parser.py` `_open_elf`、`tests/test_flash_file_parser.py` `test_elf_readers_wrap_corrupt_file_as_fileparseerror`、`tests/test_symbol_table_view.py` `test_load_corrupt_elf_does_not_crash`。

---

## `EditableComboBox.setCurrentText(任意路径)` 在多处都会踩坑，要全局统一走 `setText` 或「先 addItem 再 setCurrentIndex」

**现象**：写测试时 `page.le_send.setCurrentText("hello world")` 之后 `page.le_send.currentText()` 仍是 `""`，发送按钮不发数据。同样的形态在烧录页文件选择 `cmb_file.setCurrentText(path)` 也踩过（commit `a068fd0`）。两处不同模块、不同上下文，根因是同一个 fluent 实现细节。

**原因**：CLAUDE.md 另有详细条目（「`EditableComboBox.setCurrentText(text)` 对不在 items 里的文本是 no-op」）。简单说：`setCurrentText` 继承自 `ComboBoxBase`，行为是 `if findText(text) >= 0: setCurrentIndex(idx) else: 什么都不做`——**不**直接设 lineEdit 文本。

**处理**：分两种用法：

1. **业务持久化路径**（如最近文件）：先 `addItem(path) → setCurrentIndex(0)`，统一走 `_rebuild_file_combo` helper（参考 `src/ui/flash_page.py` `_rebuild_file_combo`）。

2. **测试 / 临时给输入框塞文本**：用 `EditableComboBox.setText(s)`（fluent 暴露的直通 LineEdit 的方法），**不要**用 `setCurrentText`。`page.le_send.setText("hello")` 直接生效。

**反模式**：测试时图省事写 `page.le_send.lineEdit().setText(...)` —— fluent `EditableComboBox` 没有 `lineEdit()` 方法（不是标准 `QComboBox`），AttributeError。

参考：`tests/test_rtt_monitor_page.py`（统一用 `setText`）；`src/ui/flash_page.py` `_rebuild_file_combo`、`src/ui/rtt_monitor_page.py:582` `setCurrentText("")` 用于清空（空串与已有空状态相等，等价于 no-op，刚好对）。

---

## 高频热路径上构造 QColor 是不必要的 alloc，预构造放模块级 dict

**现象**：RTT 监控页 `_fmt(attrs)` 在每个 ANSI 段都调，原实现 `QColor(_ANSI_COLOR_MAP.get(attrs.fg, "#dddddd"))` —— 每次都要把 hex 字符串解析一次、申请一个 QColor 对象。高吞吐流（10kHz+ 段）下是可观的 alloc 噪声。

**原因**：调色板是常量集合（16 色 ANSI + 默认前/背景），运行期不会变。每段都 `QColor(hex)` 是把模块加载就能做的事推到热路径上。

**处理**：模块加载时把 `QColor(hex)` 全部构造好，热路径只查 dict：
```python
_ANSI_QCOLORS: dict[str, QColor] = {k: QColor(v) for k, v in _ANSI_COLOR_MAP.items()}
_DEFAULT_FG_QCOLOR = QColor("#dddddd")

# _fmt 里：
fmt.setForeground(_ANSI_QCOLORS.get(attrs.fg, _DEFAULT_FG_QCOLOR))
```

微基准 1.51× 加速（200k 次 600ms → 400ms）。同模式适用于符号表 Type pill 配色（已用），任何「枚举键 → QColor」映射都该模块级预构造。

参考：`src/ui/rtt_monitor_page.py` `_ANSI_QCOLORS` / `_fmt`、`src/ui/symbol_table_view.py` `_TYPE_QCOLORS`。

---

## 自定义 QTranslator.translate 未命中必须返回 source，不能返回空串

**现象**：切换到非中文语言后，翻译表里没有的键对应控件显示**空白**（文字消失），而不是回退到中文原文。受影响：qfluentwidgets `ColorDialog` 的 OK/Cancel/Red/Green 等按钮、设置页「重置按钮行为」下拉项、关于页三张功能卡片标题/描述、设备信息行标签（除「目标设备:」外全空）。

**原因**：`JsonTranslator.translate` 未命中时 `return self._dict.get(source, "")`。Qt 的 `QCoreApplication::translate` 判定 translator 返回值时检查的是 **`isNull()` 而非 `isEmpty()`**：空串 `""` 不是 null，被当作有效译文直接采用，于是控件显示空字符串，**不会**回退到 source 原文。（"查表未命中返回空串，Qt 会回退到原文"是错误直觉。）

**处理**：未命中时返回 `source` 自身：
```python
def translate(self, context, source, disambiguation=None, n=-1):
    return self._dict.get(source, source)
```
这样翻译表缺任意键（含未来新增）都退化显示源文本，绝不空白。`zh_CN` 现在也装 translator（见下一条），`tr()` 未命中仍返回 source，行为一致。补齐翻译表里缺失的键是另一回事（数据完整性），本条只保证「永不空白」。

附带：设备信息行标签用 `self.tr(f"{text}:")`，键带冒号；翻译表键也必须带冒号（`"固件版本:"` 而非 `"固件版本"`），否则照样命中不了。已有 `"目标设备:"` 与 `"目标设备"` 两条并存即此约定。

参考：`src/core/i18n_service.py` `JsonTranslator.translate`、`src/ui/rtt_monitor_page.py` `_info_rows`。

---

## zh_CN 也必须安装 JsonTranslator — 否则第三方英文源控件（ColorDialog 等）在中文界面全程英文

**现象**：中文界面（zh_CN）下打开「选择主题色 / 选择标记颜色」颜色对话框，OK / Cancel / Edit Color / Red / Green / Blue / Opacity 全程显示英文按钮。切到日 / 法 / 韩都正常，唯独简体中文不翻。

**原因**：qfluentwidgets `ColorDialog` 内部用**英文源文本**调 `self.tr('OK' / 'Cancel' / 'Edit Color' / 'Red' / 'Green' / 'Blue' / 'Opacity')`。历史上 `zh_CN` 是默认语言，`init_translator` 走 `if lang != _DEFAULT_LANG: installTranslator(...)` —— zh_CN 时**不装 translator**，于是 `tr('OK')` 直接返回 source 英文 `'OK'`，没有任何中文译文路径。「自定义 `QTranslator.translate` 未命中返回 source」是必要前提（保证项目自身中文 `tr()` 不被改），但它解决不了第三方英文 source 这一类。

**处理**：**所有语言（含 zh_CN 默认）都安装 JsonTranslator**。新增 `src/i18n/zh_CN.json`，**只**收「英文 source → 中文」映射（当前 ColorDialog 那 7 项）；项目自身 `self.tr('中文源')` 不出现在 zh_CN.json 里，未命中返回 source（即该中文原文），行为与不装 translator 完全一致。`init_translator` / `switch_language` 删除 `if lang != _DEFAULT_LANG` 守卫，无条件装。

判别：凡是「中文界面下还冒英文」的第三方控件，多半是它内部 `tr('EnglishSource')` 而我们 zh_CN 没装 translator —— 给 zh_CN.json 加一条 `"EnglishSource": "中文"` 即可，不需要改那个第三方控件。

参考：`src/core/i18n_service.py` `init_translator` / `switch_language`、`src/i18n/zh_CN.json`、`tests/test_i18n.py`。

---

## QSS `font:` 锁定的控件（RadioButton 等）setFont 完全无效，必须 setStyleSheet 追加规则覆盖

**现象**：全局界面字体热更新（遍历 `allWidgets` + `setFont`）后，固件烧录页 SWD/JTAG 两个 RadioButton 的 family 和字号都不变，其他控件正常。

**原因**：qfluentwidgets `BUTTON.qss` 里有 `RadioButton { font: 14px --FontFamilies; }`——**控件自身 QSS 的 `font:` 规则优先级高于 `setFont()`**，全局遍历 setFont 对它无效。实测（scratch/probe_rb2.py）：`setFont(Consolas 20pt)` 后 RadioButton 仍 14px/Segoe UI，只有 `setStyleSheet` 追加 font 规则才生效。全量扫描 qss 后同类锁定控件还有：`MenuActionListWidget`（右键菜单 14px）、`InfoBar`（14px）、对话框按钮（15px）、`SwitchButton>QLabel`、`QHeaderView::section`、`TeachingTipView`（14px）、`ColorDialog QLabel`、`TimePicker` 系列等。

**处理**：
1. 统一走 `core/_ui_font.py` `sync_qss_font_locked_widgets(root, family, pt)`：对名单内控件（`_QSS_FONT_LOCKED_CLASS_NAMES`）往其 styleSheet **追加**一条 `font-family + font-size` 规则，用 `/* UI_FONT_OVERRIDE_BEGIN/END */` 哨兵注释包裹，重复调用先 strip 旧哨兵段再追加，保证幂等。发现新的锁定控件就加进名单。
2. `--FontFamilies` 模板变量只在**构造/应用样式时**解析一次——已存在控件改 `qconfig.fontFamilies` 不会刷新 family（probe_rb_family.py 实证），所以覆盖规则里 family 也要显式写。
3. ToolTip / TeachingTip / Flyout 气泡的 family 靠 `_sync_fluent_font_families(family)` 设 `qconfig.fontFamilies = [ui_family, CJK兜底...]`：气泡每次悬停/点击**重新构造**，自动读新 fontFamilies；气泡字号由 qss 锁死（ToolTip 12px / TeachingTip 14px）——这正是「气泡字号固定但 family 跟随」的实现方式，不要去解锁它。
4. 「跟随系统」不能用 `setFamily("")`（Qt 沿用上一次的 family，回不去）——启动时在 `main.py` 任何 `setFont` 之前 `capture_system_ui_family()` 冻结 QApplication 初始 family，空偏好时 `resolve_ui_family("")` 返回它。
5. 内存页 hex 显示区 family 固定跟随 RTT 的 `font_family`（等宽）、size 独立 `memory_font_size`，二者都不跟随全局 UI 字体——hex dump 列对齐依赖等宽，UI 字体切成非等宽会让列错位（用户实测后拍板）；`_custom_font` 标记挡住全局 setFont。
6. **名单收窄到项目实际用到的控件**（当前仅 RadioButton）。右键菜单/TimePicker/InfoBar 等虽也有 qss font 锁定，但项目没用到，不纳入；`_apply_ui_font` 遍历 `allWidgets()` 已能覆盖烧录页 RadioButton（构造于 MainWindow init，构造末尾必跑一次 apply）。曾尝试给「动态创建的锁定控件」装 app 级 show eventFilter 补调——**失败**：QApplication.installEventFilter 只拦截发给 app 自己的事件，监听不到子控件的 Show。若将来用到动态创建的锁定控件，要在它创建处显式补调 `sync_qss_font_locked_widgets`，不要用 app 级 filter。

参考：`src/core/_ui_font.py`、`src/ui/main_window.py` `_apply_ui_font`、`src/ui/memory_viewer_page.py` `_apply_font`、`tests/test_ui_font.py`。

---

## 动态内容 hover 提示：复用 Fluent ToolTip 而不是 QToolTip.showText

**现象**：内存查看页 hex 区 hover 显示逐字节解析（地址/u32 LE/BE/u16），用的是原生 `QToolTip.showText`——灰色原生样式，与全应用 Fluent 圆角气泡不统一。

**原因**：`qfluentwidgets.ToolTipFilter` 只支持「相对固定 widget、静态文本」的 tooltip；hover 提示需要**内容随鼠标位置动态计算 + 位置跟随鼠标**，ToolTipFilter 不覆盖这个场景。

**处理**：`ui/widgets/fluent_hover_tip.py` `FluentHoverTip`——复用 qfluentwidgets `ToolTip`（自带圆角气泡/阴影/12px 字号/`--FontFamilies` family），单例式持有一个实例，调用方 `show_at(global_pos, text)` 内部 `setText` + `move(globalPos + offset)` + `show()`，`duration=0` 不自动消失、Leave 时 `hide()`。同文本重复调用不重建，避免闪烁。family 自动跟随 `_sync_fluent_font_families`。

参考：`src/ui/widgets/fluent_hover_tip.py`、`src/ui/memory_viewer_page.py` `_show_hover_tooltip`、`tests/test_memory_viewer_page.py` `test_display_uses_fluent_hover_tip`。

---

## _tip 在 _retranslate_ui 里重复调用会叠加多个 ToolTipFilter 产生重影

**现象**：切换语言后，连接按钮、字号 A+/A−、收窄工具栏各按钮的悬浮提示出现「很重的阴影」--多个 tooltip 叠在一起。切换前正常，每次切换多叠一层。

**原因**：`_tip(widget, text)` 同时做 `setToolTip(text)` + `installEventFilter(ToolTipFilter(...))`。它在 `_build_ui`（构造）和 `_retranslate_ui`（语言切换）里都被调用。`installEventFilter` 不去重，每次调用都新增一个 `ToolTipFilter` 实例。qfluentwidgets 的 `ToolTipFilter` 在 `QEvent.Enter` 时各起一个 300ms 定时器，定时器到期各自 `showToolTip()` 弹一个 `ToolTip` -- N 个 filter = N 个气泡叠影。

**处理**：`ToolTipFilter` 只装一次，用动态属性标记：
```python
def _tip(widget, text, duration=300):
    widget.setToolTip(text)
    if not widget.property("_fluent_tip_installed"):
        widget.installEventFilter(ToolTipFilter(widget, duration))
        widget.setProperty("_fluent_tip_installed", True)
```
`ToolTipFilter.showToolTip` 每次悬停动态读取 `widget.toolTip()`，故后续 `setToolTip` 即可刷新文本，无需重装 filter。

`search_bar.py` 的 `_tip` 同形态但 `_retranslate_ui` 用的是 `setToolTip`（没重装），所以没踩；同样适用本规则以防后续误用。

参考：`src/ui/rtt_monitor_page.py` `_tip` / `_retranslate_ui`、`src/ui/widgets/search_bar.py` `_tip`。

---

## 静态按钮文字必须在 _retranslate_ui 里显式 setText，不能只靠构造时 tr()

**现象**：切换语言后「重置并暂停」按钮仍显示中文（构造时的值），不跟随语言变化。

**原因**：按钮文字在 `_build_ui` 里 `PushButton(icon, self.tr("重置并暂停"))` 设过一次，但 `_retranslate_ui` 只更新了它的 tooltip，漏了 `setText`。语言切换靠 `LanguageChange` 事件 -> `_retranslate_ui` 重设文本，构造时的 `tr()` 不会重跑。

**处理**：每个静态文字控件在 `_retranslate_ui` 里都要显式重设。状态驱动的按钮（如 btn_connect 连接/断开）按当前状态重设，且跳过连接中（disabled）态以免覆盖"连接中…"：
```python
if self.btn_connect.isEnabled():
    self.btn_connect.setText(self.tr("断开") if self._is_connected else self.tr("连接"))
self.btn_reset_halt.setText(self.tr("重置并暂停"))
```
判别标准：凡是文字不随运行状态变化的控件，`_retranslate_ui` 必须有对应 `setText`；状态驱动的控件按当前状态分支重设。

参考：`src/ui/rtt_monitor_page.py` `_retranslate_ui`（btn_reset_halt / btn_connect）。

---

## 断开连接后状态栏闪回旧值：worker 必须在 emit(False) 前清零会话时长

**现象**：点断开后，状态栏接收 / 时长一瞬间被 `_set_disconnected_ui` 抹掉，随即又被旧值"恢复"并停留。

**原因**：UI 的 `_stats_timer`（1s）始终在跑。`_do_disconnect` 若不重置 `_session_start_ts`（仍 = 连接开始时间，非 0），断开后 `get_stats()` 仍报连接态；`_set_disconnected_ui` 抹掉显示后，下一个 1s tick 的 `_update_stats` 读到 `start_ts != 0`，按连接态重算并把旧值写回。

**处理**（收发计数跨断开保留的设计）：
- `_do_disconnect` 在 `connection_state_changed.emit(False)` **之前**，持 `_stats_lock` 只置 `_session_start_ts = 0.0`（断开态标记）；**不**清 `_total_bytes / _total_lines`——收发计数跨断开保留，由 UI「重置计数」按钮（`worker.reset_counts()`）显式清零。
- `_do_connect` 同样只置 `_session_start_ts = time.time()`（新会话时长起点），不清收发累计。
- `_set_disconnected_ui` 不再抹掉发送 / 接收显示（计数保留），只重置连接状态文案 + 时长占位。

关键不变量：清零 `start_ts` 必须在 `emit(False)` 之前，保证 UI 收到断开信号时 `get_stats()` 已报断开态（`start_ts == 0` 是 UI 轮询判定连接态的唯一真源）；收发计数是跨连接累计的运行态量，仅 `reset_counts()` 清零。

参考：`src/core/jlink_worker.py` `_do_connect` / `_do_disconnect` / `reset_counts` / `get_stats`、`src/ui/rtt_monitor_page.py` `_update_stats` / `_set_disconnected_ui`。

---

## `rtt_get_num_up_buffers()` 返回声明数不是已分配数，通道数要用 buf descriptor 的 SizeOfBuffer 计数

**现象**：多通道 RTT 上线后用户报"下位机实际上只有一个通道，但 RTT 选项能选 0、1、2，且故意选超出范围的通道（如 4）连接后会停在通道 2、显示区空白，只有断开重连才正常"。

**原因**：`jlink.rtt_get_num_up_buffers()` 返回的是固件 `_SEGGER_RTT` 控制块里声明的 `MaxNumUpBuffers`（上行缓冲描述符数组大小），**含"声明了但没初始化的空槽"**。实测某 STM32F030 固件声明 3 个上行缓冲，但 `rtt_get_buf_descriptor(ch, up=True)` 显示只有 ch0 的 `SizeOfBuffer=1024`（真实缓冲，正是 SEGGER 默认 `BUFFER_SIZE_UP`），ch1/ch2 的 `SizeOfBuffer=0`（空槽，永远没数据）。用声明数 3 当通道数 -> SpinBox 显示 0/1/2，选 4 被拉回到 max=2（ch2 空槽无数据）-> 正是用户看到的"停在 2、空白"。而紧凑重连时 `rtt_get_num_up_buffers()` 会抛 `The RTT Control Block has not yet been found (wait?)`（RTT 控制块定位是异步的，connect 返回时 J-Link 可能还没扫描到 `_SEGGER_RTT`）-> 原实现回退 1 -> 只剩 ch0（有数据）-> 用户看到的"断开重连才正常"其实是回退的副作用，不是真修好了。

**处理**：
1. **通道数用 buf descriptor 的 SizeOfBuffer 计数**，不用 `rtt_get_num_up_buffers()` 的返回值。遍历各通道 `rtt_get_buf_descriptor(ch, up=True)`，数 `SizeOfBuffer > 0` 的（从 0 起连续，遇空槽即停--SEGGER RTT 通道按惯例从 0 连续分配），得实际已分配通道数（该固件 = 1）。
2. **retry 要加在 buf descriptor 探测这一层**，不是加在 `rtt_get_num_up_buffers` 上。曾犯过的错：把 retry 加在 `rtt_get_num_up_buffers`（声明数 API）上，重连后也返回 3（还是显示 0/1/2），用户现象没变 -> 被撤销。retry 必须包住"取声明数 + 遍历 buf descriptor"整体，控制块未就绪时 0/150/300/450ms 重试 4 次再回退 1。
3. **诊断方法论**：这种"现象反直觉、API 语义隐蔽"的硬件问题，不要在大工程里猜改--写最小 demo（纯 pylink / 纯 Qt / 完整 app）在真实硬件上逐步打印每步返回值，定位到根因（哪个 API 返回什么、SizeOfBuffer 几）再动手。本轮 5 个 demo（`scratch/demo1-5`，未入库）定位后才一次修对。

判别：凡是"用户说的通道数"和"API 返回的通道数"对不上，先怀疑 API 返回的是声明数而非已分配数，用 `rtt_get_buf_descriptor().SizeOfBuffer` 交叉验证。

参考：`src/core/jlink_worker.py` `_detect_num_up_channels`、commit `78660d5`。


---

## 多 J-Link 接入：设备选择 + 按 serial 连接 + auto_reconnect 串行匹配

**现象**：用户电脑同时插了多台 J-Link 时，原来的「open() 空参让 pylink 自己挑」会随机抢到一台（或弹 DLL 原生选择窗）；auto_reconnect 在用户换了另一台 J-Link 后仍会连上（因为 open() 空参不区分设备）。

**原因**：pylink 1.6.0 的 `open()` 空参 = 「any available J-Link」，多设备下不可控。必须用 `open(serial_no=int)` 按序列号打开指定设备；识别 J-Link 的唯一稳定 ID 就是 `connected_emulators()` 返回的 `SerialNumber`（int）。

**处理**：
1. **设备选择下拉**：UI 在连接按钮上方加一个 ComboBox（串口助手的串口选择同款），显示文本即 serial 号。worker 提供 `enumerate_devices_requested` → `devices_enumerated(str)`（分号分隔 `"serial|product"`，str 跨线程安全）枚举接口，UI 刷新按钮 / 启动 `QTimer.singleShot(0)` 自动触发。**worker 不做可用性裁决**——UI 自己判断「上次选中的 serial 还在不在这批里」，不在且已连接则立即断开（不等 read_thread 检出 rtt_read 异常，滞后可能几秒）。
2. **连接**：`connect_requested` 5 参（target/iface/speed/channel/jlink_serial）。serial 非空且非 "0" 时 `_do_connect` 双开都 `open(serial_no=int)`；serial == "0" 视为「未指定」走 open() 空参（UI 启动后首次连接的默认串，也是测试 fixture 的默认值——避免改 38 个既有 emit 的真实 serial）。
3. **auto_reconnect 串行匹配**：`_last_connect_params` 改 5 元组，第 5 元素是连接成功时**真实读回**的 `serial_number`（不是 UI 传的"0"）。`_reconnect_tick` 里先枚举校验目标 serial 在接入列表（不在静默等下一拍），再 `_do_connect(*params)` 内部再做一次——两层防御。

**判别**：J-Link 的唯一 ID 是 `SerialNumber`（int），不是 USB 地址、不是产品名、不是枚举索引（同一台设备插拔后枚举索引会变）。

参考：`src/core/jlink_worker.py` `_on_enumerate_devices` / `_do_connect` / `_reconnect_tick`、`src/ui/rtt_monitor_page.py` `_on_devices_enumerated` / `_on_connect_clicked`、commit `<本轮>`。

---

## J-Link 远程连接（Remote Server）：J-Link DLL 不做 DNS，域名必须 Python 侧解析

**现象**：远程功能实测时，`jlink.open(ip_addr="localhost:19020")` 报 `Cannot connect to J-Link name localhost via TCP/IP`——pylink 底层 `JLINKARM_SelectIP` 把 host 原样传给 DLL，DLL 只接受 IPv4 字面量（或它自己的设备名），**不解析主机名**。

**原因**：`open(ip_addr="ip:port")` 内部 `rsplit(':', 1)` 后直接 `JLINKARM_SelectIP(addr.encode(), port)`，没有任何 getaddrinfo 路径。

**处理**：UI 侧统一走 `src/ui/widgets/remote_host.py` 的 `resolve_remote_host(host)`——IPv4 字面量原样返回，主机名（含 localhost）用 `socket.getaddrinfo(host, None, AF_INET)` 解析成 IPv4 再传给 worker；解析失败返回 None，UI 弹「无法解析主机名」合并警告，**不要**把未解析的域名透传给 pylink（否则用户看到的是 DLL 的英文内部错误）。

其他实测结论（Remote Server @ 192.168.79.1:19020 验证）：
- 远程连接序列与本地一致：`open(ip_addr=) → close → open(ip_addr=) → rtt_start → set_tif → set_speed → connect(target)`，RTT 收发正常。
- 远程 open 后 `serial_number` 可读（可像本地一样显示 S/N）。
- 不可达主机约 3s 抛 `JLinkException`（pylink 内置，**无超时参数可设**）——所以 UI 先用 `tcp_reachable()`（connect_ex，2s 超时）预检，错误提示才能分得清「网络不通」和「J-Link 协议失败」。
- `connected_emulators(host=JLinkHost.IP)` 实测返回空——远程设备**无法**通过枚举发现，只能用户显式输 IP；自动重连远程模式必须跳过 USB 枚举校验（worker `_reconnect_tick` 的 `_reconnect_remote_addr` 分支）。

参考：`src/ui/widgets/remote_host.py`、`src/core/jlink_worker.py` `_do_connect` 远程分支 / `_reconnect_tick`、`scratch/probe_remote.py` / `scratch/probe_remote_dns.py`、commit `2165bd0`。

---

## 测试/scratch 脚本里驱动 qfluentwidgets EditableComboBox：`setCurrentIndex` 选中后 `currentText()` 可能不同步

**现象**：scratch 冒烟脚本里 `cb_jlink.setCurrentIndex(0)`（远程项就在 index 0）之后，`cb_jlink.currentText()` 仍是旧值，`currentIndexChanged` 派发的槽拿到的也是旧文本——表现像"选中了但页面没反应"。同一套路径在 pytest（qtbot）里却正常。

**原因**：qfluentwidgets `EditableComboBox` 的 lineEdit 同步依赖其内部事件/焦点路径，裸脚本无事件循环深度处理时 `setCurrentIndex` 不会可靠地把文本刷进 lineEdit（与 `setCurrentText` 对非 items 文本 no-op 是同一家族的坑）。

**处理**：UI 层冒烟脚本不要依赖 `setCurrentIndex` 触发完整链路；改为「`setText(目标文本)` + 直接调页面自己的槽（如 `page._on_jlink_selection_changed()`）」——这模拟的是用户点选后槽函数拿到的最终状态，才是要验证的页面逻辑。pytest 里 qtbot 的路径（`setCurrentIndex` + `qtbot.wait`）目前工作正常，测试照常用；只有独立 scratch 脚本需要绕。

参考：`scratch/smoke_remote_ui.py`（最终用 setText + 直调槽验证通过）。
