"""精准测时：JLinkWorker 在真硬件上的 connect / 收数据 / disconnect 三步耗时。

直接运行：
    venv\Scripts\python.exe tools\diag_disconnect.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.jlink_worker import JLinkWorker


def main() -> int:
    app = QCoreApplication(sys.argv)

    print("[init] 启动 worker + QThread")
    t0 = time.perf_counter()
    thread = QThread()
    worker = JLinkWorker()
    worker.moveToThread(thread)
    thread.started.connect(worker.initialize)
    thread.start()
    while not worker._ready and time.perf_counter() - t0 < 2.0:
        app.processEvents()
        time.sleep(0.01)
    print(f"[init] worker ready in {(time.perf_counter()-t0)*1000:.0f} ms")

    # 订阅信号 + 打时间戳
    chunks_count = 0
    state_log: list[str] = []
    log_msgs: list[str] = []

    def on_data(s: str) -> None:
        nonlocal chunks_count
        chunks_count += 1

    def on_state(connected: bool) -> None:
        state_log.append(f"t={time.perf_counter()-t_cycle:.3f}s connected={connected}")

    def on_log(level: str, msg: str) -> None:
        log_msgs.append(f"[{level}] {msg}")

    worker.rtt_data_received.connect(on_data)
    worker.connection_state_changed.connect(on_state)
    worker.log_message.connect(on_log)

    t_cycle = t0  # 防止 UnboundLocalError（第一次 on_state 调用前赋值）

    for cycle in range(1, 4):
        print(f"\n=========== cycle {cycle} ===========")
        chunks_count = 0
        state_log.clear()
        log_msgs.clear()
        t_cycle = time.perf_counter()

        # --- 连接 ---
        print(f"[c{cycle}] [t=0.000] emit connect_requested")
        worker.connect_requested.emit("STM32H750VB", "SWD", 4000, 0)

        # 等连接完成
        deadline = time.perf_counter() + 5.0
        while not any("connected=True" in s for s in state_log) and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
        t_connected = time.perf_counter() - t_cycle
        print(f"[c{cycle}] [t={t_connected:.3f}s] connection_state_changed(True) received")

        # --- 收 3 秒数据 ---
        recv_until = time.perf_counter() + 3.0
        while time.perf_counter() < recv_until:
            app.processEvents()
            time.sleep(0.02)
        t_after_recv = time.perf_counter() - t_cycle
        print(f"[c{cycle}] [t={t_after_recv:.3f}s] 收到 {chunks_count} 段 rtt_data")

        # --- 断开（关键测时点）---
        t_disconnect_start = time.perf_counter() - t_cycle
        print(f"[c{cycle}] [t={t_disconnect_start:.3f}s] emit disconnect_requested")
        worker.disconnect_requested.emit()

        # 等 connection_state_changed(False)，最多 10 秒
        deadline = time.perf_counter() + 10.0
        while not any("connected=False" in s for s in state_log) and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.01)
        t_disconnect_done = time.perf_counter() - t_cycle
        elapsed = t_disconnect_done - t_disconnect_start
        if any("connected=False" in s for s in state_log):
            print(f"[c{cycle}] [t={t_disconnect_done:.3f}s] connection_state_changed(False) received — disconnect 耗时 {elapsed*1000:.0f} ms")
        else:
            print(f"[c{cycle}] [t={t_disconnect_done:.3f}s] ★ 超过 10 秒仍未收到 disconnect 完成信号！")

        print(f"[c{cycle}] 完整 state log:")
        for s in state_log:
            print(f"  {s}")
        if log_msgs:
            print(f"[c{cycle}] log_messages:")
            for m in log_msgs:
                print(f"  {m}")

        # 间隔 1 秒进下一轮
        end = time.perf_counter() + 1.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.02)

    # --- 最终 stop ---
    print("\n=========== final stop ===========")
    t_stop = time.perf_counter()
    worker.stop_requested.emit()
    while thread.isRunning() and time.perf_counter() - t_stop < 3.0:
        app.processEvents()
        time.sleep(0.01)
    if thread.isRunning():
        print(f"thread 未在 3 秒内退出（{(time.perf_counter()-t_stop)*1000:.0f} ms）→ terminate")
        thread.terminate()
        thread.wait(1000)
    else:
        print(f"thread 在 {(time.perf_counter()-t_stop)*1000:.0f} ms 内干净退出")

    return 0


if __name__ == "__main__":
    sys.exit(main())
