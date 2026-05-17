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
        raise NotImplementedError  # Task 6

    def _do_connect(self, device: str, iface: str, speed: int) -> None:
        raise NotImplementedError  # Task 5

    def _safe_disconnect(self) -> None:
        if self._jlink is None:
            return
        try:
            self._jlink.close()
        except pylink.JLinkException as e:
            self.flash_log.emit("warn", f"close warn: {e}")
