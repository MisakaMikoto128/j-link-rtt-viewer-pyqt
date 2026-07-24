"""J-Link 烧录后端：pylink-square 1.6.0 的 flash_file 路径。

从旧 FlashWorker._do_connect / flash_file / _verify_bytewise / reset / close
平移而来，行为完全不变（CLAUDE.md 'pylink 1.6.0 连接顺序' 仍在此执行）。
"""
from __future__ import annotations

import pylink

from .base import (
    ERASE_MODE_CHIP,
    FORMAT_BIN,
    LogCallback,
    ProgressCallback,
    ProbeError,
    ProbeNotConnected,
    ProbeParams,
    VerifyMismatch,
)


class PylinkBackend:
    """J-Link via pylink-square 1.6.0。

    实现 probe.base.ProbeBackend 协议。连接序列严格按 CLAUDE.md
    'pylink 1.6.0 连接顺序'（flash 版无 rtt_start）：
    open -> close -> open(serial) -> set_tif -> set_speed -> connect。
    """

    def __init__(self, log: LogCallback) -> None:
        self._log = log
        self._jlink = pylink.JLink()
        self._params: ProbeParams | None = None

    # ============================================================
    # 连接
    # ============================================================
    def connect(self, params: ProbeParams) -> None:
        self._params = params
        j = self._jlink
        serial = params.serial
        remote = params.remote_addr

        if remote:
            # 远程模式：跳过 USB 枚举与 serial 校验，按 ip:port 双开
            if not j.opened():
                j.open(ip_addr=remote)
                ser = j.serial_number
                j.close()
                j.open(ip_addr=remote)
                self._log("info", f"J-Link SN: {ser} (远程 {remote})")
        else:
            # 本地 USB 模式：前置校验 + serial 双开
            try:
                emus = j.connected_emulators()
            except Exception as e:
                self._log("warn", f"未检测到 J-Link 设备，请检查 USB 连接 ({e})")
                raise ProbeNotConnected("no jlink")
            if not emus:
                self._log("warn", "未检测到 J-Link 设备，请检查 USB 连接")
                raise ProbeNotConnected("no jlink")
            if serial and serial != "0" and not any(
                    str(int(getattr(e, "SerialNumber", 0) or 0)) == serial
                    for e in emus):
                self._log(
                    "warn",
                    f"选中的 J-Link（S/N: {serial}）不在线，请刷新设备列表或重新选择",
                )
                raise ProbeNotConnected("jlink offline")

            if not j.opened():
                if serial and serial != "0":
                    j.open(serial_no=int(serial))
                    ser = j.serial_number
                    j.close()
                    j.open(serial_no=int(ser))
                else:
                    j.open()
                    ser = j.serial_number
                    j.close()
                    j.open(str(ser))
                self._log("info", f"J-Link SN: {ser}")

        # SWD / JTAG 二选一（CLAUDE.md 'set_tif 是错的'：不可 OR 起来）
        tif = (pylink.enums.JLinkInterfaces.SWD if params.interface == "SWD"
               else pylink.enums.JLinkInterfaces.JTAG)
        j.set_tif(tif)
        j.set_speed(int(params.speed_khz))
        j.connect(params.device_name)
        self._log("info", f"Target connected: {params.device_name}")

    # ============================================================
    # 擦除
    # ============================================================
    def erase(self, mode: str) -> None:
        if mode == ERASE_MODE_CHIP:
            self._jlink.erase()
        # sector 模式由 flash_file 内含，不显式 erase

    # ============================================================
    # 编程
    # ============================================================
    def program(self, on_progress: ProgressCallback) -> None:
        if self._params is None:
            raise ProbeError("not connected")
        p = self._params

        # 用自家解析器把固件读成带地址的段，逐段 jlink.flash 在正确物理地址烧录。
        # 不用 flash_file：其 ELF loader 对部分 axf 的段/入口处理不可靠（实测
        # STM32xx axf 烧后无法运行，同固件 hex 正常）——hex 只含裸地址数据，
        # 所以 hex 没踩。逐段按 p_paddr 烧录与 hex 等价，行为可控。
        from core import flash_file_parser as fp
        ih = fp.to_intelhex(p.file_path, p.bin_start_addr)
        segments = list(ih.segments())  # [(start, end_exclusive), ...]
        if not segments:
            raise ProbeError("固件无可烧录数据段")

        # 进度：按已烧字节占总字节比例上报（total=100 时 current 即百分比）。
        total_bytes = sum(end - start for start, end in segments) or 1
        done = 0

        for start, end in segments:
            data = bytes(ih.tobinarray(start=start, end=end - 1))
            if not data:
                continue
            self._jlink.flash(data, start)
            done += len(data)
            on_progress(min(100, done * 100 // total_bytes), 100)
            self._log("info", f"flashed {len(data)} B @ 0x{start:08X}")
        self._log("info", "program OK")

    # ============================================================
    # 校验（在 flash_file 内含 CRC verify 之上的逐字节二次保险）
    # ============================================================
    def verify(self) -> None:
        if self._params is None:
            raise ProbeError("not connected")
        from core import flash_file_parser as fp
        ih = fp.to_intelhex(self._params.file_path, self._params.bin_start_addr)
        for start, end in ih.segments():  # end 为开区间
            data = bytes(ih.tobinarray(start=start, end=end - 1))
            self._verify_range(start, data)

    def _verify_range(self, addr: int, expected: bytes) -> None:
        CHUNK = 4096
        off = 0
        while off < len(expected):
            n = min(CHUNK, len(expected) - off)
            got = bytes(self._jlink.memory_read(addr + off, n))
            if got != expected[off:off + n]:
                raise VerifyMismatch(addr + off, n)
            off += n

    # ============================================================
    # 复位
    # ============================================================
    def reset(self, halt: bool, run: bool) -> None:
        """复位目标。

        halt=True  run=False：复位后显式 halt（CPU 停在复位状态，不运行）
        halt=False run=True ：复位后运行（默认）
        halt=False run=False：仅复位（不推荐，状态不确定）
        """
        self._jlink.reset(halt=halt)
        if halt and not run:
            # 复位后显式 halt：reset(halt=True) 只保证不调 Go，不保证 CPU 停住——
            # 复位释放后 CPU 立即运行，halt() 需要在复位完成后调才有效。
            # JLINKARM_Reset() 是同步的（返回时复位已释放），但 CPU 可能已跑了几条
            # 指令——halt() 仍能停住（停在当前 PC，不是复位向量，但符合"暂停"语义）。
            self._jlink.halt()
            self._log("info", "CPU halted after reset")
        elif run:
            self._jlink.restart()
            self._log("info", "CPU running")

    # ============================================================
    # 断开（幂等；close 抛 JLinkException 不致命，参考 CLAUDE.md）
    # ============================================================
    def close(self) -> None:
        if self._jlink is None:
            return
        try:
            self._jlink.close()
        except pylink.JLinkException as e:
            self._log("warn", f"close warn: {e}")

    def connected_serial(self) -> str:
        try:
            return str(self._jlink.serial_number)
        except Exception:
            return ""
