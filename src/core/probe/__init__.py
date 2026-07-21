"""烧录器后端抽象层（probe package）。

为固件烧录提供统一 ProbeBackend 接口，使 FlashWorker 不再直接耦合 pylink：
- J-Link  -> PylinkBackend（pylink-square 1.6.0，复用现有 flash_file/verify 逻辑）
- ST-Link / CMSIS-DAP -> PyOCDBackend（pyOCD，后续步骤加）

子模块按需 import；本包 __init__ 不 re-export，避免循环依赖。
"""
