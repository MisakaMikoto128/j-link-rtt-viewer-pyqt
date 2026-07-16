"""屏幕常亮控制：通过 Windows SetThreadExecutionState 阻止系统息屏。

非 Windows 平台为 no-op。供设置页「保持屏幕常亮」勾选与启动时按配置调用。
"""
from __future__ import annotations

import sys


def apply_keep_screen_on(enabled: bool) -> None:
    """enabled=True 阻止屏幕息屏（持续生效直到关闭或进程退出）；False 恢复系统默认策略。"""
    if sys.platform != "win32":
        return
    import ctypes

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    if enabled:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )
    else:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
