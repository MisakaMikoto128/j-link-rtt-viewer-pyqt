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
    convert_file,
    detect_format,
    parse_file,
    read_elf_meta,
    read_memory_summary,
    read_sections,
    read_symbols,
    to_intelhex,
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


@pytest.mark.parametrize("reader", [read_sections, read_memory_summary, read_elf_meta])
def test_elf_readers_wrap_corrupt_file_as_fileparseerror(tmp_path, reader):
    """非 ELF 内容（但扩展名是 .axf）→ _open_elf 应在内部 catch ELFError 并 close 文件，
    抛 FileParseError；调用方不必再处理裸 ELFError。

    回归 _open_elf 漏 catch ELFError 的 bug：之前 ELFFile 构造抛 ELFError 直接逃出去，
    UI 层的 SymbolTableView.load 完全没机会消化。
    """
    p = tmp_path / "fake.axf"
    p.write_bytes(b"not an elf at all")
    with pytest.raises(FileParseError):
        reader(str(p))

def test_file_info_is_frozen():
    info = FileInfo(fmt=FORMAT_BIN, addr_start=0, addr_end=1, total_bytes=1, notes="")
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        info.addr_start = 999  # type: ignore


# ---- convert_file ----

def test_convert_elf_to_bin(tmp_path):
    out = tmp_path / "o.bin"
    convert_file(str(FIX / "blink.axf"), str(out))
    assert out.exists()
    assert out.stat().st_size == 64

def test_convert_elf_to_hex(tmp_path):
    out = tmp_path / "o.hex"
    convert_file(str(FIX / "blink.axf"), str(out))
    info = parse_file(str(out))
    assert info.fmt == FORMAT_HEX
    assert info.addr_start == 0x08000000
    assert info.total_bytes == 64

def test_convert_hex_to_bin(tmp_path):
    out = tmp_path / "h.bin"
    convert_file(str(FIX / "blink.hex"), str(out))
    assert out.stat().st_size == 64

def test_convert_bin_to_hex_uses_start_addr(tmp_path):
    out = tmp_path / "b.hex"
    convert_file(str(FIX / "blink.bin"), str(out), bin_start_addr=0x08000000)
    info = parse_file(str(out))
    assert info.addr_start == 0x08000000

def test_convert_rejects_unsupported_target(tmp_path):
    with pytest.raises(FileParseError):
        convert_file(str(FIX / "blink.bin"), str(tmp_path / "x.axf"))

def test_convert_nonexistent_source_raises(tmp_path):
    with pytest.raises(FileParseError):
        convert_file(str(FIX / "nope.bin"), str(tmp_path / "o.bin"))


# ---- to_intelhex（转换 + 校验共用）----

def test_to_intelhex_elf_segments():
    ih = to_intelhex(str(FIX / "blink.axf"))
    assert ih.segments() == [(0x08000000, 0x08000000 + 64)]
    assert len(ih) == 64

def test_to_intelhex_bin_uses_start_addr():
    ih = to_intelhex(str(FIX / "blink.bin"), bin_start_addr=0x08000000)
    assert ih.minaddr() == 0x08000000

def test_to_intelhex_nonexistent_raises():
    with pytest.raises(FileParseError):
        to_intelhex(str(FIX / "nope.hex"))


# ---- read_symbols ----

def test_read_symbols_func_and_data_only():
    syms = read_symbols(str(FIX / "blink_sym.axf"), func_and_data_only=True)
    names = {s.name for s in syms}
    assert names == {"local_helper", "main", "g_counter"}  # FILE 被过滤
    main = next(s for s in syms if s.name == "main")
    assert main.address == 0x08000000
    assert main.size == 32
    assert main.type == "FUNC"
    assert main.bind == "GLOBAL"
    assert main.section == ".text"

def test_read_symbols_all_includes_file():
    syms = read_symbols(str(FIX / "blink_sym.axf"), func_and_data_only=False)
    assert any(s.type == "FILE" and s.name == "blink.c" for s in syms)
    assert len(syms) == 4

def test_read_symbols_stripped_returns_empty():
    # blink.axf 无 .symtab（无 section header）
    assert read_symbols(str(FIX / "blink.axf")) == []

def test_read_symbols_rejects_non_elf():
    with pytest.raises(FileParseError):
        read_symbols(str(FIX / "blink.bin"))


# ---- read_sections / read_memory_summary / read_elf_meta ----

def test_read_sections_alloc_only():
    secs = read_sections(str(FIX / "blink_sym.axf"))
    # 只有 .text 是 ALLOC；.symtab/.strtab/.shstrtab 不是
    assert [s.name for s in secs] == [".text"]
    text = secs[0]
    assert text.addr == 0x08000000
    assert text.size == 64
    assert text.flags == "R-X"
    assert text.align == 4

def test_read_sections_stripped_returns_empty():
    assert read_sections(str(FIX / "blink.axf")) == []

def test_read_memory_summary():
    s = read_memory_summary(str(FIX / "blink_sym.axf"))
    assert s.text == 64 and s.data == 0 and s.bss == 0
    assert s.flash == 64 and s.ram == 0

def test_read_elf_meta_entry_and_vector():
    m = read_elf_meta(str(FIX / "blink_sym.axf"))
    assert m.entry == 0x08000000
    # payload = bytes(range(64))：前两字（小端）
    assert m.initial_sp == 0x03020100
    assert m.reset_handler == 0x07060504  # 偶数，去 thumb 位无变化

def test_section_apis_reject_non_elf():
    for fn in (read_sections, read_memory_summary, read_elf_meta):
        with pytest.raises(FileParseError):
            fn(str(FIX / "blink.bin"))
