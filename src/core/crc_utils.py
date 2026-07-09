"""CRC 工具集：常用 CRC 算法实现，供 RTT 发送脚本追加 CRC 后缀。

支持的算法：
- CRC-8（标准，poly=0x07）
- CRC-16/MODBUS（poly=0x8005，refIn/refOut=True，init=0xFFFF）
- CRC-16/CCITT（poly=0x1021，init=0xFFFF）
- CRC-16/XMODEM（poly=0x1021，init=0x0000）
- CRC-32（poly=0x04C11DB7，refIn/refOut=True，init=0xFFFFFFFF，xorOut=0xFFFFFFFF）

用法：
    from core.crc_utils import compute_crc, CRC_ALGORITHMS
    crc_bytes = compute_crc("CRC-16/MODBUS", b"\\x01\\x02\\x03")
"""
from __future__ import annotations


# 公开算法列表：(显示名, 内部 key)
CRC_ALGORITHMS: list[tuple[str, str]] = [
    ("CRC-8", "crc8"),
    ("CRC-16/MODBUS", "crc16_modbus"),
    ("CRC-16/CCITT", "crc16_ccitt"),
    ("CRC-16/XMODEM", "crc16_xmodem"),
    ("CRC-32", "crc32"),
]


def _crc8(data: bytes, poly: int = 0x07, init: int = 0x00) -> int:
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc << 1) ^ poly if crc & 0x80 else crc << 1
            crc &= 0xFF
    return crc


def _crc16(
    data: bytes,
    poly: int,
    init: int = 0x0000,
    ref_in: bool = False,
    ref_out: bool = False,
    xor_out: int = 0x0000,
) -> int:
    crc = init
    for byte in data:
        if ref_in:
            byte = _reflect8(byte)
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ poly if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    if ref_out:
        crc = _reflect16(crc)
    return crc ^ xor_out


def _crc32(
    data: bytes,
    poly: int = 0xEDB88320,  # 反转多项式 (0x04C11DB7 位翻转)
    init: int = 0xFFFFFFFF,
    xor_out: int = 0xFFFFFFFF,
) -> int:
    """标准 CRC-32：右移 + LSB 检查，算法本身已隐含 reflect。"""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
    return crc ^ xor_out


def _reflect8(val: int) -> int:
    result = 0
    for i in range(8):
        if val & (1 << i):
            result |= 1 << (7 - i)
    return result


def _reflect16(val: int) -> int:
    result = 0
    for i in range(16):
        if val & (1 << i):
            result |= 1 << (15 - i)
    return result


def _reflect32(val: int) -> int:
    result = 0
    for i in range(32):
        if val & (1 << i):
            result |= 1 << (31 - i)
    return result


def compute_crc(algorithm: str, data: bytes) -> bytes:
    """计算 CRC 并返回字节（大端序）。

    Parameters
    ----------
    algorithm : str
        算法 key，如 "crc16_modbus"（对应 CRC_ALGORITHMS 的第二列）。
    data : bytes
        待计算的原始数据。

    Returns
    -------
    bytes
        CRC 结果，大端序字节串：CRC-8 返回 1 字节，CRC-16 返回 2 字节，
        CRC-32 返回 4 字节。

    Raises
    ------
    ValueError
        未知算法 key。
    """
    if algorithm == "crc8":
        return _crc8(data).to_bytes(1, "big")
    elif algorithm == "crc16_modbus":
        val = _crc16(data, poly=0x8005, init=0xFFFF,
                     ref_in=True, ref_out=True)
        # MODBUS CRC 是小端序（低字节在前）
        return val.to_bytes(2, "little")
    elif algorithm == "crc16_ccitt":
        val = _crc16(data, poly=0x1021, init=0xFFFF)
        return val.to_bytes(2, "big")
    elif algorithm == "crc16_xmodem":
        val = _crc16(data, poly=0x1021, init=0x0000)
        return val.to_bytes(2, "big")
    elif algorithm == "crc32":
        val = _crc32(data)
        return val.to_bytes(4, "big")
    else:
        raise ValueError(f"未知 CRC 算法：{algorithm}")
