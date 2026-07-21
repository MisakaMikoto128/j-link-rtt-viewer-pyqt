"""PylinkBackend 单元测试：连接序列 / 接口枚举 / 远程 / erase / program / reset / close。

从 test_flash_worker.py 迁来（FlashWorker 编排测试留在 test_flash_worker.py）。
走 pylink mock，不需要实际 J-Link 硬件。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.probe.base import (
    FORMAT_BIN,
    FORMAT_ELF,
    ProbeNotConnected,
    ProbeParams,
)
from core.probe.jlink_backend import PylinkBackend


def _params(**overrides):
    base = dict(
        device_name="STM32H750VB",
        interface="SWD",
        speed_khz=4000,
        file_path="C:/x.axf",
        file_format=FORMAT_ELF,
        bin_start_addr=0,
        erase_mode="sector",
        post_action="reset_run",
        extra_verify=False,
        serial="",
        remote_addr="",
    )
    base.update(overrides)
    return ProbeParams(**base)


def _make_backend(monkeypatch, fake_jlink):
    monkeypatch.setattr("core.probe.jlink_backend.pylink.JLink", lambda: fake_jlink)
    log: list[tuple[str, str]] = []
    backend = PylinkBackend(lambda lvl, m: log.append((lvl, m)))
    return backend, log


# ============================================================
# 连接序列
# ============================================================

def test_connect_follows_open_close_open_dance(monkeypatch):
    """严格按 CLAUDE.md 'pylink 1.6.0 连接顺序'：open -> close -> open(serial)
    -> set_tif -> set_speed -> connect。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.serial_number = 851012345
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params())

    call_names = [c[0] for c in fake_jlink.mock_calls]
    assert "opened" in call_names
    assert "open" in call_names
    assert "close" in call_names
    assert "set_tif" in call_names
    assert "set_speed" in call_names
    assert "connect" in call_names

    fake_jlink.set_speed.assert_called_with(4000)
    fake_jlink.connect.assert_called_with("STM32H750VB")


def test_connect_uses_jtag_enum_when_iface_jtag(monkeypatch):
    import pylink as _pylink
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True   # 已开，跳过双开
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params(interface="JTAG"))

    set_tif_arg = fake_jlink.set_tif.call_args[0][0]
    assert set_tif_arg == _pylink.enums.JLinkInterfaces.JTAG


def test_connect_remote_addr_skips_usb_enum_and_opens_by_ip(monkeypatch):
    """remote_addr 非空时跳过 connected_emulators，按 ip:port 双开。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.serial_number = 602717758
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params(remote_addr="192.168.79.1:19020"))

    fake_jlink.connected_emulators.assert_not_called()
    assert fake_jlink.open.call_count == 2
    fake_jlink.open.assert_called_with(ip_addr="192.168.79.1:19020")
    fake_jlink.set_tif.assert_called_once()
    fake_jlink.set_speed.assert_called_with(4000)
    fake_jlink.connect.assert_called_with("STM32H750VB")


def test_connect_no_jlink_raises_not_connected(monkeypatch):
    """本地 USB 模式无设备 -> ProbeNotConnected。"""
    fake_jlink = MagicMock()
    fake_jlink.connected_emulators.return_value = []
    backend, log = _make_backend(monkeypatch, fake_jlink)

    with pytest.raises(ProbeNotConnected):
        backend.connect(_params())
    assert any("未检测到" in m for lvl, m in log if lvl == "warn")


def test_connect_offline_serial_raises_not_connected(monkeypatch):
    """选中 serial 不在枚举里 -> ProbeNotConnected。"""
    emu = MagicMock(SerialNumber=111)
    fake_jlink = MagicMock()
    fake_jlink.connected_emulators.return_value = [emu]
    backend, log = _make_backend(monkeypatch, fake_jlink)

    with pytest.raises(ProbeNotConnected):
        backend.connect(_params(serial="999"))
    assert any("不在线" in m for lvl, m in log if lvl == "warn")


# ============================================================
# erase / program / reset
# ============================================================

def test_erase_chip_calls_jlink_erase(monkeypatch):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params())
    backend.erase("chip")
    fake_jlink.erase.assert_called_once()


def test_erase_sector_is_noop(monkeypatch):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params())
    backend.erase("sector")
    fake_jlink.erase.assert_not_called()


def test_program_passes_bin_start_addr(monkeypatch):
    """bin 文件 addr 用 bin_start_addr；ELF/hex 用 0。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params(file_format=FORMAT_BIN, bin_start_addr=0x20000000))
    backend.program(on_progress=lambda c, t: None)
    args = fake_jlink.flash_file.call_args[0]
    assert args[1] == 0x20000000


def test_program_progress_callback_translated(monkeypatch):
    """pylink (action, str, percentage) -> backend (pct, 100)。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params())
    received: list[tuple[int, int]] = []
    backend.program(on_progress=lambda c, t: received.append((c, t)))

    # 拿到 flash_file 调用时传入的 on_progress 关键字参数并手动触发
    cb = fake_jlink.flash_file.call_args.kwargs["on_progress"]
    cb("erase", "50%", 42)
    cb("program", "75%", None)   # None 百分比兜底为 0
    assert received[-2] == (42, 100)
    assert received[-1] == (0, 100)


def test_reset_run_calls_reset_and_restart(monkeypatch):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params())
    backend.reset(halt=True, run=True)
    fake_jlink.reset.assert_called_with(halt=True)
    fake_jlink.restart.assert_called_once()


def test_reset_no_run_skips_restart(monkeypatch):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    backend, _log = _make_backend(monkeypatch, fake_jlink)

    backend.connect(_params())
    backend.reset(halt=True, run=False)
    fake_jlink.reset.assert_called_with(halt=True)
    fake_jlink.restart.assert_not_called()


# ============================================================
# close
# ============================================================

def test_close_swallows_jlink_exception(monkeypatch):
    """close 抛 JLinkException 不传播（参考 CLAUDE.md 'close/rtt_stop 抛异常不致命'）。"""
    import pylink as _pylink
    fake_jlink = MagicMock()
    fake_jlink.close.side_effect = _pylink.JLinkException("not connected")
    backend, log = _make_backend(monkeypatch, fake_jlink)

    backend.close()  # 不应抛
    assert any("close warn" in m for lvl, m in log if lvl == "warn")


def test_close_idempotent_after_none(monkeypatch):
    """backend 未 initialize（_jlink 为 None）时 close 不抛。"""
    fake_jlink = MagicMock()
    backend, _log = _make_backend(monkeypatch, fake_jlink)
    backend._jlink = None
    backend.close()
    fake_jlink.close.assert_not_called()
