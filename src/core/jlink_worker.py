"""JLinkWorker：所有 pylink 调用集中在 worker 线程（QObject + moveToThread 模式）。

设计要点（修复 cross-thread timer bug）：
1. JLinkBackend(QObject) 拥有所有状态 + 信号 + 槽。在主线程构造，moveToThread 到 worker。
2. JLinkBackend.initialize() 在 worker 线程被调用（QThread.started → initialize），
   在这里才创建 pylink.JLink / QTimer / IncrementalDecoder——确保 thread affinity 正确。
3. JLinkWorker(QThread) 是瘦壳：拥有 backend，moveToThread，转发属性/方法访问。
4. 退出：UI emit stop_requested → backend._on_stop 在 worker 线程跑 → 清理 pylink →
   QThread.currentThread().quit() 让 exec() 返回。主线程只 wait()。
"""
from __future__ import annotations

import codecs
from datetime import datetime
from pathlib import Path

import pylink
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from . import memory_service
from .logger import get_logger

_STATE_IDLE = "IDLE"
_STATE_CONNECTING = "CONNECTING"
_STATE_CONNECTED = "CONNECTED"
_STATE_DISCONNECTING = "DISCONNECTING"


class JLinkBackend(QObject):
    """所有 pylink 调用 + 状态。moveToThread 后，所有槽都在 worker 线程跑。"""

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
    connection_state_changed = Signal(bool, dict)
    log_message = Signal(str, str)
    command_result = Signal(str, bool, dict)
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

        # 这些在 initialize() 内（worker 线程）创建：
        self.jlink: pylink.JLink | None = None
        self._decoder: codecs.IncrementalDecoder | None = None
        self._poll_timer: QTimer | None = None
        self._log_file = None
        self._log_path: str | None = None

    # ============================================================
    # 在 worker 线程内的初始化
    # ============================================================
    @Slot()
    def initialize(self) -> None:
        """由 QThread.started 信号触发，在 worker 线程内跑。"""
        self.jlink = pylink.JLink()
        self._reset_utf8_decoder()
        self._poll_timer = QTimer()  # 无 parent → 归属当前（worker）线程
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self._poll_rtt)

        # 连接所有输入信号到本地槽（backend 已 moveToThread，AutoConnection = DirectConnection）
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

        self._ready = True

    def state_name(self) -> str:
        return self._state

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
                self.jlink.open()  # 不传 serial_no, pylink 自动选第一个
            tif = pylink.enums.JLinkInterfaces.SWD if iface == "SWD" \
                else pylink.enums.JLinkInterfaces.JTAG
            self.jlink.set_tif(tif)
            self.jlink.set_speed(int(speed))
            self.jlink.connect(target)
            self.jlink.rtt_start()
            self._reset_utf8_decoder()
            self._state = _STATE_CONNECTED
            info = self._collect_device_info(target, iface, speed)
            self.connection_state_changed.emit(True, info)
            self._poll_timer.start()
        except Exception as e:
            self._logger.error(f"连接失败：{e}")
            self.log_message.emit("error", f"连接失败：{e}")
            self._transition_to_idle()

    @Slot()
    def _on_disconnect(self) -> None:
        self._do_disconnect()

    def _do_disconnect(self) -> None:
        # 守卫：避免 IDLE 状态下重复发 connection_state_changed(False, {})
        was_active = self._state in (_STATE_CONNECTING, _STATE_CONNECTED)
        self._state = _STATE_DISCONNECTING
        if self._poll_timer is not None and self._poll_timer.isActive():
            self._poll_timer.stop()
        self._close_log_file()

        try:
            if self.jlink is not None and self.jlink.connected():
                self.jlink.rtt_stop()
        except Exception as e:
            self._logger.warning(f"rtt_stop 失败：{e}")
        try:
            if self.jlink is not None and self.jlink.opened():
                self.jlink.close()
        except Exception as e:
            self._logger.warning(f"close 失败：{e}")

        self._state = _STATE_IDLE
        if was_active:
            self.connection_state_changed.emit(False, {})

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

    def _poll_rtt(self) -> None:
        if self._state != _STATE_CONNECTED or self._paused:
            return
        try:
            data = self.jlink.rtt_read(self._channel, 4096)
        except Exception as e:
            self._logger.error(f"RTT 读异常：{e}")
            self.log_message.emit("error", f"RTT 读异常：{e}")
            self._transition_to_idle()
            return
        if not data:
            return
        decoded = self._decoder.decode(bytes(data))
        if decoded:
            self.rtt_data_received.emit(decoded)
            self._write_log_file(decoded)

    # ============================================================
    # 命令槽
    # ============================================================
    @Slot(str, bool)
    def _on_send_data(self, data: str, is_hex: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("send_data", False, {"error": "未连接"})
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
            self.command_result.emit("send_data", ok, {"bytes": written})
        except Exception as e:
            self._logger.error(f"发送数据失败：{e}")
            self.command_result.emit("send_data", False, {"error": str(e)})

    @Slot()
    def _on_reset_target(self) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("reset", False, {"error": "未连接"})
            return
        try:
            self.jlink.reset(1, False)
            self.command_result.emit("reset", True, {})
            self.log_message.emit("info", "目标设备已重置")
        except Exception as e:
            self._logger.error(f"重置失败：{e}")
            self.command_result.emit("reset", False, {"error": str(e)})

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
            ms = 20
        if self._poll_timer is not None:
            self._poll_timer.setInterval(ms)
        self.log_message.emit("info", f"RTT 轮询间隔设为 {ms} ms")

    @Slot(bool)
    def _on_set_power(self, enable: bool) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("power_output", False, {"error": "未连接"})
            return
        try:
            if enable:
                self.jlink.power_on(default=False)
            else:
                self.jlink.power_off(default=False)
            self.command_result.emit("power_output", True, {"enabled": enable})
        except Exception as e:
            self._logger.error(f"控制电源失败：{e}")
            self.command_result.emit("power_output", False, {"error": str(e)})

    @Slot(int, int)
    def _on_read_memory(self, addr: int, size: int) -> None:
        if self._state != _STATE_CONNECTED:
            self.command_result.emit("read_memory", False, {"error": "未连接"})
            return
        try:
            raw = memory_service.read_memory(self.jlink, addr, size)
            self.memory_read_finished.emit(addr, bytes(raw))
        except Exception as e:
            self._logger.error(f"读内存失败：{e}")
            self.command_result.emit("read_memory", False, {"error": str(e)})

    @Slot(str, int, int)
    def _on_export_firmware(self, path: str, start_addr: int, size: int) -> None:
        if self._state != _STATE_CONNECTED:
            self.firmware_export_finished.emit(False, "", "未连接")
            return
        was_active = self._poll_timer.isActive()
        self._poll_timer.stop()
        try:
            def cb(cur: int, total: int) -> None:
                self.firmware_export_progress.emit(cur, total)
            memory_service.export_firmware(self.jlink, path, start_addr, size, cb)
            self.firmware_export_finished.emit(True, path, "")
        except Exception as e:
            self._logger.error(f"导出固件失败：{e}")
            self.firmware_export_finished.emit(False, path, str(e))
        finally:
            if was_active and self._state == _STATE_CONNECTED:
                self._poll_timer.start()

    @Slot(str)
    def _on_start_log(self, log_dir: str) -> None:
        if self._log_file is not None:
            return
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._log_path = str(Path(log_dir) / f"rtt_{stamp}.log")
            self._log_file = open(self._log_path, "a", encoding="utf-8", buffering=1)
            self.command_result.emit("log_recording", True, {"path": self._log_path})
        except Exception as e:
            self._logger.error(f"开始日志记录失败：{e}")
            self.command_result.emit("log_recording", False, {"error": str(e)})

    @Slot()
    def _on_stop_log(self) -> None:
        self._close_log_file()
        self.command_result.emit("log_recording", True, {"stopped": True})

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
    # 停止
    # ============================================================
    @Slot()
    def _on_stop(self) -> None:
        """worker 线程内自清理 + 退出 QThread 事件循环。"""
        self._do_disconnect()
        if self._poll_timer is not None:
            self._poll_timer.stop()
        # 在 worker 线程内调 quit() 让 exec() 返回
        thread = QThread.currentThread()
        if thread is not None:
            thread.quit()


class JLinkWorker(QThread):
    """瘦壳 QThread：持有 JLinkBackend，moveToThread，转发属性访问。

    历史原因：测试和 UI 代码用 `worker.connect_requested.emit(...)` /
    `worker.state_name()` / `worker._channel` 等接口。本类用 __getattr__
    把所有访问转发给 backend，保持兼容。
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = JLinkBackend()
        self.backend.moveToThread(self)
        # QThread.started 在 worker 线程 emit，AutoConnection 到 backend.initialize
        # （backend 已 moveToThread）→ initialize 在 worker 线程跑
        self.started.connect(self.backend.initialize)

    def __getattr__(self, name: str):
        """转发属性/信号/方法访问到 backend。仅在常规属性查找失败时调用。"""
        # 避免无限递归：访问 self.backend 失败时直接 raise
        if name == "backend":
            raise AttributeError(name)
        return getattr(self.backend, name)
