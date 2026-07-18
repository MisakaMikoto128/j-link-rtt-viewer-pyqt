"""memory_service：format_hex_dump 纯函数 + read_memory/export_firmware 用 mock jlink。"""
from unittest.mock import MagicMock

import pytest

from core import memory_service


def test_format_hex_dump_basic():
    data = bytes(range(16))
    out = memory_service.format_hex_dump(data, base_addr=0x08000000)
    assert "0x08000000" in out
    assert "00 01 02 03" in out
    assert "0F" in out


def test_format_hex_dump_multi_line():
    data = bytes(range(40))
    out = memory_service.format_hex_dump(data, base_addr=0x20000000)
    lines = out.splitlines()
    assert len(lines) == 3  # 16 + 16 + 8
    assert "0x20000000" in lines[0]
    assert "0x20000010" in lines[1]
    assert "0x20000020" in lines[2]


def test_format_hex_dump_ascii_column():
    data = b"Hello\x00World\x01\x02"
    out = memory_service.format_hex_dump(data, base_addr=0)
    assert "|Hello.World..|" in out  # 非可打印替换为 .


def test_format_hex_dump_empty():
    assert memory_service.format_hex_dump(b"", base_addr=0) == ""


def test_read_memory_returns_bytes():
    jlink = MagicMock()
    # memory_read(addr, word_count, nbits=32) 返回 list[int]（32-bit 字，小端写回）
    jlink.memory_read.return_value = [0x12345678, 0xCAFEBABE]
    result = memory_service.read_memory(jlink, addr=0x08000000, size=8)
    assert result == bytes.fromhex("78563412BEBAFECA")
    jlink.memory_read.assert_called_once_with(0x08000000, 2, nbits=32)


def test_read_memory_truncates_to_requested_size():
    jlink = MagicMock()
    jlink.memory_read.return_value = [0x11223344]
    result = memory_service.read_memory(jlink, addr=0, size=3)
    assert result == bytes.fromhex("443322")


def test_export_firmware_chunked_and_progress(tmp_path):
    jlink = MagicMock()
    # 16 KB → 4 chunks of 4 KB, each chunk 1024 words
    jlink.memory_read.side_effect = [
        [0xAAAAAAAA] * 1024,
        [0xBBBBBBBB] * 1024,
        [0xCCCCCCCC] * 1024,
        [0xDDDDDDDD] * 1024,
    ]
    progress = []
    out_file = tmp_path / "fw.bin"

    memory_service.export_firmware(
        jlink,
        save_path=str(out_file),
        start_addr=0x08000000,
        size=16 * 1024,
        progress_cb=lambda cur, total: progress.append((cur, total)),
    )

    assert out_file.stat().st_size == 16 * 1024
    blob = out_file.read_bytes()
    assert blob[:4] == bytes.fromhex("AAAAAAAA")
    assert blob[4096:4100] == bytes.fromhex("BBBBBBBB")
    assert progress[-1] == (4, 4)  # 最后一次回调是完成


def test_export_firmware_handles_partial_chunk(tmp_path):
    jlink = MagicMock()
    jlink.memory_read.side_effect = [
        [0xAAAAAAAA] * 1024,
        [0xBBBBBBBB] * 250,  # 1000 bytes
    ]
    out_file = tmp_path / "fw.bin"
    memory_service.export_firmware(
        jlink, save_path=str(out_file),
        start_addr=0, size=4096 + 1000,
        progress_cb=lambda c, t: None,
    )
    assert out_file.stat().st_size == 4096 + 1000


