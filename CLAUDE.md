# 项目经验笔记

为后续维护积累的实际踩坑经验。每条都带 **现象 / 原因 / 处理** 三段。

---

## pylink `close()` 在未连接时抛 `JLinkException`

**现象**：调用 `jlink.close()` 抛 `pylink.errors.JLinkException: There is no connected JLink.`

**原因**：pylink 把"没 open 过"和"open 过但已 close"都当成"无连接"，再次 close() 直接抛异常。原 PyWebView 项目里没有守卫，断开成功后稍微再点一次断开就报错。

**处理**：close 前必须 `if jlink.opened():`；同理 rtt_stop 前必须 `if jlink.connected():`。守卫语句应该把整个 close/rtt_stop 块各自包一层 try/except，except 内 `log_message.emit('warning', ...)`，不要让单一次清理失败阻断退出路径。

参考：`src/core/jlink_worker.py` `_do_disconnect`

---

## 不要做 "open → 取 serial → close → 再 open" 双开

**现象**：原项目 `jlink_service.connect()` 里有这样的代码：
```python
self.jlink.open()
ser_num = self.jlink.serial_number
self.jlink.close()
self.jlink.open(str(ser_num))
```

**原因**：本意可能是想"显式按序列号打开"，但 pylink 1.6.0 的 `open()` 不传 serial_no 时本来就会选第一个可用 J-Link。多余的 close 引入了线程时序窗口（read thread 可能还在用 jlink 句柄），是后续 close 死锁的根因之一。

**处理**：直接一次 `if not jlink.opened(): jlink.open()` 即可，不再传 serial_no 也不再双开。如果未来要支持多 J-Link 选择，加一个"选择序列号"对话框，把选中的 serial 传给 `open(serial_no=...)`，而不是先 open 再 close 再 open。

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

## 启动时不要做 main thread → worker QTimer.singleShot 信号转发

**现象**：日志出现 `QObject::startTimer: Timers cannot be started from another thread`，窗口关闭时 worker 不响应 stop_requested，主线程 wait 超时强制 terminate。

**原因**：曾在 `MainWindow.__init__` 用 `QTimer.singleShot(500, lambda: worker.signal.emit())` 给 worker 投递初始化配置。singleShot 在主线程触发，emit 触发 worker 内部 `_on_set_poll_interval` 调 `self._poll_timer.setInterval(ms)`，而此时 worker 的 run() 可能尚未完成初始化，_poll_timer 状态不一致；后续退出时 worker 事件循环无法正常响应 stop_requested。

**处理**：删除 main thread 到 worker 的 startup signal 转发；worker 以 `run()` 内的默认值（20 ms）运行。用户在设置页改 SpinBox 时才 emit，届时各 timer 已稳定。

参考：`src/ui/main_window.py` `__init__`
