"""FlashWorker：固件烧录后台业务对象。

和 JLinkWorker 完全独立：自己的 ProbeBackend 实例 + 自己的 QThread。
用户负责确保烧录前 RTT 页已断开（同设备检测见 flash_page._on_start_flash）。
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .logger import get_logger
from .probe.base import (
    BURNER_KIND_JLINK,
    ERASE_MODE_CHIP,
    ERASE_MODE_SECTOR,
    FORMAT_BIN,
    FORMAT_ELF,
    FORMAT_HEX,
    POST_ACTION_NONE,
    POST_ACTION_HALT,
    POST_ACTION_RESET_RUN,
    STAGE_CONNECT,
    STAGE_DISCONNECT,
    STAGE_ERASE,
    STAGE_PROGRAM,
    STAGE_RESET,
    STAGE_VERIFY,
    ProbeParams,
)
from .probe.factory import make_backend

# 常量 re-export：UI 层沿用 `from core.flash_worker import ERASE_MODE_CHIP` 等
# 旧 import 路径，真源在 probe.base。
__all__ = [
    "BURNER_KIND_JLINK",
    "ERASE_MODE_CHIP",
    "ERASE_MODE_SECTOR",
    "FORMAT_BIN",
    "FORMAT_ELF",
    "FORMAT_HEX",
    "POST_ACTION_NONE",
    "POST_ACTION_HALT",
    "POST_ACTION_RESET_RUN",
    "STAGE_CONNECT",
    "STAGE_DISCONNECT",
    "STAGE_ERASE",
    "STAGE_PROGRAM",
    "STAGE_RESET",
    "STAGE_VERIFY",
    "FlashParams",
    "FlashWorker",
]


@dataclass(frozen=True)
class FlashParams:
    file_path: str
    file_format: str  # FORMAT_*
    bin_start_addr: int  # 仅 bin 用，其它格式忽略
    device_name: str
    interface: str  # "SWD" | "JTAG"
    speed_khz: int
    erase_mode: str  # ERASE_MODE_*
    post_action: str  # POST_ACTION_*
    extra_verify: bool
    jlink_serial: str = ""  # 指定烧录器 serial；空/"0" 表示未指定
    remote_addr: str = ""  # 远程模式 "ip:port"；空 = 本地 USB
    burner_kind: str = BURNER_KIND_JLINK  # 烧录器类型，见 probe.base.BURNER_KIND_*
    erase_only: bool = False  # True = 只整片擦除不烧录（复用完整连接/断开流程）


class FlashWorker(QObject):
    """烧录后台业务对象。**必须 moveToThread 到一个 QThread 后再用**。"""

    # ---- 输入信号 ----
    flash_requested = Signal()  # 配合 set_pending_params() lock
    stop_requested = Signal()  # 关窗清理用
    enumerate_pyocd_targets_requested = Signal()  # UI 首次打开 Flash 页触发枚举

    # ---- 输出信号 ----
    flash_started = Signal()
    flash_stage_changed = Signal(str)  # STAGE_*
    flash_progress = Signal(int, int)  # (current_bytes, total_bytes)
    flash_log = Signal(str, str)  # (level, msg) - "info"/"warn"/"error"
    flash_finished = Signal(bool, str)  # (success, summary_text)
    pyocd_probes_enumerated = Signal(str)  # "kind|serial|product;..."（非 J-Link probe）
    # pyOCD target 库枚举完成（worker 线程后台跑，填充 functools.cache）。
    # UI 收到后读 read_cached_pyocd_* 命中 cache，避免主线程枚举卡 UI ~500ms。
    pyocd_target_infos_ready = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._logger = get_logger()
        # 这些在 initialize() 内（worker 线程）创建：
        self._backend = None  # type: ignore[assignment]
        self._pyocd_enum_timer: QTimer | None = None
        # 参数 setter + lock，避开跨线程 Signal 传 dataclass
        self._pending_params: FlashParams | None = None
        self._params_lock = threading.Lock()
        self._t_start: float = 0.0
        # worker 线程内串行访问：_run_flash 进行中置 True。UI 已用 _set_inputs_enabled
        # 挡住重复点烧录；此处是双保险——多余的 flash_requested 立即拒绝 emit
        # flash_finished(False)，不排队等前一次烧完（远程 connect 卡 3s DLL 时
        # 排队会拖长测试 teardown）。
        self._busy = False

    def set_pending_params(self, params: FlashParams) -> None:
        """UI 线程调；GIL+lock 保护，不走 Qt 信号 marshalling。"""
        with self._params_lock:
            self._pending_params = params

    @Slot()
    def initialize(self) -> None:
        """在 worker 线程启动时创建 timer 并连接信号。"""
        if not os.environ.get("JLINK_RTT_TEST_MODE"):
            self._pyocd_enum_timer = QTimer()
            self._pyocd_enum_timer.setInterval(200)
            self._pyocd_enum_timer.timeout.connect(self._on_enumerate_pyocd)
            self._pyocd_enum_timer.start()
        self.flash_requested.connect(self._on_flash_requested)
        self.stop_requested.connect(self._on_stop)
        # pyOCD target 枚举改按需（Flash 页首次打开触发），不在启动时枚举
        # （~1s CMSIS-Pack 索引拖慢启动）。见 _on_enumerate_pyocd_targets。
        self.enumerate_pyocd_targets_requested.connect(self._on_enumerate_pyocd_targets)
        self._logger.info("FlashWorker initialized in worker thread")

    @Slot()
    def _on_enumerate_pyocd_targets(self) -> None:
        """按需枚举 pyOCD target 库（Flash 页首次打开触发），填充 cache。

        不碰 J-Link DLL（不崩）；~500ms-数秒（CMSIS-Pack 索引），worker 线程
        不卡 UI。UI 读 read_cached_pyocd_* 命中 cache，收 pyocd_target_infos_ready
        后刷新下拉。测试模式跳过。
        """
        if os.environ.get("JLINK_RTT_TEST_MODE"):
            return
        try:
            from core.target_discovery import get_pyocd_target_infos
            get_pyocd_target_infos()
        except Exception as e:
            self._logger.warning(f"pyocd target 枚举失败：{e}")
        self.pyocd_target_infos_ready.emit()

    @Slot()
    def _on_enumerate_pyocd(self) -> None:
        """枚举 pyOCD 可见 probe（非 J-Link），emit 给 UI 合并下拉。"""
        from .probe.enumerator import enumerate_pyocd_probes

        try:
            probes = enumerate_pyocd_probes()
        except Exception as e:
            self._logger.warning(f"pyocd enumerate failed: {e}")
            return
        chunks = [f"{p.kind}|{p.serial}|{p.product}" for p in probes]
        self.pyocd_probes_enumerated.emit(";".join(chunks))

    def _log(self, level: str, msg: str) -> None:
        """backend 回调入口：透传到 flash_log 信号。"""
        self.flash_log.emit(level, msg)

    @Slot()
    def _on_stop(self) -> None:
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception as e:
                self._logger.warning(f"FlashWorker stop close warn: {e}")
        if self._pyocd_enum_timer is not None:
            self._pyocd_enum_timer.stop()
            self._pyocd_enum_timer.deleteLater()
            self._pyocd_enum_timer = None
        t = self.thread()
        if t is not None:
            t.quit()

    @Slot()
    def _on_flash_requested(self) -> None:
        if self._busy:
            self.flash_log.emit("warn", "烧录进行中，忽略多余的 flash_requested")
            self.flash_finished.emit(False, "busy")
            return
        with self._params_lock:
            params = self._pending_params
            self._pending_params = None
        if params is None:
            self.flash_log.emit("warn", "flash_requested 收到但 pending_params 为空")
            return
        self._run_flash(params)

    def _run_flash(self, p: FlashParams) -> None:
        self._busy = True
        try:
            self._run_flash_impl(p)
        finally:
            self._busy = False

    def _run_flash_impl(self, p: FlashParams) -> None:
        # 上次烧录若 post_action=halt，backend 未 close（保持 CPU halt），先释放
        if self._backend is not None:
            with contextlib.suppress(Exception):
                self._backend.close()
            self._backend = None
        self.flash_started.emit()
        self._t_start = time.time()
        erase_only = p.erase_only
        self.flash_log.emit("info", "=== Flash session ===" + ("（仅整片擦除）" if erase_only else ""))
        if not erase_only:
            self.flash_log.emit("info", f"File: {p.file_path} ({p.file_format})")
        self.flash_log.emit("info", f"Device: {p.device_name} | {p.interface} @ {p.speed_khz} kHz")
        backend = None
        try:
            backend = make_backend(p.burner_kind, self._log)
            self._backend = backend  # 供 _on_stop 兜底关闭（worker 线程串行，无竞态）
            probe_params = ProbeParams(
                device_name=p.device_name,
                interface=p.interface,
                speed_khz=p.speed_khz,
                file_path=p.file_path,
                file_format=p.file_format,
                bin_start_addr=p.bin_start_addr,
                erase_mode=p.erase_mode,
                post_action=p.post_action,
                extra_verify=p.extra_verify,
                serial=p.jlink_serial,
                remote_addr=p.remote_addr,
                erase_only=erase_only,
            )

            self.flash_stage_changed.emit(STAGE_CONNECT)
            backend.connect(probe_params)

            if erase_only:
                # 只整片擦除不烧录：复用完整 连接→断开 流程，跳过 program/verify/reset。
                self.flash_stage_changed.emit(STAGE_ERASE)
                backend.erase(ERASE_MODE_CHIP)
                self.flash_log.emit("info", "chip erase OK")
            else:
                if p.erase_mode == ERASE_MODE_CHIP:
                    self.flash_stage_changed.emit(STAGE_ERASE)
                    backend.erase(p.erase_mode)
                    self.flash_log.emit("info", "chip erase OK")

                self.flash_stage_changed.emit(STAGE_PROGRAM)
                backend.program(on_progress=self._on_progress)
                self.flash_log.emit("info", "program OK")

                if p.extra_verify:
                    self.flash_stage_changed.emit(STAGE_VERIFY)
                    backend.verify()
                    self.flash_log.emit("info", "extra verify OK")

                if p.post_action in (POST_ACTION_HALT, POST_ACTION_RESET_RUN):
                    self.flash_stage_changed.emit(STAGE_RESET)
                    backend.reset(
                        halt=(p.post_action == POST_ACTION_HALT),
                        run=(p.post_action == POST_ACTION_RESET_RUN),
                    )

            self.flash_stage_changed.emit(STAGE_DISCONNECT)
            # post_action=halt：不 close。JLINKARM_Close 会 resume CPU（放跑），
            # 无法保持 halt。backend 句柄保留，下次烧录或关窗时 close。
            if p.post_action != POST_ACTION_HALT:
                backend.close()

            elapsed = time.time() - self._t_start
            self.flash_log.emit("info", f"=== Done ({elapsed:.1f}s) ===")
            self.flash_finished.emit(True, "整片擦除完成" if erase_only else "烧录成功")

        except Exception as e:
            self.flash_log.emit("error", f"{type(e).__name__}: {e}")
            if backend is not None:
                with contextlib.suppress(Exception):
                    backend.close()
            self.flash_finished.emit(False, str(e))
        finally:
            if p.post_action != POST_ACTION_HALT:
                # 清掉活跃 backend 引用（_on_stop 仅在关窗时跑）
                self._backend = None
            # post_action=halt：保留 _backend（未 close），下次 _run_flash 开头 close

    def _on_progress(self, current: int, total: int) -> None:
        """backend program 进度回调 -> flash_progress 信号。"""
        self.flash_progress.emit(current, total)
