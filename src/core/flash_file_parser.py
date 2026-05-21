"""固件文件解析：纯函数 + 零 Qt 依赖，可独立单元测试。

支持格式：
- .axf / .elf → ELF32 Program Headers (pyelftools)
- .hex        → Intel HEX (intelhex)
- .bin        → 起始地址由调用方提供

设计责任划分：
- 本层：格式合法性 + 文件内地址范围 + 总字节数
- UI 层：把 FileParseError 转 InfoBar
- J-Link DLL 层：地址是否真的落在芯片 Flash 范围（不在这边维护芯片表）
"""
from __future__ import annotations

import os
from dataclasses import dataclass

FORMAT_ELF = "elf"
FORMAT_HEX = "hex"
FORMAT_BIN = "bin"

_EXT_MAP = {
    ".axf": FORMAT_ELF,
    ".elf": FORMAT_ELF,
    ".hex": FORMAT_HEX,
    ".bin": FORMAT_BIN,
}


class FileParseError(Exception):
    """文件不存在 / 格式损坏 / 不支持的后缀都抛这个。"""


@dataclass(frozen=True)
class FileInfo:
    fmt: str               # FORMAT_ELF / FORMAT_HEX / FORMAT_BIN
    addr_start: int        # bin 模式由调用方提供；其它格式从文件读
    addr_end: int          # exclusive
    total_bytes: int       # 实际要烧的字节数
    notes: str             # 人类可读补充


@dataclass(frozen=True)
class Symbol:
    name: str
    address: int
    size: int
    type: str              # FUNC / OBJECT / SECTION / FILE / NOTYPE ...
    bind: str              # LOCAL / GLOBAL / WEAK
    section: str           # 所属 section 名（或 ABS / UNDEF / COMMON）


