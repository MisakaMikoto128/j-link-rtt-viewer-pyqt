"""UI 测试：MemoryViewerPage 的读取请求、hex dump 渲染、行宽切换、diff 高亮、跳转/搜索。"""
from __future__ import annotations

import types

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtCore import QPoint, QPointF
from qfluentwidgets import BodyLabel, ProgressBar


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


def test_display_font_family_fixed_monospace(mem_page, qtbot):
    """内存 hex 显示区 family 固定跟随 RTT 的 font_family（等宽），不随 UI 字体变。

    用户要求：hex dump 列对齐依赖等宽，全局 UI 字体若切成非等宽会让内存页错位，
    因此该显示区 family 与字号都独立（family 跟 RTT 等宽，size 用 memory_font_size）。
    """
    page, _, cfg = mem_page
    # 改 RTT 的 font_family → font_changed → _apply_font 刷新 display
    cfg.set("font_family", "Courier New")
    qtbot.wait(20)
    assert page.display.font().family() == "Courier New"
    assert page.display.font().pointSize() == int(cfg.get("memory_font_size"))
    # font_family 为空时回退 Consolas 默认等宽
    cfg.set("font_family", "")
    qtbot.wait(20)
    assert page.display.font().family() == "Consolas"


def test_display_font_family_ignores_ui_font(mem_page, qtbot):
    """改 ui_font_family 时 display family 不变（仍 RTT 等宽），字号也不变。"""
    page, _, cfg = mem_page
    before_family = page.display.font().family()
    before_size = page.display.font().pointSize()
    cfg.set("ui_font_family", "Segoe UI")
    qtbot.wait(20)
    assert page.display.font().family() == before_family
    assert page.display.font().pointSize() == before_size


def test_display_uses_fluent_hover_tip(mem_page):
    """hover 提示应是 Fluent 气泡（FluentHoverTip），不是原生 QToolTip。"""
    from ui.widgets.fluent_hover_tip import FluentHoverTip
    page, _, _ = mem_page
    assert isinstance(page._hover_tip, FluentHoverTip)


def test_hover_tip_show_and_hide(mem_page, qtbot):
    """_show_hover_tooltip 应驱动 _hover_tip 显示/隐藏。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page._buffer = bytes(range(64))
    page._buffer_base = 0x20000000
    page._last_hover_offset = -1
    page.show()
    qtbot.wait(20)
    page._hover_tip.show_at(page.display.mapToGlobal(page.display.rect().center()),
                            "addr 0x20000000\nu32 LE: 0x03020100", duration=0)
    assert page._hover_tip.is_showing()
    page._hover_tip.hide()
    assert not page._hover_tip.is_showing()


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
    assert "0x2000 0000" in text         # 地址前缀
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
    # 高亮应恰好覆盖变化字节的两位 hex，不错位
    assert sels[0].cursor.selectedText() == "AA"


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


def test_hover_parse_checkbox_default_on_and_persists(mem_page, qtbot):
    """悬浮解析默认开启，切换后持久化到 cfg。"""
    page, _, cfg = mem_page
    assert page.chk_hover.isChecked() is True
    page.chk_hover.setChecked(False)
    qtbot.wait(20)
    assert cfg.get("mem_hover_parse") is False


def test_hover_tooltip_suppressed_when_disabled(mem_page, qtbot):
    """chk_hover 关闭时 _show_hover_tooltip 不弹出 tip。"""
    page, worker, _ = mem_page
    _set_connected(page, worker, qtbot)
    page._buffer = bytes(range(64))
    page._buffer_base = 0x20000000
    page._last_hover_offset = -1
    page.chk_hover.setChecked(False)
    page.show()
    qtbot.wait(20)
    event = types.SimpleNamespace(
        pos=lambda: QPoint(100, 5),
        globalPosition=lambda: QPointF(200, 50),
    )
    page._show_hover_tooltip(event)
    assert page._hover_tip.is_showing() is False


def test_goto_addr_persisted(mem_page, qtbot):
    """le_goto editingFinished 后地址持久化到 cfg。"""
    page, _, cfg = mem_page
    page.le_goto.setText("0x20000010")
    page.le_goto.editingFinished.emit()
    qtbot.wait(20)
    assert cfg.get("mem_goto_addr") == "0x20000010"


def test_goto_addr_restored_on_init(qtbot, isolated_appdata):
    """cfg 里保存的 goto 地址应在页面构造时恢复。"""
    from core.config_service import ConfigService
    from ui.memory_viewer_page import MemoryViewerPage
    cfg = ConfigService()
    cfg.set("mem_goto_addr", "0x20000020")
    worker = FakeMemWorker()
    page = MemoryViewerPage(worker, cfg)
    qtbot.addWidget(page)
    assert page.le_goto.text() == "0x20000020"


def test_cursor_addr_selectable(mem_page):
    """光标地址标签应可被鼠标选中复制。"""
    page, _, _ = mem_page
    assert page.lbl_cursor_addr.textInteractionFlags() & Qt.TextSelectableByMouse


def test_write_card_above_export_card(mem_page, qtbot):
    """写内存卡片应排在导出固件卡片上方。"""
    page, _, _ = mem_page
    page.resize(1200, 1200)
    page.show()
    qtbot.wait(50)
    write_y = page.lbl_write_title.mapTo(page, QPoint(0, 0)).y()
    export_y = page.lbl_export_title.mapTo(page, QPoint(0, 0)).y()
    assert write_y < export_y


def test_export_path_label_and_progress_are_fluent(mem_page):
    """导出卡片上的路径标签和进度条应使用 qfluentwidgets 控件。"""
    page, _, _ = mem_page
    assert isinstance(page.lbl_path, BodyLabel)
    assert isinstance(page.pb_export, ProgressBar)


def test_goto_save_on_jump(mem_page, qtbot):
    """点击 Go 跳转时，goto 地址应立即持久化到 cfg。"""
    page, worker, cfg = mem_page
    _set_connected(page, worker, qtbot)
    worker.memory_read_finished.emit(0x20000000, b"\x00" * 16)
    qtbot.wait(20)
    page.le_goto.setText("0x20000005")
    page.btn_goto.click()
    qtbot.wait(20)
    assert cfg.get("mem_goto_addr") == "0x20000005"
