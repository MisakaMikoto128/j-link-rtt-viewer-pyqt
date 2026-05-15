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


def format_hex_dump(data: bytes, base_addr: int = 0) -> str:
    """格式化为 ``0xAAAA...:  HH HH HH HH ... |ascii|`` 多行字符串。"""
    if not data:
        return ""
    lines: list[str] = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        addr = base_addr + offset

        hex_parts: list[str] = []
        for j in range(16):
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
