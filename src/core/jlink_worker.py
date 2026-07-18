"""JLinkWorker：所有 pylink 调用都在 worker 线程，标准 Qt QObject + moveToThread 范式。

**重要：本类不继承 QThread。**

Qt 官方反复强调："不要继承 QThread 把业务逻辑放进去"。正确做法：
- 业务对象（本类）就是 QObject，所有信号/槽/状态都在这里。
- 调用方（MainWindow / 测试 fixture）外部创建 QThread，调 `worker.moveToThread(thread)`、
  `thread.started.connect(worker.initialize)`、`thread.start()`。
- worker 拥有真正的 worker 线程 thread affinity，所有信号槽操作都在同一线程，
  无 cross-thread 警告。

读循环：
- 用 `threading.Thread + time.sleep(0.1)` 独立于 Qt 事件循环，emit 信号只是 post 到主线程队列。
- disconnect 时先 `_stop_read = True` + `read_thread.join(timeout=2.0)`，确保读线程退出后才 rtt_stop/close。

设备枚举：
- worker 内建 200ms 自动枚举广播 `devices_enumerated`，UI 各页面纯消费，不各自起轮询（全局唯一轮询源）。
- 保留 `enumerate_devices_requested` 信号，方便页面/测试手动触发一次，worker 自己的 timer 也复用同一槽。

退出流程：
- 主线程 emit stop_requested → worker._on_stop 在 worker 线程跑 →
  清理 pylink → `self.thread().quit()` 让外部 thread 的 exec() 返回。
- 主线程 `thread.wait(timeout)`，不主动 quit/terminate（除非超时兜底）。
"""
from __future__ import annotations

import codecs
import threading
import time
from datetime import datetime
from pathlib import Path

import pylink
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import memory_service
from .logger import get_logger

_STATE_IDLE = "IDLE"
_STATE_CONNECTING = "CONNECTING"
_STATE_CONNECTED = "CONNECTED"
_STATE_DISCONNECTING = "DISCONNECTING"

# reset_mode 公开常量：worker 派发 + UI 配置 + settings combo 三处都引用，避免散落硬字符串
RESET_MODE_NORMAL = "normal"
RESET_MODE_AUTO_RECONNECT = "auto_reconnect"
# halt 不是用户可配的 reset_mode，而是「重置并暂停」按钮专用的一次性意图：
# reset 后让 CPU 停在复位状态（不运行、不断开重连）。
RESET_MODE_HALT = "halt"

# RTT 通道模型（worker 是权威定义，UI 只引用常量）：
# - 视图通道 -1 = 「全部通道」（合并视图），>=0 = 具体上行通道
# - 发送通道恒为具体通道：跟随最近选中的具体通道，初始 0
# - 探测范围：MCU 端 _SEGGER_RTT 控制块上报的 MaxNumUpBuffers（rtt_get_num_up_buffers）
CHANNEL_ALL = -1
CHANNEL_DEFAULT = 0


def encode_send_payload(data: str, is_hex: bool) -> bytes:
    """把发送文本编码为写入 RTT 的字节：HEX 模式按十六进制解码，否则 UTF-8 编码。

    worker _on_send_data 实际写入 与 UI 即时统计发送字节数 共用，避免编码逻辑重复。
    """
    if is_hex:
        cleaned = data.replace(" ", "").replace("\n", "").replace("\r", "")
        if len(cleaned) % 2 != 0:
            cleaned += "0"
        return bytes.fromhex(cleaned)
    return data.encode("utf-8")


