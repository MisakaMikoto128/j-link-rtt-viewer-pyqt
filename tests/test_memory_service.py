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
