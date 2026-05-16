"""JLinkWorker 端到端测试：接真硬件，验证 Qt 信号链路。

直接运行（需要 J-Link + STM32H750VB 连着，MCU 在 RTT ch0 输出日志）：
    venv\Scripts\python.exe tools\e2e_worker.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread

# 把 src 加到 path
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.jlink_worker import JLinkWorker


def main() -> int:
    app = QCoreApplication(sys.argv)

    # === 1. 创建 worker + 外部 QThread + moveToThread ===
    print("[step 1] 创建 worker + QThread")
    thread = QThread()
    worker = JLinkWorker()
    worker.moveToThread(thread)
    thread.started.connect(worker.initialize)
    thread.start()

    # 等 worker 就绪
    deadline = time.time() + 2.0
    while not worker._ready and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    if not worker._ready:
        print("[FAIL] worker 启动超时")
        return 1
    print(f"[step 1] OK，worker._ready=True")

    # === 2. 订阅信号 ===
    print("\n[step 2] 订阅 rtt_data_received / connection_state_changed / log_message")
    received_chunks: list[str] = []
    states: list[tuple[bool, dict]] = []
    log_msgs: list[tuple[str, str]] = []

    worker.rtt_data_received.connect(lambda s: received_chunks.append(s))
    worker.connection_state_changed.connect(lambda c, info: states.append((c, dict(info))))
    worker.log_message.connect(lambda lvl, msg: log_msgs.append((lvl, msg)))

    # === 3. emit connect_requested ===
    print("\n[step 3] emit connect_requested(STM32H750VB, SWD, 4000, ch=0)")
    worker.connect_requested.emit("STM32H750VB", "SWD", 4000, 0)

    # 等待连接结果 + 5 秒采集数据
    end = time.time() + 8.0
    last_chunk_count = -1
    while time.time() < end:
        app.processEvents()
        if len(received_chunks) != last_chunk_count:
            print(f"  [t={end-time.time():.1f}s remain] chunks={len(received_chunks)}, "
                  f"latest connected={states[-1][0] if states else 'no state yet'}")
            last_chunk_count = len(received_chunks)
        time.sleep(0.02)

    # === 4. 报告结果 ===
    print(f"\n[step 4] 收到 {len(received_chunks)} 个 rtt_data 段，总 {sum(len(s) for s in received_chunks)} 字符")
    if received_chunks:
        print(f"  第一段前 120 字符: {received_chunks[0][:120]!r}")
        last_text = "".join(received_chunks)[-200:]
        print(f"  最后 200 字符: {last_text!r}")

    print(f"\n[step 4] 收到 {len(states)} 次 connection_state_changed:")
    for c, info in states:
        print(f"  connected={c}, info_keys={list(info.keys())[:5]}")

    print(f"\n[step 4] 收到 {len(log_msgs)} 条 log_message:")
    for lvl, msg in log_msgs[:10]:
        print(f"  [{lvl}] {msg}")

    # === 5. 断开 ===
    print("\n[step 5] emit disconnect_requested")
    worker.disconnect_requested.emit()
    end = time.time() + 2.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)

    # === 6. 停止 worker ===
    print("\n[step 6] emit stop_requested")
    worker.stop_requested.emit()
    end = time.time() + 3.0
    while thread.isRunning() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)

    if thread.isRunning():
        print("[step 6] thread 未在 3 秒内停止，强制 terminate")
        thread.terminate()
        thread.wait(1000)
    else:
        print("[step 6] thread 干净退出")

    # === 7. 结论 ===
    print("\n=== 结论 ===")
    if not received_chunks:
        print("FAIL: 8 秒内没收到任何 rtt_data_received 信号")
        print("       原因可能：")
        print("       - jlink_worker 的连接顺序仍有 bug（不该）")
        print("       - rtt_data_received 信号没正确跨线程投递（架构问题）")
        print("       - _poll_timer 没真正在 worker 线程跑")
        return 2

    if sum(len(s) for s in received_chunks) < 200:
        print(f"WARN: 收到的数据量太少 ({sum(len(s) for s in received_chunks)} 字符)，预期 ≥1000")
        return 3

    print(f"PASS: 成功收到 {sum(len(s) for s in received_chunks)} 字符的 RTT 数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
