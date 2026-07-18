"""FlashPage 与 RTT 监控页协调测试（J-Link 枚举 / 烧录器选择 / 烧录前后回连）。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal


class FakeRttWorker(QObject):
    devices_enumerated = Signal(str)
    connection_state_changed = Signal(bool)
    disconnect_requested = Signal()
    connect_requested = Signal(str, str, int, int, str)

    def __init__(self, state: str = "IDLE", serial: str = "") -> None:
        super().__init__()
        self._state = state
        self._serial = serial
        self.disconnects: list = []
        self.connects: list = []
        self.disconnect_requested.connect(lambda: self.disconnects.append(None))
        self.connect_requested.connect(lambda *a: self.connects.append(a))

    def state_name(self) -> str:
        return self._state

    def get_device_info(self) -> dict:
        return {"jlink_serial": self._serial}


class _SignalSpy(QObject):
    """极简跨线程信号计数器。"""
    def __init__(self, signal: Signal) -> None:
        super().__init__()
        self.count = 0
        signal.connect(self._on_fired)

    def _on_fired(self) -> None:
        self.count += 1


@pytest.fixture
def flash_coord(qtbot, isolated_appdata, fixtures_dir):
    from core.config_service import ConfigService
    from ui.flash_page import FlashPage
    cfg = ConfigService()
    worker = FakeRttWorker()
    page = FlashPage(cfg, rtt_worker=worker)
    qtbot.addWidget(page)
    page.show()
    yield page, worker, cfg, fixtures_dir
    page.shutdown()


def _process():
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()


def test_burner_combo_rebuilt_on_enumeration(flash_coord):
    page, worker, _cfg, _fd = flash_coord
    worker.devices_enumerated.emit("111|A;222|B")
    _process()
    assert page.cmb_burner.count() == 2
    assert page.cmb_burner.itemText(0) == "111"
    assert page.cmb_burner.itemText(1) == "222"
    assert page.cmb_burner.currentText() == "111"


def test_offline_burner_shows_red_dot(flash_coord):
    page, worker, cfg, _fd = flash_coord
    cfg.set("flash_jlink_serial", "222")
    worker.devices_enumerated.emit("111|A")
    _process()
    assert page.cmb_burner.currentText() == "222"
    assert not page._burner_status_dot.isHidden()
    assert page.cmb_burner.isReadOnly()


def test_start_flash_without_burner_warns_and_does_not_request(flash_coord):
    page, _worker, _cfg, fixtures_dir = flash_coord
    page._select_file(str(fixtures_dir / "blink.bin"))
    page.cmb_device.setCurrentText("STM32H750VB")
    page.rb_swd.setChecked(True)
    page.cmb_speed.setCurrentText("4000")
    assert page.cmb_burner.count() == 0

    spy = _SignalSpy(page._worker.flash_requested)
    page.btn_flash.click()
    _process()
    assert spy.count == 0


def test_start_flash_with_offline_burner_warns_and_does_not_request(flash_coord):
    page, worker, cfg, fixtures_dir = flash_coord
    cfg.set("flash_jlink_serial", "999")
    worker.devices_enumerated.emit("111|A")
    _process()
    page._select_file(str(fixtures_dir / "blink.bin"))
    page.cmb_device.setCurrentText("STM32H750VB")
    assert page.cmb_burner.currentText() == "999"
    assert not page._burner_status_dot.isHidden()

    spy = _SignalSpy(page._worker.flash_requested)
    page.btn_flash.click()
    _process()
    assert spy.count == 0


def test_same_serial_disconnects_rtt_before_flash(flash_coord, qtbot):
    page, worker, _cfg, fixtures_dir = flash_coord
    worker._state = "CONNECTED"
    worker._serial = "111"
    worker.devices_enumerated.emit("111|A")
    _process()

    page._select_file(str(fixtures_dir / "blink.bin"))
    page.cmb_device.setCurrentText("STM32H750VB")

    spy = _SignalSpy(page._worker.flash_requested)
    page.btn_flash.click()
    _process()

    assert len(worker.disconnects) == 1
    assert spy.count == 0
    assert page._rtt_pending_disconnect is True

    worker.connection_state_changed.emit(False)
    qtbot.waitSignal(page._worker.flash_requested, timeout=1000)
    _process()
    assert spy.count == 1
    assert page._rtt_pending_disconnect is False


def test_different_serial_flashes_directly(flash_coord, qtbot):
    page, worker, _cfg, fixtures_dir = flash_coord
    worker._state = "CONNECTED"
    worker._serial = "111"
    worker.devices_enumerated.emit("111|A;222|B")
    _process()
    page.cmb_burner.setCurrentIndex(page.cmb_burner.findText("222"))
    _process()

    page._select_file(str(fixtures_dir / "blink.bin"))
    page.cmb_device.setCurrentText("STM32H750VB")

    page.btn_flash.click()
    qtbot.waitSignal(page._worker.flash_requested, timeout=1000)
    assert len(worker.disconnects) == 0


def test_reconnect_rtt_after_flash_finished(flash_coord, qtbot):
    page, worker, cfg, fixtures_dir = flash_coord
    cfg.set("target_mcu", "STM32H750VB")
    cfg.set("interface", "SWD")
    cfg.set("speed_khz", 4000)
    cfg.set("rtt_channel", 0)

    worker._state = "CONNECTED"
    worker._serial = "111"
    worker.devices_enumerated.emit("111|A")
    _process()

    page._select_file(str(fixtures_dir / "blink.bin"))
    page.cmb_device.setCurrentText("STM32H750VB")
    page.btn_flash.click()
    qtbot.waitSignal(worker.disconnect_requested, timeout=1000)
    worker.connection_state_changed.emit(False)
    qtbot.waitSignal(page._worker.flash_requested, timeout=1000)

    page._worker.flash_finished.emit(True, "ok")
    qtbot.waitSignal(worker.connect_requested, timeout=1000)
    _process()

    assert len(worker.connects) == 1
    args = worker.connects[0]
    assert args[0] == "STM32H750VB"
    assert args[1] == "SWD"
    assert args[2] == 4000
    assert args[3] == 0
    assert args[4] == "111"


def test_set_rtt_busy_delegates_to_rtt_page(flash_coord):
    page, _worker, _cfg, _fd = flash_coord
    calls: list[bool] = []

    class DummyRttPage:
        def set_flash_busy(self, busy: bool) -> None:
            calls.append(busy)

    page._rtt_page_ref = DummyRttPage()
    page._set_rtt_busy(True)
    page._set_rtt_busy(False)
    assert calls == [True, False]
