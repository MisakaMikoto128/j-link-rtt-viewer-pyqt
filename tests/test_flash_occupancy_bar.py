"""FlashOccupancyBar（烧录页固件文件卡片内的紧凑 Flash 占用条）测试。

锁住需求：加载固件（任意格式）后显示其在目标 Flash 中的位置/占比；占用区
颜色跟随主题色；无固件时无占用区；无设备时显示占位提示。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def bar(qtbot):
    from ui.firmware_analysis_view import FlashOccupancyBar

    w = FlashOccupancyBar()
    qtbot.addWidget(w)
    return w


@pytest.fixture
def device():
    from core.target_discovery import TargetDeviceInfo

    # flash_addr=0x08000000, flash_size=256KB
    return TargetDeviceInfo(
        name="TESTDEV",
        vendor="Test",
        flash_addr=0x08000000,
        flash_size=256 * 1024,
        ram_addr=0x20000000,
        ram_size=64 * 1024,
    )


def test_initial_state_no_device_no_firmware(bar):
    """初始：无设备 + 无固件 -> _fw_start/_fw_end 为 None，_device 为 None。"""
    assert bar._device is None
    assert bar._fw_start is None
    assert bar._fw_end is None


def test_set_device_and_firmware_range(bar, device):
    """设置设备 + 固件范围后，内部状态正确保存（占用区数据就绪）。"""
    bar.set_device_info(device)
    bar.set_firmware_range(0x08000000, 0x08010000)
    assert bar._device is device
    assert bar._fw_start == 0x08000000
    assert bar._fw_end == 0x08010000


def test_clear_resets_firmware_range(bar, device):
    """clear() 清掉固件范围（无固件占用），但保留设备信息。"""
    bar.set_device_info(device)
    bar.set_firmware_range(0x08000000, 0x08010000)
    bar.clear()
    assert bar._fw_start is None
    assert bar._fw_end is None
    assert bar._device is device


def test_paint_no_crash_all_states(bar, device, qtbot):
    """paintEvent 在四种状态下都不抛异常（offscreen 渲染）。"""
    bar.resize(400, 24)
    # 1. 无设备无固件
    bar.repaint()
    # 2. 有设备无固件
    bar.set_device_info(device)
    bar.repaint()
    # 3. 有设备有固件
    bar.set_firmware_range(0x08000000, 0x08008000)
    bar.repaint()
    # 4. 固件溢出 Flash
    bar.set_firmware_range(0x08000000, 0x08000000 + 512 * 1024)
    bar.repaint()


def test_flash_page_has_compact_bar(qtbot, isolated_appdata, monkeypatch):
    """FlashPage 文件卡片内有 flash_bar，且与 analysis_view 是不同实例。"""
    from core.config_service import ConfigService
    from core.probe.base import BURNER_KIND_JLINK
    from core.target_discovery import TargetDeviceInfo
    from ui import flash_page
    from ui.flash_page import FlashPage

    # isolated_appdata 下磁盘缓存空，read_cached_* 返回空 -> _lookup_target_info 返回
    # None -> flash_bar._device 为 None。注入测试用 info，验证"构造时回填 device
    # info"的 UI 逻辑（而非缓存存在性）。
    fake_info = TargetDeviceInfo(
        name="STM32H750VB",
        vendor="ST",
        flash_addr=0x08000000,
        flash_size=128 * 1024,
        ram_addr=0x20000000,
        ram_size=1024 * 1024,
    )
    monkeypatch.setattr(
        flash_page,
        "read_cached_target_infos_for_burner_kind",
        lambda kind: (fake_info,) if kind == BURNER_KIND_JLINK else (),
    )
    monkeypatch.setattr(
        flash_page,
        "read_cached_target_names_for_burner_kind",
        lambda kind: [fake_info.name] if kind == BURNER_KIND_JLINK else [],
    )
    cfg = ConfigService()
    page = FlashPage(cfg)
    qtbot.addWidget(page)
    try:
        assert page.flash_bar is not None
        assert page.flash_bar is not page.analysis_view.flashmap
        # 构造时已按 prefs 回填的 cmb_device 初始化 device info
        assert page.flash_bar._device is not None
    finally:
        page.shutdown()


def test_flash_bar_updates_on_file_select(qtbot, isolated_appdata, fixtures_dir):
    """选 bin 固件后 flash_bar 收到固件范围（即使符号面板隐藏也更新）。"""
    from core.config_service import ConfigService
    from ui.flash_page import FlashPage

    cfg = ConfigService()
    page = FlashPage(cfg)
    qtbot.addWidget(page)
    try:
        page._select_file(str(fixtures_dir / "blink.bin"))
        # bin 文件：addr_start/addr_end 已写入 bar
        assert page.flash_bar._fw_start is not None
        assert page.flash_bar._fw_end is not None
        assert page.flash_bar._fw_end > page.flash_bar._fw_start
    finally:
        page.shutdown()