class JLinkWorker(QObject):
    """J-Link 后台业务对象。**必须 moveToThread 到一个 QThread 后再用**。"""

    # ---- 输入信号 ----
    connect_requested = Signal(str, str, int, int, str)
    # 枚举电脑上接入的 J-Link 列表（UI 设备下拉框填充）。worker 自己枚举：
    # pylink 实例在 worker 线程创建，UI 直接调 pylink 会跨线程抢 DLL。
    enumerate_devices_requested = Signal()
    disconnect_requested = Signal()
    send_data_requested = Signal(str, bool)
    reset_requested = Signal(str)  # mode: "normal" / "auto_reconnect" —— worker 内部一条龙
    set_rtt_channel_requested = Signal(int)
    set_pause_receive_requested = Signal(bool)
    set_power_output_requested = Signal(bool)
    set_poll_interval_requested = Signal(int)
    set_encoding_requested = Signal(str)
    read_memory_requested = Signal(int, int)
    write_memory_requested = Signal(int, bytes)
    export_firmware_requested = Signal(str, int, int)
    start_log_recording_requested = Signal(str)
    stop_log_recording_requested = Signal()
    set_auto_reconnect_requested = Signal(bool)
    stop_requested = Signal()

    # ---- 输出信号 ----
    # 通道号 + 文本。int+str 是安全的跨线程类型（dict 会踩 PySide6 marshalling 坑）。
    # 显示区分发 / 按通道历史 / 全部通道合并 都在 UI 侧做，worker 只负责读 + 标注来源。
    rtt_data_received = Signal(int, str)
    # 意外断开（物理掉线）：read_thread 在 rtt_read 抛异常且仍处于连接态时
    # 置 _unexpected_disconnect_pending，由 worker 线程的 _drain_rtt_buffer 检出，
    # 在 worker 线程 emit 设备标识给 UI（红字提示）。不让 read_thread 直接 emit：
    # native threading.Thread emit Qt 跨线程信号在 PySide6 上不可靠（同 _rtt_drain_buffer）。
    unexpected_disconnect = Signal(str)
    # 自动重连状态：worker -> UI，在显示区追加带时间戳的染色行。
    # arg1=kind（disconnect_reconnecting/attempt/success/failed/cancelled），
    # arg2=detail（设备标识 / 重试次数 str）。不传 dict，符合跨线程信号规则。
    reconnect_status = Signal(str, str)
    # 注意：connection_state_changed 不传 dict——PySide6 跨线程 emit dict
    # 会触发 setParent cross-thread 警告并卡 worker 线程。设备信息改用
    # get_device_info() 同步方法（lock 保护）让 UI 主动取。
    connection_state_changed = Signal(bool)
    log_message = Signal(str, str)
    # command_result：dict → str（msg/error）。理由同 connection_state_changed：
    # 跨线程 emit dict 在 PySide6 上不可靠，会触发 setParent cross-thread 警告并卡线程。
    command_result = Signal(str, bool, str)
    memory_read_finished = Signal(int, bytes)
    firmware_export_progress = Signal(int, int)
    firmware_export_finished = Signal(bool, str, str)
    # 枚举结果：分号分隔一行一台 "serial|product"（str 跨线程安全，dict/list 不行），
    # 无设备时传空串。字段解析（含 acProduct 乱码容忍）在 UI 侧。
    devices_enumerated = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._logger = get_logger()
        self._state: str = _STATE_IDLE
        self._view_channel: int = CHANNEL_DEFAULT   # UI 选择的视图通道（-1 = 全部）
        self._send_channel: int = CHANNEL_DEFAULT   # 实际发送通道（恒为具体通道）
        self._num_up_channels: int = 1              # MCU 上报的上行通道数（连接后探测）
        self._paused: bool = False
        self._ready: bool = False

        # 这些在 initialize() 内（worker 线程）创建/启动：
        self.jlink: pylink.JLink | None = None
        self._decoders: dict[int, codecs.IncrementalDecoder] = {}  # 每通道独立 decoder
        self._encoding: str = "utf-8"      # 可由 UI 改：utf-8/gbk/utf-16-le/...
        self._read_thread: threading.Thread | None = None
        self._stop_read: bool = False
        self._poll_interval: float = 0.1   # 100ms，匹配参考项目
        self._log_file = None
        self._log_path: str | None = None

        # RTT 数据中转：read_thread 写入 (channel, text)（lock 保护），worker 线程
        # QTimer 取出合并 emit。**关键**：避免 native threading.Thread 直接 emit Qt signal。
        self._rtt_drain_buffer: list[tuple[int, str]] = []
        self._rtt_drain_lock = threading.Lock()
        self._rtt_drain_timer: QTimer | None = None

        # 意外断开中转：read_thread 检出 rtt_read 异常（物理掉线）时置位，
        # _drain_rtt_buffer 在 worker 线程检出后调 _on_unexpected_disconnect 闭环。
        # 同 _rtt_drain_buffer：read_thread 不直接 emit Qt 信号。
        self._unexpected_disconnect_lock = threading.Lock()
        self._unexpected_disconnect_pending = False

        # 设备信息：worker 端用 lock 保护，UI 通过 get_device_info() 同步读。
        # 不通过信号传 dict——dict 跨线程 marshalling 在 PySide6 上会卡 worker 线程。
        self._device_info: dict = {}
        self._info_lock = threading.Lock()

        # 上次成功连接的参数，给 auto_reconnect 模式重连用（worker 自给自足，
        # UI 不再需要重新 emit connect_requested）。serial 是 5 元组的第 5 元素，
        # auto_reconnect 重连时必须显式带它：只有上次那台 J-Link 还在接入
        # （serial 出现在 connected_emulators() 里）才允许重连——否则用户换了
        # 另一台 J-Link 会被 open() 空参默认误连（CLAUDE.md 多 J-Link 设计原则）。
        self._last_connect_params: tuple[str, str, int, int, str] | None = None

        # 自动重连（物理掉线后）：UI 勾选「自动重连」使能。掉线时若已使能，
        # 启动 _reconnect_timer 轮询 connected_emulators()，只认上次那个 serial，
        # 找到后 _do_connect 重连；失败 3s 后重试，直到成功或用户取消。
        self._auto_reconnect_enabled: bool = False
        self._reconnect_timer: QTimer | None = None
        self._reconnect_target_serial: str | None = None
        self._reconnect_attempt: int = 0

        # 设备枚举：worker 内建 200ms 轮询 timer，自动广播 devices_enumerated。
        # UI 各页面只连接信号消费，不各自起轮询（全局唯一轮询源）。
        self._enum_timer: QTimer | None = None

        # 数据吞吐统计（按通道）：read_thread 写入，UI 1s 一次同步读取（GIL + lock 保护）
        self._stats_lock = threading.Lock()
        self._channel_stats: dict[int, tuple[int, int]] = {}  # ch -> (bytes, lines)
        self._session_start_ts: float = 0.0   # 0 = 未开始会话

    # ============================================================
    # worker 线程初始化（由外部 QThread.started 触发）
    # ============================================================
    def set_initial_encoding(self, encoding: str) -> None:
        """主线程在 thread.start() 之前同步设置初始编码。

        Why: MainWindow 启动时如果通过 set_encoding_requested.emit 传递初始编码，
        会和 thread.started → initialize() 形成竞态——emit 时槽尚未连接，
        信号被静默丢弃，启动后 worker 永远停在默认 utf-8（即便 user_prefs 里
        存的是 gbk）。直接同步赋值绕过信号路径，安全因为 worker 线程还没启动。
        """
        if encoding:
            self._encoding = encoding

    @Slot()
    def initialize(self) -> None:
        """在 worker 线程内创建所有 thread-affinity 敏感的对象。"""
        self.jlink = pylink.JLink()
        self._reset_decoders()

        # 把输入信号连到本地槽（同线程，DirectConnection）
        self.connect_requested.connect(self._on_connect)
        self.enumerate_devices_requested.connect(self._on_enumerate_devices)
        self.disconnect_requested.connect(self._on_disconnect)
        self.send_data_requested.connect(self._on_send_data)
        self.reset_requested.connect(self._on_reset)
        self.set_rtt_channel_requested.connect(self._on_set_channel)
        self.set_pause_receive_requested.connect(self._on_set_paused)
        self.set_power_output_requested.connect(self._on_set_power)
        self.set_poll_interval_requested.connect(self._on_set_poll_interval)
        self.set_encoding_requested.connect(self._on_set_encoding)
        self.read_memory_requested.connect(self._on_read_memory)
        self.write_memory_requested.connect(self._on_write_memory)
        self.export_firmware_requested.connect(self._on_export_firmware)
        self.start_log_recording_requested.connect(self._on_start_log)
        self.stop_log_recording_requested.connect(self._on_stop_log)
        self.set_auto_reconnect_requested.connect(self._on_set_auto_reconnect)
        self.stop_requested.connect(self._on_stop)

        # RTT 数据中转 timer：worker 线程内 QObject，affinity 跟 self（worker_thread）。
        # 50ms 一次从 read_thread 写入的 buffer 取出 → emit 给 UI。
        # emit 在 worker_thread context 进行，Qt 跨线程信号传递走标准 QueuedConnection，行为可靠。
        self._rtt_drain_timer = QTimer()
        self._rtt_drain_timer.setInterval(50)
        self._rtt_drain_timer.timeout.connect(self._drain_rtt_buffer)
        self._rtt_drain_timer.start()

        # 自动重连轮询 timer：3s 一次，worker 线程 affinity。掉线后按需 start；
        # 成功 / 取消时 stop。timeout 在 worker 线程触发，_reconnect_tick 内 emit
        # reconnect_status 安全（同 _drain_rtt_buffer 的 worker-thread emit 模式）。
        self._reconnect_timer = QTimer()
        self._reconnect_timer.setInterval(3000)
        self._reconnect_timer.timeout.connect(self._reconnect_tick)

        # J-Link 设备枚举 timer：200ms 一次，worker 线程内自动广播 devices_enumerated。
        # UI 各页面只消费该信号，不各自起轮询（全局唯一轮询源）。
        self._enum_timer = QTimer()
        self._enum_timer.setInterval(200)
        self._enum_timer.timeout.connect(self._on_enumerate_devices)
        self._enum_timer.start()

        self._ready = True
        self._logger.info("JLinkWorker initialized in worker thread")

    def state_name(self) -> str:
        """状态名（线程安全：Python 单赋值原子）。"""
        return self._state

    def get_device_info(self) -> dict:
        """同步取设备信息副本。由 UI 主线程在 _on_state_changed(True) 时调用。

        用 lock 而非跨线程信号传 dict——避免 PySide6 setParent cross-thread 警告。
        """
        with self._info_lock:
            return dict(self._device_info)

    def get_stats(self, channel: int | None = None) -> dict:
        """同步取吞吐统计。channel=None → 全部通道合计；否则只取该通道。

        返回 {"bytes": int, "lines": int, "session_start_ts": float}。
        session_start_ts == 0 表示尚未开始 / 已断开。返回 dict 安全（非跨线程信号，
        是 UI 主动调的同步方法）。
        """
        with self._stats_lock:
            if channel is None:
                b = sum(v[0] for v in self._channel_stats.values())
                ln = sum(v[1] for v in self._channel_stats.values())
            else:
                b, ln = self._channel_stats.get(channel, (0, 0))
            return {"bytes": b, "lines": ln, "session_start_ts": self._session_start_ts}

    def get_num_up_channels(self) -> int:
        """同步取 MCU 上报的上行通道数（连接时探测）。UI 用来动态调 SpinBox 上限。"""
        return self._num_up_channels

    def reset_counts(self) -> None:
        """清零所有通道的收发字节/行计数，保留会话时长（_session_start_ts 不变）。

        供 UI「重置计数」按钮调用：用户主动清零收发统计，会话时长继续累计。
        与 _read_loop 增量写之间的竞争由 _stats_lock 串行化。
        """
        with self._stats_lock:
            self._channel_stats = {}

    # ============================================================
    # 设备枚举（UI J-Link 下拉框数据源）
    # ============================================================
    @Slot()
    def _on_enumerate_devices(self) -> None:
        """枚举当前接入的 J-Link，结果通过 devices_enumerated 发回 UI。

        设备可用性状态完全由 UI 自己判断（上次枚举拿到过几台、当前 combo
        选中项是否还在这批里），worker 不在枚举时做状态裁决。

        格式约定：分号分隔多台，单台 "serial|product"；无设备/异常时发空串
        （异常只记日志，不再 emit log_message 到 UI —— 200ms 高频轮询下弹
        warning 会刷屏）。acProduct 含 '|' 或 ';' 时截断丢弃——这两个字符是
        分隔符，混进字段会破坏 UI 解析。
        """
        try:
            emus = self.jlink.connected_emulators()
        except Exception as e:
            self._logger.warning(f"枚举 J-Link 失败：{e}")
            # 高频轮询失败不弹 UI warning，避免刷屏；只发空串让红点/列表更新
            self.devices_enumerated.emit("")
            return
        lines: list[str] = []
        for e in emus:
            try:
                serial = int(getattr(e, "SerialNumber", 0) or 0)
            except Exception:
                serial = 0
            if serial <= 0:
                continue  # 非法序列号无法用于 open(serial_no=...)，跳过
            raw = getattr(e, "acProduct", b"")
            if isinstance(raw, (bytes, bytearray)):
                product = bytes(raw).decode("utf-8", errors="replace").strip("\x00").strip()
            else:
                product = str(raw or "").strip()
            for sep in ("|", ";"):
                if sep in product:
                    product = product.split(sep, 1)[0].strip()
            lines.append(f"{serial}|{product}")
        self.devices_enumerated.emit(";".join(lines))

    # ============================================================
    # 连接 / 断开
    # ============================================================
    @Slot(str, str, int, int, str)
    def _on_connect(self, target: str, iface: str, speed: int, channel: int,
                    jlink_serial: str) -> None:
        """connect_requested 信号槽。实现在 _do_connect，方便其他 worker 内部
        路径（如 _reset_with_reconnect）以普通函数方式直接调，不走信号队列。"""
        # 手动连接取消正在进行的自动重连，避免与定时器的 _do_connect 赛跑
        self._stop_reconnect()
        self._do_connect(target, iface, speed, channel, jlink_serial)

    def _do_connect(self, target: str, iface: str, speed: int, channel: int,
                    jlink_serial: str = "") -> None:
        if self._state == _STATE_CONNECTED:
            self.log_message.emit("warning", "已连接，先断开再切换设备")
            return
        self._state = _STATE_CONNECTING
        self._view_channel = channel
        if channel >= 0:
            self._send_channel = channel
        try:
            # 预查 J-Link 是否接入：connected_emulators() 用 JLINKARM_EMU_GetList 纯枚举，
            # 不弹 DLL 原生选择窗。空则气泡提示 + 回退（_do_disconnect emit 状态 False
            # 让 UI 按钮回正），避免无设备时 jlink.open() 弹出只能鼠标关闭的 DLL 弹窗。
            emus = self.jlink.connected_emulators()
            if not emus:
                self.log_message.emit("warning", "未检测到 J-Link 设备，请检查 USB 连接")
                self._do_disconnect()
                return
            # jlink_serial 是 UI 传的选中 J-Link serial，也可能是 "0"（UI 启动后
            # 首次连接的默认串）。"0" 视为「未指定」：跳过 serial 匹配校验 + 走
            # open() 空参（让 pylink 自己挑唯一接入的设备）。真实 serial 才校验
            # 「这台还在接入」——否则 open(serial) 对不存在的 serial 会直接抛，
            # 且「上次是 A，现在只剩 B」时 auto_reconnect 会误连 B（CLAUDE.md
            # 多 J-Link 设计原则）。
            if jlink_serial and jlink_serial != "0" and not any(
                    str(int(getattr(e, "SerialNumber", 0) or 0)) == jlink_serial
                    for e in emus):
                self.log_message.emit(
                    "warning", f"选中的 J-Link（S/N: {jlink_serial}）不在线，请刷新设备列表或重新选择")
                self._do_disconnect()
                return
            if not self.jlink.opened():
                # 参考项目的双开模式（pylink 1.6.0 稳定工作的关键模式）：
                # 先 open() 一次取 serial，close，再 open(serial)，然后 rtt_start
                # 注意：rtt_start 必须在 connect(target) 之前调用
                if jlink_serial and jlink_serial != "0":
                    # 指定了具体 J-Link：双开都按 serial（open() 空参在多设备下
                    # 可能弹 DLL 选择窗或抢到另一台）
                    self.jlink.open(serial_no=int(jlink_serial))
                    ser_num = self.jlink.serial_number
                    self.jlink.close()
                    self.jlink.open(serial_no=int(ser_num))
                    self.jlink.rtt_start()
                else:
                    # 未指定（"0" / 空串）：open() 空参，pylink 自己挑唯一接入的设备；
                    # 内部调用方（auto_reconnect / reset_with_reconnect）永远带真实
                    # serial，不走这里
                    self.jlink.open()
                    ser_num = self.jlink.serial_number
                    self.jlink.close()
                    self.jlink.open(str(ser_num))
                    self.jlink.rtt_start()

            tif = pylink.enums.JLinkInterfaces.SWD if iface == "SWD" \
                else pylink.enums.JLinkInterfaces.JTAG
            self.jlink.set_tif(tif)
            self.jlink.set_speed(int(speed))
            self.jlink.connect(target)
            self._reset_decoders()
            self._num_up_channels = self._detect_num_up_channels()

            if self.jlink.connected():
                self._state = _STATE_CONNECTED
                # 连接成功：以实际打开的 serial（open 后真实读回）记入参数，
                # 保证 auto_reconnect 重连时只认这台 J-Link。
                # ser_num 只在「未 opened() 时走双开」分支赋值；opened()=True 的
                # 已打开路径没有这变量，此时回退用 jlink.serial_number 直接读。
                if self.jlink.opened():
                    sn = self.jlink.serial_number
                    actual_serial = str(int(sn)) if sn is not None else ""
                else:
                    actual_serial = jlink_serial or ""
                self._last_connect_params = (target, iface, speed, channel, actual_serial)
                info = self._collect_device_info(target, iface, speed)
                with self._info_lock:
                    self._device_info = info
                # 新会话：仅重置时长起点；收发计数跨连接累计，由 reset_counts() 显式清零
                with self._stats_lock:
                    self._session_start_ts = time.time()
                self._logger.info(
                    f"已连接 {target} ({iface} {speed}kHz, RTT 上行通道数 {self._num_up_channels})")
                self.connection_state_changed.emit(True)
                self._restart_read_thread()
            else:
                self._logger.error("connect(target) 后 connected() 仍为 False")
                self.log_message.emit("error", "连接目标失败")
                self._do_disconnect()
        except Exception as e:
            self._logger.error(f"连接失败：{e}")
            self.log_message.emit("error", f"连接失败：{e}")
            self._do_disconnect()

    @Slot()
    def _on_disconnect(self) -> None:
        # 手动断开取消自动重连（用户主动行为，不应再自动重连）
        self._stop_reconnect()
        self._do_disconnect()

    def _do_disconnect(self) -> None:
        was_active = self._state in (_STATE_CONNECTING, _STATE_CONNECTED)
        self._state = _STATE_DISCONNECTING
        self._logger.info("disconnect: 开始")

        # 1. 停读线程（同 reset / export 共用 helper）
        self._pause_read_thread()

        # 2. 掉线竞态闭环：read_thread 可能在死前已置 _unexpected_disconnect_pending，
        #    但 drain timer(50ms) 还没来得及转。这里先把它 drain 掉——否则 buffer 数据
        #    会先 emit 给 UI，而 state(False) 在 was_active=False 下不 emit，UI 卡死。
        #    （bug：多通道读循环高频触发 rtt_read 异常时此竞态概率显著升高）
        #    注意：本方法入口已把 _state 置为 DISCONNECTING，所以 _on_unexpected_disconnect
        #    内的「state != CONNECTED 直接 return」守卫在这里**不会**命中——这正是想要的：
        #    走这里说明 drain timer 还没闭环，我们必须替它把红字提示发出去 + 清理。
        with self._unexpected_disconnect_lock:
            pending_unexpected = self._unexpected_disconnect_pending
            self._unexpected_disconnect_pending = False
        if pending_unexpected:
            # 临时恢复 CONNECTED 让 _on_unexpected_disconnect 的守卫通过（它会再走
            # 一遍 _do_disconnect 做清理，那时 pending 已清，走正常分支到 IDLE）。
            # auto_reconnect 路径的 _start_reconnect 也在 _on_unexpected_disconnect 里。
            self._state = _STATE_CONNECTED
            self._on_unexpected_disconnect()
            return  # _on_unexpected_disconnect 内部已走完整断开流程

        # 3. 正常断开：把 buffer 残留数据按通道 emit 出去（最后一帧不丢）
        self._emit_pending_drain()

        self._close_log_file()

        # 4. rtt_stop + close（无条件调用，pylink 1.6.0 直接调，
        #    异常只 warning 不阻断——守卫反而会因内部状态时序问题误判）
        try:
            self.jlink.rtt_stop()
        except Exception as e:
            self._logger.warning(f"rtt_stop 失败：{e}")
        try:
            self.jlink.close()
        except Exception as e:
            self._logger.warning(f"close 失败：{e}")

        self._state = _STATE_IDLE
        # 对称重置 _stop_read：让 flag 生命周期清晰（契约：disconnect 完后是干净态）
        self._stop_read = False
        # 清掉可能残留的意外断开标记：正常断开路径不走红字提示
        self._unexpected_disconnect_pending = False
        with self._info_lock:
            self._device_info = {}
        self._num_up_channels = 1
        self._decoders.clear()
        # 仅重置会话时长为 0（断开态标记）：收发计数跨断开保留，由 reset_counts()
        # 显式清零。start_ts=0 让 UI 的 _update_stats 显示时长占位、不再按连接态累计。
        with self._stats_lock:
            self._session_start_ts = 0.0
        if was_active:
            self._logger.info("已断开 J-Link")
            self.connection_state_changed.emit(False)

    def _detect_num_up_channels(self) -> int:
        """连接后探测 MCU 端实际【已分配】的 RTT 上行通道数。

        关键：不用 rtt_get_num_up_buffers() 的返回值当通道数--它返回的是固件声明的
        MaxNumUpBuffers（描述符数组大小），含「声明了但没初始化的空槽」。实测某
        STM32F030 固件声明 3 个上行缓冲，但只有 ch0 真正分配了缓冲（SizeOfBuffer=1024），
        ch1/ch2 的 SizeOfBuffer=0（空槽，永远没数据）。若用声明数 3，SpinBox 会显示
        0/1/2 且选 4 拉回到 2（空槽无数据）--正是用户报的 bug。

        正确口径：遍历各通道 buf descriptor，数 SizeOfBuffer>0 的（从 0 起连续）。
        空槽即停（SEGGER RTT 通道按惯例从 0 连续分配）。

        RTT 控制块定位是异步的--紧凑重连（断开立即重连 / auto_reconnect）时
        rtt_get_num_up_buffers 会抛 "The RTT Control Block has not yet been found"。
        故失败/返回 0 时短间隔重试，仍失败回退 1（只读 ch0，行为同旧版）。
        """
        last_err: str = ""
        for attempt in range(4):   # 0ms / 150ms / 300ms / 450ms，覆盖典型定位窗口
            try:
                declared = int(self.jlink.rtt_get_num_up_buffers())
                if declared < 1:
                    raise RuntimeError(f"declared={declared}")
                allocated = 0
                for ch in range(declared):
                    desc = self.jlink.rtt_get_buf_descriptor(ch, up=True)
                    if getattr(desc, "SizeOfBuffer", 0) > 0:
                        allocated += 1
                    else:
                        break   # 从 0 起连续，遇空槽即停
                if allocated >= 1:
                    self._logger.info(
                        f"RTT 通道数探测：声明 {declared} / 实际分配 {allocated}（第 {attempt + 1} 次尝试）")
                    return allocated
                last_err = f"声明{declared}但无已分配缓冲"
            except Exception as e:
                last_err = str(e)
            if attempt < 3:
                time.sleep(0.15)
        self._logger.warning(f"探测 RTT 上行通道数失败（重试 4 次），回退 1：{last_err}")
        return 1

    def _collect_device_info(self, target: str, iface: str, speed: int) -> dict:
        try:
            return {
                "jlink_firmware": self.jlink.firmware_version,
                "jlink_hardware": str(self.jlink.hardware_version),
                "jlink_serial": str(self.jlink.serial_number),
                "core_name": self.jlink.core_name(),
                "core_id": hex(self.jlink.core_id()),
                "core_cpu": self.jlink.core_cpu(),
                "target_device": target,
                "interface": iface,
                "speed_khz": speed,
            }
        except Exception as e:
            self._logger.warning(f"获取设备信息失败：{e}")
            return {"target_device": target, "interface": iface, "speed_khz": speed}

    # ============================================================
    # RTT 读循环
    # ============================================================
    def _reset_decoders(self) -> None:
        """清空每通道 decoder 表。懒创建：下一个读拍按需重建，用当前编码。"""
        self._decoders = {}

    def _get_decoder(self, channel: int) -> codecs.IncrementalDecoder:
        """每通道独立 incremental decoder——不同通道是独立字节流，共享会让
        一通道的半字节状态污染另一通道。懒创建，编码切换时 _reset_decoders 全清。"""
        dec = self._decoders.get(channel)
        if dec is None:
            try:
                dec = codecs.getincrementaldecoder(self._encoding)(errors="replace")
            except LookupError:
                self._logger.warning(f"未知编码 {self._encoding}，回退 utf-8")
                self._encoding = "utf-8"
                dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
            self._decoders[channel] = dec
        return dec

    def _poll_all_channels(self) -> None:
        """一拍：遍历所有已配置上行通道（0.._num_up_channels-1），读到的数据
        标注通道号进 drain buffer + 按通道累计统计。与 UI 当前查看哪个通道无关——
        切通道是纯显示行为，worker 始终读所有通道，保证各通道历史完整。"""
        for ch in range(self._num_up_channels):
            data = self.jlink.rtt_read(ch, 4096)
            if not data:
                continue
            raw_len = len(data)
            decoded = self._get_decoder(ch).decode(bytes(data))
            if not decoded:
                continue
            with self._rtt_drain_lock:
                self._rtt_drain_buffer.append((ch, decoded))
            with self._stats_lock:
                b, ln = self._channel_stats.get(ch, (0, 0))
                self._channel_stats[ch] = (b + raw_len, ln + decoded.count("\n"))
            self._write_log_file(decoded)

    def _read_loop(self) -> None:
        """daemon 读线程：在独立 Python 线程跑，照搬参考项目模式。

        **不在此线程 emit Qt signal**——native threading.Thread emit Qt signal 在 PySide6
        上偶发会产生 setParent cross-thread 警告并污染主线程事件循环。
        改为把数据写到 _rtt_drain_buffer，由 worker 线程的 _rtt_drain_timer 50ms 一次
        合并 emit 给 UI。

        通过 self._stop_read 标志退出。disconnect 时主流程先 _stop_read=True
        再 join 这个线程，确保读循环干净结束后才调 rtt_stop/close。
        """
        while not self._stop_read:
            try:
                if self._state == _STATE_CONNECTED and not self._paused and self.jlink is not None:
                    self._poll_all_channels()
            except Exception as e:
                # 不 emit log_message——避免 native thread 跨线程 emit。
                # 错误从 logger 文件看，足够诊断。
                self._logger.error(f"RTT 读异常：{e}")
                # 仅在仍处于连接态时标记意外断开：正常 _do_disconnect 会先把
                # _state 置为 DISCONNECTING，此时异常属于 teardown 副作用，不算意外。
                if self._state == _STATE_CONNECTED:
                    with self._unexpected_disconnect_lock:
                        self._unexpected_disconnect_pending = True
                self._stop_read = True
                break
            time.sleep(self._poll_interval)

    @Slot()
    def _drain_rtt_buffer(self) -> None:
        """worker 线程槽：50ms 一次，把 read_thread 累积的数据合并 emit 给 UI。"""
        # read_thread 检出的意外断开（物理掉线）：在 worker 线程闭环处理
        with self._unexpected_disconnect_lock:
            unexpected = self._unexpected_disconnect_pending
            self._unexpected_disconnect_pending = False
        # 先把残留数据 emit 出去（断开前最后一帧），再处理意外断开
        self._emit_pending_drain()
        if unexpected:
            self._on_unexpected_disconnect()

    def _emit_pending_drain(self) -> None:
        """把 drain buffer 里残留数据按通道分组 emit（不处理意外断开标记）。

        拆成独立 helper：_drain_rtt_buffer（50ms timer）与 _do_disconnect（掉线竞态下
        抢先 emit 最后一帧）共用，避免 disconnect 路径和 drain timer 重复 emit。
        """
        with self._rtt_drain_lock:
            if not self._rtt_drain_buffer:
                return
            grouped: dict[int, list[str]] = {}
            for ch, text in self._rtt_drain_buffer:
                grouped.setdefault(ch, []).append(text)
            self._rtt_drain_buffer.clear()
        for ch, parts in grouped.items():
            self.rtt_data_received.emit(ch, "".join(parts))

    def _on_unexpected_disconnect(self) -> None:
        """read_thread 检出物理掉线后，在 worker 线程闭环：捕获设备标识 ->
        emit 给 UI 显示红字提示 -> _do_disconnect 清理并 emit 连接状态 False。

        状态守卫：若已不在 CONNECTED（用户主动断开与掉线竞态），直接返回，
        不重复清理也不误报红字。
        """
        if self._state != _STATE_CONNECTED:
            return
        with self._info_lock:
            serial = self._device_info.get("jlink_serial")
        identifier = f"J-Link {serial}" if serial else "-"
        self._logger.error(f"检测到意外断开：{identifier}")
        if self._auto_reconnect_enabled and self._last_connect_params and serial:
            # 自动重连：发"正在尝试自动重连"提示 -> 干净断开 -> 启动轮询定时器。
            # 用 reconnect_status 而非 unexpected_disconnect：UI 显示橙色"正在重连"行。
            self.reconnect_status.emit("disconnect_reconnecting", identifier)
            self._do_disconnect()
            self._start_reconnect(str(serial))
        else:
            self.unexpected_disconnect.emit(identifier)
            self._do_disconnect()

    # ============================================================
    # 自动重连（物理掉线后）
    # ============================================================
    @Slot(bool)
    def _on_set_auto_reconnect(self, enabled: bool) -> None:
        """UI 勾选/取消「自动重连」。取消时若正在重连，停止并提示。"""
        self._auto_reconnect_enabled = enabled
        if not enabled:
            self._stop_reconnect(emit_cancelled=True)

    def _start_reconnect(self, serial: str) -> None:
        """non-slot helper：记录目标 serial + 复位计数 + 启动 3s 轮询 timer。"""
        self._reconnect_target_serial = serial
        self._reconnect_attempt = 0
        if self._reconnect_timer is not None and not self._reconnect_timer.isActive():
            self._reconnect_timer.start()

    def _stop_reconnect(self, *, emit_cancelled: bool = False) -> None:
        """non-slot helper：停 timer + 清状态。emit_cancelled=True 时发「已取消」提示。"""
        was_active = (self._reconnect_timer is not None
                      and self._reconnect_timer.isActive())
        if self._reconnect_timer is not None:
            self._reconnect_timer.stop()
        self._reconnect_target_serial = None
        self._reconnect_attempt = 0
        if emit_cancelled and was_active:
            self.reconnect_status.emit("cancelled", "")

    @Slot()
    def _reconnect_tick(self) -> None:
        """worker 线程 3s 一次：轮询目标 J-Link 是否回来，回来就 _do_connect 重连。

        串行匹配：只认 _reconnect_target_serial（= 上次连接的 J-Link），避免用户
        插了另一个 J-Link 被误连。成功 -> 停 timer + emit success；失败 -> emit
        failed，下一拍（3s）再试；设备没回来 -> 静默等下一拍（不计数、不刷屏）。

        双重校验：先在本方法里枚举一次确认目标 serial 在接入列表里（不在则静默
        等下一拍，不 attempt），再让 _do_connect 内部用 5 元组第 5 元素的真实
        serial 做最终把关——两层防御，即便 _do_connect 的校验被绕过，这里也挡住。
        """
        if self._reconnect_target_serial is None:
            self._stop_reconnect()
            return
        # 已连上（竞态/手动连接成功）-> 收尾
        if self._state == _STATE_CONNECTED:
            self._stop_reconnect()
            return
        # 目标 J-Link 还没回来 -> 静默等下一拍（不 attempt、不刷屏）
        try:
            emus = self.jlink.connected_emulators()
        except Exception as e:
            self._logger.warning(f"重连轮询枚举失败：{e}")
            return   # 枚举本身异常，下一拍再试
        if not any(str(int(getattr(e, "SerialNumber", 0) or 0)) == self._reconnect_target_serial
                   for e in emus):
            return   # 设备还没插回，静默等
        self._reconnect_attempt += 1
        n = self._reconnect_attempt
        self.reconnect_status.emit("attempt", str(n))
        params = self._last_connect_params
        if params is None:
            self._stop_reconnect(emit_cancelled=True)
            return
        # 5 元组第 5 元素 = 上次连接的 J-Link serial，交给 _do_connect 最终把关
        self._do_connect(*params)
        if self._state == _STATE_CONNECTED:
            self.reconnect_status.emit("success", str(n))
            self._stop_reconnect()
        else:
            self.reconnect_status.emit("failed", str(n))   # 3s 后下一拍再试


    @Slot(str, bool)
    def _on_send_data(self, data: str, is_hex: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("send_data", False, "未连接")
            return
        try:
            payload = encode_send_payload(data, is_hex)
            written = self.jlink.rtt_write(self._send_channel, payload)
            ok = written == len(payload)
            self.command_result.emit("send_data", ok, "" if ok else "rtt_write 写入不完整")
        except Exception as e:
            self._logger.error(f"发送数据失败（通道 {self._send_channel}）：{e}")
            raw = str(e).strip()
            # pylink/J-Link DLL 的通用错误（如 "Unspecified error."）对用户无意义，
            # 替换成可操作提示；有具体信息的原样保留。
            if not raw or "unspecified" in raw.lower():
                msg = f"发送失败（通道 {self._send_channel}）：J-Link 通信异常，请检查连接与通道"
            else:
                msg = f"发送失败（通道 {self._send_channel}）：{raw}"
            self.command_result.emit("send_data", False, msg)

    def _pause_read_thread(self) -> None:
        """停 read_thread 并 join；做 jlink reset / rtt_stop_start / export
        之类阻断动作前必须调，pylink/SEGGER DLL 不支持读循环并发占句柄。"""
        self._stop_read = True
        if self._read_thread is not None and self._read_thread.is_alive():
            self._read_thread.join(timeout=2.0)
            if self._read_thread.is_alive():
                self._logger.warning("read_thread join 超时（仍 alive）")
        self._read_thread = None

    def _restart_read_thread(self) -> None:
        """启一条新读线程。pause 后 / 新连接后调用。"""
        self._stop_read = False
        self._read_thread = threading.Thread(
            target=self._read_loop, name="JLinkReadThread", daemon=True
        )
        self._read_thread.start()

    @Slot(str)
    def _on_reset(self, mode: str) -> None:
        """一条龙重置 —— UI 只发模式，剩下全在 worker 闭环。

        mode 决定治法（针对 pylink 缓存 RTT 控制块地址在 reset 后过期的 bug）：
        - "normal":         5 步 dance —— reset + rtt_stop/start，连接保留。
                            快，对大多数 MCU 够用；少数 MCU 上不可靠。
        - "auto_reconnect": reset + 断开 + 等 MCU boot + 重连，整个 J-Link 会话
                            推倒重来。慢 ~500ms 但 100% 可靠。
        """
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("reset", False, "未连接")
            return
        if mode == RESET_MODE_AUTO_RECONNECT:
            self._reset_with_reconnect()
        elif mode == RESET_MODE_HALT:
            self._reset_and_halt()
        else:
            self._reset_in_place()

    def _reset_in_place(self) -> None:
        """治法 A：5 步 dance（保留 J-Link 会话）。"""
        self._pause_read_thread()
        ok, err = True, ""
        try:
            self.jlink.reset(1, False)
            time.sleep(0.1)  # 等 MCU 重新初始化 _SEGGER_RTT 控制块
            self.jlink.rtt_stop()
            self.jlink.rtt_start()
            self._reset_decoders()
        except Exception as e:
            ok, err = False, str(e)
            self._logger.error(f"重置失败：{e}")
        finally:
            self._restart_read_thread()  # 成败都要让读线程恢复
        self.command_result.emit("reset", ok, err)
        if ok:
            self.log_message.emit("info", "目标设备已重置")

    def _reset_and_halt(self) -> None:
        """治法 C：reset 后让 CPU 停在复位状态（halt=True），不运行、不断开重连。

        与「仅重置」不同——这里 reset 第二参 halt=True，CPU 复位后停在复位向量、
        不执行启动代码，可用于上电瞬间状态调试。MCU 停着不跑，所以不会再产生
        新 RTT 数据；保留 J-Link 会话与读线程，待用户后续操作恢复运行。
        """
        self._pause_read_thread()
        ok, err = True, ""
        try:
            self.jlink.reset(0, True)  # halt=True
        except Exception as e:
            ok, err = False, str(e)
            self._logger.error(f"重置并暂停失败：{e}")
        finally:
            self._restart_read_thread()  # 成败都要让读线程恢复
        self.command_result.emit("reset", ok, err)
        if ok:
            self.log_message.emit("info", "目标设备已重置并暂停（halt，停在复位状态）")

    def _reset_with_reconnect(self) -> None:
        """治法 B：reset → disconnect → 等 boot → reconnect，整个会话重建。"""
        params = self._last_connect_params
        if params is None:
            self.command_result.emit("reset", False, "无连接参数可重连")
            return

        # 1. 先发 reset 命令让 MCU 真重启（必须在 disconnect 前，需要 J-Link 通路）
        try:
            self.jlink.reset(1, False)
        except Exception as e:
            self._logger.error(f"重置失败：{e}")
            self.command_result.emit("reset", False, str(e))
            return

        # 2. 干净断开 J-Link 会话（会 emit connection_state_changed(False)，UI 自然看到）
        self._do_disconnect()

        # 3. 等 MCU 完成 boot —— reset + disconnect 已花 ~100-200ms，
        #    再延 300ms 共 ~500ms，覆盖 STM32H7 / nRF52 等典型 MCU 上电时间
        time.sleep(0.3)

        # 4. 重连（会 emit connection_state_changed(True) + 设备信息回填）
        self._do_connect(*params)

        self.command_result.emit("reset", True, "")
        self.log_message.emit("info", "目标设备已重置（自动重连完成）")

    @Slot(int)
    def _on_set_channel(self, channel: int) -> None:
        """UI 切换视图通道。-1 = 全部通道；>=0 = 具体通道（同时成为发送通道）。"""
        self._view_channel = channel
        if channel >= 0:
            self._send_channel = channel
            self.log_message.emit("info", f"RTT 通道切换为 {channel}")
        else:
            self.log_message.emit("info", f"RTT 通道切换为全部（发送仍走 {self._send_channel}）")

    @Slot(bool)
    def _on_set_paused(self, paused: bool) -> None:
        self._paused = paused

    @Slot(int)
    def _on_set_poll_interval(self, ms: int) -> None:
        if ms < 1:
            ms = 100
        if ms == int(self._poll_interval * 1000):
            return
        self._poll_interval = ms / 1000.0
        self.log_message.emit("info", f"RTT 轮询间隔设为 {ms} ms")

    @Slot(str)
    def _on_set_encoding(self, encoding: str) -> None:
        """切换 RTT 解码编码（utf-8 / gbk / utf-16-le / latin-1 / ascii）。清空所有通道 decoder。"""
        if not encoding:
            return
        if encoding == self._encoding:
            return
        self._encoding = encoding
        self._reset_decoders()
        self._logger.info(f"RTT 解码编码切换为 {encoding}")

    @Slot(bool)
    def _on_set_power(self, enable: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("power_output", False, "未连接")
            return
        try:
            if enable:
                #  default=False（当前用法）：立即打开/关闭电源输出，仅本次生效。
                self.jlink.power_on(default=False)
            else:
                self.jlink.power_off(default=False)
            self.command_result.emit("power_output", True, "")
        except Exception as e:
            self._logger.error(f"控制电源失败：{e}")
            self.command_result.emit("power_output", False, str(e))

    @Slot(int, int)
    def _on_read_memory(self, addr: int, size: int) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("read_memory", False, "未连接")
            return
        try:
            raw = memory_service.read_memory(self.jlink, addr, size)
            self.memory_read_finished.emit(addr, bytes(raw))
        except Exception as e:
            self._logger.error(f"读内存失败：{e}")
            self.command_result.emit("read_memory", False, str(e))

    @Slot(int, bytes)
    def _on_write_memory(self, addr: int, data: bytes) -> None:
        """写内存：高风险操作（可能 brick 目标），UI 已做二次确认。"""
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("write_memory", False, "未连接")
            return
        try:
            written = memory_service.write_memory(self.jlink, addr, data)
            self._logger.info(f"写内存 0x{addr:08X}，{len(data)} 字节")
            self.command_result.emit("write_memory", True, f"已写入 {written} 字节")
        except Exception as e:
            self._logger.error(f"写内存失败：{e}")
            self.command_result.emit("write_memory", False, str(e))

    @Slot(str, int, int)
    def _on_export_firmware(self, path: str, start_addr: int, size: int) -> None:
        if self._state != _STATE_CONNECTED:
            self.firmware_export_finished.emit(False, "", "未连接")
            return
        # 导出期间停读线程：read_loop 的 rtt_read 和 export 的 memory_read 共享同
        # 一个 jlink 实例；并发会抢句柄。用真停线程而非 _paused 标志（后者只是
        # 让循环跳过 read，rtt_read 调用窗口仍可能和 memory_read 重叠）。
        self._pause_read_thread()
        try:
            def cb(cur: int, total: int) -> None:
                self.firmware_export_progress.emit(cur, total)
            memory_service.export_firmware(self.jlink, path, start_addr, size, cb)
            self.firmware_export_finished.emit(True, path, "")
        except Exception as e:
            self._logger.error(f"导出固件失败：{e}")
            self.firmware_export_finished.emit(False, path, str(e))
        finally:
            self._restart_read_thread()

    @Slot(str)
    def _on_start_log(self, log_dir: str) -> None:
        if self._log_file is not None:
            return
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._log_path = str(Path(log_dir) / f"rtt_{stamp}.log")
            self._log_file = open(self._log_path, "a", encoding="utf-8", buffering=1)
            self.command_result.emit("log_recording", True, "")
        except Exception as e:
            self._logger.error(f"开始日志记录失败：{e}")
            self.command_result.emit("log_recording", False, str(e))

    @Slot()
    def _on_stop_log(self) -> None:
        self._close_log_file()
        self.command_result.emit("log_recording", True, "")

    def _close_log_file(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
            self._log_path = None

    def _write_log_file(self, text: str) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.write(text)
            self._log_file.flush()
        except Exception as e:
            self._logger.warning(f"写日志文件失败：{e}")

    # ============================================================
    # 停止：worker 线程内自清理 + 退出 thread 事件循环
    # ============================================================
    @Slot()
    def _on_stop(self) -> None:
        self._do_disconnect()
        # _do_disconnect 已经停了读线程
        # 在 worker 线程内显式 stop + deleteLater drain timer，避免应用退出时
        # 主线程 GC 销毁 timer 触发 cross-thread killTimer 警告
        # （timer 的 thread affinity 是 worker_thread）
        if self._rtt_drain_timer is not None:
            self._rtt_drain_timer.stop()
            self._rtt_drain_timer.deleteLater()
            self._rtt_drain_timer = None
        if self._reconnect_timer is not None:
            self._reconnect_timer.stop()
            self._reconnect_timer.deleteLater()
            self._reconnect_timer = None
        if self._enum_timer is not None:
            self._enum_timer.stop()
            self._enum_timer.deleteLater()
            self._enum_timer = None
        t = self.thread()
        if t is not None:
            t.quit()
