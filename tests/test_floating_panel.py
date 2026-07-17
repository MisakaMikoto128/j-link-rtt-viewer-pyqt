"""冒烟测试：收窄模式下 ToolToggleButton 控制左侧面板悬浮卡片的显隐。

验证要点：
1. 正常模式下：_config_panel 在布局中，悬浮卡片隐藏，toggle 按钮不可见。
2. 收窄模式下：_config_panel 被 reparent 到悬浮卡片，toggle 按钮可见。
3. toggle 按钮点击 → 悬浮卡片可见 + toggle checked。
4. 再次点击 → 悬浮卡片隐藏 + toggle unchecked。
5. 收窄模式下展开卡片不会退出收窄模式。
6. 从收窄模式回到正常模式：面板回到布局，卡片隐藏，toggle 复位。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal


class FakeWorker(QObject):
    connect_requested = Signal(str, str, int, int, str)
    disconnect_requested = Signal()
    enumerate_devices_requested = Signal()
    reset_requested = Signal(str)
    send_data_requested = Signal(str, bool)
    set_pause_receive_requested = Signal(bool)
    set_power_output_requested = Signal(bool)
    set_rtt_channel_requested = Signal(int)
    set_encoding_requested = Signal(str)
    set_poll_interval_requested = Signal(int)
    start_log_recording_requested = Signal(str)
    stop_log_recording_requested = Signal()
    rtt_data_received = Signal(int, str)
    unexpected_disconnect = Signal(str)
    reconnect_status = Signal(str, str)
    connection_state_changed = Signal(bool)
    command_result = Signal(str, bool, str)
    log_message = Signal(str, str)
    devices_enumerated = Signal(str)
    stop_requested = Signal()
    set_auto_reconnect_requested = Signal(bool)

    def __init__(self):
        super().__init__()
        self._device_info = {"target_device": "STM32H750VB",
                             "interface": "SWD", "speed_khz": 4000}

    def get_device_info(self) -> dict:
        return dict(self._device_info)

    def get_stats(self, channel: int | None = None) -> dict:
        return {"bytes": 0, "lines": 0, "session_start_ts": 0.0}

    def get_num_up_channels(self) -> int:
        return 1


@pytest.fixture
def rtt_page(qtbot, isolated_appdata):
    from core.config_service import ConfigService
    from ui.rtt_monitor_page import RTTMonitorPage
    cfg = ConfigService()
    worker = FakeWorker()
    page = RTTMonitorPage(worker, cfg)
    qtbot.addWidget(page)
    page.resize(1000, 600)  # 正常模式宽度
    page.show()
    qtbot.wait(50)
    # offscreen 模式下 resize 可能不触发 resizeEvent，显式确保正常模式
    page._set_config_panel_visible(True)
    return page, worker, cfg


# ── 正常模式 ──────────────────────────────────────────────────────

def test_normal_mode_panel_in_layout_card_hidden(rtt_page, qtbot):
    """正常模式：面板在 main_split 布局中，悬浮卡片隐藏，toggle 按钮不可见。"""
    page, _, _ = rtt_page
    assert page._config_visible is True
    assert page._floating_card.isVisible() is False
    assert page._toolbar.isVisible() is False
    assert page.btn_panel_toggle.isVisible() is False
    # 面板的 parent 应该不是悬浮卡片（在布局中）
    assert page._config_panel.parent() is not page._floating_card


# ── 收窄模式 ──────────────────────────────────────────────────────

def test_narrow_mode_panel_reparented_to_card(rtt_page, qtbot):
    """收窄模式：面板被 reparent 到悬浮卡片，toggle 按钮可见，卡片默认隐藏。"""
    page, _, _ = rtt_page
    page._set_config_panel_visible(False)  # 模拟收窄模式
    qtbot.wait(50)
    assert page._config_visible is False
    assert page._toolbar.isVisible() is True
    assert page.btn_panel_toggle.isVisible() is True
    # 面板已 reparent 到悬浮卡片
    assert page._config_panel.parent() is page._floating_card
    # 卡片默认隐藏
    assert page._floating_card.isVisible() is False
    assert page.btn_panel_toggle.isChecked() is False


def test_toggle_button_shows_card(rtt_page, qtbot):
    """点击 toggle 按钮 → 悬浮卡片可见 + 按钮 checked。"""
    page, _, _ = rtt_page
    page._set_config_panel_visible(False)
    qtbot.wait(50)
    page.btn_panel_toggle.setChecked(True)
    qtbot.wait(300)  # 等动画完成
    assert page._floating_card.isVisible() is True
    assert page.btn_panel_toggle.isChecked() is True


def test_toggle_button_hides_card(rtt_page, qtbot):
    """再次点击 toggle → 悬浮卡片隐藏 + 按钮 unchecked。"""
    page, _, _ = rtt_page
    page._set_config_panel_visible(False)
    qtbot.wait(50)
    page.btn_panel_toggle.setChecked(True)
    qtbot.wait(300)
    page.btn_panel_toggle.setChecked(False)
    qtbot.wait(300)
    assert page._floating_card.isVisible() is False
    assert page.btn_panel_toggle.isChecked() is False


def test_card_popup_does_not_exit_narrow_mode(rtt_page, qtbot):
    """收窄模式下弹出卡片，应用仍处于收窄模式。"""
    page, _, _ = rtt_page
    page._set_config_panel_visible(False)
    qtbot.wait(50)
    page.btn_panel_toggle.setChecked(True)
    qtbot.wait(300)
    # 仍在收窄模式
    assert page._config_visible is False
    assert page._toolbar.isVisible() is True
    # 面板仍在卡片中
    assert page._config_panel.parent() is page._floating_card


def test_back_to_normal_mode_restores_panel(rtt_page, qtbot):
    """收窄→正常：面板回到布局，卡片隐藏，toggle 复位。"""
    page, _, _ = rtt_page
    page._set_config_panel_visible(False)
    qtbot.wait(50)
    page.btn_panel_toggle.setChecked(True)
    qtbot.wait(300)
    # 回到正常模式
    page._set_config_panel_visible(True)
    qtbot.wait(50)
    assert page._config_visible is True
    assert page._floating_card.isVisible() is False
    assert page.btn_panel_toggle.isChecked() is False
    assert page._toolbar.isVisible() is False
    # 面板回到布局
    assert page._config_panel.parent() is not page._floating_card


def test_card_content_is_same_panel_widget(rtt_page, qtbot):
    """悬浮卡片承载的是同一个 _config_panel 实例（状态保留）。"""
    page, _, _ = rtt_page
    # 记录正常模式下的一些状态
    original_channel = page.sp_channel.value()
    page._set_config_panel_visible(False)
    qtbot.wait(50)
    # 收窄后面板里的控件应该还是同一对象
    assert page.sp_channel.value() == original_channel
    # 展开卡片，控件仍可交互
    page.btn_panel_toggle.setChecked(True)
    qtbot.wait(300)
    page.sp_channel.setValue(7)
    assert page.sp_channel.value() == 7


def test_card_show_animation_only_x_direction(rtt_page, qtbot):
    """展开动画起点 Y 与目标 Y 一致，不会出现 Y 方向位移。"""
    from PySide6.QtCore import QPoint
    page, _, _ = rtt_page
    page._set_config_panel_visible(False)
    qtbot.wait(50)
    # 在动画开始前捕获 start value
    page.btn_panel_toggle.setChecked(True)
    # 立即检查动画的 start/end value（动画刚启动）
    pos_anim = page._card_pos_anim
    start_val = pos_anim.startValue()
    end_val = pos_anim.endValue()
    assert start_val is not None and end_val is not None
    # Y 必须一致——只有 X 变化
    assert start_val.y() == end_val.y()
    # X 应该相差 40px（从左滑入）
    assert end_val.x() - start_val.x() == 40
    qtbot.wait(300)
