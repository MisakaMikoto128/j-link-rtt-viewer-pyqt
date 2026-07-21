"""按 burner_kind 选 ProbeBackend 实例。

第 1 步只接 J-Link；ST-Link / CMSIS-DAP 在后续步骤加 PyOCDBackend 分支。
"""
from __future__ import annotations

from .base import (
    BURNER_KIND_CMSIS_DAP,
    BURNER_KIND_JLINK,
    BURNER_KIND_STLINK,
    LogCallback,
    ProbeBackend,
    ProbeError,
)
from .jlink_backend import PylinkBackend
from .pyocd_backend import PyOCDBackend


def make_backend(burner_kind: str, log: LogCallback) -> ProbeBackend:
    """按 burner_kind 选 backend。

    burner_kind 见 base.BURNER_KIND_*。未知类型抛 ProbeError。

    backend 实例在调用方线程（FlashWorker worker 线程）创建，其内部 pylink/pyOCD
    对象的 thread affinity 跟随创建线程。
    """
    if burner_kind == BURNER_KIND_JLINK:
        return PylinkBackend(log)
    if burner_kind in (BURNER_KIND_STLINK, BURNER_KIND_CMSIS_DAP):
        return PyOCDBackend(log)
    raise ProbeError(f"不支持的烧录器类型：{burner_kind}")