def detect_format(path: str) -> str:
    """按后缀判断；不读文件头。未知后缀抛 FileParseError。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in _EXT_MAP:
        raise FileParseError(f"不支持的文件后缀：{ext or '(无后缀)'}")
    return _EXT_MAP[ext]


def parse_file(path: str, bin_start_addr: int = 0) -> FileInfo:
    """统一入口；按格式分派。bin_start_addr 仅在 fmt=='bin' 时使用。"""
    if not os.path.exists(path):
        raise FileParseError(f"文件不存在：{path}")
    fmt = detect_format(path)
    if fmt == FORMAT_ELF:
        return _parse_elf(path)
    if fmt == FORMAT_HEX:
        return _parse_hex(path)
    return _parse_bin(path, bin_start_addr)


def _parse_elf(path: str) -> FileInfo:
    try:
        from elftools.elf.elffile import ELFFile
        from elftools.common.exceptions import ELFError
    except ImportError as e:
        raise FileParseError(f"pyelftools 未安装：{e}")
    try:
        with open(path, "rb") as f:
            elf = ELFFile(f)
            load_segs = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"
                         and s["p_filesz"] > 0]
            if not load_segs:
                raise FileParseError("ELF 中无 LOAD 段")
            addrs_start = [s["p_paddr"] for s in load_segs]
            addrs_end = [s["p_paddr"] + s["p_filesz"] for s in load_segs]
            total = sum(s["p_filesz"] for s in load_segs)
            return FileInfo(
                fmt=FORMAT_ELF,
                addr_start=min(addrs_start),
                addr_end=max(addrs_end),
                total_bytes=total,
                notes=f"{len(load_segs)} LOAD segment(s)",
            )
    except ELFError as e:
        raise FileParseError(f"ELF 解析失败：{e}")
    except FileParseError:
        raise
    except Exception as e:
        raise FileParseError(f"ELF 读取异常：{e}")


def _parse_hex(path: str) -> FileInfo:
    try:
        from intelhex import IntelHex, HexRecordError
    except ImportError as e:
        raise FileParseError(f"intelhex 未安装：{e}")
    try:
        ih = IntelHex()
        ih.loadhex(path)
        if len(ih) == 0:
            raise FileParseError("HEX 文件为空")
        return FileInfo(
            fmt=FORMAT_HEX,
            addr_start=ih.minaddr(),
            addr_end=ih.maxaddr() + 1,
            total_bytes=len(ih),
            notes=f"{ih.maxaddr() - ih.minaddr() + 1} address span",
        )
    except (HexRecordError, ValueError) as e:
        raise FileParseError(f"HEX 解析失败：{e}")
    except FileParseError:
        raise
    except Exception as e:
        raise FileParseError(f"HEX 读取异常：{e}")


def _parse_bin(path: str, start_addr: int) -> FileInfo:
    size = os.path.getsize(path)
    if size == 0:
        raise FileParseError("BIN 文件为空")
    return FileInfo(
        fmt=FORMAT_BIN,
        addr_start=start_addr,
        addr_end=start_addr + size,
        total_bytes=size,
        notes=f"raw {size} bytes",
    )


# ---- 格式转换（另存为）----

def _import_intelhex():
    try:
        from intelhex import IntelHex
        return IntelHex
    except ImportError as e:
        raise FileParseError(f"intelhex 未安装：{e}")


def to_intelhex(path: str, bin_start_addr: int = 0):
    """把任意支持格式（axf/elf/hex/bin）读成带地址的 IntelHex。

    供格式转换（另存为）与烧录后校验复用，避免各处重复 ELF/HEX 解析。
    bin_start_addr 仅在源是 .bin 时使用。
    """
    if not os.path.exists(path):
        raise FileParseError(f"文件不存在：{path}")
    return _to_intelhex(path, detect_format(path), bin_start_addr)


def _to_intelhex(src_path: str, src_fmt: str, bin_start_addr: int):
    """把任意支持格式读成 IntelHex（含地址）。"""
    IntelHex = _import_intelhex()
    if src_fmt == FORMAT_ELF:
        try:
            from elftools.elf.elffile import ELFFile
            from elftools.common.exceptions import ELFError
        except ImportError as e:
            raise FileParseError(f"pyelftools 未安装：{e}")
        ih = IntelHex()
        try:
            with open(src_path, "rb") as f:
                elf = ELFFile(f)
                load = [s for s in elf.iter_segments()
                        if s["p_type"] == "PT_LOAD" and s["p_filesz"] > 0]
                if not load:
                    raise FileParseError("ELF 中无 LOAD 段")
                for s in load:
                    ih.puts(s["p_paddr"], s.data())
        except ELFError as e:
            raise FileParseError(f"ELF 解析失败：{e}")
        return ih
    if src_fmt == FORMAT_HEX:
        ih = IntelHex()
        ih.loadhex(src_path)
        return ih
    # BIN
    ih = IntelHex()
    with open(src_path, "rb") as f:
        ih.frombytes(f.read(), offset=bin_start_addr)
    return ih


def convert_file(src_path: str, dst_path: str, bin_start_addr: int = 0) -> str:
    """把 src 固件转换并写到 dst。目标格式由 dst 后缀决定，仅支持 .bin / .hex。

    bin_start_addr 仅在源是 .bin 时用于定位地址。返回 dst_path。
    """
    if not os.path.exists(src_path):
        raise FileParseError(f"源文件不存在：{src_path}")
    src_fmt = detect_format(src_path)
    dst_ext = os.path.splitext(dst_path)[1].lower()
    if dst_ext not in (".bin", ".hex"):
        raise FileParseError(f"另存目标仅支持 .bin / .hex：{dst_ext or '(无后缀)'}")

    ih = _to_intelhex(src_path, src_fmt, bin_start_addr)
    if len(ih) == 0:
        raise FileParseError("源文件没有可转换的数据")
    try:
        if dst_ext == ".bin":
            ih.tobinfile(dst_path)
        else:
            ih.write_hex_file(dst_path)
    except Exception as e:
        raise FileParseError(f"写出失败：{e}")
    return dst_path


# ---- 符号表（仅 ELF/axf）----

def _shndx_name(elf, shndx) -> str:
    if isinstance(shndx, str):
        return shndx.replace("SHN_", "")
    try:
        sec = elf.get_section(shndx)
        return sec.name or str(shndx)
    except Exception:
        return str(shndx)


def read_symbols(path: str, func_and_data_only: bool = True) -> list[Symbol]:
    """读 ELF/axf 的 .symtab。func_and_data_only=True 时只保留 FUNC/OBJECT。

    非 ELF 抛 FileParseError；ELF 被 strip（无 .symtab）时返回空列表。
    """
    fmt = detect_format(path)
    if fmt != FORMAT_ELF:
        raise FileParseError("仅 ELF/axf 文件含符号表")
    try:
        from elftools.elf.elffile import ELFFile
        from elftools.common.exceptions import ELFError
    except ImportError as e:
        raise FileParseError(f"pyelftools 未安装：{e}")
    try:
        with open(path, "rb") as f:
            elf = ELFFile(f)
            symtab = elf.get_section_by_name(".symtab")
            if symtab is None:
                return []
            out: list[Symbol] = []
            for sym in symtab.iter_symbols():
                if not sym.name:
                    continue
                t = sym["st_info"]["type"]
                typ = t[4:] if t.startswith("STT_") else t
                if func_and_data_only and typ not in ("FUNC", "OBJECT"):
                    continue
                b = sym["st_info"]["bind"]
                bind = b[4:] if b.startswith("STB_") else b
                out.append(Symbol(
                    name=sym.name,
                    address=sym["st_value"],
                    size=sym["st_size"],
                    type=typ,
                    bind=bind,
                    section=_shndx_name(elf, sym["st_shndx"]),
                ))
            return out
    except ELFError as e:
        raise FileParseError(f"ELF 解析失败：{e}")
