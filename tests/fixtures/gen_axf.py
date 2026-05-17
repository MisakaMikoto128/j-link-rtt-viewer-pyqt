"""Generate tests/fixtures/blink.axf — minimal ELF32 LE ARM with 1 LOAD seg.

LOAD segment at p_paddr=0x08000000, p_filesz=64 (bytes 0x00..0x3F).
Used by tests/test_flash_file_parser.py. Re-run if blink.axf is lost.
"""
import struct

ELF_HEADER_SIZE = 52
PHDR_SIZE = 32
payload = bytes(range(64))
data_off = ELF_HEADER_SIZE + PHDR_SIZE

elf_header = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8  # e_ident
elf_header += struct.pack("<HHIIIIIHHHHHH",
    2,        # e_type ET_EXEC
    0x28,     # e_machine EM_ARM
    1,        # e_version
    0x08000000,  # e_entry
    ELF_HEADER_SIZE,  # e_phoff
    0,        # e_shoff
    0x05000000,  # e_flags EF_ARM_EABI_VER5
    ELF_HEADER_SIZE,  # e_ehsize
    PHDR_SIZE,
    1,        # e_phnum
    0, 0, 0,  # e_shentsize, e_shnum, e_shstrndx
)
phdr = struct.pack("<IIIIIIII",
    1,            # p_type PT_LOAD
    data_off,     # p_offset
    0x08000000,   # p_vaddr
    0x08000000,   # p_paddr
    len(payload), # p_filesz
    len(payload), # p_memsz
    5,            # p_flags PF_R|PF_X
    4,            # p_align
)
open("tests/fixtures/blink.axf", "wb").write(elf_header + phdr + payload)
print("blink.axf written")
