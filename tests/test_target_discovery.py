"""Tests for src/core/target_discovery.py.

These tests hit real pylink / pyOCD target enumerations (where installed) and
verify the filtering/caching/routing logic.
"""

from __future__ import annotations

import pytest

from core.probe.base import (
    BURNER_KIND_CMSIS_DAP,
    BURNER_KIND_JLINK,
    BURNER_KIND_STLINK,
)
from core.target_discovery import (
    TargetDeviceInfo,
    get_pylink_target_infos,
    get_pylink_target_names,
    get_pyocd_target_infos,
    get_pyocd_target_names,
    target_infos_for_burner_kind,
    target_names_for_burner_kind,
)


@pytest.mark.parametrize("_first_call", [True])
def test_pylink_target_names_returns_sorted_uppercase(
    isolated_appdata, _first_call, jlink_dll_available
):
    """get_pylink_target_names returns an uppercase, sorted tuple with common MCUs.

    需要真实 J-Link DLL（pylink.supported_devices）；CI 无驱动时 skip。
    """
    if not jlink_dll_available:
        pytest.skip("需要 J-Link DLL（本机未安装 SEGGER 驱动）")
    names = get_pylink_target_names()

    assert isinstance(names, tuple)
    assert len(names) > 0
    assert all(name == name.upper() for name in names)
    assert list(names) == sorted(names)
    assert "STM32F030C8" in names


def test_pylink_target_names_filters_non_mcu_noise(jlink_dll_available):
    """Non-MCU entries like ARM7/Cortex-A are filtered out; STM32 stays.

    需要真实 J-Link DLL；CI 无驱动时 skip。
    """
    if not jlink_dll_available:
        pytest.skip("需要 J-Link DLL（本机未安装 SEGGER 驱动）")
    names = set(get_pylink_target_names())

    assert "ARM7" not in names
    assert "CORTEX-A5" not in names
    assert "CORTEX-A9" not in names
    assert any(name.startswith("STM32") for name in names)


def test_pyocd_target_names_returns_sorted_uppercase():
    """get_pyocd_target_names returns an uppercase, sorted tuple with known targets."""
    names = get_pyocd_target_names()

    assert isinstance(names, tuple)
    assert all(name == name.upper() for name in names)
    assert list(names) == sorted(names)
    assert "STM32F103RC" in names or "STM32F030C8" in names


@pytest.mark.parametrize(
    "kind, expected",
    [
        (BURNER_KIND_JLINK, get_pylink_target_names),
        (BURNER_KIND_CMSIS_DAP, get_pyocd_target_names),
        (BURNER_KIND_STLINK, get_pyocd_target_names),
    ],
)
def test_target_names_for_burner_kind_routes_correctly(kind, expected):
    """target_names_for_burner_kind delegates to the correct source."""
    assert target_names_for_burner_kind(kind) == expected()


@pytest.mark.parametrize(
    "kind, expected",
    [
        (BURNER_KIND_JLINK, get_pylink_target_infos),
        (BURNER_KIND_CMSIS_DAP, get_pyocd_target_infos),
        (BURNER_KIND_STLINK, get_pyocd_target_infos),
    ],
)
def test_target_infos_for_burner_kind_routes_correctly(kind, expected):
    """target_infos_for_burner_kind delegates to the correct source."""
    assert target_infos_for_burner_kind(kind) == expected()


def test_get_pylink_target_names_cached():
    """The pylink result is cached: two calls return the same tuple object."""
    first = get_pylink_target_names()
    second = get_pylink_target_names()

    assert first is second


def test_target_discovery_no_config_chip_models_dependency(
    isolated_appdata, jlink_dll_available
):
    """Target discovery is independent of ConfigService chip_models.

    需 J-Link DLL 提供设备列表；CI 无驱动时 skip。
    """
    if not jlink_dll_available:
        pytest.skip("需要 J-Link DLL（本机未安装 SEGGER 驱动）")
    names = get_pylink_target_names()

    assert isinstance(names, tuple)
    assert all(isinstance(name, str) for name in names)


