"""RTT 监控页面 probe backend 抽象层。

与 flash 的 ProbeBackend（base.py）平行：RTT 监控页面需要长时间持 probe 做
RTT 读写，与 flash 烧录的一次性会话语义不同，故独立协议。

双 backend（详见 memory project_rtt_dual_backend）：
- J-Link -> PylinkRttBackend（pylink 1.6.0，固件辅助 RTT，host 零 SWD poll）
- ST-Link / CMSIS-DAP -> PyocdRttBackend（pyOCD 0.45，host 端 SWD 轮询）

JLinkWorker 保留编排（读循环 / drain / stats / 日志 / 状态机 / decoder /
意外断开），backend 只管 probe-specific 调用。worker 持久拥有 pylink.JLink
实例（enum + J-Link 连接复用，当前行为），PylinkRttBackend 包它；
PyocdRttBackend 自管 pyOCD session（connect 建 / disconnect 销）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .base import (
    BURNER_KIND_CMSIS_DAP,
    BURNER_KIND_JLINK,
    BURNER_KIND_STLINK,
    LogCallback,
    ProbeError,
)

# ============================================================
# backend reset 模式（与 worker RESET_MODE_* 分开：worker 模式含编排，
# backend 模式只描述 probe 侧复位语义）
# ============================================================
RTT_RESET_NORMAL = "normal"  # 复位 + 运行 + RTT 重启（pylink rtt_stop/start 清缓存控制块地址）
RTT_RESET_HALT = "halt"  # 复位 + halt（CPU 停复位向量）
RTT_RESET_PRE_RECONNECT = "pre_reconnect"  # 复位 + 运行（不重启 RTT，worker 随后 disconnect+reconnect）


# ============================================================
# 连接参数 / 结果
# ============================================================
@dataclass(frozen=True)
class RttConnectParams:
    """RTT 连接参数（worker 从 UI 信号翻译而来）。

    target 对 J-Link 是 SEGGER device_name（如 STM32F103RB）；对 pyOCD 是
    target_override 候选（PyocdRttBackend 内部按 IDCODE 解析成 pyOCD target type）。
    """

    target: str
    iface: str  # "SWD" | "JTAG"
    speed_khz: int
    serial: str = ""  # USB serial；空/"0" = 未指定
    remote_addr: str = ""  # "ip:port"（仅 J-Link 远程）


@dataclass(frozen=True)
class RttConnectResult:
    """backend.connect 成功返回。

    actual_serial：实际打开的 probe serial（auto_reconnect / _last_connect_params 用）。
    num_up_channels：MCU 端已分配的上行通道数（J-Link 探测 SizeOfBuffer>0；
                     pyOCD = len(rtt.up_channels)）。
    device_info：设备信息（J-Link: firmware/core_name；pyOCD: probe.product/target）。
    """

    actual_serial: str
    num_up_channels: int
    device_info: dict


# ============================================================
# RttBackend 协议
# ============================================================
class RttBackend(Protocol):
    """RTT probe backend 协议（structurally typed）。

    一次 RTT 会话的调用序列（由 JLinkWorker 编排）：
        connect(params)              # 建 probe 连接 + 启 RTT
        [read_rtt / write_rtt]       # 读循环 + UI 发送
        [reset(halt, run)]           # 重置（backend 内部处理 RTT 重启）
        [read_memory / write_memory] # 内存查看
        [power_on / power_off]       # 仅 J-Link（supports_power 区分）
        disconnect()                 # 停 RTT + 关 probe（幂等）

    connect 抛 ProbeNotConnected（含可读 msg），worker catch 后 emit log_message
    + _do_disconnect。backend 自身的 log 回调用于 info 级（"J-Link SN: xxx"）。
    """

    def connect(self, params: RttConnectParams) -> RttConnectResult: ...
    def disconnect(self) -> None: ...
    def read_rtt(self, channel: int, max_bytes: int) -> bytes: ...
    def write_rtt(self, channel: int, data: bytes) -> int: ...
    def reset(self, mode: str) -> None: ...
    def read_memory(self, addr: int, size: int) -> bytes: ...
    def write_memory(self, addr: int, data: bytes) -> int: ...
    def is_connected(self) -> bool: ...
    def supports_power(self) -> bool: ...
    def power_on(self) -> None: ...
    def power_off(self) -> None: ...


# ============================================================
# 工厂
# ============================================================
def make_rtt_backend(burner_kind: str, log: LogCallback, *, jlink_shared=None) -> RttBackend:
    """按 burner_kind 选 RttBackend。

    jlink_shared：J-Link 时传入 worker 持久拥有的 pylink.JLink 实例（enum +
    连接复用，CLAUDE.md 'pylink 1.6.0 连接顺序' 在此执行）。非 J-Link 忽略。

    backend 实例在调用方线程（JLinkWorker worker 线程）创建，其内部 pylink/pyOCD
    对象的 thread affinity 跟随创建线程。
    """
    if burner_kind == BURNER_KIND_JLINK:
        from .pylink_rtt_backend import PylinkRttBackend

        if jlink_shared is None:
            raise ProbeError("J-Link RTT backend 需要共享 pylink.JLink 实例")
        return PylinkRttBackend(jlink_shared, log)
    if burner_kind in (BURNER_KIND_STLINK, BURNER_KIND_CMSIS_DAP):
        from .pyocd_rtt_backend import PyocdRttBackend

        return PyocdRttBackend(log)
    raise ProbeError(f"不支持的烧录器类型：{burner_kind}")
