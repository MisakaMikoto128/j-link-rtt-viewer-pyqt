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
    """创建 JLinkWorker（纯 QObject）+ 外部 QThread + moveToThread。"""
    from PySide6.QtCore import QThread
    from core import jlink_worker as jw_mod

    fake_jlink_cls = MagicMock()
    fake_jlink_instance = MagicMock()
    fake_jlink_instance.opened.return_value = False
    fake_jlink_instance.connected.return_value = False
    fake_jlink_cls.return_value = fake_jlink_instance

    monkeypatch.setattr(jw_mod.pylink, "JLink", fake_jlink_cls)

    worker = jw_mod.JLinkWorker()
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.initialize)
    thread.start()

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
    while thread.isRunning() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    if thread.isRunning():
        thread.terminate()
        thread.wait(1000)


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
    w.connection_state_changed.connect(lambda c: states.append(c))

    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(1.0)

    # 调用顺序（pylink 1.6.0）：open() → close() → open(serial) → rtt_start() → set_tif() → set_speed() → connect()
    assert jl.open.called
    assert jl.set_tif.called
    assert jl.set_speed.called
    assert jl.set_speed.call_args[0][0] == 4000
    assert jl.connect.call_args[0][0] == "STM32G070CB"
    assert jl.rtt_start.called

    assert True in states
    # 信号不再传 dict，设备信息走同步方法
    info = w.get_device_info()
    assert info.get("target_device") == "STM32G070CB"
    assert info.get("interface") == "SWD"
    assert info.get("speed_khz") == 4000
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
    # _on_connect 会启动真实的 _read_thread（threading.Thread）
    assert w._read_thread is not None
    assert w._read_thread.is_alive(), "连接后 read_thread 应启动"

    w.disconnect_requested.emit()
    _drain_events(0.5)

    assert jl.rtt_stop.called
    assert jl.close.called
    assert w.state_name() == "IDLE"
    # _do_disconnect 必须 join read_thread 并把句柄置 None
    assert w._read_thread is None, "disconnect 后 read_thread 句柄应已清空"
    # _stop_read 对称重置回 False（避免下次连接前残留 True）
    assert w._stop_read is False, "disconnect 末尾应将 _stop_read 重置为 False"


def test_disconnect_always_calls_cleanup(worker):
    """pylink 1.6.0 断开模式：rtt_stop/close 无条件调用，异常只 warning 不阻断。"""
    w, jl = worker
    # 连接然后让 opened/connected 都变 false（模拟中途掉线）
    jl.opened.return_value = True
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    jl.opened.return_value = False
    jl.connected.return_value = False
    # 让 rtt_stop/close 抛异常——断开不应因此失败
    import pylink.errors
    jl.rtt_stop.side_effect = pylink.errors.JLinkException(-1)
    jl.close.side_effect = pylink.errors.JLinkException(-1)

    w.disconnect_requested.emit()
    _drain_events(0.5)

    # 无条件调用，即使内部状态是"未连接"
    jl.rtt_stop.assert_called()
    jl.close.assert_called()
    # 状态回到 IDLE
    assert w.state_name() == "IDLE"


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
    """stop_requested 必须 worker 自己 quit 所在 thread，主线程只 wait。"""
    from PySide6.QtCore import QThread
    from core import jlink_worker as jw_mod

    fake_jlink_cls = MagicMock()
    fake_jlink_instance = MagicMock()
    fake_jlink_instance.opened.return_value = False
    fake_jlink_instance.connected.return_value = False
    fake_jlink_cls.return_value = fake_jlink_instance
    monkeypatch.setattr(jw_mod.pylink, "JLink", fake_jlink_cls)

    worker = jw_mod.JLinkWorker()
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.initialize)
    thread.start()

    deadline = time.time() + 2.0
    while not worker._ready and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    worker.stop_requested.emit()
    deadline = time.time() + 3.0
    while thread.isRunning() and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)

    assert not thread.isRunning(), "stop_requested 后 thread 应已退出"
    # drain timer 必须由 worker 在自己线程内 stop+deleteLater，否则退出会有
    # cross-thread killTimer 警告（CLAUDE.md「worker 线程内的 QTimer 退出前必须自己 stop」）
    assert worker._rtt_drain_timer is None, "worker 退出前应已清理 drain timer"


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
    w.command_result.connect(lambda c, ok, msg: results.append((c, ok, msg)))

    w.send_data_requested.emit("hello", False)
    _drain_events(0.3)
    assert ("send_data", False) == (results[0][0], results[0][1])