def test_pylink_target_infos_returns_sorted_uppercase(jlink_dll_available):
    """get_pylink_target_infos returns TargetDeviceInfo tuple, uppercase and sorted.

    需 J-Link DLL；CI 无驱动时 skip。
    """
    if not jlink_dll_available:
        pytest.skip("需要 J-Link DLL（本机未安装 SEGGER 驱动）")
    infos = get_pylink_target_infos()

    assert isinstance(infos, tuple)
    assert len(infos) > 0
    assert all(isinstance(info, TargetDeviceInfo) for info in infos)
    assert all(info.name == info.name.upper() for info in infos)
    assert [info.name for info in infos] == sorted(info.name for info in infos)


def test_pylink_target_infos_contain_stm32f030c8_metadata(jlink_dll_available):
    """STM32F030C8 entry carries vendor and correct main-flash addr/size.

    需 J-Link DLL；CI 无驱动时 skip。

    回归：首个命中的「STM32F030C8 (allow opt. bytes)」变体的 legacy FlashAddr/
    FlashSize 是选项字节垃圾（0x06000000 / 65552）。修复后必须从 aFlashArea 主
    区域取到 0x08000000 / 65536。
    """
    if not jlink_dll_available:
        pytest.skip("需要 J-Link DLL（本机未安装 SEGGER 驱动）")
    infos = get_pylink_target_infos()
    info = next((i for i in infos if i.name == "STM32F030C8"), None)

    assert info is not None
    assert info.vendor == "ST"
    assert info.flash_addr == 0x08000000
    assert info.flash_size == 65536  # 64 KB 主 Flash，不是 65552
    assert info.ram_addr == 0x20000000
    assert info.ram_size == 8192


def test_pick_main_region_prefers_largest_area():
    """_pick_main_region 取 Size 最大的区域，滤掉选项字节小区域。"""
    from core.target_discovery import _pick_main_region

    class _Area:
        def __init__(self, addr, size):
            self.Addr = addr
            self.Size = size

    # STM32F030C8 (allow opt. bytes)：area[0]=16B 选项字节，area[1]=64KB 主 Flash
    areas = [_Area(0x06000000, 16), _Area(0x08000000, 65536), _Area(0, 0)]
    addr, size = _pick_main_region(areas, 0x06000000, 65552)
    assert (addr, size) == (0x08000000, 65536)


def test_pick_main_region_falls_back_to_legacy_when_array_empty():
    """aFlashArea 全空时回退 legacy 顶层字段。"""
    from core.target_discovery import _pick_main_region

    class _Area:
        def __init__(self, addr, size):
            self.Addr = addr
            self.Size = size

    addr, size = _pick_main_region([_Area(0, 0), _Area(0, 0)], 0x08000000, 65536)
    assert (addr, size) == (0x08000000, 65536)
    # legacy 也非法 -> None
    assert _pick_main_region([], None, None) == (None, None)


def test_pyocd_target_infos_returns_sorted_uppercase():
    """get_pyocd_target_infos returns TargetDeviceInfo tuple, uppercase and sorted."""
    infos = get_pyocd_target_infos()

    assert isinstance(infos, tuple)
    assert all(isinstance(info, TargetDeviceInfo) for info in infos)
    assert all(info.name == info.name.upper() for info in infos)
    assert [info.name for info in infos] == sorted(info.name for info in infos)


def test_pyocd_target_infos_have_memory_or_none():
    """pyOCD TargetDeviceInfo entries have valid memory fields or None."""
    infos = get_pyocd_target_infos()
    assert len(infos) > 0
    for info in infos:
        assert isinstance(info.name, str) and info.name
        if info.flash_addr is not None:
            assert isinstance(info.flash_addr, int)
        if info.flash_size is not None:
            assert isinstance(info.flash_size, int) and info.flash_size >= 0
        if info.ram_addr is not None:
            assert isinstance(info.ram_addr, int)
        if info.ram_size is not None:
            assert isinstance(info.ram_size, int) and info.ram_size >= 0
