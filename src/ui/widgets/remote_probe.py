"""远程主机 TCP 可达性探测：QThreadPool 调度 + 信号回传主线程。

RTT 监控页 / 固件烧录页共用。QRunnable 在线程池跑 tcp_reachable（阻塞
socket connect_ex，2s 超时），完成后通过 RemoteProbeHelper 发信号回 UI
线程，避免 UI 卡顿。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QObject, QRunnable, Signal

from .remote_host import tcp_reachable


class RemoteProbeHelper(QObject):
    """QThreadPool 线程向主线程回传 TCP 探测结果的信号中转对象。"""

    probe_done = Signal(bool)


class TcpReachableRunnable(QRunnable):
    """在线程池中执行 tcp_reachable，完成后通过 helper 发信号回 UI 线程。"""

    def __init__(self, ip: str, port: int, helper: RemoteProbeHelper) -> None:
        super().__init__()
        self._ip = ip
        self._port = port
        self._helper = helper

    def run(self) -> None:
        try:
            ok = tcp_reachable(self._ip, self._port)
        except Exception:
            ok = False
        # 页面对象已销毁（测试 teardown / 关窗）时 helper 随之删除，
        # 池化线程后到的 emit 抛 RuntimeError，静默丢弃即可。
        with contextlib.suppress(RuntimeError):
            self._helper.probe_done.emit(ok)
