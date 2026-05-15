"""内存查看页：地址 hex dump + 固件分块导出。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
)

from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker
from core.memory_service import format_hex_dump


_SIZE_PRESETS = [
    ("128 KB", 128 * 1024),
    ("256 KB", 256 * 1024),
    ("512 KB", 512 * 1024),
    ("1 MB", 1024 * 1024),
    ("2 MB", 2 * 1024 * 1024),
    ("自定义", -1),
]


def _parse_int(text: str) -> int:
    text = text.strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text)


class MemoryViewerPage(QWidget):
    def __init__(self, worker: JLinkWorker, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("memory-viewer")
        self._worker = worker
        self._cfg = cfg
        self._connected = False

        self._build_ui()
        self._wire_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- 读取区 ----
        read_card = CardWidget(self)
        read_lay = QHBoxLayout(read_card)
        read_lay.addWidget(BodyLabel("起始地址"))
        self.le_read_addr = LineEdit(self)
        self.le_read_addr.setText("0x08000000")
        self.le_read_addr.setMaximumWidth(140)
        read_lay.addWidget(self.le_read_addr)
        read_lay.addWidget(BodyLabel("大小 (字节)"))
        self.le_read_size = LineEdit(self)
        self.le_read_size.setText("0x100")
        self.le_read_size.setMaximumWidth(100)
        read_lay.addWidget(self.le_read_size)
        self.btn_read = PrimaryPushButton("读取", self)
        self.btn_clear = PushButton("清空", self)
        read_lay.addWidget(self.btn_read)
        read_lay.addWidget(self.btn_clear)
        read_lay.addStretch(1)
        root.addWidget(read_card)

        # ---- Hex 显示 ----
        self.display = QPlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Consolas", 12)
        self.display.setFont(font)
        root.addWidget(self.display, 1)

        # ---- 导出固件 ----
        export_card = CardWidget(self)
        ex_root = QVBoxLayout(export_card)
        ex_root.addWidget(BodyLabel("导出固件"))

        ex_row = QHBoxLayout()
        ex_row.addWidget(QLabel("起始地址"))
        self.le_ex_addr = LineEdit(self)
        self.le_ex_addr.setText("0x08000000")
        self.le_ex_addr.setMaximumWidth(140)
        ex_row.addWidget(self.le_ex_addr)
        ex_row.addWidget(QLabel("大小"))
        self.cb_ex_preset = ComboBox(self)
        for label, _ in _SIZE_PRESETS:
            self.cb_ex_preset.addItem(label)
        ex_row.addWidget(self.cb_ex_preset)
        self.le_ex_custom = LineEdit(self)
        self.le_ex_custom.setPlaceholderText("0x100000")
        self.le_ex_custom.setMaximumWidth(120)
        self.le_ex_custom.setEnabled(False)
        ex_row.addWidget(self.le_ex_custom)

        self.btn_choose = PushButton("选择保存路径", self)
        ex_row.addWidget(self.btn_choose)
        ex_row.addStretch(1)
        ex_root.addLayout(ex_row)

        self.lbl_path = QLabel("（未选择保存路径）", self)
        ex_root.addWidget(self.lbl_path)

        bottom = QHBoxLayout()
        self.btn_export = PrimaryPushButton("开始导出", self)
        self.btn_export.setEnabled(False)
        self.pb_export = QProgressBar(self)
        self.pb_export.setRange(0, 100)
        self.pb_export.setValue(0)
        bottom.addWidget(self.btn_export)
        bottom.addWidget(self.pb_export, 1)
        ex_root.addLayout(bottom)
        root.addWidget(export_card)

        self._save_path: str = ""
        self._set_enabled_by_connection(False)

    def _wire_signals(self) -> None:
        self.btn_read.clicked.connect(self._on_read_clicked)
        self.btn_clear.clicked.connect(self.display.clear)
        self.cb_ex_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.btn_choose.clicked.connect(self._on_choose_path)
        self.btn_export.clicked.connect(self._on_export_clicked)

        self._worker.connection_state_changed.connect(
            lambda c, _info: self._set_enabled_by_connection(c)
        )
        self._worker.memory_read_finished.connect(self._on_memory_read)
        self._worker.firmware_export_progress.connect(self._on_export_progress)
        self._worker.firmware_export_finished.connect(self._on_export_finished)
        self._worker.command_result.connect(self._on_command_result)

    def _set_enabled_by_connection(self, connected: bool) -> None:
        self._connected = connected
        self.btn_read.setEnabled(connected)
        self.btn_export.setEnabled(connected and bool(self._save_path))
        if not connected:
            InfoBar.warning(
                "未连接 J-Link",
                "请先到 RTT 监控页连接 J-Link，再进行内存读取或导出",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000,
            )

    def _on_read_clicked(self) -> None:
        try:
            addr = _parse_int(self.le_read_addr.text())
            size = _parse_int(self.le_read_size.text())
        except ValueError as e:
            InfoBar.warning("地址/大小格式错误", str(e), parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        if size <= 0 or size > 16 * 1024 * 1024:
            InfoBar.warning("大小越界", "1B - 16MB", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        self._worker.read_memory_requested.emit(addr, size)

    def _on_memory_read(self, addr: int, raw: bytes) -> None:
        self.display.setPlainText(format_hex_dump(raw, addr))

    def _on_preset_changed(self, idx: int) -> None:
        _, size = _SIZE_PRESETS[idx]
        self.le_ex_custom.setEnabled(size < 0)

    def _on_choose_path(self) -> None:
        from datetime import datetime
        default = f"firmware_{datetime.now():%Y%m%d_%H%M%S}.bin"
        path, _ = QFileDialog.getSaveFileName(self, "选择导出路径", default, "Binary (*.bin);;All (*)")
        if path:
            self._save_path = path
            self.lbl_path.setText(path)
            self.btn_export.setEnabled(self._connected)

    def _on_export_clicked(self) -> None:
        try:
            start = _parse_int(self.le_ex_addr.text())
        except ValueError as e:
            InfoBar.warning("地址格式错误", str(e), parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        idx = self.cb_ex_preset.currentIndex()
        _, preset_size = _SIZE_PRESETS[idx]
        if preset_size < 0:
            try:
                size = _parse_int(self.le_ex_custom.text())
            except ValueError as e:
                InfoBar.warning("大小格式错误", str(e), parent=self,
                                position=InfoBarPosition.TOP, duration=2000)
                return
        else:
            size = preset_size

        InfoBar.warning(
            "RTT 接收将暂停",
            f"导出 {size // 1024} KB 期间无法接收 RTT 数据",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )
        self.pb_export.setValue(0)
        self.btn_export.setEnabled(False)
        self._worker.export_firmware_requested.emit(self._save_path, start, size)

    def _on_export_progress(self, current: int, total: int) -> None:
        pct = int(current * 100 / total)
        self.pb_export.setValue(pct)

    def _on_export_finished(self, ok: bool, path: str, err: str) -> None:
        self.btn_export.setEnabled(self._connected)
        if ok:
            InfoBar.success("导出完成", path, parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
        else:
            InfoBar.error("导出失败", err, parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _on_command_result(self, cmd: str, ok: bool, payload: dict) -> None:
        if cmd == "read_memory" and not ok:
            InfoBar.error("读取失败", payload.get("error", ""), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
