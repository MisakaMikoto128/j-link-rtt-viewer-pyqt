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
