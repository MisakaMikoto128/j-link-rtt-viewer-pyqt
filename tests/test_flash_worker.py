"""FlashWorker 单元测试：dataclass / 常量 / stage 编排 / 错误兜底。

走 pylink mock（注入到 PylinkBackend），不需要实际 J-Link 硬件。
连接序列 / 接口枚举 / close 容错等实现细节测试见 test_jlink_backend.py；
本文件聚焦 FlashWorker 的 stage 编排 + 信号透传 + 错误兜底。
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from core.flash_worker import (
    BURNER_KIND_JLINK,
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
    with pytest.raises(FrozenInstanceError):
        p.file_path = "/y.bin"  # type: ignore


def test_worker_signals_present():
    w = FlashWorker()
    for name in ("flash_requested", "stop_requested",
                 "flash_started", "flash_stage_changed",
                 "flash_progress", "flash_log", "flash_finished"):
        assert hasattr(w, name), f"missing signal: {name}"


# ============================================================
# _run_flash 成功 / 错误路径测试
# monkeypatch 注入 PylinkBackend 内的 pylink.JLink
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


def _make_worker(monkeypatch, fake_jlink):
    """monkeypatch PylinkBackend 内的 pylink.JLink，返回 initialize 后的 worker。"""
    monkeypatch.setattr("core.probe.jlink_backend.pylink.JLink", lambda: fake_jlink)
    w = FlashWorker()
    w.initialize()
    return w


def test_run_flash_success_elf_sector_reset_run(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.serial_number = 851012345
    w = _make_worker(monkeypatch, fake_jlink)
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
    w = _make_worker(monkeypatch, fake_jlink)
    p = _params_default(file_format=FORMAT_BIN, bin_start_addr=0x20000000)
    w._run_flash(p)
    qapp.processEvents()
    args = fake_jlink.flash_file.call_args[0]
    assert args[1] == 0x20000000


def test_run_flash_chip_erase_calls_erase_before_program(monkeypatch, qapp):
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    w = _make_worker(monkeypatch, fake_jlink)
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
    w = _make_worker(monkeypatch, fake_jlink)
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
    w = _make_worker(monkeypatch, fake_jlink)
    w._run_flash(_params_default(post_action=POST_ACTION_RESET))
    qapp.processEvents()
    fake_jlink.reset.assert_called_with(halt=True)
    fake_jlink.restart.assert_not_called()


def test_run_flash_connect_failure(monkeypatch, qapp):
    """connect 抛异常 -> flash_finished(False, ...) 且 backend.close 被调。"""
    import pylink as _pylink
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = False
    fake_jlink.connect.side_effect = _pylink.JLinkException("Could not connect")
    w = _make_worker(monkeypatch, fake_jlink)
    log = _collect_signals(w)

    w._run_flash(_params_default())
    qapp.processEvents()

    assert log[-1][0] == "finished"
    assert log[-1][1] is False
    fake_jlink.close.assert_called()
    # 不应该到达 program 阶段
    stages = [e[1] for e in log if e[0] == "stage"]
    assert STAGE_PROGRAM not in stages


def test_run_flash_program_failure(monkeypatch, qapp):
    """flash_file 抛异常 -> finished(False) + 错误 log 已写。"""
    import pylink as _pylink
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    fake_jlink.flash_file.side_effect = _pylink.JLinkException("Erase failed")
    w = _make_worker(monkeypatch, fake_jlink)
    log = _collect_signals(w)

    w._run_flash(_params_default())
    qapp.processEvents()

    errors = [e for e in log if e[0] == "log" and e[1] == "error"]
    assert any("Erase failed" in e[2] for e in errors)
    assert log[-1] == ("finished", False, "Erase failed")
    fake_jlink.close.assert_called()


def test_on_stop_closes_backend_and_quits_thread(monkeypatch, qapp):
    """_on_stop 调 backend.close -> thread.quit()。"""
    from core.probe.factory import make_backend
    fake_jlink = MagicMock()
    fake_jlink.opened.return_value = True
    w = _make_worker(monkeypatch, fake_jlink)
    # 新设计：backend 按 burner_kind 在 _run_flash 内动态创建（initialize 不预建）。
    # 模拟"有活跃 backend"的 shutdown 场景：手动建一个 jlink backend。
    w._backend = make_backend(BURNER_KIND_JLINK, w._log)
    fake_thread = MagicMock()
    # 替换 self.thread() -- QObject 没法直接 setattr 'thread' 方法，monkeypatch
    monkeypatch.setattr(w, "thread", lambda: fake_thread)
    w._on_stop()
    fake_jlink.close.assert_called()
    fake_thread.quit.assert_called()


def test_run_flash_routes_backend_by_burner_kind(monkeypatch, qapp):
    """_run_flash 按 FlashParams.burner_kind 选 backend，不固定 jlink。

    选 cmsisdap 时必须 make_backend("cmsisdap", ...) -> PyOCDBackend；否则
    CMSIS-DAP 烧录误用 PylinkBackend 报 "jlink offline"。
    """
    calls: list[str] = []
    fake_backend = MagicMock()
    monkeypatch.setattr("core.flash_worker.make_backend",
                        lambda kind, log: (calls.append(kind) or fake_backend))
    w = FlashWorker()
    w.initialize()
    w._run_flash(_params_default(burner_kind="cmsisdap"))
    qapp.processEvents()
    assert calls == ["cmsisdap"]
