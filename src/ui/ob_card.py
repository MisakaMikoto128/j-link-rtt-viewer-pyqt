"""Option bytes (RDP + WRP) card for the firmware flash page.

Placed above the symbol-table card.  Reads/sets RDP level and WRP status on
the selected burner via the JSON-driven OB module.  RDP and WRP are
independent option-byte fields and are shown/controlled in separate sections.

Handles F0 power-cycle, unverified-family warning, and RTT disconnect
coordination (disconnect-only, no reconnect -- per CLAUDE.md).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from core.option_bytes import (
    RdpLevel,
    load_profile,
    read_rdp_level,
    read_wrp_status,
    set_rdp_level,
    set_wrp,
)
from core.probe.base import ProbeParams
from core.probe.factory import make_backend
from core.probe.ob_adapter import make_ob_adapter, read_device_id

from ._ui_helpers import section_separator
from . import _infobar


# ---------------------------------------------------------------------------
# Worker -- runs OB op in a background thread
# ---------------------------------------------------------------------------
class ObWorker(QObject):
    busy = Signal(bool)
    message = Signal(str)     # status / read result (prefix "RDP = X" / "WRP = X" updates labels)
    warning = Signal(str)     # non-fatal warning (unverified family)
    error = Signal(str)       # fatal error

    def __init__(self) -> None:
        super().__init__()
        self._thread: QThread | None = None

    def start(self) -> None:
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None

    @Slot(str, str, str, str, int, str, str)
    def run_op(self, burner_kind: str, serial: str, device_name: str,
               interface: str, speed_khz: int, op: str, level: str) -> None:
        self.busy.emit(True)
        try:
            backend = make_backend(burner_kind, lambda *_: None)
            backend.connect(ProbeParams(
                device_name=device_name, interface=interface, speed_khz=speed_khz,
                file_path="", file_format="bin", bin_start_addr=0,
                erase_mode="chip", post_action="none", extra_verify=False,
                serial=serial, remote_addr="",
            ))
            try:
                if op == "reset":
                    # 复位不需 device_id/profile, 直接调 backend.reset (兼容所有烧录器)
                    backend.reset(halt=False, run=True)
                    self.message.emit(self.tr("已复位"))
                    return
                adapter = make_ob_adapter(backend)
                device_id = read_device_id(adapter)
                profile = load_profile(device_id)
                if not profile.verified:
                    self.warning.emit(f"DeviceID {device_id} -> family {profile.family} (UNVERIFIED)")
                if op == "read":
                    lvl = read_rdp_level(device_id, adapter)
                    self.message.emit(f"RDP = {lvl.value}")
                elif op == "set":
                    res = set_rdp_level(device_id, RdpLevel[level], adapter)
                    msg = f"RDP set to {RdpLevel[level].value}"
                    if res.obl_status == "needs_power_cycle":
                        msg += " - " + self.tr("断电再上电 (POR) 使其生效")
                    self.message.emit(msg)
                elif op == "wrp_read":
                    status = read_wrp_status(device_id, adapter)
                    self.message.emit(f"WRP = {self.tr(status)}")
                elif op == "wrp_enable":
                    res = set_wrp(device_id, adapter, protect_all=True)
                    msg = self.tr("已启用全片写保护")
                    if res == "needs_power_cycle":
                        msg += " - " + self.tr("断电再上电 (POR) 使其生效")
                    self.message.emit(msg)
                elif op == "wrp_clear":
                    res = set_wrp(device_id, adapter, protect_all=False)
                    msg = self.tr("已清除写保护")
                    if res == "needs_power_cycle":
                        msg += " - " + self.tr("断电再上电 (POR) 使其生效")
                    self.message.emit(msg)
                else:
                    self.error.emit(f"unknown op: {op}")
            finally:
                backend.close()
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")
        finally:
            self.busy.emit(False)


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------
class OptionBytesCard(CardWidget):
    """Option bytes (RDP + WRP) card.  RDP and WRP are independent sections."""

    op_request = Signal(str, str, str, str, int, str, str)  # kind, serial, device, iface, speed, op, level

    def __init__(self, get_burner_params, rtt_worker=None, parent=None) -> None:
        super().__init__(parent)
        self._get_burner_params = get_burner_params
        self._rtt_worker = rtt_worker
        self._pending_op: tuple[str, str] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # ---- title + hint ----
        self.lbl_title = StrongBodyLabel("")
        layout.addWidget(self.lbl_title)

        self.lbl_hint = BodyLabel("")
        self.lbl_hint.setWordWrap(True)
        layout.addWidget(self.lbl_hint)

        # ---- RDP section ----
        self.lbl_rdp_section = StrongBodyLabel("")
        layout.addWidget(self.lbl_rdp_section)

        row_rdp = QHBoxLayout()
        self.lbl_rdp_current = BodyLabel("")
        row_rdp.addWidget(self.lbl_rdp_current)
        self.lbl_rdp = BodyLabel("-")
        self.lbl_rdp.setStyleSheet("font-weight: bold;")
        row_rdp.addWidget(self.lbl_rdp)
        self.btn_rdp_read = PushButton("")
        self.btn_rdp_read.clicked.connect(self._on_rdp_read)
        row_rdp.addWidget(self.btn_rdp_read)
        self.lbl_rdp_set = BodyLabel("")
        row_rdp.addWidget(self.lbl_rdp_set)
        self.cmb_rdp_level = ComboBox()
        row_rdp.addWidget(self.cmb_rdp_level)
        self.btn_rdp_set = PrimaryPushButton("")
        self.btn_rdp_set.clicked.connect(self._on_rdp_set)
        row_rdp.addWidget(self.btn_rdp_set)
        row_rdp.addStretch()
        layout.addLayout(row_rdp)

        # ---- separator ----
        layout.addWidget(section_separator(self))

        # ---- WRP section ----
        self.lbl_wrp_section = StrongBodyLabel("")
        layout.addWidget(self.lbl_wrp_section)

        row_wrp = QHBoxLayout()
        self.lbl_wrp_current = BodyLabel("")
        row_wrp.addWidget(self.lbl_wrp_current)
        self.lbl_wrp_status = BodyLabel("-")
        self.lbl_wrp_status.setStyleSheet("font-weight: bold;")
        row_wrp.addWidget(self.lbl_wrp_status)
        self.btn_wrp_read = PushButton("")
        self.btn_wrp_read.clicked.connect(self._on_wrp_read)
        row_wrp.addWidget(self.btn_wrp_read)
        self.btn_wrp_enable = PrimaryPushButton("")
        self.btn_wrp_enable.clicked.connect(self._on_wrp_enable)
        row_wrp.addWidget(self.btn_wrp_enable)
        self.btn_wrp_clear = PushButton("")
        self.btn_wrp_clear.clicked.connect(self._on_wrp_clear)
        row_wrp.addWidget(self.btn_wrp_clear)
        row_wrp.addStretch()
        layout.addLayout(row_wrp)

        # ---- status ----
        self.lbl_status = BodyLabel("")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        # ---- worker ----
        self._worker = ObWorker()
        self._worker.busy.connect(self._on_busy)
        self._worker.message.connect(self._on_message)
        self._worker.warning.connect(self._on_warning)
        self._worker.error.connect(self._on_error)
        self.op_request.connect(self._worker.run_op)
        self._worker.start()

        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.lbl_title.setText(self.tr("选项字 (Option Bytes)"))
        self.lbl_hint.setText(self.tr(
            "RDP 读保护与 WRP 写保护相互独立,可分别设置。"
        ))
        self.lbl_rdp_section.setText(self.tr("读保护 (RDP)"))
        self.lbl_rdp_current.setText(self.tr("当前 RDP:"))
        self.btn_rdp_read.setText(self.tr("读取"))
        self.lbl_rdp_set.setText(self.tr("设置:"))
        self.cmb_rdp_level.clear()
        self.cmb_rdp_level.addItems([self.tr("L0 (无保护)"), self.tr("L1 (读保护)")])
        self.btn_rdp_set.setText(self.tr("写入"))

        self.lbl_wrp_section.setText(self.tr("写保护 (WRP)"))
        self.lbl_wrp_current.setText(self.tr("当前 WRP:"))
        self.btn_wrp_read.setText(self.tr("读取"))
        self.btn_wrp_enable.setText(self.tr("启用全片写保护"))
        self.btn_wrp_clear.setText(self.tr("清除"))

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        super().changeEvent(event)

    # ---- RTT coordination (disconnect-only, no reconnect) ----
    def _maybe_disconnect_rtt(self, params) -> bool:
        if self._rtt_worker is None:
            return False
        try:
            if self._rtt_worker.state_name() != "CONNECTED":
                return False
            info = self._rtt_worker.get_device_info()
            rtt_serial = str(info.get("jlink_serial", "") or "")
        except Exception:
            return False
        serial = params[1]
        if serial and rtt_serial and serial == rtt_serial:
            self._rtt_worker.disconnect_requested.emit()
            return True
        return False

    # ---- RDP button handlers ----
    def _on_rdp_read(self) -> None:
        params = self._get_burner_params()
        if not params or not params[0]:
            self.lbl_status.setText(self.tr("请先选择烧录器和设备"))
            return
        if self._maybe_disconnect_rtt(params):
            self.lbl_status.setText(self.tr("RTT 占用同一烧录器, 正在断开…"))
            self._pending_op = ("read", "")
            QTimer.singleShot(1500, self._dispatch_pending)
            return
        self._dispatch("read", "")

    def _on_rdp_set(self) -> None:
        params = self._get_burner_params()
        if not params or not params[0]:
            self.lbl_status.setText(self.tr("请先选择烧录器和设备"))
            return
        level = "L0" if self.cmb_rdp_level.currentIndex() == 0 else "L1"
        if level == "L1":
            box = MessageBox(
                self.tr("确认设置 L1?"),
                self.tr("L1 (读保护) 锁定 flash 读访问。\nL1->L0 触发 mass erase (flash 全丢)。\n\n继续?"),
                self.window(),
            )
            box.yesButton.setText(self.tr("确认"))
            box.cancelButton.setText(self.tr("取消"))
            if not box.exec():
                return
        if self._maybe_disconnect_rtt(params):
            self.lbl_status.setText(self.tr("RTT 占用同一烧录器, 正在断开…"))
            self._pending_op = ("set", level)
            QTimer.singleShot(1500, self._dispatch_pending)
            return
        self._dispatch("set", level)

    # ---- WRP button handlers ----
    def _on_wrp_read(self) -> None:
        params = self._get_burner_params()
        if not params or not params[0]:
            self.lbl_status.setText(self.tr("请先选择烧录器和设备"))
            return
        if self._maybe_disconnect_rtt(params):
            self.lbl_status.setText(self.tr("RTT 占用同一烧录器, 正在断开…"))
            self._pending_op = ("wrp_read", "")
            QTimer.singleShot(1500, self._dispatch_pending)
            return
        self._dispatch("wrp_read", "")

    def _on_wrp_enable(self) -> None:
        params = self._get_burner_params()
        if not params or not params[0]:
            self.lbl_status.setText(self.tr("请先选择烧录器和设备"))
            return
        box = MessageBox(
            self.tr("确认启用全片写保护?"),
            self.tr("启用后该扇区无法再烧录,需先清除。继续?"),
            self.window(),
        )
        box.yesButton.setText(self.tr("确认"))
        box.cancelButton.setText(self.tr("取消"))
        if not box.exec():
            return
        if self._maybe_disconnect_rtt(params):
            self.lbl_status.setText(self.tr("RTT 占用同一烧录器, 正在断开…"))
            self._pending_op = ("wrp_enable", "")
            QTimer.singleShot(1500, self._dispatch_pending)
            return
        self._dispatch("wrp_enable", "")

    def _on_wrp_clear(self) -> None:
        params = self._get_burner_params()
        if not params or not params[0]:
            self.lbl_status.setText(self.tr("请先选择烧录器和设备"))
            return
        box = MessageBox(
            self.tr("确认清除写保护?"),
            self.tr("清除后全片可写。继续?"),
            self.window(),
        )
        box.yesButton.setText(self.tr("确认"))
        box.cancelButton.setText(self.tr("取消"))
        if not box.exec():
            return
        if self._maybe_disconnect_rtt(params):
            self.lbl_status.setText(self.tr("RTT 占用同一烧录器, 正在断开…"))
            self._pending_op = ("wrp_clear", "")
            QTimer.singleShot(1500, self._dispatch_pending)
            return
        self._dispatch("wrp_clear", "")

    def do_reset(self) -> None:
        """复位目标芯片 (兼容所有烧录器)。供 flash_page 复位按钮调用。"""
        params = self._get_burner_params()
        if not params or not params[0]:
            self.lbl_status.setText(self.tr("请先选择烧录器和设备"))
            return
        if self._maybe_disconnect_rtt(params):
            self.lbl_status.setText(self.tr("RTT 占用同一烧录器, 正在断开…"))
            self._pending_op = ("reset", "")
            QTimer.singleShot(1500, self._dispatch_pending)
            return
        self._dispatch("reset", "")

    def _dispatch_pending(self) -> None:
        if self._pending_op is None:
            return
        op, level = self._pending_op
        self._pending_op = None
        self._dispatch(op, level)

    def _dispatch(self, op: str, level: str) -> None:
        params = self._get_burner_params()
        if not params:
            self.lbl_status.setText(self.tr("烧录器参数丢失"))
            return
        kind, serial, device, iface, speed = params
        if op == "read":
            status_msg = self.tr("操作中…")
        elif op == "set":
            status_msg = self.tr("写入中…")
        elif op in ("wrp_read",):
            status_msg = self.tr("操作中…")
        elif op in ("wrp_enable", "wrp_clear"):
            status_msg = self.tr("写入中…")
        else:
            status_msg = self.tr("操作中…")
        self.lbl_status.setText(status_msg)
        self.op_request.emit(kind, serial, device, iface, speed, op, level)

    # ---- worker signals ----
    @Slot(bool)
    def _on_busy(self, busy: bool) -> None:
        self.btn_rdp_read.setEnabled(not busy)
        self.btn_rdp_set.setEnabled(not busy)
        self.cmb_rdp_level.setEnabled(not busy)
        self.btn_wrp_read.setEnabled(not busy)
        self.btn_wrp_enable.setEnabled(not busy)
        self.btn_wrp_clear.setEnabled(not busy)

    @Slot(str)
    def _on_message(self, msg: str) -> None:
        if msg.startswith("RDP = "):
            self.lbl_rdp.setText(msg.split("RDP = ", 1)[1])
        elif msg.startswith("WRP = "):
            self.lbl_wrp_status.setText(msg.split("WRP = ", 1)[1])
        self.lbl_status.setText(msg)

    @Slot(str)
    def _on_warning(self, msg: str) -> None:
        _infobar.warn(self, self.tr("未验证家族"), msg, duration=5000)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self.lbl_status.setText(msg)
        _infobar.error(self, self.tr("操作失败"), msg, duration=8000)

    def cleanup(self) -> None:
        self._worker.stop()
