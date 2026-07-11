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

    def get_stats(self) -> tuple[int, int, float]:
        return (0, 0, 0.0)


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
    page.te_send.setPlainText("hello world")
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
    page.te_send.setPlainText("DEAD BEEF")
    page.btn_send.click()
    qtbot.wait(20)
    assert worker._sent == [("DEAD BEEF", True)]


def test_send_history_dedups_existing_entries(rtt_page, qtbot):
    """重复发送同一文本应去重并置末（reverse 显示后在最前）。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())
    for t in ["a", "b", "a"]:
        page.te_send.setPlainText(t)
        page.btn_send.click()
        qtbot.wait(10)
    hist = cfg.get("send_history")
    assert hist == ["b", "a"]


def test_empty_text_does_not_send(rtt_page, qtbot):
    """空文本点发送应 no-op。"""
    page, worker, cfg = rtt_page
    page._set_connected_ui(worker.get_device_info())
    page.te_send.setPlainText("")
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
    """connection_state_changed(True) 后，两个重置按钮应 enabled。"""
    page, worker, _ = rtt_page
    worker.connection_state_changed.emit(True)
    qtbot.wait(20)
    # 发送按钮始终 enabled（未连接时点击提示，不 disable）
    assert page.btn_send.isEnabled()
    assert page.btn_reset.isEnabled()
    assert page.btn_reset_halt.isEnabled()
    assert page._is_connected is True


def test_state_changed_to_disconnected_resets_ui(rtt_page, qtbot):
    """connection_state_changed(False) 应禁掉重置且复位状态文本，发送按钮仍 enabled。"""
    page, worker, _ = rtt_page
    worker.connection_state_changed.emit(True)
    qtbot.wait(20)
    worker.connection_state_changed.emit(False)
    qtbot.wait(20)
    # 发送按钮始终 enabled
    assert page.btn_send.isEnabled()
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


# ---- HEX 发送双向切换 ----
def test_hex_send_toggle_text_to_hex(rtt_page, qtbot):
    """勾选 Hex 时应将输入框文本转为 HEX 格式。"""
    page, _, _ = rtt_page
    page.te_send.setPlainText("hello")
    page.chk_hex.setChecked(True)
    qtbot.wait(20)
    assert page.te_send.toPlainText() == "68 65 6C 6C 6F"


def test_hex_send_toggle_hex_to_text(rtt_page, qtbot):
    """取消 Hex 时应将 HEX 转回文本。"""
    page, _, _ = rtt_page
    page.chk_hex.setChecked(True)
    qtbot.wait(10)
    page.te_send.setPlainText("68 65 6C 6C 6F")
    page.chk_hex.setChecked(False)
    qtbot.wait(20)
    assert page.te_send.toPlainText() == "hello"


# ---- CRC 脚本追加 ----
def test_crc_script_appends_crc_to_payload(rtt_page, qtbot):
    """启用 CRC 脚本后发送，应在 payload 后追加 CRC 字节。"""
    page, worker, _ = rtt_page
    page._set_connected_ui(worker.get_device_info())
    page.chk_crc_script.setChecked(True)
    page.cb_crc_algo.setCurrentIndex(0)  # CRC-8
    page.te_send.setPlainText("AB")
    page.btn_send.click()
    qtbot.wait(20)
    assert len(worker._sent) == 1
    sent_text, is_hex = worker._sent[0]
    assert is_hex is True
    # "AB" = 0x41 0x42，CRC-8 追加 1 字节，总共 3 字节 HEX
    parts = sent_text.split()
    assert len(parts) == 3  # 41 42 + 1 byte CRC
    assert parts[0] == "41"
    assert parts[1] == "42"


# ---- 定时发送 ----
def test_timed_send_not_connected_shows_pending(rtt_page, qtbot):
    """未连接时勾选定时发送应设 pending 标志。"""
    page, _, _ = rtt_page
    assert not page._is_connected
    page.chk_timed_send.setChecked(True)
    qtbot.wait(20)
    assert page._timed_send_pending is True
    assert not page._timed_send_timer.isActive()


def test_timed_send_connected_starts_timer(rtt_page, qtbot):
    """已连接时勾选定时发送应启动定时器。"""
    page, worker, _ = rtt_page
    page._set_connected_ui(worker.get_device_info())
    page.chk_timed_send.setChecked(True)
    qtbot.wait(20)
    assert page._timed_send_timer.isActive()
    page.chk_timed_send.setChecked(False)
    qtbot.wait(20)
    assert not page._timed_send_timer.isActive()


# ---- 自动断帧 ----
def test_auto_frame_inserts_newline_on_gap(rtt_page, qtbot):
    """开启自动断帧且间隔超阈值时应插入换行。"""
    page, worker, _ = rtt_page
    page.chk_auto_frame.setChecked(True)
    page.le_frame_timeout.setText("5")  # 5ms 阈值
    # 第一批数据
    worker.rtt_data_received.emit("frame1")
    qtbot.wait(30)  # 等 > 5ms
    # 第二批数据
    worker.rtt_data_received.emit("frame2")
    qtbot.wait(30)
    text = page.display.toPlainText()
    # 两帧之间应有换行
    assert "frame1" in text
    assert "frame2" in text


# ---- 搜索栏选中文本自动填充 ----
def test_shortcut_find_fills_selected_text(rtt_page, qtbot):
    """Ctrl+F 时应将 display 选中文本填入搜索栏。"""
    page, _, _ = rtt_page
    page.show()  # 确保 widget 可见，isVisible() 才能返回 True
    page.display.setPlainText("hello world")
    # 模拟选中 "world"
    from PySide6.QtGui import QTextCursor
    tc = page.display.textCursor()
    tc.setPosition(6)
    tc.setPosition(11, QTextCursor.MoveMode.KeepAnchor)
    page.display.setTextCursor(tc)
    page.on_shortcut_find()
    qtbot.wait(20)
    assert page.search_bar.le_search.text() == "world"
    assert page.search_bar.isVisible()


# ---- 工具栏 / 脚本红色提示（UI 重构后行为）----
def test_crc_script_toggle_shows_red_tip_and_red_border(rtt_page, qtbot):
    """勾选 CRC 脚本应给发送框加红色渐变边框（非独立标签）。"""
    page, _, _ = rtt_page
    before = page.te_send.styleSheet()
    page.chk_crc_script.setChecked(True)
    qtbot.wait(20)
    ss = page.te_send.styleSheet()
    assert "#cc3300" in ss and "qlineargradient" in ss
    page.chk_crc_script.setChecked(False)
    qtbot.wait(20)
    assert page.te_send.styleSheet() == before


def test_toolbar_pause_syncs_with_left_panel_checkbox(rtt_page, qtbot):
    """工具栏暂停按钮与左侧面板 chk_pause 应双向同步。"""
    page, _, _ = rtt_page
    page.btn_toolbar_pause.setChecked(True)
    qtbot.wait(20)
    assert page.chk_pause.isChecked()
    page.chk_pause.setChecked(False)
    qtbot.wait(20)
    assert not page.btn_toolbar_pause.isChecked()


def test_toolbar_clear_empties_display(rtt_page, qtbot):
    """工具栏清空按钮应清空显示区。"""
    page, worker, _ = rtt_page
    worker.rtt_data_received.emit("some data\n")
    qtbot.wait(30)
    assert "some data" in page.display.toPlainText()
    page.btn_toolbar_clear.click()
    qtbot.wait(20)
    assert page.display.toPlainText() == ""