# ----------------------------------------------------------------------------
# format_hex_dump — bytes_per_row 行布局契约（被内存页 _cursor_byte_offset 依赖）
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("bpr", [8, 16, 32])
def test_format_hex_dump_row_layout_contract(bpr):
    """每行结构：``0xXXXXXXXX:  HH HH HH HH  HH HH ... |ascii|``

    地址前缀 ``0x{addr:08X}:`` = 11 字符，再加 ``  ``（2 空格）→ hex 区起始 col 13。
    每字节 ``HH `` = 3 字符（join 后 stride 3），每 4 字节后追加 1 个分组空格。
    **这是 _cursor_byte_offset / _select_buffer_range 反推位置的硬契约——
    一旦此测失败，必须同步修源代码里的 hex_start 常量。**
    """
    data = bytes(range(bpr))
    out = memory_service.format_hex_dump(data, base_addr=0x10000000, bytes_per_row=bpr)
    line = out.splitlines()[0]
    assert line.startswith("0x10000000:  "), f"地址前缀必须 0xXXXXXXXX:<两空格> (line: {line!r})"
    # hex 区起始位置硬约束 — col 13
    hex_start = 13
    assert line[hex_start:hex_start + 2] == "00"
    # 第一字节后是空格，第 4 字节后是双空格（1 个分组空格 + 1 个普通空格）
    assert line[hex_start + 2] == " "
    # byte_chars * 4 = 12 处应为分组空格（紧跟第 4 字节后的额外空格）
    assert line[hex_start + 11] == " "
    assert line[hex_start + 12] == " "
    # ASCII 列 | 应在结尾
    assert "|" in line and line.rstrip().endswith("|")
    # 地址前缀节奏与数据区一致：第 j 个字节的两位 hex 应正好在 _byte_start_col(j) 处
    for j in range(min(bpr, 16)):
        col = hex_start + j * 3 + (j // 4)
        expected = f"{j:02X}"
        assert line[col:col + 2] == expected, f"bpr={bpr} j={j} 期望 {expected!r} 在 col={col} (got {line[col:col+2]!r})"


def test_format_hex_dump_address_prefix_contiguous():
    """样例行：base_addr=0x20000080, data=bytes(range(16)) 的连续 8 位地址前缀。"""
    out = memory_service.format_hex_dump(bytes(range(16)), base_addr=0x20000080)
    line = out.splitlines()[0]
    assert line.startswith("0x20000080:  ")
    assert "00 01 02 03  04 05 06 07  08 09 0A 0B  0C 0D 0E 0F" in line


def test_format_hex_dump_bytes_per_row_8():
    data = bytes(range(16))
    out = memory_service.format_hex_dump(data, base_addr=0, bytes_per_row=8)
    lines = out.splitlines()
    assert len(lines) == 2  # 8 + 8


def test_format_hex_dump_bytes_per_row_32():
    data = bytes(range(64))
    out = memory_service.format_hex_dump(data, base_addr=0, bytes_per_row=32)
    lines = out.splitlines()
    assert len(lines) == 2


def test_format_hex_dump_invalid_bytes_per_row_falls_back_to_16():
    data = bytes(range(16))
    out = memory_service.format_hex_dump(data, base_addr=0, bytes_per_row=7)
    # 落回 16 → 16 字节一行
    assert len(out.splitlines()) == 1


# ----------------------------------------------------------------------------
# format_as_c_array
# ----------------------------------------------------------------------------

def test_format_as_c_array_basic():
    out = memory_service.format_as_c_array(bytes([0x12, 0x34, 0x56, 0x78]), name="sample", bytes_per_row=8)
    assert "uint8_t sample[4] = {" in out
    assert "0x12, 0x34, 0x56, 0x78" in out
    assert out.endswith("};")


def test_format_as_c_array_multi_row():
    data = bytes(range(20))
    out = memory_service.format_as_c_array(data, name="buf", bytes_per_row=8)
    lines = out.splitlines()
    # 头 + 3 行内容（8+8+4）+ 尾
    assert len(lines) == 5
    assert lines[0] == "uint8_t buf[20] = {"
    # 中间行除最后一行外尾必须有逗号
    assert lines[1].endswith(",")
    assert lines[2].endswith(",")
    assert not lines[3].endswith(",")  # 最后一组无逗号
    assert lines[-1] == "};"


def test_format_as_c_array_empty():
    out = memory_service.format_as_c_array(b"", name="x")
    assert out == "uint8_t x[0] = {};"


# ----------------------------------------------------------------------------
# parse_value — 8 dtypes × 2 endians + 边界条件
# ----------------------------------------------------------------------------

def test_parse_value_u32_le_be():
    data = bytes.fromhex("78563412")
    assert memory_service.parse_value(data, 0, "u32", little_endian=True) == "305419896 (0x12345678)"
    assert memory_service.parse_value(data, 0, "u32", little_endian=False) == "2018915346 (0x78563412)"


def test_parse_value_i32_negative():
    # 0xFFFFFFFF LE = -1 (signed 32-bit)
    data = bytes.fromhex("FFFFFFFF")
    assert memory_service.parse_value(data, 0, "i32", little_endian=True) == "-1"


def test_parse_value_u16_i16():
    data = bytes.fromhex("00FF")  # LE: 0xFF00 = 65280 unsigned, -256 signed
    assert memory_service.parse_value(data, 0, "u16", little_endian=True) == "65280 (0xFF00)"
    assert memory_service.parse_value(data, 0, "i16", little_endian=True) == "-256"


def test_parse_value_u8_i8():
    data = bytes([0x80])
    assert memory_service.parse_value(data, 0, "u8", little_endian=True) == "128 (0x80)"
    assert memory_service.parse_value(data, 0, "i8", little_endian=True) == "-128"


def test_parse_value_float():
    # 1.0f IEEE 754 = 0x3F800000
    data = bytes.fromhex("0000803F")  # LE
    result = memory_service.parse_value(data, 0, "float", little_endian=True)
    assert result.startswith("1")


def test_parse_value_double():
    # 1.0 IEEE 754 = 0x3FF0000000000000
    data = bytes.fromhex("000000000000F03F")  # LE
    result = memory_service.parse_value(data, 0, "double", little_endian=True)
    assert result.startswith("1")


def test_parse_value_out_of_bounds():
    """offset + sizeof(dtype) > len(data) → "—" (em dash)."""
    data = bytes([0x12, 0x34])
    assert memory_service.parse_value(data, 0, "u32", True) == "—"
    assert memory_service.parse_value(data, 1, "u16", True) == "—"


def test_parse_value_offset_at_boundary():
    """正好 fits 的边界 offset 可以解析。"""
    data = bytes.fromhex("00112233")
    assert memory_service.parse_value(data, 0, "u32", True) != "—"
    assert memory_service.parse_value(data, 2, "u16", True) != "—"


def test_parse_value_unknown_dtype():
    assert memory_service.parse_value(b"\x00\x00\x00\x00", 0, "u64", True) == "—"
