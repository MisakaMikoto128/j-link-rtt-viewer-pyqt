"""J-Link DLL（JLinkARM.dll）全局访问锁。

根因（实测定位，详见 CLAUDE.md「pylink DLL 全局句柄不支持跨线程并发」）：
JLinkARM.dll 的所有句柄是**进程级全局**的（pylink ``opened()`` 查的是
``JLINKARM_IsOpen()`` 全局状态，所有 pylink.JLink 实例共享同一份 DLL 状态）。
DLL **不支持多个线程并发调用**——任意两个线程同时打 DLL（如 RTT worker 的
connect/open/close 与主线程 target_discovery 的 3700 次 supported_device 枚举、
或 200ms 设备枚举），会触发 DLL 内部断言 → access violation 0x14 / 0xc000001d。

因此**所有**碰 pylink DLL 的调用——无论哪条线程（RTT worker / FlashWorker /
主线程 target_discovery）——都必须走这同一把进程级 RLock 串行化。

用法：
    from core._dll_global import dll_lock
    with dll_lock():
        jlink.open(...)
RLock：同一线程内嵌套（如 _do_connect 持锁调 _detect_num_up_channels）可重入。
"""
from __future__ import annotations

import threading

_lock = threading.RLock()


def dll_lock() -> threading.RLock:
    """返回进程级 J-Link DLL 访问锁（所有 pylink 调用共用）。"""
    return _lock
