"""JLinkWorker：状态机、连接/断开序列、命令分发。

所有 pylink 都用 MagicMock。Worker 跑在子 QThread，主线程驱动 Qt 事件循环
处理 queued connection。
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, Qt


@pytest.fixture
def worker(qapp, monkeypatch):
    """创建 JLinkWorker 并 mock 掉 pylink.JLink。"""
    from core import jlink_worker as jw_mod

    fake_jlink_cls = MagicMock()
    fake_jlink_instance = MagicMock()
    fake_jlink_instance.opened.return_value = False
    fake_jlink_instance.connected.return_value = False
    fake_jlink_cls.return_value = fake_jlink_instance

    monkeypatch.setattr(jw_mod.pylink, "JLink", fake_jlink_cls)

    worker = jw_mod.JLinkWorker()
    worker.start()

    # 等待 worker 进入事件循环
    deadline = time.time() + 2.0
    while not worker._ready and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert worker._ready, "worker 启动超时"

    yield worker, fake_jlink_instance

    # 清理
    worker.stop_requested.emit()
    deadline = time.time() + 3.0
    while worker.isRunning() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    if worker.isRunning():
        worker.terminate()
        worker.wait(1000)


def _drain_events(timeout=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def test_initial_state_idle(worker):
    w, _ = worker
    assert w.state_name() == "IDLE"


def test_connect_sequence(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True

    states = []
    w.connection_state_changed.connect(lambda c, info: states.append((c, dict(info))))

    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(1.0)

    # 调用顺序：open() → set_tif() → set_speed() → connect() → rtt_start()
    assert jl.open.called
    assert jl.set_tif.called
    assert jl.set_speed.called
    assert jl.set_speed.call_args[0][0] == 4000
    assert jl.connect.call_args[0][0] == "STM32G070CB"
    assert jl.rtt_start.called

    assert any(c is True for c, _ in states)
    assert w.state_name() == "CONNECTED"


def test_no_double_open(worker):
    w, jl = worker
    jl.opened.return_value = True  # 已 open
    jl.connected.return_value = True

    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(1.0)

    assert jl.open.call_count == 0  # 已 open 不再 open


def test_disconnect_sequence_with_guards(worker):
    w, jl = worker
    # 先连上
    jl.opened.return_value = True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    w.disconnect_requested.emit()
    _drain_events(0.5)

    assert jl.rtt_stop.called
    assert jl.close.called
    assert w.state_name() == "IDLE"


def test_disconnect_skips_close_if_not_opened(worker):
    w, jl = worker
    # 连接然后让 opened/connected 都变 false
    jl.opened.return_value = True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    jl.opened.return_value = False
    jl.connected.return_value = False

    w.disconnect_requested.emit()
    _drain_events(0.5)

    # 守卫生效：connected() False → rtt_stop 不调；opened() False → close 不调
    jl.rtt_stop.assert_not_called()
    jl.close.assert_not_called()


def test_reconnect_after_disconnect(worker):
    """断开后立即重连：复现原项目"无法再次打开"场景。"""
    w, jl = worker

    jl.opened.return_value = True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    w.disconnect_requested.emit()
    _drain_events(0.5)
    jl.opened.return_value = False
    jl.connected.return_value = False

    # 重连
    open_calls_before = jl.open.call_count
    jl.opened.side_effect = [False, True]  # 第一次 check False → open() → 之后 True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    assert jl.open.call_count > open_calls_before
    assert w.state_name() == "CONNECTED"


def test_set_tif_swd_vs_jtag(worker):
    import pylink
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True

    w.connect_requested.emit("STM32G070CB", "JTAG", 4000, 0)
    _drain_events(0.5)
    assert jl.set_tif.call_args[0][0] == pylink.enums.JLinkInterfaces.JTAG

    w.disconnect_requested.emit()
    _drain_events(0.3)

    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)
    assert jl.set_tif.call_args[0][0] == pylink.enums.JLinkInterfaces.SWD


def test_stop_requested_quits_thread(qapp, monkeypatch):
    """stop_requested 必须 worker 自己 quit()，不能外部 quit()。"""
    from core import jlink_worker as jw_mod

    fake_jlink_cls = MagicMock()
    fake_jlink_instance = MagicMock()
    fake_jlink_instance.opened.return_value = False
    fake_jlink_instance.connected.return_value = False
    fake_jlink_cls.return_value = fake_jlink_instance
    monkeypatch.setattr(jw_mod.pylink, "JLink", fake_jlink_cls)

    w = jw_mod.JLinkWorker()
    w.start()
    deadline = time.time() + 2.0
    while not w._ready and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    w.stop_requested.emit()
    deadline = time.time() + 3.0
    while w.isRunning() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert not w.isRunning(), "stop_requested 后 worker 应已退出"


def test_send_data_text(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_write.return_value = 5
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.send_data_requested.emit("hello", False)
    _drain_events(0.3)
    jl.rtt_write.assert_called_once_with(0, b"hello")


def test_send_data_hex(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_write.return_value = 3
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.send_data_requested.emit("AA BB\nCC", True)
    _drain_events(0.3)
    jl.rtt_write.assert_called_once_with(0, bytes.fromhex("AABBCC"))


def test_send_data_when_not_connected(worker):
    w, jl = worker
    results = []
    w.command_result.connect(lambda c, ok, p: results.append((c, ok, dict(p))))

    w.send_data_requested.emit("hello", False)
    _drain_events(0.3)
    assert ("send_data", False) == (results[0][0], results[0][1])


def test_reset_target(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.reset_target_requested.emit()
    _drain_events(0.3)
    jl.reset.assert_called_once()


def test_set_channel_takes_effect(worker):
    w, jl = worker
    w.set_rtt_channel_requested.emit(5)
    _drain_events(0.2)
    assert w._channel == 5


def test_power_output_on_off(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.set_power_output_requested.emit(True)
    _drain_events(0.2)
    jl.power_on.assert_called_once()

    w.set_power_output_requested.emit(False)
    _drain_events(0.2)
    jl.power_off.assert_called_once()


def test_read_memory_emits_bytes(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.memory_read.return_value = [0x12345678]
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    received = []
    w.memory_read_finished.connect(lambda addr, raw: received.append((addr, bytes(raw))))

    w.read_memory_requested.emit(0x08000000, 4)
    _drain_events(0.5)
    assert received == [(0x08000000, bytes.fromhex("78563412"))]


def test_export_firmware_progress(worker, tmp_path):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    # 8 KB = 2 chunks, 每 chunk 1024 words
    jl.memory_read.side_effect = [[0xAA] * 1024, [0xBB] * 1024]
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    progress = []
    finished = []
    w.firmware_export_progress.connect(lambda c, t: progress.append((c, t)))
    w.firmware_export_finished.connect(lambda ok, p, err: finished.append((ok, p, err)))

    out = tmp_path / "fw.bin"
    w.export_firmware_requested.emit(str(out), 0x08000000, 8 * 1024)
    _drain_events(1.5)

    assert progress[-1] == (2, 2)
    assert finished and finished[0][0] is True
    assert out.stat().st_size == 8 * 1024


def test_log_recording_writes_file(worker, tmp_path):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.start_log_recording_requested.emit(str(tmp_path))
    _drain_events(0.2)
    assert w._log_file is not None

    # 模拟一次 RTT 输出
    w._write_log_file("hello log\n")
    w.stop_log_recording_requested.emit()
    _drain_events(0.2)

    logs = list(tmp_path.glob("*.log"))
    assert len(logs) == 1
    assert "hello log" in logs[0].read_text(encoding="utf-8")