def test_reset_target(worker):
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)

    w.reset_requested.emit("normal")
    _drain_events(0.3)
    jl.reset.assert_called_once()


def test_reset_and_halt(worker):
    """重置并暂停：reset 第二参 halt=True，且不触发断开/重连。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)
    jl.reset.reset_mock()
    jl.close.reset_mock()

    from core.jlink_worker import RESET_MODE_HALT
    w.reset_requested.emit(RESET_MODE_HALT)
    _drain_events(0.3)

    jl.reset.assert_called_once_with(0, True)  # halt=True
    jl.close.assert_not_called()               # 不断开


def test_set_channel_takes_effect(worker):
    w, jl = worker
    w.set_rtt_channel_requested.emit(5)
    _drain_events(0.2)
    assert w._view_channel == 5
    assert w._send_channel == 5


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


def test_unexpected_disconnect_emits_signal(worker):
    """物理掉线：rtt_read 抛异常 -> emit unexpected_disconnect(设备标识) + 转断开态。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.serial_number = 12345678
    # 读循环空转，不产生噪声也不提前触发异常
    jl.rtt_read.return_value = []
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)
    assert w.state_name() == "CONNECTED"

    unexpected = []
    w.unexpected_disconnect.connect(lambda d: unexpected.append(d))
    states = []
    w.connection_state_changed.connect(lambda c: states.append(c))

    # 模拟物理掉线：下一次 rtt_read 抛异常
    jl.rtt_read.side_effect = RuntimeError("device gone")

    # 轮询等待 read_thread 命中异常 + drain timer(50ms) 检出闭环
    deadline = time.time() + 2.0
    while not unexpected and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)

    assert unexpected, "应 emit unexpected_disconnect"
    assert "12345678" in unexpected[0], f"标识应含 J-Link serial，got {unexpected[0]!r}"
    assert w.state_name() == "IDLE"
    assert False in states, "应 emit connection_state_changed(False)"


def test_normal_disconnect_does_not_emit_unexpected(worker):
    """用户主动断开不应触发意外断开红字提示。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_read.return_value = []
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    unexpected = []
    w.unexpected_disconnect.connect(lambda d: unexpected.append(d))
    w.disconnect_requested.emit()
    _drain_events(0.6)

    assert not unexpected, "主动断开不应 emit unexpected_disconnect"
    assert w.state_name() == "IDLE"


def test_connect_prechecks_jlink_presence(worker):
    """无 J-Link 时点击连接：connected_emulators 返回空 -> 气泡 warning + 不调 open（不弹 DLL 原生窗）。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.connected_emulators.return_value = []   # 无设备接入

    logs = []
    w.log_message.connect(lambda lv, m: logs.append((lv, m)))
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.6)

    assert ("warning", "未检测到 J-Link 设备，请检查 USB 连接") in logs
    assert not jl.open.called, "无设备不应调 jlink.open（避免弹出只能鼠标关闭的 DLL 窗）"
    assert w.state_name() == "IDLE"


