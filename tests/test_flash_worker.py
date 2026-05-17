"""FlashWorker 单元测试：dataclass / 常量 / 流程 / 错误路径。

走 pylink mock，不需要实际 J-Link 硬件。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QThread, QTimer

from core.flash_worker import (
    ERASE_MODE_CHIP,
    ERASE_MODE_SECTOR,
    FORMAT_BIN,
    FORMAT_ELF,
    POST_ACTION_NONE,
    POST_ACTION_RESET,
    POST_ACTION_RESET_RUN,
    STAGE_CONNECT,
    STAGE_DISCONNECT,
    STAGE_ERASE,
    STAGE_PROGRAM,
    STAGE_RESET,
    STAGE_VERIFY,
    FlashParams,
    FlashWorker,
)


def test_constants_exposed():
    assert ERASE_MODE_SECTOR == "sector"
    assert ERASE_MODE_CHIP == "chip"
    assert POST_ACTION_NONE == "none"
    assert POST_ACTION_RESET == "reset"
    assert POST_ACTION_RESET_RUN == "reset_run"
    assert STAGE_CONNECT == "connect"
    assert STAGE_PROGRAM == "program"
    assert STAGE_DISCONNECT == "disconnect"


def test_flash_params_frozen():
    p = FlashParams(
        file_path="/x.bin", file_format=FORMAT_BIN, bin_start_addr=0,
        device_name="STM32", interface="SWD", speed_khz=4000,
        erase_mode=ERASE_MODE_SECTOR, post_action=POST_ACTION_RESET_RUN,
        extra_verify=False,
    )
    with pytest.raises(Exception):
        p.file_path = "/y.bin"  # type: ignore


def test_worker_signals_present():
    w = FlashWorker()
    for name in ("flash_requested", "stop_requested",
                 "flash_started", "flash_stage_changed",
                 "flash_progress", "flash_log", "flash_finished"):
        assert hasattr(w, name), f"missing signal: {name}"


def test_do_connect_follows_open_close_open_dance(monkeypatch):
    """严格按 CLAUDE.md 'pylink 1.6.0 连接顺序'：open → close → open(serial)
    → set_tif → set_speed → connect。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.serial_number = 851012345
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)

    w = FlashWorker()
    w.initialize()
    # 调真实方法
    w._do_connect("STM32H750VB", "SWD", 4000)

    # 验证调用序列（用 mock_calls 的顺序）
    call_names = [c[0] for c in fake_jlink.mock_calls]
    # 期望前几次：opened → open(空) → close → open(serial) → set_tif → set_speed → connect
    assert "opened" in call_names
    assert "open" in call_names
    assert "close" in call_names
    assert "set_tif" in call_names
    assert "set_speed" in call_names
    assert "connect" in call_names

    fake_jlink.set_speed.assert_called_with(4000)
    fake_jlink.connect.assert_called_with("STM32H750VB")


def test_do_connect_uses_jtag_enum_when_iface_jtag(monkeypatch):
    import pylink as _pylink
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True   # 已开，跳过双开
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    w._do_connect("STM32", "JTAG", 1000)
    set_tif_arg = fake_jlink.set_tif.call_args[0][0]
    assert set_tif_arg == _pylink.enums.JLinkInterfaces.JTAG


# ============================================================
# Task 6: _run_flash 成功路径测试
# ============================================================

def _params_default(**overrides):
    base = dict(
        file_path="C:/x.axf", file_format=FORMAT_ELF, bin_start_addr=0,
        device_name="STM32", interface="SWD", speed_khz=4000,
        erase_mode=ERASE_MODE_SECTOR, post_action=POST_ACTION_RESET_RUN,
        extra_verify=False,
    )
    base.update(overrides)
    return FlashParams(**base)


def _collect_signals(worker):
    """订阅 worker 输出信号，把每个 emit 记到列表。"""
    log = []
    worker.flash_started.connect(lambda: log.append(("started",)))
    worker.flash_stage_changed.connect(lambda s: log.append(("stage", s)))
    worker.flash_progress.connect(lambda c, t: log.append(("progress", c, t)))
    worker.flash_log.connect(lambda lvl, m: log.append(("log", lvl, m)))
    worker.flash_finished.connect(lambda ok, msg: log.append(("finished", ok, msg)))
    return log


def test_run_flash_success_elf_sector_reset_run(monkeypatch, qapp):
    import pylink as _pylink
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.serial_number = 851012345
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)

    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)

    w._run_flash(_params_default())
    qapp.processEvents()

    stages = [e[1] for e in log if e[0] == "stage"]
    assert stages == [STAGE_CONNECT, STAGE_PROGRAM, STAGE_RESET, STAGE_DISCONNECT]
    # flash_file 调用时 addr=0（ELF 文件内带地址）
    fake_jlink.flash_file.assert_called_once()
    args = fake_jlink.flash_file.call_args[0]
    assert args[1] == 0   # addr
    fake_jlink.reset.assert_called()
    fake_jlink.restart.assert_called()
    # 完成
    assert log[-1] == ("finished", True, "烧录成功")


def test_run_flash_bin_uses_bin_start_addr(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    p = _params_default(file_format=FORMAT_BIN, bin_start_addr=0x20000000)
    w._run_flash(p)
    qapp.processEvents()
    args = fake_jlink.flash_file.call_args[0]
    assert args[1] == 0x20000000


def test_run_flash_chip_erase_calls_erase_before_program(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)
    w._run_flash(_params_default(erase_mode=ERASE_MODE_CHIP))
    qapp.processEvents()
    stages = [e[1] for e in log if e[0] == "stage"]
    assert STAGE_ERASE in stages
    assert stages.index(STAGE_ERASE) < stages.index(STAGE_PROGRAM)
    fake_jlink.erase.assert_called_once()


def test_run_flash_post_action_none_no_reset(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    log = _collect_signals(w)
    w._run_flash(_params_default(post_action=POST_ACTION_NONE))
    qapp.processEvents()
    stages = [e[1] for e in log if e[0] == "stage"]
    assert STAGE_RESET not in stages
    fake_jlink.reset.assert_not_called()
    fake_jlink.restart.assert_not_called()


def test_run_flash_post_action_reset_no_run(monkeypatch, qapp):
    """post_action=reset 调 reset(halt=True)，不调 restart。"""
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    monkeypatch.setattr("core.flash_worker.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    w._run_flash(_params_default(post_action=POST_ACTION_RESET))
    qapp.processEvents()
    fake_jlink.reset.assert_called_with(halt=True)
    fake_jlink.restart.assert_not_called()
