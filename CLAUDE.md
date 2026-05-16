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

**处理**：直接 `decoded = decoder.decode(bytes(data))` 即可，半字节缓冲在 decoder 内部维护。不要在外层叠一层 byte_buffer。每次重连前 `_reset_utf8_decoder()` 重建 decoder，避免上次掉线残留污染。

参考：`src/core/jlink_worker.py` `_poll_rtt` / `_reset_utf8_decoder`

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
