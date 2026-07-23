# CMSIS-DAP / ST-Link RTT 支持实施方案(pyOCD 路线)

> 状态:待执行 | 撰写日期:2026-07-22 | 目标版本:v0.2.0
> 执行者须知:本文档是**完整实施契约**。严格按「约束清单」执行,违反任何一条即视为未完成。每个阶段有明确验收标准,全部通过才算完成。

---

## 0. 背景与目标

### 0.1 现状

- 本项目 RTT 查看**仅支持 J-Link**(走 `pylink-square==1.6.0`),代码在 `src/core/jlink_worker.py`(`JLinkWorker`)。
- 烧录功能已支持 CMSIS-DAP / ST-Link(走 pyOCD,`src/core/probe/pyocd_backend.py` 的 `PyOCDBackend`,仅实现 connect/erase/program/verify/reset/close,**未触及 RTT**)。
- 已安装 pyOCD **0.45.0**(`.venv`,`requirements.txt` 约束 `pyocd>=0.36`)。

### 0.2 目标

让 RTT 监控页可以选择 CMSIS-DAP / ST-Link 烧录器,查看目标 MCU 的 SEGGER RTT 输出(含多通道、发送下行数据、统计、日志录制),体验与 J-Link 路径一致。

### 0.3 技术依据(已完成全网调研,结论可靠)

- pyOCD **自 0.33.0(2022-01)起内置 RTT**,实现于 `pyocd/debug/rtt.py`,是**纯 host 软件实现**:通过普通内存读写定位 `_SEGGER_RTT` 控制块并操作环形缓冲,**任何调试器(CMSIS-DAP/ST-Link/J-Link)都能用**,官方明确 "works with all debug probe types, not only J-Link"。
- DAPLink 固件本身无原生 RTT,但 host 工具借其 CMSIS-DAP 内存访问即可实现——本方案即此路径。
- **API 以本地 `.venv/Lib/site-packages/pyocd/debug/rtt.py`(0.45.0)源码为准**。调研所得接口骨架:
  ```python
  from pyocd.debug.rtt import RTTControlBlock
  cb = RTTControlBlock.from_target(target, address=None, size=None,
                                   control_block_id=b'SEGGER RTT')
  cb.start()                       # 搜索并解析控制块,填充 up_channels/down_channels
  data: bytes = cb.up_channels[n].read()
  written: int = cb.down_channels[n].write(data, blocking=False)
  ```
  ⚠️ 执行第一步必须通读本地 venv 的 `rtt.py` 全文,核对以下细节并记录到本文件末节「0.4 API 核对记录」:
  1. `GenericRTTUpChannel` 缓冲区大小字段的准确属性路径(预期在 ctypes 结构体上,如 `channel._buffer.sizeOfBuffer` 之类——**以源码为准**);
  2. `start()` 在找不到控制块时的行为(抛什么异常 / 静默返回?);
  3. `up_channels` 是按 `MaxNumUpBuffers`(声明数)还是已分配数实例化;
  4. `read()` 返回类型是 `bytes` 还是 `bytearray`;
  5. 目标 halt 状态下 `read()` 是否仍可工作(实现直接 `read_memory_block32`,理论上可以,但 RTT 数据产生需要目标运行)。

### 0.4 API 核对记录

> 执行者在 Phase 0 完成后填写此节,作为后续阶段的依据。

```
(待填)
```

---

## 1. 总体架构决策

### 1.1 决策:共享基类 + 双 worker,不重写 J-Link 路径

```
                    MainWindow
                   /          \
        QThread A              QThread B
        JLinkWorker            PyocdRttWorker      ← 都继承 BaseRttWorker
        (现有,纯搬家)           (新增)
                   \          /
              RttMonitorPage(持有两个 worker 引用,
               按当前选中设备类型路由信号)
```

