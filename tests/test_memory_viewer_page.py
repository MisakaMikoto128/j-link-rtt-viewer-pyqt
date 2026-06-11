"""UI 测试：MemoryViewerPage 的读取请求、hex dump 渲染、行宽切换、diff 高亮、跳转/搜索。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal


class FakeMemWorker(QObject):
    """MemoryViewerPage 使用的 JLinkWorker 信号子集。"""
    read_memory_requested = Signal(int, int)
    write_memory_requested = Signal(int, bytes, bool)
    export_firmware_requested = Signal(int, int, str)
    set_pause_receive_requested = Signal(bool)
    connection_state_changed = Signal(bool)
    memory_read_finished = Signal(int, bytes)
    firmware_export_progress = Signal(int, int)
    firmware_export_finished = Signal(bool, str, str)
    command_result = Signal(str, bool, str)

    def __init__(self):
        super().__init__()
        self._reads: list[tuple[int, int]] = []
        self._writes: list[tuple[int, bytes, bool]] = []
        self.read_memory_requested.connect(
            lambda a, n: self._reads.append((a, n)))
        self.write_memory_requested.connect(
            lambda a, b, v: self._writes.append((a, bytes(b), v)))


@pytest.fixture
def mem_page(qtbot, isolated_appdata):
    from core.config_service import ConfigService
    from ui.memory_viewer_page import MemoryViewerPage
    cfg = ConfigService()
    worker = FakeMemWorker()
    page = MemoryViewerPage(worker, cfg)
    qtbot.addWidget(page)
    return page, worker, cfg


def _set_connected(page, worker, qtbot):
    worker.connection_state_changed.emit(True)
    qtbot.wait(20)


def test_read_button_disabled_until_connected(mem_page, qtbot):
    page, worker, _ = mem_page
    assert not page.btn_read.isEnabled()
    _set_connected(page, worker, qtbot)
    assert page.btn_read.isEnabled()


def test_read_click_emits_addr_and_size(mem_page, qtbot):
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page.le_read_addr.setText("0x20000000")
    page.le_read_size.setText("64")
    page.btn_read.click()
    qtbot.wait(20)
    assert worker._reads == [(0x20000000, 64)]


def test_read_decimal_address_accepted(mem_page, qtbot):
    """非 0x 开头按十进制解释——_parse_int 支持两种。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page.le_read_addr.setText("536870912")    # = 0x20000000
    page.le_read_size.setText("16")
    page.btn_read.click()
    qtbot.wait(20)
    assert worker._reads == [(0x20000000, 16)]


def test_read_bad_address_does_not_emit(mem_page, qtbot):
    """非法地址应被拦截，不 emit。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page.le_read_addr.setText("not-a-hex")
    page.le_read_size.setText("16")
    page.btn_read.click()
    qtbot.wait(20)
    assert worker._reads == []


def test_zero_or_oversized_read_rejected(mem_page, qtbot):
    """size=0 或 > 16MB 应被拦截。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page.le_read_addr.setText("0x20000000")
    for bad_size in ("0", str(16 * 1024 * 1024 + 1)):
        page.le_read_size.setText(bad_size)
        page.btn_read.click()
    qtbot.wait(20)
    assert worker._reads == []


def test_memory_read_renders_hex_dump(mem_page, qtbot):
    """收到 memory_read_finished 后 display 应显示 hex dump。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    data = bytes(range(32))
    worker.memory_read_finished.emit(0x20000000, data)
    qtbot.wait(50)
    text = page.display.toPlainText()
    assert "20000000" in text.lower()         # 地址前缀
    assert "00 01 02 03" in text              # 起始字节


def test_row_width_change_persists_to_cfg(mem_page, qtbot):
    """切换字节/行 ComboBox 应持久化到 cfg。"""
    page, _, cfg = mem_page
    page.cb_row_width.setCurrentText("32")
    qtbot.wait(20)
    assert int(cfg.get("mem_bytes_per_row")) == 32


def test_diff_highlight_after_second_read_with_change(mem_page, qtbot):
    """同地址同长度二次读，变化字节应产生 ExtraSelection。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page.chk_diff.setChecked(True)
    worker.memory_read_finished.emit(0x20000000, b"\x00" * 16)
    qtbot.wait(20)
    assert page.display.extraSelections() == []
    # 第二帧第 5 字节变了
    new = bytearray(16); new[5] = 0xAA
    worker.memory_read_finished.emit(0x20000000, bytes(new))
    qtbot.wait(50)
    sels = page.display.extraSelections()
    assert len(sels) == 1                     # 只有 1 字节变化


def test_diff_highlight_resets_when_addr_changes(mem_page, qtbot):
    """读不同地址 → diff 不应触发（即便长度一样）。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page.chk_diff.setChecked(True)
    worker.memory_read_finished.emit(0x20000000, b"\x00" * 16)
    qtbot.wait(20)
    worker.memory_read_finished.emit(0x20000010, b"\xff" * 16)
    qtbot.wait(50)
    assert page.display.extraSelections() == []


def test_disconnect_disables_buttons_and_stops_auto_refresh(mem_page, qtbot):
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page.chk_auto_refresh.setChecked(True)
    qtbot.wait(20)
    worker.connection_state_changed.emit(False)
    qtbot.wait(20)
    assert not page.btn_read.isEnabled()
    assert not page.chk_auto_refresh.isChecked()


def test_goto_invalid_outside_buffer_no_crash(mem_page, qtbot):
    """跳转到 buffer 外的地址应 warn 不崩溃。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    worker.memory_read_finished.emit(0x20000000, b"\x00" * 16)
    qtbot.wait(20)
    page.le_goto.setText("0x20001000")        # 在 buffer 外
    page.btn_goto.click()
    qtbot.wait(20)
    # 没崩就算通过；具体 InfoBar 状态不强制断言（InfoBar 是异步动画）
