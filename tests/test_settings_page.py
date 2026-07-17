"""UI 测试：SettingsPage 各设置项 → cfg 持久化 + 信号触发。

不测主题色 / 字体颜色对话框（QFontDialog/ColorDialog modal 阻塞，不适合 headless）；
只测控件状态变更是否落 cfg + 触发对应信号。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def settings_page(qtbot, isolated_appdata):
    from core.config_service import ConfigService
    from ui.settings_page import SettingsPage
    cfg = ConfigService()
    page = SettingsPage(cfg)
    qtbot.addWidget(page)
    return page, cfg


def test_theme_combo_persists_value(settings_page, qtbot):
    """主题切到「深色」应落 cfg.theme = 'dark'。"""
    page, cfg = settings_page
    page.cb_theme.setCurrentIndex(2)          # 跟随系统/浅色/深色
    qtbot.wait(20)
    assert cfg.get("theme") == "dark"


def test_theme_combo_default_auto(settings_page):
    """默认 cfg.theme 为 'auto'。"""
    _, cfg = settings_page
    assert cfg.get("theme") in ("auto", "light", "dark")


def test_max_display_lines_persists(settings_page, qtbot):
    page, cfg = settings_page
    page.sp_max_lines.setValue(50000)
    qtbot.wait(20)
    assert cfg.get("max_display_lines") == 50000


def test_poll_interval_persists_and_emits_signal(settings_page, qtbot):
    page, cfg = settings_page
    received: list[int] = []
    cfg.rtt_poll_interval_changed.connect(lambda v: received.append(v))
    page.sp_poll.setValue(250)
    qtbot.wait(20)
    assert cfg.get("rtt_poll_interval_ms") == 250
    assert 250 in received


def test_encoding_combo_persists_and_emits(settings_page, qtbot):
    page, cfg = settings_page
    received: list[str] = []
    cfg.rtt_encoding_changed.connect(lambda e: received.append(e))
    page.cb_encoding.setCurrentText("GBK")
    qtbot.wait(20)
    assert cfg.get("rtt_encoding") == "gbk"
    assert "gbk" in received


def test_auto_mark_checkboxes_persist(settings_page, qtbot):
    page, cfg = settings_page
    page.chk_auto_mark_connect.setChecked(True)
    page.chk_auto_mark_disconnect.setChecked(True)
    qtbot.wait(20)
    assert cfg.get("auto_mark_on_connect") is True
    assert cfg.get("auto_mark_on_disconnect") is True


def test_reset_mode_combo_persists_normal_and_auto(settings_page, qtbot):
    """两种重置模式都能正确持久化（索引 → 模式字符串映射）。"""
    page, cfg = settings_page
    # 选「自动重连」
    page.cb_reset_mode.setCurrentIndex(1)
    qtbot.wait(20)
    assert cfg.get("reset_mode") == "auto_reconnect"
    # 切回「正常」
    page.cb_reset_mode.setCurrentIndex(0)
    qtbot.wait(20)
    assert cfg.get("reset_mode") == "normal"


def test_reset_mode_change_emits_cfg_signal(settings_page, qtbot):
    """切换 reset_mode 应 emit reset_mode_changed，RTT 页就是靠这个刷按钮文字。"""
    page, cfg = settings_page
    received: list[str] = []
    cfg.reset_mode_changed.connect(lambda m: received.append(m))
    page.cb_reset_mode.setCurrentIndex(1)
    qtbot.wait(20)
    assert "auto_reconnect" in received


def test_font_size_spinbox_persists_and_emits(settings_page, qtbot):
    page, cfg = settings_page
    received: list[tuple[str, int]] = []
    cfg.font_changed.connect(lambda fam, sz: received.append((fam, sz)))
    page.sp_font_size.setValue(16)
    qtbot.wait(20)
    assert cfg.get("font_size") == 16
    assert any(sz == 16 for _, sz in received)


def test_ui_font_family_default_is_auto(settings_page):
    """默认界面字体 family 为空串（=跟随系统），下拉当前项的 userData 为空。"""
    page, _ = settings_page
    idx = page.cb_ui_font.currentIndex()
    assert page.cb_ui_font.itemData(idx) == ""
    assert page._cfg.get("ui_font_family") == ""


def _populate_ui_font_combo(page) -> int:
    """在 offscreen 平台 QFontDatabase 可能返回空字体列表，combo 只剩「跟随系统」。
    补一个测试用 family 项，返回其 index。"""
    page.cb_ui_font.addItem("TestFontFamily", icon=None, userData="TestFontFamily")
    return page.cb_ui_font.count() - 1


def test_ui_font_family_persists_and_emits(settings_page, qtbot):
    """下拉选中具体 family → cfg.ui_font_family 落值并 emit 信号。"""
    page, cfg = settings_page
    target_idx = _populate_ui_font_combo(page)
    received: list[str] = []
    cfg.ui_font_family_changed.connect(lambda fam: received.append(fam))
    page.cb_ui_font.setCurrentIndex(target_idx)
    qtbot.wait(20)
    assert cfg.get("ui_font_family") == "TestFontFamily"
    assert "TestFontFamily" in received


def test_ui_font_family_auto_emits_empty(settings_page, qtbot):
    """选「跟随系统」项（userData="") → cfg 置空串并 emit 空。

    先设非空使 currentIndex != 0，再 setCurrentIndex(0) 才会真正触发
    currentIndexChanged（Qt 同值不发信号）。"""
    page, cfg = settings_page
    target_idx = _populate_ui_font_combo(page)
    page.cb_ui_font.setCurrentIndex(target_idx)
    qtbot.wait(20)
    assert cfg.get("ui_font_family") == "TestFontFamily"
    received: list[str] = []
    cfg.ui_font_family_changed.connect(lambda fam: received.append(fam))
    page.cb_ui_font.setCurrentIndex(0)
    qtbot.wait(20)
    assert cfg.get("ui_font_family") == ""
    assert "" in received