def test_auto_reconnect_after_unexpected_disconnect(worker):
    """物理掉线 + 自动重连使能：emit reconnect_status(disconnect_reconnecting)，
    串行匹配 serial 后重连成功 -> emit success；只认上次那台 J-Link。"""
    from types import SimpleNamespace
    from PySide6.QtCore import QMetaObject
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.serial_number = 12345678
    jl.rtt_read.return_value = []
    # 枚举返回目标 serial（同一台 J-Link 还在）
    jl.connected_emulators.return_value = [SimpleNamespace(SerialNumber=12345678)]

    w.set_auto_reconnect_requested.emit(True)
    _drain_events(0.2)
    assert w._auto_reconnect_enabled is True

    status = []
    w.reconnect_status.connect(lambda k, d: status.append((k, d)))
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)
    assert w.state_name() == "CONNECTED"

    # 模拟物理掉线：rtt_read 抛异常 -> drain 检出 -> 走自动重连路径
    jl.rtt_read.side_effect = RuntimeError("device gone")
    deadline = time.time() + 2.0
    while not any(k == "disconnect_reconnecting" for k, _ in status) and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert any(k == "disconnect_reconnecting" for k, _ in status), f"应 emit disconnect_reconnecting，got {status}"
    assert w.state_name() == "IDLE"
    assert w._reconnect_timer is not None and w._reconnect_timer.isActive(), "重连 timer 应已启动"

    # 恢复 rtt_read（重连后的新读线程不应再立即抛异常），手动驱动一拍重连（worker 线程）
    jl.rtt_read.side_effect = None
    jl.rtt_read.return_value = []
    QMetaObject.invokeMethod(w, "_reconnect_tick", Qt.ConnectionType.QueuedConnection)
    deadline = time.time() + 2.0
    while not any(k == "success" for k, _ in status) and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert any(k == "success" for k, _ in status), f"应 emit success，got {status}"
    assert w.state_name() == "CONNECTED"


def test_auto_reconnect_only_matches_same_serial(worker):
    """串行匹配：枚举里没有目标 serial 时不重连（避免误连另一台 J-Link）。"""
    from types import SimpleNamespace
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.serial_number = 12345678
    jl.rtt_read.return_value = []
    w.set_auto_reconnect_requested.emit(True)
    _drain_events(0.2)
    status = []
    w.reconnect_status.connect(lambda k, d: status.append((k, d)))
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)

    # 掉线
    jl.rtt_read.side_effect = RuntimeError("gone")
    deadline = time.time() + 2.0
    while not any(k == "disconnect_reconnecting" for k, _ in status) and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)

    # 枚举返回的是【另一台】J-Link（serial 不同）-> 不应 attempt
    jl.connected_emulators.return_value = [SimpleNamespace(SerialNumber=99999999)]
    jl.rtt_read.side_effect = None
    from PySide6.QtCore import QMetaObject
    QMetaObject.invokeMethod(w, "_reconnect_tick", Qt.ConnectionType.QueuedConnection)
    _drain_events(0.4)
    assert not any(k == "attempt" for k, _ in status), "serial 不匹配不应发起重连"
    assert not any(k == "success" for k, _ in status), "另一台 J-Link 不应被误连"
    assert w.state_name() == "IDLE"


# ============================================================
# 多通道 RTT（v0.4.0）
# ============================================================
def _make_channel_reader(per_channel: dict):
    """生成 rtt_read(channel, n) 的 side_effect：按通道返回预置数据（读一次后清空）。"""
    def _read(ch, n):
        data = per_channel.get(ch, b"")
        per_channel[ch] = b""
        return list(data) if data else []
    return _read


def test_connect_detects_num_up_channels(worker):
    """连接成功后调 rtt_get_num_up_buffers 探测通道数；get_num_up_channels 可读。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_get_num_up_buffers.return_value = 3
    jl.rtt_read.return_value = []
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)
    assert w.state_name() == "CONNECTED"
    assert jl.rtt_get_num_up_buffers.called, "连接后应探测上行通道数"
    assert w.get_num_up_channels() == 3


def test_connect_detect_num_up_channels_fallback(worker):
    """rtt_get_num_up_buffers 抛异常（旧固件/控制块未找到）时回退 1，不影响连接。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_get_num_up_buffers.side_effect = RuntimeError("not supported")
    jl.rtt_read.return_value = []
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.5)
    assert w.state_name() == "CONNECTED", "探测失败不应让连接失败"
    assert w.get_num_up_channels() == 1


