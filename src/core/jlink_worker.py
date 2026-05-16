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


class JLinkWorker(QObject):
    """J-Link 后台业务对象。**必须 moveToThread 到一个 QThread 后再用**。"""

    # ---- 输入信号 ----
    connect_requested = Signal(str, str, int, int)
    disconnect_requested = Signal()
    send_data_requested = Signal(str, bool)
    reset_target_requested = Signal()
    set_rtt_channel_requested = Signal(int)
    set_pause_receive_requested = Signal(bool)
    set_power_output_requested = Signal(bool)
    set_poll_interval_requested = Signal(int)
    read_memory_requested = Signal(int, int)
    export_firmware_requested = Signal(str, int, int)
    start_log_recording_requested = Signal(str)
    stop_log_recording_requested = Signal()
    stop_requested = Signal()

    # ---- 输出信号 ----
    rtt_data_received = Signal(str)
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

    def __init__(self) -> None:
        super().__init__()
        self._logger = get_logger()
        self._state: str = _STATE_IDLE
        self._channel: int = 0
        self._paused: bool = False
        self._ready: bool = False

        # 这些在 initialize() 内（worker 线程）创建/启动：
        self.jlink: pylink.JLink | None = None
        self._decoder: codecs.IncrementalDecoder | None = None
        self._read_thread: threading.Thread | None = None
        self._stop_read: bool = False
        self._poll_interval: float = 0.1   # 100ms，匹配参考项目
        self._log_file = None
        self._log_path: str | None = None

        # RTT 数据中转：read_thread 写入 buffer（lock 保护），worker 线程 QTimer 取出合并 emit。
        # **关键**：避免 native threading.Thread 直接 emit Qt signal——
        # PySide6 从非 QThread 的 pthread emit 跨线程信号会产生 setParent cross-thread 警告，
        # 并可能让主线程事件循环异常（卡 UI）。
        self._rtt_drain_buffer: list[str] = []
        self._rtt_drain_lock = threading.Lock()
        self._rtt_drain_timer: QTimer | None = None

        # 设备信息：worker 端用 lock 保护，UI 通过 get_device_info() 同步读。
        # 不通过信号传 dict——dict 跨线程 marshalling 在 PySide6 上会卡 worker 线程。
        self._device_info: dict = {}
        self._info_lock = threading.Lock()

    # ============================================================
    # worker 线程初始化（由外部 QThread.started 触发）
    # ============================================================
    @Slot()
    def initialize(self) -> None:
        """在 worker 线程内创建所有 thread-affinity 敏感的对象。"""
        self.jlink = pylink.JLink()
        self._reset_utf8_decoder()

        # 把输入信号连到本地槽（同线程，DirectConnection）
        self.connect_requested.connect(self._on_connect)
        self.disconnect_requested.connect(self._on_disconnect)
        self.send_data_requested.connect(self._on_send_data)
        self.reset_target_requested.connect(self._on_reset_target)
        self.set_rtt_channel_requested.connect(self._on_set_channel)
        self.set_pause_receive_requested.connect(self._on_set_paused)
        self.set_power_output_requested.connect(self._on_set_power)
        self.set_poll_interval_requested.connect(self._on_set_poll_interval)
        self.read_memory_requested.connect(self._on_read_memory)
        self.export_firmware_requested.connect(self._on_export_firmware)
        self.start_log_recording_requested.connect(self._on_start_log)
        self.stop_log_recording_requested.connect(self._on_stop_log)
        self.stop_requested.connect(self._on_stop)

        # RTT 数据中转 timer：worker 线程内 QObject，affinity 跟 self（worker_thread）。
        # 50ms 一次从 read_thread 写入的 buffer 取出 → emit 给 UI。
        # emit 在 worker_thread context 进行，Qt 跨线程信号传递走标准 QueuedConnection，行为可靠。
        self._rtt_drain_timer = QTimer()
        self._rtt_drain_timer.setInterval(50)
        self._rtt_drain_timer.timeout.connect(self._drain_rtt_buffer)
        self._rtt_drain_timer.start()

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

    # ============================================================
    # 连接 / 断开
    # ============================================================
    @Slot(str, str, int, int)
    def _on_connect(self, target: str, iface: str, speed: int, channel: int) -> None:
        if self._state == _STATE_CONNECTED:
            self.log_message.emit("warning", "已连接，先断开再切换设备")
            return
        self._state = _STATE_CONNECTING
        self._channel = channel
        try:
            if not self.jlink.opened():
                # 参考项目的双开模式（pylink 1.6.0 稳定工作的关键模式）：
                # 先 open() 一次取 serial，close，再 open(serial)，然后 rtt_start
                # 注意：rtt_start 必须在 connect(target) 之前调用
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
            self._reset_utf8_decoder()

            if self.jlink.connected():
                self._state = _STATE_CONNECTED
                info = self._collect_device_info(target, iface, speed)
                with self._info_lock:
                    self._device_info = info
                self._logger.info(f"已连接 {target} ({iface} {speed}kHz, RTT ch{channel})")
                self.connection_state_changed.emit(True)
                # 启动读线程
                self._stop_read = False
                self._read_thread = threading.Thread(
                    target=self._read_loop, name="JLinkReadThread", daemon=True
                )
                self._read_thread.start()
            else:
                self._logger.error("connect(target) 后 connected() 仍为 False")
                self.log_message.emit("error", "连接目标失败")
                self._transition_to_idle()
        except Exception as e:
            self._logger.error(f"连接失败：{e}")
            self.log_message.emit("error", f"连接失败：{e}")
            self._transition_to_idle()

    @Slot()
    def _on_disconnect(self) -> None:
        self._do_disconnect()

    def _do_disconnect(self) -> None:
        was_active = self._state in (_STATE_CONNECTING, _STATE_CONNECTED)
        self._state = _STATE_DISCONNECTING
        self._logger.info("disconnect: 开始")

        # 1. 通知读线程退出，join with timeout（参考项目模式）
        self._stop_read = True
        if self._read_thread is not None and self._read_thread.is_alive():
            self._logger.info("disconnect: 等 read_thread join")
            self._read_thread.join(timeout=2.0)
            if self._read_thread.is_alive():
                self._logger.warning("disconnect: read_thread join 超时（仍 alive）")
            else:
                self._logger.info("disconnect: read_thread 已退出")
        self._read_thread = None

        # 把 buffer 里残留的数据也 emit 出去，避免最后一帧丢失
        with self._rtt_drain_lock:
            tail = "".join(self._rtt_drain_buffer)
            self._rtt_drain_buffer.clear()
        if tail:
            self.rtt_data_received.emit(tail)

        self._close_log_file()
        self._logger.info("disconnect: 日志文件已关")

        # 2. rtt_stop + close（无条件调用，pylink 1.6.0 直接调，
        #    异常只 warning 不阻断——守卫反而会因内部状态时序问题误判）
        try:
            self.jlink.rtt_stop()
            self._logger.info("disconnect: rtt_stop 完成")
        except Exception as e:
            self._logger.warning(f"rtt_stop 失败：{e}")
        try:
            self.jlink.close()
            self._logger.info("disconnect: close 完成")
        except Exception as e:
            self._logger.warning(f"close 失败：{e}")

        self._state = _STATE_IDLE
        # 对称重置 _stop_read：让 flag 生命周期清晰，未来加 reconnect helper 不踩坑
        self._stop_read = False
        with self._info_lock:
            self._device_info = {}
        if was_active:
            self._logger.info("已断开 J-Link")
            self.connection_state_changed.emit(False)
            self._logger.info("disconnect: connection_state_changed 已 emit")

    def _transition_to_idle(self) -> None:
        self._do_disconnect()

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
    def _reset_utf8_decoder(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

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
                    data = self.jlink.rtt_read(self._channel, 4096)
                    if data:
                        decoded = self._decoder.decode(bytes(data))
                        if decoded:
                            with self._rtt_drain_lock:
                                self._rtt_drain_buffer.append(decoded)
                            self._write_log_file(decoded)
            except Exception as e:
                # 不 emit log_message——避免 native thread 跨线程 emit。
                # 错误从 logger 文件看，足够诊断。
                self._logger.error(f"RTT 读异常：{e}")
                self._stop_read = True
                break
            time.sleep(self._poll_interval)

    @Slot()
    def _drain_rtt_buffer(self) -> None:
        """worker 线程槽：50ms 一次，把 read_thread 累积的数据合并 emit 给 UI。"""
        with self._rtt_drain_lock:
            if not self._rtt_drain_buffer:
                return
            merged = "".join(self._rtt_drain_buffer)
            self._rtt_drain_buffer.clear()
        self.rtt_data_received.emit(merged)

    # ============================================================
    # 命令槽
    # ============================================================
    @Slot(str, bool)
    def _on_send_data(self, data: str, is_hex: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("send_data", False, "未连接")
            return
        try:
            if is_hex:
                cleaned = data.replace(" ", "").replace("\n", "").replace("\r", "")
                if len(cleaned) % 2 != 0:
                    cleaned += "0"
                payload = bytes.fromhex(cleaned)
            else:
                payload = data.encode("utf-8")
            written = self.jlink.rtt_write(self._channel, payload)
            ok = written == len(payload)
            self.command_result.emit("send_data", ok, "" if ok else "rtt_write 写入不完整")
        except Exception as e:
            self._logger.error(f"发送数据失败：{e}")
            self.command_result.emit("send_data", False, str(e))

    @Slot()
    def _on_reset_target(self) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("reset", False, "未连接")
            return
        try:
            self.jlink.reset(1, False)
            self.command_result.emit("reset", True, "")
            self.log_message.emit("info", "目标设备已重置")
        except Exception as e:
            self._logger.error(f"重置失败：{e}")
            self.command_result.emit("reset", False, str(e))

    @Slot(int)
    def _on_set_channel(self, channel: int) -> None:
        self._channel = channel
        self.log_message.emit("info", f"RTT 通道切换为 {channel}")

    @Slot(bool)
    def _on_set_paused(self, paused: bool) -> None:
        self._paused = paused

    @Slot(int)
    def _on_set_poll_interval(self, ms: int) -> None:
        if ms < 1:
            ms = 100
        self._poll_interval = ms / 1000.0
        self.log_message.emit("info", f"RTT 轮询间隔设为 {ms} ms")

    @Slot(bool)
    def _on_set_power(self, enable: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("power_output", False, "未连接")
            return
        try:
            if enable:
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

    @Slot(str, int, int)
    def _on_export_firmware(self, path: str, start_addr: int, size: int) -> None:
        if self._state != _STATE_CONNECTED:
            self.firmware_export_finished.emit(False, "", "未连接")
            return
        # 导出期间暂停 RTT 读循环
        was_paused = self._paused
        self._paused = True
        try:
            def cb(cur: int, total: int) -> None:
                self.firmware_export_progress.emit(cur, total)
            memory_service.export_firmware(self.jlink, path, start_addr, size, cb)
            self.firmware_export_finished.emit(True, path, "")
        except Exception as e:
            self._logger.error(f"导出固件失败：{e}")
            self.firmware_export_finished.emit(False, path, str(e))
        finally:
            self._paused = was_paused

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
        # self.thread() 返回 worker 被 moveTo 的那个 QThread
        t = self.thread()
        if t is not None:
            t.quit()
