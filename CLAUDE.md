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

## Nuitka 打包 qfluentwidgets / pylink 资源

**现象**：Nuitka 打包后运行，qfluentwidgets qss 找不到、pylink 找不到 JLinkARM.dll。

**原因**：Nuitka 默认只打包 .py 源码，包内的 .qss / .dll / 图片资源不会跟着进。

**处理**：`build_nuitka.bat` 加：
```
--include-package=qfluentwidgets
--include-package-data=qfluentwidgets
--include-package=pylink
--include-package-data=pylink
```

参考：`build_nuitka.bat`

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
