"""flash_file_parser 单元测试。

固件 fixture 在 tests/fixtures/blink.{bin,hex,axf}：
- bin: 256 字节 0x00..0xFF
- hex: 64 字节 (4×16) 起始地址 0x08000000
- axf: ELF32 ARM, 1 LOAD seg @ 0x08000000 size 64
"""
from pathlib import Path

import pytest

from core.flash_file_parser import (
    FORMAT_BIN,
    FORMAT_ELF,
    FORMAT_HEX,
    FileInfo,
    FileParseError,
    detect_format,
    parse_file,
)

FIX = Path(__file__).parent / "fixtures"


def test_detect_format_by_extension():
    assert detect_format("a.axf") == FORMAT_ELF
    assert detect_format("a.ELF") == FORMAT_ELF
    assert detect_format("a.hex") == FORMAT_HEX
    assert detect_format("a.bin") == FORMAT_BIN

def test_detect_format_unknown():
    with pytest.raises(FileParseError):
        detect_format("a.txt")

def test_parse_bin_uses_provided_addr():
    info = parse_file(str(FIX / "blink.bin"), bin_start_addr=0x20000000)
    assert info.fmt == FORMAT_BIN
    assert info.addr_start == 0x20000000
    assert info.addr_end == 0x20000000 + 256
    assert info.total_bytes == 256

def test_parse_hex_extracts_range():
    info = parse_file(str(FIX / "blink.hex"))
    assert info.fmt == FORMAT_HEX
    assert info.addr_start == 0x08000000
    assert info.addr_end == 0x08000000 + 64
    assert info.total_bytes == 64

def test_parse_elf_extracts_load_segments():
    info = parse_file(str(FIX / "blink.axf"))
    assert info.fmt == FORMAT_ELF
    assert info.addr_start == 0x08000000
    assert info.addr_end == 0x08000000 + 64
    assert info.total_bytes == 64

def test_parse_nonexistent_raises():
    with pytest.raises(FileParseError):
        parse_file(str(FIX / "does_not_exist.bin"))

def test_parse_empty_bin_raises(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    with pytest.raises(FileParseError):
        parse_file(str(p))

def test_parse_corrupt_hex_raises(tmp_path):
    p = tmp_path / "bad.hex"
    p.write_text(":FFFFFFFFFFGG\n")  # 非法字符
    with pytest.raises(FileParseError):
        parse_file(str(p))

def test_parse_corrupt_elf_raises(tmp_path):
    p = tmp_path / "bad.axf"
    p.write_bytes(b"\x7fELFXXXXXX")  # ELF magic 后面截断
    with pytest.raises(FileParseError):
        parse_file(str(p))

def test_file_info_is_frozen():
    info = FileInfo(fmt=FORMAT_BIN, addr_start=0, addr_end=1, total_bytes=1, notes="")
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        info.addr_start = 999  # type: ignore
