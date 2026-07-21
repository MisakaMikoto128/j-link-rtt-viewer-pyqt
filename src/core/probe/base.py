"""烧录器后端抽象层接口。

为固件烧录提供统一的 ProbeBackend 协议，使 FlashWorker 不再直接耦合 pylink：
J-Link 走 PylinkBackend，ST-Link / CMSIS-DAP 走 PyOCDBackend（后续步骤加）。

设计原则（参考 CLAUDE.md）：
- 本层零 Qt 依赖；worker 注入 log 回调让 backend 往外发消息。
- 常量单点真源在此；flash_worker re-export 保持 UI 层 import 兼容。
- 跨线程不在此层：backend 实例在 worker 线程创建并使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


# ============================================================
# 烧录器类型
# ============================================================
BURNER_KIND_JLINK = "jlink"
BURNER_KIND_STLINK = "stlink"
BURNER_KIND_CMSIS_DAP = "cmsisdap"


# ============================================================
# 擦除 / 后置动作 / 文件格式 / 阶段
# 值与旧 flash_worker 常量一致，UI 层无感（flash_worker re-export）。
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


# ============================================================
# 错误
# ============================================================
class ProbeError(Exception):
    """Probe 层统一错误基类。FlashWorker 顶层 catch 后透传到 flash_log('error', ...)。"""


class ProbeNotConnected(ProbeError):
    """连接失败 / 设备不在线。"""


class VerifyMismatch(ProbeError):
    """烧录后逐字节校验失败。"""
    def __init__(self, addr: int, n: int) -> None:
        super().__init__(f"verify mismatch at 0x{addr:08X}: {n} bytes")
        self.addr = addr
        self.n = n


class UnsupportedFormat(ProbeError):
    """backend 不支持的固件格式。"""


# ============================================================
# 回调签名
# ============================================================
# 进度：(current_bytes, total_bytes)。total=100 时 current 即百分比。
ProgressCallback = Callable[[int, int], None]
# 日志：(level, msg)，level ∈ {"info","warn","error"}。
LogCallback = Callable[[str, str], None]


# ============================================================
# 数据结构
# ============================================================
@dataclass(frozen=True)
class ProbeInfo:
    """枚举的烧录器描述项（UI 下拉 + 同设备检测用）。"""
    kind: str              # BURNER_KIND_*
    serial: str            # 唯一标识（J-Link=int serial；ST-Link/DAP=USB iSerial）
    product: str           # 显示名
    remote_addr: str = ""  # 仅 J-Link 远程


@dataclass(frozen=True)
class ProbeParams:
    """backend connect / program / verify 所需参数。

    FlashWorker 把 FlashParams 翻译成这个，避免 backend 反向依赖 UI 层数据类。
    """
    device_name: str
    interface: str            # "SWD" | "JTAG"
    speed_khz: int
    file_path: str
    file_format: str          # FORMAT_*
    bin_start_addr: int       # 仅 bin 用
    erase_mode: str           # ERASE_MODE_*
    post_action: str          # POST_ACTION_*
    extra_verify: bool
    serial: str = ""          # USB serial；空/"0" = 未指定
    remote_addr: str = ""     # 远程 "ip:port"；空 = 本地 USB


# ============================================================
# ProbeBackend 接口
# ============================================================
class ProbeBackend(Protocol):
    """烧录器后端统一接口。

    一次烧录会话的调用序列（由 FlashWorker._run_flash 编排）：
        connect(params)              # 建立连接，存内部
        [erase(mode)]                # 仅 chip 模式
        program(on_progress)         # 烧录
        [verify()]                   # 仅 extra_verify=True
        [reset(halt, run)]           # 仅 post_action != none
        close()                      # 始终调用（幂等）

    backend 实例一次会话后可复用（close 仅关句柄，重新 connect 会再 open）。
    """

    def connect(self, params: ProbeParams) -> None: ...
    def erase(self, mode: str) -> None: ...
    def program(self, on_progress: ProgressCallback) -> None: ...
    def verify(self) -> None: ...
    def reset(self, halt: bool, run: bool) -> None: ...
    def close(self) -> None: ...
    def connected_serial(self) -> str: ...
