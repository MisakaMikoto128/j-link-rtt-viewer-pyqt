"""Generate tests/fixtures/blink_sym.axf — ELF32 LE ARM with a real .symtab.

Layout:
- 1 PT_LOAD segment at p_paddr=0x08000000, p_filesz=64 (so parse_file works too)
- sections: NULL / .text / .symtab / .strtab / .shstrtab
- symbols: blink.c (FILE, local), local_helper (FUNC, local),
           main (FUNC, global), g_counter (OBJECT, global)

Used by tests/test_flash_file_parser.py symbol-table tests. Re-run if lost.
"""
import struct

payload = bytes(range(64))

# ---- string tables ----
# .strtab: \0 blink.c \0 local_helper \0 main \0 g_counter \0
strtab = b"\x00blink.c\x00local_helper\x00main\x00g_counter\x00"
off_blink = strtab.index(b"blink.c")
off_helper = strtab.index(b"local_helper")
off_main = strtab.index(b"main")
off_counter = strtab.index(b"g_counter")

# .shstrtab
shstrtab = b"\x00.text\x00.symtab\x00.strtab\x00.shstrtab\x00"
sh_text = shstrtab.index(b".text")
sh_symtab = shstrtab.index(b".symtab")
sh_strtab = shstrtab.index(b".strtab")
sh_shstr = shstrtab.index(b".shstrtab")

# ---- symbols (Elf32_Sym: name, value, size, info, other, shndx) ----
STT_FUNC, STT_OBJECT, STT_FILE = 2, 1, 4
STB_LOCAL, STB_GLOBAL = 0, 1
SHN_ABS = 0xFFF1
TEXT_SHNDX = 1


def sym(name, value, size, bind, typ, shndx):
    info = (bind << 4) | typ
    return struct.pack("<IIIBBH", name, value, size, info, 0, shndx)


symbols = b""
symbols += sym(0, 0, 0, 0, 0, 0)                                       # null
symbols += sym(off_blink, 0, 0, STB_LOCAL, STT_FILE, SHN_ABS)          # blink.c
symbols += sym(off_helper, 0x08000020, 16, STB_LOCAL, STT_FUNC, TEXT_SHNDX)
symbols += sym(off_main, 0x08000000, 32, STB_GLOBAL, STT_FUNC, TEXT_SHNDX)
symbols += sym(off_counter, 0x20000000, 4, STB_GLOBAL, STT_OBJECT, TEXT_SHNDX)
FIRST_GLOBAL = 3  # indices 0,1,2 are local

# ---- file offsets ----
ELF_HEADER_SIZE = 52
PHDR_SIZE = 32
SHDR_SIZE = 40
N_SECTIONS = 5

off_phdr = ELF_HEADER_SIZE
off_text = off_phdr + PHDR_SIZE
off_symtab = off_text + len(payload)
off_strtab = off_symtab + len(symbols)
off_shstrtab = off_strtab + len(strtab)
off_shdr = off_shstrtab + len(shstrtab)

# ---- ELF header ----
elf_header = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
elf_header += struct.pack(
    "<HHIIIIIHHHHHH",
    2,            # e_type ET_EXEC
    0x28,         # e_machine EM_ARM
    1,            # e_version
    0x08000000,   # e_entry
    off_phdr,     # e_phoff
    off_shdr,     # e_shoff
    0x05000000,   # e_flags
    ELF_HEADER_SIZE,
    PHDR_SIZE,
    1,            # e_phnum
    SHDR_SIZE,
    N_SECTIONS,   # e_shnum
    4,            # e_shstrndx -> .shstrtab
)

phdr = struct.pack(
    "<IIIIIIII",
    1, off_text, 0x08000000, 0x08000000,
    len(payload), len(payload), 5, 4,
)


def shdr(name, typ, flags, addr, offset, size, link, info, align, entsize):
    return struct.pack("<IIIIIIIIII", name, typ, flags, addr, offset,
                       size, link, info, align, entsize)


shdrs = b""
shdrs += shdr(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)                            # NULL
shdrs += shdr(sh_text, 1, 6, 0x08000000, off_text, len(payload),
              0, 0, 4, 0)                                              # .text
shdrs += shdr(sh_symtab, 2, 0, 0, off_symtab, len(symbols),
              3, FIRST_GLOBAL, 4, 16)                                  # .symtab
shdrs += shdr(sh_strtab, 3, 0, 0, off_strtab, len(strtab), 0, 0, 1, 0)  # .strtab
shdrs += shdr(sh_shstr, 3, 0, 0, off_shstrtab, len(shstrtab), 0, 0, 1, 0)

blob = elf_header + phdr + payload + symbols + strtab + shstrtab + shdrs
open("tests/fixtures/blink_sym.axf", "wb").write(blob)
print("blink_sym.axf written:", len(blob), "bytes")
