"""RTT 监控页：控制栏 + 选项栏 + 显示区 + 搜索栏 + 发送栏。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QTextCharFormat, QTextCursor, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    EditableComboBox,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SpinBox,
)

from core.ansi_parser import AnsiAttrs, parse_ansi
from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker


_ANSI_COLOR_MAP = {
    "black": "#000000",
    "red": "#cc0000",
    "green": "#00aa00",
    "yellow": "#cc9900",
    "blue": "#3366cc",
    "magenta": "#aa00aa",
    "cyan": "#00aaaa",
    "white": "#dddddd",
    "bright_black": "#666666",
    "bright_red": "#ff5555",
    "bright_green": "#55ff55",
    "bright_yellow": "#ffff55",
    "bright_blue": "#5599ff",
    "bright_magenta": "#ff55ff",
    "bright_cyan": "#55ffff",
    "bright_white": "#ffffff",
}


class RTTMonitorPage(QWidget):
    def __init__(self, worker: JLinkWorker, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("rtt-monitor")
        self._worker = worker
        self._cfg = cfg

        self._build_ui()
        self._wire_signals()
        self._apply_font(cfg.get("font_family"), cfg.get("font_size"))

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        # ---- 控制栏 ----
        ctrl = QHBoxLayout()
        ctrl.addWidget(BodyLabel("目标设备"))
        self.cb_target = EditableComboBox(self)
        self.cb_target.addItems(self._cfg.get_chip_list())
        last_mcu = self._cfg.get("target_mcu")
        if last_mcu:
            self.cb_target.setCurrentText(last_mcu)
        self.cb_target.setMinimumWidth(180)
        ctrl.addWidget(self.cb_target)

        ctrl.addWidget(BodyLabel("接口"))
        self.cb_iface = ComboBox(self)
        self.cb_iface.addItems(["SWD", "JTAG"])
        self.cb_iface.setCurrentText(self._cfg.get("interface"))
        ctrl.addWidget(self.cb_iface)

        ctrl.addWidget(BodyLabel("速度(kHz)"))
        self.cb_speed = ComboBox(self)
        for s in self._cfg.get_default_speeds():
            self.cb_speed.addItem(str(s))
        cur_speed = str(self._cfg.get("speed_khz"))
        if self.cb_speed.findText(cur_speed) < 0:
            self.cb_speed.addItem(cur_speed)
        self.cb_speed.setCurrentText(cur_speed)
        ctrl.addWidget(self.cb_speed)

        ctrl.addWidget(BodyLabel("RTT 通道"))
        self.sp_channel = SpinBox(self)
        self.sp_channel.setRange(0, 15)
        self.sp_channel.setValue(self._cfg.get("rtt_channel"))
        ctrl.addWidget(self.sp_channel)

        self.btn_connect = PrimaryPushButton("连接", self)
        self.btn_reset = PushButton("重置目标", self)
        self.btn_reset.setEnabled(False)
        ctrl.addWidget(self.btn_connect)
        ctrl.addWidget(self.btn_reset)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # ---- 选项栏 ----
        opt = QHBoxLayout()
        self.chk_auto_scroll = CheckBox("自动滚动")
        self.chk_auto_scroll.setChecked(self._cfg.get("auto_scroll"))
        self.chk_pause = CheckBox("暂停接收")
        self.chk_power = CheckBox("电源输出")
        self.chk_power.setEnabled(False)
        self.chk_log_rec = CheckBox("实时日志记录")
        self.btn_clear = PushButton("清除", self)
        self.btn_save = PushButton("💾 保存当前", self)
        opt.addWidget(self.chk_auto_scroll)
        opt.addWidget(self.chk_pause)
        opt.addWidget(self.chk_power)
        opt.addWidget(self.chk_log_rec)
        opt.addStretch(1)
        opt.addWidget(self.btn_clear)
        opt.addWidget(self.btn_save)
        root.addLayout(opt)

        # ---- 显示区 ----
        self.display = QPlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setMaximumBlockCount(self._cfg.get("max_display_lines"))
        self.display.setLineWrapMode(QPlainTextEdit.NoWrap)
        root.addWidget(self.display, 1)

        # ---- 发送栏 ----
        send = QHBoxLayout()
        from qfluentwidgets import LineEdit
        self.le_send = LineEdit(self)
        self.le_send.setPlaceholderText("输入要发送的数据 (Hex 模式下用 16 进制字符)")
        self.chk_hex = CheckBox("Hex")
        self.chk_hex.setChecked(self._cfg.get("hex_send_mode"))
        self.btn_send = PushButton("发送", self)
        self.btn_send.setEnabled(False)
        send.addWidget(self.le_send, 1)
        send.addWidget(self.chk_hex)
        send.addWidget(self.btn_send)
        root.addLayout(send)

    # ------------------------------------------------------------------
    # 信号接线
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_reset.clicked.connect(self._worker.reset_target_requested.emit)
        self.btn_clear.clicked.connect(self.display.clear)
        self.chk_pause.toggled.connect(self._worker.set_pause_receive_requested.emit)
        self.chk_power.toggled.connect(self._worker.set_power_output_requested.emit)
        self.sp_channel.valueChanged.connect(self._on_channel_changed)
        self.chk_auto_scroll.toggled.connect(lambda v: self._cfg.set("auto_scroll", v))
        self.chk_hex.toggled.connect(lambda v: self._cfg.set("hex_send_mode", v))
        self.btn_send.clicked.connect(self._on_send_clicked)

        self._worker.rtt_data_received.connect(self._on_rtt_data)
        self._worker.connection_state_changed.connect(self._on_state_changed)

        self._cfg.font_changed.connect(self._apply_font)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_connect_clicked(self) -> None:
        if self.btn_connect.text() == "连接":
            target = self.cb_target.currentText().strip()
            if not target:
                InfoBar.warning("提示", "请先选择目标芯片", parent=self,
                                position=InfoBarPosition.TOP, duration=2000)
                return
            iface = self.cb_iface.currentText()
            speed = int(self.cb_speed.currentText())
            channel = self.sp_channel.value()
            # 持久化用户选择
            self._cfg.set("target_mcu", target)
            self._cfg.set("interface", iface)
            self._cfg.set("speed_khz", speed)
            self._cfg.set("rtt_channel", channel)
            self._worker.connect_requested.emit(target, iface, speed, channel)
        else:
            self._worker.disconnect_requested.emit()

    def _on_channel_changed(self, ch: int) -> None:
        self._cfg.set("rtt_channel", ch)
        self._worker.set_rtt_channel_requested.emit(ch)

    def _on_send_clicked(self) -> None:
        text = self.le_send.text()
        if not text:
            return
        self._worker.send_data_requested.emit(text, self.chk_hex.isChecked())
        # 加入历史
        hist = list(self._cfg.get("send_history"))
        if text in hist:
            hist.remove(text)
        hist.append(text)
        self._cfg.set("send_history", hist)

    def _on_state_changed(self, connected: bool, _info: dict) -> None:
        self.btn_connect.setText("断开" if connected else "连接")
        self.btn_reset.setEnabled(connected)
        self.btn_send.setEnabled(connected)
        self.chk_power.setEnabled(connected)

    def _on_rtt_data(self, text: str) -> None:
        # 自动滚动判断必须在插入文本前
        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.End)
        for seg, attrs in parse_ansi(text):
            cursor.insertText(seg, self._fmt(attrs))

        if at_bottom and self.chk_auto_scroll.isChecked():
            sb.setValue(sb.maximum())

    def _fmt(self, attrs: AnsiAttrs) -> QTextCharFormat:
        fmt = QTextCharFormat()
        if attrs.fg:
            fmt.setForeground(QColor(_ANSI_COLOR_MAP.get(attrs.fg, "#dddddd")))
        if attrs.bg:
            fmt.setBackground(QColor(_ANSI_COLOR_MAP.get(attrs.bg, "#222222")))
        if attrs.bold:
            f = fmt.font()
            f.setBold(True)
            fmt.setFont(f)
        return fmt

    def _apply_font(self, family: str, size: int) -> None:
        font = QFont(family, size)
        if font.family() != family:
            # 字体回落到等宽字体
            font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            font.setPointSize(size)
        self.display.setFont(font)
