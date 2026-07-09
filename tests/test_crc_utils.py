"""CRC 算法正确性测试。

使用业界标准校验向量 "123456789"（ASCII 0x31-0x39）验证各算法结果。
参考：https://reveng.sourceforge.io/crc-catalogue/all.htm
"""
from __future__ import annotations

import pytest

from core.crc_utils import CRC_ALGORITHMS, compute_crc


# 标准测试向量：输入 b"123456789"，各算法的预期输出
_VECTORS: list[tuple[str, bytes]] = [
    # (algo_key, expected_crc_bytes)
    ("crc8", bytes([0xF4])),
    # MODBUS CRC 是小端序：0x4B37 → 低字节在前 → b"\x37\x4B"
    ("crc16_modbus", bytes([0x37, 0x4B])),
    ("crc16_ccitt", bytes([0x29, 0xB1])),
    ("crc16_xmodem", bytes([0x31, 0xC3])),
    ("crc32", bytes([0xCB, 0xF4, 0x39, 0x26])),
]

TEST_DATA = b"123456789"


@pytest.mark.parametrize("algo_key,expected", _VECTORS,
                         ids=[v[0] for v in _VECTORS])
def test_crc_standard_vectors(algo_key: str, expected: bytes):
    """各算法对 '123456789' 的结果必须与标准校验向量一致。"""
    result = compute_crc(algo_key, TEST_DATA)
    assert result == expected, (
        f"{algo_key}: 期望 {expected.hex().upper()}，"
        f"实际 {result.hex().upper()}"
    )


def test_crc8_empty_data():
    """CRC-8 空输入应返回 init 值 (0x00)。"""
    assert compute_crc("crc8", b"") == b"\x00"


def test_crc16_modbus_empty_data():
    """CRC-16/MODBUS 空输入：init=0xFFFF，refOut=True，xorOut=0x0000。"""
    result = compute_crc("crc16_modbus", b"")
    # init=0xFFFF, no data processed, refOut → reflect(0xFFFF) = 0xFFFF, xorOut=0 → 0xFFFF
    # 小端序 → b"\xFF\xFF"
    assert result == b"\xFF\xFF"


def test_crc32_empty_data():
    """CRC-32 空输入：init=0xFFFFFFFF，reflect → 0xFFFFFFFF，xorOut=0xFFFFFFFF → 0x00000000。"""
    result = compute_crc("crc32", b"")
    assert result == b"\x00\x00\x00\x00"


def test_compute_crc_unknown_algo_raises():
    """未知算法 key 应抛 ValueError。"""
    with pytest.raises(ValueError, match="未知 CRC"):
        compute_crc("crc999", b"data")


def test_crc_algorithms_list_has_expected_entries():
    """公开算法列表应包含 5 种算法，且每项为 (显示名, key) 二元组。"""
    assert len(CRC_ALGORITHMS) == 5
    for display_name, key in CRC_ALGORITHMS:
        assert isinstance(display_name, str) and display_name
        assert isinstance(key, str) and key


def test_crc_single_byte():
    """单字节 0x00 的 CRC-8 应可计算且不崩溃。"""
    result = compute_crc("crc8", b"\x00")
    assert len(result) == 1


def test_crc16_result_is_2_bytes():
    """CRC-16 系列必须返回 2 字节。"""
    for key in ("crc16_modbus", "crc16_ccitt", "crc16_xmodem"):
        assert len(compute_crc(key, b"test")) == 2


def test_crc32_result_is_4_bytes():
    """CRC-32 必须返回 4 字节。"""
    assert len(compute_crc("crc32", b"test")) == 4
