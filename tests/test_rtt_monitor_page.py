"""UI 测试：RTTMonitorPage 的发送历史、hex 模式、自动滚动 guard、插入标记、reset 路由。

worker 用一个轻量 QObject 替身，只暴露 RTTMonitorPage 实际连接的信号 + 方法。
这样可以脱离真 JLinkWorker / pylink / QThread，让单页测试稳定快速。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal


class FakeWorker(QObject):
    """与 JLinkWorker 同形的信号 stub。RTTMonitorPage 只需要这些通道。"""
    connect_requested = Signal(str, str, int, int)
    disconnect_requested = Signal()
    reset_requested = Signal(str)
    send_data_requested = Signal(str, bool)
    set_pause_receive_requested = Signal(bool)
    set_power_output_requested = Signal(bool)
    set_rtt_channel_requested = Signal(int)
    set_encoding_requested = Signal(str)
    set_poll_interval_requested = Signal(int)
    start_log_recording_requested = Signal(str)
    stop_log_recording_requested = Signal()
    rtt_data_received = Signal(str)
    connection_state_changed = Signal(bool)
    command_result = Signal(str, bool, str)
    log_message = Signal(str, str)
    stop_requested = Signal()

    def __init__(self):
        super().__init__()
        self._device_info = {
            "target_device": "STM32H750VB",
            "interface": "SWD",
            "speed_khz": 4000,
        }
        self._sent: list[tuple[str, bool]] = []
        self._resets: list[str] = []
        self.send_data_requested.connect(
            lambda t, h: self._sent.append((t, h)))
        self.reset_requested.connect(lambda m: self._resets.append(m))

    def get_device_info(self) -> dict:
        return dict(self._device_info)

    def get_throughput_snapshot(self) -> dict:
        return {"total_bytes": 0, "total_lines": 0}

    def get_log_path(self) -> str | None:
        return None


@pytest.fixture
def rtt_page(qtbot, isolated_appdata):
    """RTTMonitorPage + FakeWorker；APPDATA 走 tmp 不污染真实配置。"""
    from core.config_service import ConfigService
    from ui.rtt_monitor_page import RTTMonitorPage
    cfg = ConfigService()
    worker = FakeWorker()
    page = RTTMonitorPage(worker, cfg)
    qtbot.addWidget(page)
    return page, worker, cfg


def test_send_text_routes_through_worker_and_persists_history(rtt_page, qtbot):
    """点击发送应 emit send_data_requested(text, hex=False) 并更新 send_history。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())  # 解锁 btn_send
    page.le_send.setText("hello world")
    page.btn_send.click()
    qtbot.wait(20)
    assert worker._sent == [("hello world", False)]
    assert "hello world" in cfg.get("send_history")


def test_hex_checkbox_persists_and_passes_to_send(rtt_page, qtbot):
    """勾上 Hex 后再发送，emit 时 hex 参数应为 True，且 cfg 已持久化。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())
    page.chk_hex.setChecked(True)
    qtbot.wait(20)
    assert cfg.get("hex_send_mode") is True
    page.le_send.setText("DEAD BEEF")
    page.btn_send.click()
    qtbot.wait(20)
    assert worker._sent == [("DEAD BEEF", True)]


def test_send_history_dedups_existing_entries(rtt_page, qtbot):
    """重复发送同一文本应去重并置末（reverse 显示后在最前）。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())
    for t in ["a", "b", "a"]:
        page.le_send.setText(t)
        page.btn_send.click()
        qtbot.wait(10)
    hist = cfg.get("send_history")
    assert hist == ["b", "a"]


def test_empty_text_does_not_send(rtt_page, qtbot):
    """空文本点发送应 no-op。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())
    page.le_send.setText("")
    page.btn_send.click()
    qtbot.wait(20)
    assert worker._sent == []


def test_reset_button_routes_with_configured_mode(rtt_page, qtbot):
    """点重置按钮 emit reset_requested(cfg.reset_mode)。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())
    cfg.set("reset_mode", "auto_reconnect")
    cfg.flush()
    page.btn_reset.click()
    qtbot.wait(20)
    assert worker._resets[-1] == "auto_reconnect"


def test_reset_halt_button_routes_with_halt_mode_regardless_of_cfg(rtt_page, qtbot):
    """重置并暂停 emit 固定 'halt'，不受 cfg.reset_mode 影响。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())
    cfg.set("reset_mode", "auto_reconnect")  # 故意设个干扰值
    page.btn_reset_halt.click()
    qtbot.wait(20)
    assert worker._resets[-1] == "halt"


def test_state_changed_to_connected_enables_send_and_reset(rtt_page, qtbot):
    """connection_state_changed(True) 后，发送 + 两个重置按钮都应 enabled。"""
    page, worker, _ = rtt_page
    worker.connection_state_changed.emit(True)
    qtbot.wait(20)
    assert page.btn_send.isEnabled()
    assert page.btn_reset.isEnabled()
    assert page.btn_reset_halt.isEnabled()
    assert page._is_connected is True


def test_state_changed_to_disconnected_resets_ui(rtt_page, qtbot):
    """connection_state_changed(False) 应禁掉发送/重置且复位状态文本。"""
    page, worker, _ = rtt_page
    worker.connection_state_changed.emit(True)
    qtbot.wait(20)
    worker.connection_state_changed.emit(False)
    qtbot.wait(20)
    assert not page.btn_send.isEnabled()
    assert not page.btn_reset.isEnabled()
    assert page._is_connected is False
    assert "未连接" in page.lbl_status_state.text()


def test_rtt_data_received_appends_to_display(rtt_page, qtbot):
    """worker.rtt_data_received 进来的文本应追加到 display。"""
    page, worker, _ = rtt_page
    worker.rtt_data_received.emit("hello from MCU\n")
    qtbot.wait(50)
    assert "hello from MCU" in page.display.toPlainText()


def test_insert_mark_text_writes_session_marker(rtt_page, qtbot):
    """_insert_mark_text 应在 display 追加 ──── text ──── 分隔行。"""
    page, _, _ = rtt_page
    page._insert_mark_text("已连接 STM32 @ 12:34:56")
    text = page.display.toPlainText()
    assert "──── 已连接 STM32 @ 12:34:56 ────" in text


def test_shortcut_connect_no_op_when_already_connected(rtt_page, qtbot):
    """F2 在已连接状态下应 no-op（不触发按钮 click），避免误发起断开。"""
    page, worker, _ = rtt_page
    page._set_connected_ui(worker.get_device_info())
    page.on_shortcut_connect()
    qtbot.wait(20)
    # 仍是已连接（按钮文字「断开」未变）
    assert page._is_connected is True
    assert page.btn_connect.text() == "断开"


def test_programmatic_scroll_guard_blocks_auto_scroll_uncheck(rtt_page, qtbot):
    """guard 期间用户即便看到 sb.value 变化也不会触发 auto_scroll 取消勾选。"""
    page, _, _ = rtt_page
    assert page.chk_auto_scroll.isChecked()
    sb = page.display.verticalScrollBar()
    with page._programmatic_scroll_guard():
        sb.setValue(0)        # 模拟程序性回滚到顶部
    qtbot.wait(20)
    # 仍勾选 — guard 内的滚动事件被 _on_display_scrolled 忽略
    assert page.chk_auto_scroll.isChecked()
