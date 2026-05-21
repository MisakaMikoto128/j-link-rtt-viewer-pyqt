"""FlashWorker：固件烧录后台业务对象。

**和 JLinkWorker 完全独立**：自己的 pylink.JLink 实例 + 自己的 QThread。
用户负责确保烧录前 RTT 页已断开（不自动协调）。

设计要点（参考 JLinkWorker 同款套路）：
- 不继承 QThread；调用方外部创建 QThread + moveToThread。
- 所有 pylink.JLink 操作都在 worker 线程。
- 参数传递避开 PySide6 跨线程 Signal 传 dict 的坑：UI 调
  set_pending_params() 用 lock，然后 emit 无参 flash_requested。
- 退出清理：_on_stop 槽内 _safe_disconnect → thread.quit()。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pylink
from PySide6.QtCore import QObject, Signal, Slot

from .logger import get_logger

# ============================================================
# 公开常量（避免散落字面值，参考 CLAUDE.md "模式/枚举字符串必须有常量"）
# ============================================================
ERASE_MODE_SECTOR = "sector"
ERASE_MODE_CHIP = "chip"

POST_ACTION_NONE = "none"
POST_ACTION_RESET = "reset"
POST_ACTION_RESET_RUN = "reset_run"

FORMAT_ELF = "elf"
FORMAT_HEX = "hex"
FORMAT_BIN = "bin"

STAGE_CONNECT = "connect"
STAGE_ERASE = "erase"
STAGE_PROGRAM = "program"
STAGE_VERIFY = "verify"
STAGE_RESET = "reset"
STAGE_DISCONNECT = "disconnect"


@dataclass(frozen=True)
class FlashParams:
    file_path: str
    file_format: str          # FORMAT_*
    bin_start_addr: int       # 仅 bin 用，其它格式忽略
    device_name: str
    interface: str            # "SWD" | "JTAG"
    speed_khz: int
    erase_mode: str           # ERASE_MODE_*
    post_action: str          # POST_ACTION_*
    extra_verify: bool


class FlashWorker(QObject):
    """烧录后台业务对象。**必须 moveToThread 到一个 QThread 后再用**。"""

    # ---- 输入信号 ----
    flash_requested = Signal()           # 配合 set_pending_params() lock
    stop_requested = Signal()            # 关窗清理用

    # ---- 输出信号 ----
    flash_started = Signal()
    flash_stage_changed = Signal(str)        # STAGE_*
    flash_progress = Signal(int, int)        # (current_bytes, total_bytes)
    flash_log = Signal(str, str)             # (level, msg) — "info"/"warn"/"error"
    flash_finished = Signal(bool, str)       # (success, summary_text)

    def __init__(self) -> None:
        super().__init__()
        self._logger = get_logger()
        # 这些在 initialize() 内（worker 线程）创建：
        self._jlink: pylink.JLink | None = None
        # 参数 setter + lock，避开跨线程 Signal 传 dataclass
        self._pending_params: FlashParams | None = None
        self._params_lock = threading.Lock()
        # 进度回调用：在 _run_flash 启动时记录 total
        self._current_total: int = 0
        self._t_start: float = 0.0

    def set_pending_params(self, params: FlashParams) -> None:
        """UI 线程调；GIL+lock 保护，不走 Qt 信号 marshalling。"""
        with self._params_lock:
            self._pending_params = params

    @Slot()
    def initialize(self) -> None:
        """thread.started → 这里。worker 线程内创建 pylink.JLink。"""
        self._jlink = pylink.JLink()
        # 把输入信号连到本地槽
        self.flash_requested.connect(self._on_flash_requested)
        self.stop_requested.connect(self._on_stop)
        self._logger.info("FlashWorker initialized in worker thread")

    @Slot()
    def _on_stop(self) -> None:
        self._safe_disconnect()
        t = self.thread()
        if t is not None:
            t.quit()

    @Slot()
    def _on_flash_requested(self) -> None:
        with self._params_lock:
            params = self._pending_params
            self._pending_params = None
        if params is None:
            self.flash_log.emit("warn", "flash_requested 收到但 pending_params 为空")
            return
        self._run_flash(params)

    # 下面在 Task 5/6/7 里实现：
    def _run_flash(self, p: FlashParams) -> None:
        self.flash_started.emit()
        self._t_start = time.time()
        self.flash_log.emit("info", "=== Flash session ===")
        self.flash_log.emit("info",
            f"File: {p.file_path} ({p.file_format})")
        self.flash_log.emit("info",
            f"Device: {p.device_name} | {p.interface} @ {p.speed_khz} kHz")
        self.flash_log.emit("info",
            f"Options: erase={p.erase_mode} post={p.post_action} verify={p.extra_verify}")
        try:
            # --- connect ---
            self.flash_stage_changed.emit(STAGE_CONNECT)
            self._do_connect(p.device_name, p.interface, p.speed_khz)

            # --- chip erase（sector 由 flash_file 内含，不显式 emit STAGE_ERASE）---
            if p.erase_mode == ERASE_MODE_CHIP:
                self.flash_stage_changed.emit(STAGE_ERASE)
                self._jlink.erase()
                self.flash_log.emit("info", "chip erase OK")

            # --- program ---
            addr = p.bin_start_addr if p.file_format == FORMAT_BIN else 0
            self.flash_stage_changed.emit(STAGE_PROGRAM)
            self._current_total = 0
            self._jlink.flash_file(p.file_path, addr,
                                   on_progress=self._on_pylink_progress)
            self.flash_log.emit("info", "flash_file OK")

            # --- extra verify ---
            if p.extra_verify:
                self.flash_stage_changed.emit(STAGE_VERIFY)
                self._verify_bytewise(p)
                self.flash_log.emit("info", "extra verify OK")

            # --- post action ---
            if p.post_action in (POST_ACTION_RESET, POST_ACTION_RESET_RUN):
                self.flash_stage_changed.emit(STAGE_RESET)
                self._jlink.reset(halt=(p.post_action == POST_ACTION_RESET))
                if p.post_action == POST_ACTION_RESET_RUN:
                    self._jlink.restart()
                    self.flash_log.emit("info", "CPU running")

            # --- disconnect ---
            self.flash_stage_changed.emit(STAGE_DISCONNECT)
            self._safe_disconnect()

            elapsed = time.time() - self._t_start
            self.flash_log.emit("info", f"=== Done ({elapsed:.1f}s) ===")
            self.flash_finished.emit(True, "烧录成功")

        except Exception as e:
            self.flash_log.emit("error", f"{type(e).__name__}: {e}")
            self._safe_disconnect()
            self.flash_finished.emit(False, str(e))

    def _do_connect(self, device: str, iface: str, speed: int) -> None:
        """严格按 CLAUDE.md 'pylink 1.6.0 连接顺序'：open → close → open(serial)
        → set_tif → set_speed → connect。"""
        j = self._jlink
        if j is None:
            raise RuntimeError("FlashWorker 未 initialize")
        if not j.opened():
            j.open()
            ser = j.serial_number
            j.close()
            j.open(str(ser))
            self.flash_log.emit("info", f"J-Link SN: {ser}")
        tif = (pylink.enums.JLinkInterfaces.SWD if iface == "SWD"
               else pylink.enums.JLinkInterfaces.JTAG)
        j.set_tif(tif)
        j.set_speed(int(speed))
        j.connect(device)
        self.flash_log.emit("info", f"Target connected: {device}")

    def _safe_disconnect(self) -> None:
        if self._jlink is None:
            return
        try:
            self._jlink.close()
        except pylink.JLinkException as e:
            self.flash_log.emit("warn", f"close warn: {e}")

    def _on_pylink_progress(self, action, progress_string, percentage) -> None:
        """pylink flash_file 的 on_progress 回调。

        pylink 1.6.0 签名（推测）：(action, progress_string, percentage)
        如果实际签名不符，需要对照 pylink 文档调整。

        percentage: int 0-100
        """
        try:
            pct = int(percentage) if percentage is not None else 0
        except (TypeError, ValueError):
            pct = 0
        # 没有精确 byte 数，把百分比 * 100 当 total = 100 报上去
        self.flash_progress.emit(pct, 100)

    def _verify_bytewise(self, p: FlashParams) -> None:
        """按文件实际内容逐字节比对（在 flash_file 内含 CRC verify 之上的二次保险）。

        ELF/HEX/BIN 的解析复用 flash_file_parser.to_intelhex（不在 worker 里
        重复实现），再按 IntelHex 的连续段分块校验。pylink memory_read 一次
        最多读 4096 字节，_verify_range 内分块。
        """
        from core import flash_file_parser as fp
        ih = fp.to_intelhex(p.file_path, p.bin_start_addr)
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
                raise RuntimeError(
                    f"verify mismatch at 0x{addr + off:08X}: {n} bytes")
            off += n
