"""JLinkWorker：所有 pylink 调用集中在这一条 QThread。

设计要点：
1. __init__ 在主线程；run() 内才是 worker 线程。pylink.JLink / QTimer / IncrementalDecoder
   都在 run() 内创建，确保 thread affinity 正确。
2. 输入信号用 Qt.QueuedConnection 投递到 worker 自己的事件循环。
3. stop_requested 由 worker 自己处理：清理 pylink → quit() → run() 退出；
   主线程不能外部 quit()，否则和阻塞中的 C 调用赛跑。
4. 连接时 if not opened(): open()；断开时 if connected(): rtt_stop(); if opened(): close()。
"""
from __future__ import annotations

import codecs
import os
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


class JLinkWorker(QThread):
    # ---- 输入信号 ----
    connect_requested = Signal(str, str, int, int)   # target, iface, speed, channel
    disconnect_requested = Signal()
    send_data_requested = Signal(str, bool)
    reset_target_requested = Signal()
    set_rtt_channel_requested = Signal(int)
    set_pause_receive_requested = Signal(bool)
    set_power_output_requested = Signal(bool)
    read_memory_requested = Signal(int, int)
    export_firmware_requested = Signal(str, int, int)
    start_log_recording_requested = Signal(str)
    stop_log_recording_requested = Signal()
    stop_requested = Signal()

    # ---- 输出信号 ----
    rtt_data_received = Signal(str)
    connection_state_changed = Signal(bool, dict)
    log_message = Signal(str, str)             # level, msg
    command_result = Signal(str, bool, dict)
    memory_read_finished = Signal(int, bytes)  # addr, raw bytes
    firmware_export_progress = Signal(int, int)
    firmware_export_finished = Signal(bool, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._logger = get_logger()
        self._state: str = _STATE_IDLE
        self._channel: int = 0
        self._paused: bool = False
        self._ready: bool = False  # 测试用：worker 事件循环已就绪

        # 这些在 run() 内创建：
        self.jlink: pylink.JLink | None = None
        self._decoder: codecs.IncrementalDecoder | None = None
        self._poll_timer: QTimer | None = None
        self._log_file = None
        self._log_path: str | None = None

    # ============================================================
    # 线程入口
    # ============================================================
    def run(self) -> None:
        # 所有依赖事件循环的 QObject 在这里创建
        self.jlink = pylink.JLink()
        self._reset_utf8_decoder()
        self._poll_timer = QTimer()  # 无 parent → 归属当前线程
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self._poll_rtt)

        # 注：信号 ↔ 槽连接在主线程已经建立（信号对象在 __init__），
        # 默认 AutoConnection 会变 QueuedConnection（跨线程）
        self.connect_requested.connect(self._on_connect, type=Qt.ConnectionType.QueuedConnection)
        self.disconnect_requested.connect(self._on_disconnect, type=Qt.ConnectionType.QueuedConnection)
        self.send_data_requested.connect(self._on_send_data, type=Qt.ConnectionType.QueuedConnection)
        self.reset_target_requested.connect(self._on_reset_target, type=Qt.ConnectionType.QueuedConnection)
        self.set_rtt_channel_requested.connect(self._on_set_channel, type=Qt.ConnectionType.QueuedConnection)
        self.set_pause_receive_requested.connect(self._on_set_paused, type=Qt.ConnectionType.QueuedConnection)
        self.set_power_output_requested.connect(self._on_set_power, type=Qt.ConnectionType.QueuedConnection)
        self.read_memory_requested.connect(self._on_read_memory, type=Qt.ConnectionType.QueuedConnection)
        self.export_firmware_requested.connect(self._on_export_firmware, type=Qt.ConnectionType.QueuedConnection)
        self.start_log_recording_requested.connect(self._on_start_log, type=Qt.ConnectionType.QueuedConnection)
        self.stop_log_recording_requested.connect(self._on_stop_log, type=Qt.ConnectionType.QueuedConnection)
        self.stop_requested.connect(self._on_stop, type=Qt.ConnectionType.QueuedConnection)

        self._ready = True
        self.exec()

    # ============================================================
    # 状态查询（仅给测试 / 调试用，必须线程安全；Python 单赋值原子，简单读 OK）
    # ============================================================
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
    # 命令槽（占位，下一 Task 实现）
    # ============================================================
    @Slot(str, bool)
    def _on_send_data(self, data: str, is_hex: bool) -> None:
        # Task 8 实现
        pass

    @Slot()
    def _on_reset_target(self) -> None:
        pass

    @Slot(int)
    def _on_set_channel(self, channel: int) -> None:
        self._channel = channel
        self.log_message.emit("info", f"RTT 通道切换为 {channel}")

    @Slot(bool)
    def _on_set_paused(self, paused: bool) -> None:
        self._paused = paused

    @Slot(bool)
    def _on_set_power(self, enable: bool) -> None:
        pass

    @Slot(int, int)
    def _on_read_memory(self, addr: int, size: int) -> None:
        pass

    @Slot(str, int, int)
    def _on_export_firmware(self, path: str, start_addr: int, size: int) -> None:
        pass

    @Slot(str)
    def _on_start_log(self, log_dir: str) -> None:
        pass

    @Slot()
    def _on_stop_log(self) -> None:
        pass

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
        """主线程发 stop_requested → worker 自己清理 + quit。"""
        self._do_disconnect()
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self.quit()  # 让 run() 的 exec() 返回
