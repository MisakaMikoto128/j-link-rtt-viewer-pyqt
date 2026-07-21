"""PyOCDBackend 单元测试：target 解析（CMSIS-Pack part_number 通配匹配）。

聚焦 _resolve_target_type 把用户填的 device_name 解析成 pack part_number。
不需要实际硬件 / pack；ManagedPacks.get_installed_targets 与 TARGET dict 均 mock。
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _stub_packs(monkeypatch, part_numbers):
    """把 ManagedPacks.get_installed_targets 替换为返回给定 part_number 列表。"""
    import pyocd.target as _pyocd_target
    monkeypatch.setattr(_pyocd_target, "TARGET", {})  # builtin dict 清空，强制走 pack 路径
    fakes = []
    for pn in part_numbers:
        t = MagicMock()
        t.part_number = pn
        fakes.append(t)
    monkeypatch.setattr(
        "pyocd.target.pack.pack_target.ManagedPacks.get_installed_targets",
        lambda: fakes,
    )


def test_pack_part_wildcard_eq():
    """'x' 视为单字符通配（仅 pattern 里是 'x' 的位置才通配）。"""
    from core.probe.pyocd_backend import _pack_part_wildcard_eq
    assert _pack_part_wildcard_eq("stm32f030c8tx", "stm32f030c8t6") is True
    assert _pack_part_wildcard_eq("stm32f030c8tx", "stm32f030c8t7") is True
    assert _pack_part_wildcard_eq("stm32f030c8tx", "stm32f030c8") is False  # 长度不同
    assert _pack_part_wildcard_eq("stm32f030c8", "stm32f030c8") is True
    # pattern 非 'x' 位必须严格相等：'t' != 'a' -> False
    assert _pack_part_wildcard_eq("stm32f030c8tx", "stm32f030c8ab") is False


def test_resolve_target_full_part_number_matches_wildcard(monkeypatch):
    """用户填完整型号 STM32F030C8T6 应匹配 pack 的 STM32F030C8Tx。

    'x' 通配：stm32f030c8tx 与 stm32f030c8t6 长度相同、'x' 位通配 -> 命中。
    """
    _stub_packs(monkeypatch, ["STM32F030C8Tx"])
    from core.probe.pyocd_backend import PyOCDBackend
    assert PyOCDBackend._resolve_target_type("STM32F030C8T6") == "STM32F030C8Tx"


def test_resolve_target_short_segger_name_matches_prefix(monkeypatch):
    """SEGGER 短名 STM32F030C8（不带后缀）仍应前缀匹配到 STM32F030C8Tx。"""
    _stub_packs(monkeypatch, ["STM32F030C8Tx"])
    from core.probe.pyocd_backend import PyOCDBackend
    assert PyOCDBackend._resolve_target_type("STM32F030C8") == "STM32F030C8Tx"


def test_resolve_target_case_insensitive(monkeypatch):
    """大小写无关。"""
    _stub_packs(monkeypatch, ["STM32F030C8Tx"])
    from core.probe.pyocd_backend import PyOCDBackend
    assert PyOCDBackend._resolve_target_type("stm32f030c8t6") == "STM32F030C8Tx"


def test_resolve_target_unknown_returns_none(monkeypatch):
    """无匹配 pack -> None（connect 层报装 pack 提示）。"""
    _stub_packs(monkeypatch, ["STM32F030C8Tx"])
    from core.probe.pyocd_backend import PyOCDBackend
    assert PyOCDBackend._resolve_target_type("STM32F999XY") is None


def test_swd_err_hint_appends_for_swd_errors():
    """SWD 通信类错误（IDCODE/DP/parity/transfer）追加接线排查提示。"""
    from core.probe.pyocd_backend import _swd_err_hint
    for err in (
        "STLink error (9): Get IDCODE error",
        "STLink error (22): DP error",
        "STLink error (23): DP parity error",
        "TransferFaultError: Memory transfer fault (STLink error (17): AP fault)",
    ):
        out = _swd_err_hint(err)
        assert "VREF" in out and "SWDIO" in out, f"hint missing for: {err}"
        assert err in out  # 原消息保留


def test_swd_err_hint_passthrough_for_other_errors():
    """非 SWD 类错误原样返回，不追加提示。"""
    from core.probe.pyocd_backend import _swd_err_hint
    for err in (
        "probe open failed: device not found",
        "FileNotFoundError: /x.bin",
        "",
    ):
        assert _swd_err_hint(err) == err
