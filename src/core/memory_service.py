"""内存读取 / Hex 转储 / 固件导出。

调用方必须保证 jlink 已经 connected()，本模块只关心数据搬运。
read_memory / export_firmware 必须在持有 pylink 的那条线程（JLinkWorker）内调用。
format_hex_dump 是纯字符串函数，UI 端用，不需要 pylink。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

CHUNK_BYTES = 4096  # 每次 memory_read 的字节数（1024 个 32-bit 字）


def read_memory(jlink, addr: int, size: int) -> bytes:
    """读 size 字节，返回原始 bytes（小端字节序）。"""
    word_count = (size + 3) // 4
    words = jlink.memory_read(addr, word_count, nbits=32)
    out = bytearray()
    for w in words:
        out.append(w & 0xFF)
        out.append((w >> 8) & 0xFF)
        out.append((w >> 16) & 0xFF)
        out.append((w >> 24) & 0xFF)
    return bytes(out[:size])


def export_firmware(
    jlink,
    save_path: str,
    start_addr: int,
    size: int,
    progress_cb: Callable[[int, int], None],
) -> None:
    """按 4 KB 分块流式写入文件；progress_cb(current_chunk, total_chunks)。"""
    total_chunks = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for i in range(total_chunks):
            chunk_addr = start_addr + i * CHUNK_BYTES
            current_chunk_size = min(CHUNK_BYTES, size - i * CHUNK_BYTES)
            chunk = read_memory(jlink, chunk_addr, current_chunk_size)
            f.write(chunk)
            progress_cb(i + 1, total_chunks)


def format_hex_dump(data: bytes, base_addr: int = 0, bytes_per_row: int = 16) -> str:
    """格式化为 ``0xAAAA...:  HH HH HH HH ... |ascii|`` 多行字符串。

    bytes_per_row 支持 8 / 16 / 32（默认 16）。每 4 字节用空格分隔便于看 word。
    """
    if not data:
        return ""
    if bytes_per_row not in (8, 16, 32):
        bytes_per_row = 16
    lines: list[str] = []
    for offset in range(0, len(data), bytes_per_row):
        chunk = data[offset:offset + bytes_per_row]
        addr = base_addr + offset

        hex_parts: list[str] = []
        for j in range(bytes_per_row):
            if j < len(chunk):
                hex_parts.append(f"{chunk[j]:02X}")
            else:
                hex_parts.append("  ")
            if j % 4 == 3:
                hex_parts.append(" ")
        hex_col = " ".join(hex_parts)

        ascii_col = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        lines.append(f"0x{addr:08X}:  {hex_col} |{ascii_col}|")
    return "\n".join(lines)


def format_as_c_array(data: bytes, name: str = "data", bytes_per_row: int = 16) -> str:
    """转 C 数组字面量字符串：``uint8_t data[N] = { 0x.., 0x.., ... };``"""
    if not data:
        return f"uint8_t {name}[0] = {{}};"
    lines: list[str] = [f"uint8_t {name}[{len(data)}] = {{"]
    for offset in range(0, len(data), bytes_per_row):
        chunk = data[offset:offset + bytes_per_row]
        row = ", ".join(f"0x{b:02X}" for b in chunk)
        suffix = "," if offset + bytes_per_row < len(data) else ""
        lines.append(f"    {row}{suffix}")
    lines.append("};")
    return "\n".join(lines)


def parse_value(data: bytes, offset: int, dtype: str, little_endian: bool = True) -> str:
    """从 data[offset:] 解析一个数据类型，返回格式化字符串。

    dtype: u8/u16/u32/i8/i16/i32/float/double
    超出 data 长度返回 "—"。
    """
    import struct
    sizes = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "float": 4, "double": 8}
    if dtype not in sizes:
        return "—"
    size = sizes[dtype]
    if offset < 0 or offset + size > len(data):
        return "—"
    buf = bytes(data[offset:offset + size])
    endian = "<" if little_endian else ">"
    fmt_map = {"u8": "B", "i8": "b", "u16": "H", "i16": "h",
               "u32": "I", "i32": "i", "float": "f", "double": "d"}
    try:
        value = struct.unpack(endian + fmt_map[dtype], buf)[0]
    except struct.error:
        return "—"
    if dtype in ("float", "double"):
        return f"{value:.6g}"
    if dtype.startswith("u"):
        # 同时显示 hex 形式
        width = size * 2
        return f"{value} (0x{value:0{width}X})"
    return str(value)