def test_read_loop_reads_all_channels_and_tags(worker):
    """多通道：读循环遍历所有通道，rtt_data_received 带通道号，统计按通道记账。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_get_num_up_buffers.return_value = 2
    per_ch = {0: b"hello-ch0\n", 1: b"hello-ch1\n"}
    jl.rtt_read.side_effect = _make_channel_reader(per_ch)

    received = []
    w.rtt_data_received.connect(lambda ch, text: received.append((ch, text)))
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.8)

    assert w.state_name() == "CONNECTED"
    channels_seen = {ch for ch, _ in received}
    assert channels_seen == {0, 1}, f"应读到 0/1 两个通道，got {channels_seen}"
    texts = dict()
    for ch, t in received:
        texts[ch] = texts.get(ch, "") + t
    assert "hello-ch0" in texts.get(0, "")
    assert "hello-ch1" in texts.get(1, "")

    st0 = w.get_stats(0)
    st1 = w.get_stats(1)
    st_all = w.get_stats(None)
    assert st0["bytes"] >= len("hello-ch0\n")
    assert st1["bytes"] >= len("hello-ch1\n")
    assert st_all["bytes"] == st0["bytes"] + st1["bytes"], "全部通道 = 各通道合计"


def test_per_channel_decoder_independence(worker):
    """两通道各自 decoder：UTF-8 多字节字符跨包拆分，两通道互不污染。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_get_num_up_buffers.return_value = 2
    received = []
    w.rtt_data_received.connect(lambda ch, text: received.append((ch, text)))
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.3)
    assert w.state_name() == "CONNECTED"

    # ch0 先发半字节，ch1 发完整字符——若共享 decoder，ch1 会被 ch0 的半字节污染
    hanzi = "中".encode("utf-8")  # 3 字节
    per_ch = {0: hanzi[:1], 1: "X".encode("utf-8")}
    jl.rtt_read.side_effect = _make_channel_reader(per_ch)
    _drain_events(0.5)
    # ch0 补发剩余字节
    per_ch = {0: hanzi[1:]}
    jl.rtt_read.side_effect = _make_channel_reader(per_ch)
    _drain_events(0.5)

    texts = dict()
    for ch, t in received:
        texts[ch] = texts.get(ch, "") + t
    assert "中" in texts.get(0, ""), f"ch0 应拼出完整汉字，got {texts.get(0)!r}"
    assert "X" in texts.get(1, ""), f"ch1 应有完整 X，got {texts.get(1)!r}"


def test_set_channel_all_keeps_send_channel(worker):
    """切到 -1（全部通道）不改变发送通道；切回具体通道更新发送通道。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_get_num_up_buffers.return_value = 3
    jl.rtt_read.return_value = []
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.4)

    w.set_rtt_channel_requested.emit(2)
    _drain_events(0.2)
    assert w._view_channel == 2
    assert w._send_channel == 2

    w.set_rtt_channel_requested.emit(-1)  # 全部通道
    _drain_events(0.2)
    assert w._view_channel == -1
    assert w._send_channel == 2, "全部通道视图不应改动发送通道"

    w.set_rtt_channel_requested.emit(0)
    _drain_events(0.2)
    assert w._send_channel == 0


def test_send_uses_send_channel(worker):
    """rtt_write 始终走 _send_channel（与视图通道无关）。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_get_num_up_buffers.return_value = 2
    jl.rtt_read.return_value = []
    jl.rtt_write.return_value = 5
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.4)

    w.set_rtt_channel_requested.emit(1)
    _drain_events(0.2)
    w.set_rtt_channel_requested.emit(-1)  # 视图切全部，发送通道仍是 1
    _drain_events(0.2)
    w.send_data_requested.emit("hello", False)
    _drain_events(0.3)
    assert jl.rtt_write.called
    assert jl.rtt_write.call_args[0][0] == 1, "发送应走 _send_channel=1，不是视图通道 -1"


def test_reset_counts_clears_all_channels(worker):
    """reset_counts 清零所有通道的统计。"""
    w, jl = worker
    jl.opened.return_value = False
    jl.connected.return_value = True
    jl.rtt_get_num_up_buffers.return_value = 2
    per_ch = {0: b"a\n", 1: b"b\n"}
    jl.rtt_read.side_effect = _make_channel_reader(per_ch)
    w.connect_requested.emit("STM32G070CB", "SWD", 4000, 0)
    _drain_events(0.6)
    assert w.get_stats(None)["bytes"] > 0

    w.reset_counts()
    assert w.get_stats(None)["bytes"] == 0
    assert w.get_stats(0)["bytes"] == 0
    assert w.get_stats(1)["bytes"] == 0
