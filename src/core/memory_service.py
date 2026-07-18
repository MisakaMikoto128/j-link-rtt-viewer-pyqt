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


def write_memory(jlink, addr: int, data: bytes) -> int:
    """写 bytes 到 MCU 内存（按 32-bit 字写入）。返回成功写入的字节数。

    **高风险**：写错地址可能让目标 MCU 失去响应直到下次复位。
    调用方（UI）必须先做用户确认。

    实现：pylink.memory_write32(addr, [word_list])。不足 4 字节末尾用 0xFF 补齐。
    """
    if not data:
        return 0
    # 补齐到 4 字节边界（高位补 0xFF，常见 flash 默认值）
    padded = bytes(data) + b"\xff" * ((-len(data)) % 4)
    words = []
    for i in range(0, len(padded), 4):
        w = padded[i] | (padded[i+1] << 8) | (padded[i+2] << 16) | (padded[i+3] << 24)
        words.append(w)
    jlink.memory_write32(addr, words)
    return len(data)


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

        hex_col_parts: list[str] = []
        for j in range(0, bytes_per_row, 4):
            group = []
            for k in range(4):
                idx = j + k
                if idx < len(chunk):
                    group.append(f"{chunk[idx]:02X}")
                else:
                    group.append("  ")
            hex_col_parts.append(" ".join(group))
        hex_col = "  ".join(hex_col_parts)

        ascii_col = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        # 地址前缀按 4 位 hex 分组，与 hex 数据区节奏一致（每 4 字节一组，组间 2 空格）
        lines.append(f"0x{addr >> 16:04X} {addr & 0xFFFF:04X}:  {hex_col} |{ascii_col}|")
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