- 抽出 `BaseRttWorker`,容纳**全部通用机制**:drain buffer + 50ms drain timer、读线程生命周期(`_read_loop` / `_pause_read_thread` / `_restart_read_thread`)、每通道 IncrementalDecoder、统计(`_channel_stats` / `get_stats` / `reset_counts`)、日志录制、状态机(`_STATE_*`)、意外断开闭环(`_unexpected_disconnect_pending` → `_drain_rtt_buffer` → `_on_unexpected_disconnect`)。
- `JLinkWorker(BaseRttWorker)`:**现有代码原样搬家**,只保留 J-Link 特有的探针原语实现。类名、所有 Signal 签名、行为**一律不变**。
- `PyocdRttWorker(BaseRttWorker)**:新增,只实现 pyOCD 特有的探针原语。

### 1.2 基类与子类的职责切分(模板方法模式)

基类定义以下**抽象原语**,子类实现:

| 原语 | JLinkWorker 实现(现有逻辑搬家) | PyocdRttWorker 实现(新增) |
|---|---|---|
| `_probe_connect(params) -> None` | pylink 双开 + rtt_start + set_tif/speed + connect(**严格保持现有顺序**) | pyOCD session 建立 + `target.resume()` + `RTTControlBlock.from_target().start()`(带重试) |
| `_probe_close() -> None` | `rtt_stop()` + `close()`,各自 try/except | `session.close()`,try/except |
| `_probe_read(channel, size) -> bytes` | `jlink.rtt_read(ch, 4096)` | `cb.up_channels[ch].read()` |
| `_probe_write(channel, data) -> int` | `jlink.rtt_write(ch, data)` | `cb.down_channels[ch].write(data)` |
| `_probe_num_up_channels() -> int` | 现有 `_detect_num_up_channels`(声明数 → SizeOfBuffer>0 计数,4 次重试) | 同等语义:遍历 `cb.up_channels`,按缓冲区 SizeOfBuffer>0 从 0 连续计数(**复用 CLAUDE.md「声明数≠已分配数」教训**) |
| `_probe_device_info() -> dict` | 现有 `_collect_device_info` | pyOCD 版:目标型号 / 烧录器类型+product / serial / 接口 / 速度 |
| `_probe_reset(halt: bool) -> None` | `jlink.reset()`(+ halt 处理) | `target.reset_and_halt()` / `target.reset()` |
| `_probe_set_power(on: bool) -> None` | `jlink.power_on/off` | **抛 `NotImplementedError`**,worker 转为 `command_result(False, "该烧录器不支持目标供电控制")` |
| `_probe_enumerate() -> str` | 现有 `_on_enumerate_devices` 的 jlink 枚举,输出 `"serial|product;..."` | `core.probe.enumerator.enumerate_pyocd_probes()`,输出 `"kind|serial|product;..."` |

基类的 `_poll_all_channels` 改为调 `self._probe_read(ch, 4096)`;`_on_send_data` 调 `self._probe_write`;`_do_connect` / `_do_disconnect` 骨架留在基类,探针调用点换成原语。

### 1.3 为什么不选"单一 worker + backend 策略类"

把现有 J-Link 路径拆进 backend 策略类要改动每一处 `self.jlink.*` 调用点,而 `test_jlink_worker.py` 的 fixture 直接 `monkeypatch.setattr(pylink, "JLink", fake)` 并依赖 worker 内部结构。双 worker 方案:
- J-Link 路径是**纯代码搬家**(方法体一字不动),现有测试不改一行即可验证无回归;
- pyOCD 路径是**全新代码**,不触碰任何久经考验的 J-Link 时序(CLAUDE.md 里十几条血泪教训全部天然保留);
- 两个 worker 各自独占一条 QThread,互不阻塞,符合项目既有 `moveToThread` 范式。

代价是 UI 要持有两个 worker 引用并路由——这是可控的、集中在 `RttMonitorPage` 的改动。

---

## 2. 约束清单(硬性,违者返工)

以下全部来自 CLAUDE.md 已验证教训与本次架构决策,执行中**不得违反**:

### 线程与 Qt

1. **worker 直接继承 QObject,严禁继承 QThread**;由外部创建 QThread + `moveToThread` + `thread.started.connect(worker.initialize)`。
2. **native `threading.Thread`(读线程)永远不直接 emit Qt signal、不 emit `log_message`**;只写 `self._rtt_drain_buffer`(持 `_rtt_drain_lock`),由 worker 线程 50ms drain timer 统一 emit。读线程异常路径只 `_logger.error()` + 置 `_unexpected_disconnect_pending`。
3. **跨线程 Signal 参数只准 `bool/int/str/bytes`**——严禁 dict/list/自定义对象。设备信息走 `get_device_info()` 同步方法 + lock。
4. worker → UI 信号一律显式 `Qt.QueuedConnection`。
5. `_on_stop` 内在 `thread().quit()` **之前**显式 stop + deleteLater 所有 worker 线程内创建的 QTimer(PyocdRttWorker 的 drain timer、枚举 timer 同此)。
6. 所有依赖事件循环/跨线程的对象(QTimer、pyOCD session、decoder)**必须在 `initialize()` 或更晚创建**,不在 `__init__`。

### pylink / J-Link 路径

7. **J-Link 现有连接序列一字不改**:`open → close → open(serial) → rtt_start → set_tif → set_speed → connect`。搬家时连同注释一起搬。
8. `pylink-square==1.6.0` 不动;`set_tif` 二选一不 OR。
9. J-Link 的清理调用(`rtt_stop`/`close`)逐个 try/except + warning,不用 `opened()`/`connected()` 守卫。
10. 现有 `tests/` **全部测试不许修改**(新功能加新文件);每阶段结束 `pytest` 全绿才允许进入下一阶段。唯一例外:若 Phase 1 纯搬家导致某测试 import 路径失效,只允许改 import,不许改断言。

### pyOCD 路径

11. **pyOCD session 非线程安全**:`PyocdRttWorker` 增加 `self._io_lock = threading.Lock()`,`_probe_read` / `_probe_write` / `_probe_reset` / `_probe_num_up_channels` 内部全部持锁。阻断性操作(connect/disconnect/reset_with_reconnect)仍走基类 `_pause_read_thread` 先停读线程。(J-Link 侧不加锁,保持原样。)
12. **pyOCD 连接后必须 `target.resume()`**:session open 后目标可能处于 halt,RTT 不产生数据。defensive 调一次,包 try/except。
13. **控制块搜索带重试**:`RTTControlBlock.from_target(target).start()` 在固件刚复位/RTT 未初始化时会找不到控制块。与 `_detect_num_up_channels` 同策略:0/150/300/450ms 共 4 次,仍失败则视为"目标无 RTT",连接失败并给出可操作中文提示(「未找到 RTT 控制块:确认固件已初始化 SEGGER RTT,或先复位运行」)。
14. **通道数按已分配计数**:pyOCD 按 `MaxNumUpBuffers`(声明数)实例化 `up_channels`,必须按各通道缓冲区 SizeOfBuffer>0 从 0 连续计数(遇 0 即停),得到真实通道数。属性路径以 0.4 节核对结果为准。
15. 下行通道不存在时(固件只定义了 up buffer),`cb.down_channels[ch]` 会 IndexError——捕获并 `command_result("send_data", False, "目标固件无该通道的下行缓冲,无法发送")`。
16. pyOCD 枚举在 `JLINK_RTT_TEST_MODE` 环境变量存在时跳过(与 `FlashWorker.initialize` 同款守卫),避免测试环境 USB 枚举。

### UI / 配置 / i18n

17. **烧录器选择 combo 遵守单向数据流**(CLAUDE.md 烧录器下拉三条教训):`_selected_probe_kind` / `_selected_probe_serial` 是唯一真源;combo 只负责显示和捕获点选;程序重建 combo 后**不**从 `currentText()`/`currentIndex()` 反解逻辑状态;`setCurrentIndex(idx)` 后补 `setText(itemText(idx))`。
18. **不用控件文本当状态**;`_is_connected` 真状态字段模式沿用。
19. 模式/类型字符串必须有模块级常量:`PROBE_KIND_JLINK = "jlink"` / `PROBE_KIND_PYOCD = "pyocd"`(定义在基类模块,UI/worker 共用 import)。烧录器细分类复用现有 `BURNER_KIND_STLINK` / `BURNER_KIND_CMSIS_DAP`。
20. 新增用户可见字符串一律 `self.tr(...)` 并同步 `src/i18n/*.json`(含 zh_CN.json 若涉及英文 source 的第三方控件)。
21. `_retranslate_ui` 中所有静态文本显式 setText;tooltip 遵守 `_tip` 幂等模式(`_fluent_tip_installed` 属性守卫)。
22. 新增 cfg key 进 `config_service.DEFAULTS`;`set()` 高频值节流由现有机制保证,不绕过。
23. `PyocdRttWorker` 的日志、错误提示文案风格与 J-Link 侧一致(中文、可操作)。

### 范围约束(v1 明确不做)

24. **内存查看页(`read_memory`/`write_memory`/`export_firmware`)v1 不支持 pyOCD 连接**——这些信号只接在 `JLinkWorker` 上。pyOCD 连接状态下内存页表现 = 未连接(JLinkWorker 处于 idle),这是可接受的已知边界,在 README 注明。
25. pyOCD 远程探针(probe server)不支持;RTT 页「远程连接...」项仍仅属 J-Link。
26. 不做 SEGGER SystemView。
27. `requirements.txt` 仅把 pyocd 注释更新(`# 烧录 + CMSIS-DAP/ST-Link RTT`),版本约束保持 `pyocd>=0.36`(RTT API 自 0.33 起稳定)。

---

## 3. 文件级改动清单

### 3.1 新增文件

| 文件 | 内容 |
|---|---|
| `src/core/base_rtt_worker.py` | `BaseRttWorker`(QObject):全部通用机制 + 抽象原语(见 1.2)。从 `jlink_worker.py` 搬入。模块级常量 `_STATE_*`、`PROBE_KIND_JLINK` / `PROBE_KIND_PYOCD` 也放这里 |
| `src/core/pyocd_rtt_worker.py` | `PyocdRttWorker(BaseRttWorker)`:pyOCD 原语实现 + `_io_lock` + pyOCD 枚举 timer |
| `tests/test_pyocd_rtt_worker.py` | pyOCD worker 单测(fixture 仿 `test_jlink_worker.py`,mock pyOCD) |
| `tests/test_base_rtt_worker_refactor.py`(可选) | 若有基类特有的新逻辑(如 io_lock 之外的通用新代码)才建 |
| `scratch/smoke_pyocd_rtt.py` | 真实硬件冒烟脚本(有 DAPLink 时手动跑,不进 CI) |

### 3.2 修改文件

| 文件 | 改动 |
|---|---|
| `src/core/jlink_worker.py` | `JLinkWorker` 改为继承 `BaseRttWorker`,删除已搬走的通用代码,只留 J-Link 原语 + jlink 特有信号(`connect_remote_requested`、`set_power_output_requested` 等)。**所有 Signal 签名不变** |
| `src/ui/main_window.py` | 创建第二条 QThread + `PyocdRttWorker`,同样 moveToThread/initialize;`closeEvent` 中对两个 worker 都 emit `stop_requested` + wait;把两个 worker 引用传给 `RttMonitorPage` |
| `src/ui/rtt_monitor_page.py` | 见第 4 节 |
| `src/core/config_service.py` | DEFAULTS 增加:`"rtt_probe_kind": "jlink"`、`"last_pyocd_serial": ""` |
| `src/i18n/*.json` | 新增字符串翻译 |
| `requirements.txt` | 更新 pyocd 行注释 |
| `README.md` | 功能列表补充 CMSIS-DAP/ST-Link RTT;注明内存页边界(约束 24) |
| `CLAUDE.md` | 完成后追加本次新踩坑(如有) |

---

## 4. RttMonitorPage 详细设计

### 4.1 设备选择 combo(`cb_jlink`)数据模型

item userData 改为结构化字符串(单点解析):

```
"jlink:<serial>"            本地 J-Link(现有设备)
"pyocd:<burner_kind>:<serial>"   CMSIS-DAP / ST-Link
"remote:"                   远程连接...(现有,仅 J-Link)
```

label 沿用现有格式:`"{product}: {serial}"`。新增模块级单点真源:

```python
def _probe_item_data(kind: str, serial: str, burner_kind: str = "") -> str: ...
def _parse_probe_item(data: str) -> tuple[str, str, str]:  # (probe_kind, burner_kind, serial)
```

⚠️ 现有 `_serial_from_label` 的"从 label 反解 serial"模式**废弃**,一律读 userData(约束 17)。

combo 真源字段:`self._selected_probe_kind` / `self._selected_serial`(jlink serial 或 pyocd serial)/ `self._selected_burner_kind`(pyocd 时有效)。点选槽是唯一 combo→真源同步点;重建函数是真源→combo 同步点。

### 4.2 设备列表来源合并

- J-Link 设备:现有 `JLinkWorker.devices_enumerated`(`"serial|product;..."`)。
- pyOCD 设备:`PyocdRttWorker` 新增信号 `pyocd_devices_enumerated = Signal(str)`(格式 `"burner_kind|serial|product;..."`),由其枚举 timer(200ms,`JLINK_RTT_TEST_MODE` 守卫)调 `core.probe.enumerator.enumerate_pyocd_probes()` 产生。
- 页面收两路信号,合并重建 combo(重建即真源→combo 同步,约束 17)。
- 远程项固定排最后。
- 选中校验:真源 serial 不在当前列表 → J-Link 已连接则断开(现有行为);pyOCD 已连接则同样立即断开(同一处理函数,按 kind 取列表)。

### 4.3 连接路由

```python
def _on_connect_clicked(self):
    if self._is_connected:
        self._request_disconnect(...)   # 向"当前活动 worker"emit disconnect_requested
        return
    kind = self._selected_probe_kind
    if kind == PROBE_KIND_JLINK:
        if 远程项: self._jlink_worker.connect_remote_requested.emit(...)
        else:      self._jlink_worker.connect_requested.emit(target, iface, speed, channel, serial)
    else:  # pyocd
        self._pyocd_worker.connect_requested.emit(target, iface, speed, channel, serial)
        # burner_kind 通过第 6 参或单独 set 信号传递——见 4.4
```

`PyocdRttWorker.connect_requested = Signal(str, str, int, int, str, str)`(target, iface, speed, channel, burner_kind, serial)——**比 J-Link 多一参,是故意拆开的不同信号**,不共用(避免给 J-Link 信号塞无效参数)。

### 4.4 双 worker 信号接线

页面 `_active_worker()` helper 返回当前连接的 worker;以下信号**两个 worker 都接**到同一组页面槽:

```
rtt_data_received / connection_state_changed / log_message /
command_result / unexpected_disconnect / devices 类(各自格式不同,分开接)
```

页面状态真源:`_is_connected` + `_active_probe_kind`。`connection_state_changed` 槽内先判断 sender 是否=当前 `_active_worker()`(用 `self.sender()`),不是则忽略(防止非活动 worker 的残留信号污染 UI)。

`_set_connected_ui` 需要按 `_active_probe_kind` 差异化:
- pyOCD 时 `chk_power.setEnabled(False)` + tooltip「该烧录器不支持目标供电控制」;
- 设备信息行:pyOCD 的 `get_device_info()` 返回的 key 集合与 J-Link 对齐(目标设备/烧录器/serial/接口/速度),`_info_rows` 缺的 key 补 label + i18n。

### 4.5 断开 / 掉线 / 统计

- 断开按钮 → `_active_worker().disconnect_requested.emit()`。
- `unexpected_disconnect(identifier)`:两个 worker 都接,提示文案区分设备类型(identifier 由 worker 侧组好,如 `"CMSIS-DAP 0037..."`)。
- `_update_stats` 轮询改读 `_active_worker().get_stats()`(未连接时读谁都不重要,但统一走 active,J-Link 为默认)。
- `reset_counts` / `set_rtt_channel_requested` / `set_pause_receive_requested` / `set_encoding_requested` / `set_poll_interval_requested` / `start_log_recording_requested` / `stop_log_recording_requested` 同样路由到 active worker。**注意**:这些"设置类"信号最好两个 worker 都 emit 一遍(让非活动 worker 也同步配置,切换连接后行为一致)——除 `reset_counts` 只发 active。

### 4.6 cfg 持久化

- 连接成功后写:`rtt_probe_kind`、`last_jlink_serial` 或 `last_pyocd_serial`(按 kind)。
- 启动恢复:按 `rtt_probe_kind` + 对应 serial 在合并列表里找;找不到 → 保持离线占位(约束 17 的单向数据流,占位 label 用缓存的 product:`flash_burner_cache` 已有 serial→{kind,product} 缓存模式,pyOCD 探针复用该 key,不新建)。

### 4.7 自动重连(auto_reconnect)

现有机制:UI 侧轮询(USB 枚举 200ms / 远程 TCP 400ms)发现"上次设备回来了"就重连。扩展:
- 页面记录 `_last_connect_route = (probe_kind, burner_kind, serial, target, iface, speed, channel)`;
- pyOCD 路线路的"设备回来了"判定 = `enumerate_pyocd_probes()` 结果中存在同 kind+serial;
- 重连 emit 4.3 的对应信号。
- 若本轮实现超期,此节可降级为「pyOCD 路线不做自动重连,掉线只提示」,但**必须在 README 和设置页明示**。

---

## 5. PyocdRttWorker 连接序列(基准实现)

```python
def _probe_connect(self, params) -> None:
    # params: (target_name, iface, speed, channel, burner_kind, serial)
    from pyocd.core.helpers import ConnectHelper
    from pyocd.debug.rtt import RTTControlBlock

    # 1) target_override 解析——复用 PyOCDBackend._resolve_target_type 的同款逻辑。
    #    ⚠️ 不许复制粘贴:把 _resolve_target_type + _pack_part_wildcard_eq 从
    #    pyocd_backend.py 提取到 src/core/probe/pyocd_target.py(单点真源),
    #    PyOCDBackend 和本 worker 都 import。
    target_override = resolve_pyocd_target(params.target_name)
    if target_override is None:
        raise ProbeConnectError(f"未知 target:{params.target_name}(pyOCD 未安装对应 pack)")

    # 2) 建 session(按 burner_kind + serial 选定探针)
    self._session = ConnectHelper.session_with_chosen_probe(
        unique_id=(params.serial or None),
        target_override=target_override,
        options={
            "transport": "swd" if params.iface == "SWD" else "jtag",
            "frequency": int(params.speed) * 1000,
        },
    )
    if self._session is None:
        raise ProbeConnectError("未找到指定烧录器,请刷新设备列表")
    self._session.open()
    self._target = self._session.target

    # 3) RTT 需要目标运行(约束 12)
    try:
        self._target.resume()
    except Exception as e:
        self._logger.warning(f"target.resume warn: {e}")

    # 4) 定位 RTT 控制块,带重试(约束 13)
    last_err = ""
    for attempt in range(4):
        try:
            cb = RTTControlBlock.from_target(self._target)
            cb.start()
            if cb.up_channels:
                self._rtt_cb = cb
                break
        except Exception as e:
            last_err = str(e)
        if attempt < 3:
            time.sleep(0.15)
    else:
        raise ProbeConnectError("未找到 RTT 控制块:确认固件已初始化 SEGGER RTT 并正在运行")
```

`_probe_close`:`session.close()` + try/except(约束 9 同款);`_probe_read` 持 `_io_lock` 调 `self._rtt_cb.up_channels[ch].read()`;读异常(USB 断开 → pyOCD 抛 `ProbeError`/`DeviceError`)沿基类既有意外断开闭环走,**不需要新机制**。

`_probe_device_info` 返回示例:

```python
{
    "target_device": target_override,
    "probe": f"{burner_kind_label}: {serial}",   # 如 "CMSIS-DAP: 0037..."
    "interface": iface, "speed_khz": speed,
    # key 集合与 J-Link 侧 _collect_device_info 对齐;没有的语义(如 J-Link 固件版本)不填空key
}
```

---

## 6. 分阶段实施与验收

### Phase 0 — API 核对(0.5 天)

- 通读 `.venv/Lib/site-packages/pyocd/debug/rtt.py` 全文,填写本文 0.4 节;
- 写 `scratch/probe_pyocd_rtt_api.py`:用 MagicMock/fake target 验证 `from_target`/`start`/`read`/`write` 的调用形状与返回类型;
- **验收**:0.4 节填完,核对项 1-5 全部有明确答案。

### Phase 1 — 基类抽取,纯搬家(1 天)

- 建 `base_rtt_worker.py`,把通用机制从 `jlink_worker.py` 原样搬入(方法体不动,`_probe_*` 抽象出来);
- `JLinkWorker` 改继承,删搬走部分;
- **验收**:`pytest tests/ -x -q` 全绿,且除 import 外**零测试改动**;手动跑一遍 app,J-Link 连接/收发/断开/关闭窗口无警告(对照 CLAUDE.md killTimer/setParent 两条,控制台必须干净)。

### Phase 2 — PyocdRttWorker + 单测(2 天)

- 实现 `pyocd_rtt_worker.py` 全部原语;
- 提取 `resolve_pyocd_target` 到 `src/core/probe/pyocd_target.py`,`pyocd_backend.py` 改为 import(约束:单点真源);
- 新测试 `tests/test_pyocd_rtt_worker.py`,fixture 仿 `test_jlink_worker.py`(moveToThread 模式),mock 点:
  - `pyocd.core.helpers.ConnectHelper.session_with_chosen_probe` → fake session/target;
  - `pyocd.debug.rtt.RTTControlBlock.from_target` → fake control block(up_channels 返回预设 bytes,down_channels 记录 write);
  - **信号 spy 用 QObject bound-method 槽,不用裸 lambda**(CLAUDE.md 教训);
- 用例至少覆盖:连接成功/连接失败(未知 target、找不到控制块重试 4 次后报错)/读数据进 drain/发送数据/无下行缓冲报错/断开清理/意外断开闭环/num_up_channels 按 SizeOfBuffer 计数(声明 3 实际 1 的用例,对齐 CLAUDE.md 同款坑);
- **验收**:新旧测试全绿。

### Phase 3 — UI 集成(2 天)

- 按第 4 节改 `rtt_monitor_page.py` + `main_window.py` + `config_service.py` + i18n;
- 新增/扩展 `tests/test_rtt_monitor_page.py` 用例:pyOCD 设备出现在 combo/点选后真源同步/连接路由到 pyocd worker 且参数正确/断开后 UI 复位/chk_power 在 pyOCD 下禁用/cfg 恢复;
- **验收**:全绿 + 手动跑 app,UI 在无硬件时 pyOCD 设备为空不崩。

### Phase 4 — 自动重连 + reset 模式(1 天)

- 4.7 节实现;`reset_requested` 两种模式(normal/auto_reconnect)对 pyOCD 路线可用;
- **验收**:新增用例全绿。

### Phase 5 — 冒烟与文档(0.5 天)

- 全量 `pytest`;`scratch/smoke_pyocd_rtt.py` 真实 DAPLink 冒烟(有硬件时):连接 → 收 RTT → 发下行 → 断开 → 关闭,控制台零 Qt 警告;
- 更新 README / requirements.txt 注释 / CLAUDE.md(新坑)。

---

## 7. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| Phase 1 搬家引入隐性回归 | 中 | 现有 38+ 测试是安全网,全绿才放行;搬家用"整方法剪切",不逐行重写 |
| pyOCD session 双线程并发(读线程 vs 发送/复位) | 中 | `_io_lock`(约束 11);发送路径持锁粒度=单次 write,不阻塞读拍 |
| 控制块搜索慢(全 RAM 扫描) | 低 | pyOCD 内部按 memory map 默认 RAM 区搜;实测慢则后续支持 cfg 指定搜索地址(v1 不做) |
| `cb_jlink` 数据模型改动踩 EditableComboBox 坑 | 中 | 严格约束 17;参考 `flash_page.py` `_rebuild_burner_combo` 现成范式 |
| 两个 worker 信号串扰 | 低 | `connection_state_changed` 槽内 `self.sender()` 校验(4.4) |
| pyOCD 枚举与 J-Link 枚举互相误列(J-Link 也会被 pyOCD 看到) | 中 | `enumerator._probe_kind` 已按类型名过滤掉 JLink,复用即可,不要重复过滤逻辑 |

---

## 8. 完成定义(Definition of Done)

1. `pytest tests/ -q` 全绿(旧测试零断言改动);
2. RTT 页可选 CMSIS-DAP/ST-Link,连接后能收 RTT(多通道)、发下行、统计/日志录制正常;
3. J-Link 全部既有功能无回归(手动过一遍:本地 USB/远程/复位两模式/暂停/HEX/编码切换);
4. 关闭窗口控制台零 Qt 跨线程警告;
5. README + i18n + CLAUDE.md 更新完毕;
6. 本文 0.4 节已填写。
