"""RTT 监控页：控制栏 + 选项栏 + 显示区 + 搜索栏 + 发送栏。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QTextCharFormat, QTextCursor, QColor
from PySide6.QtWidgets import (
    QCompleter,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    EditableComboBox,
    FluentIcon,
    HeaderCardWidget,
    InfoBar,
    InfoBarPosition,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    TransparentToolButton,
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

        # RTT 数据节流批量渲染：每收到数据先入缓冲，50ms 刷一次屏
        self._rtt_buffer: list[str] = []
        self._rtt_flush_timer = QTimer(self)
        self._rtt_flush_timer.setInterval(50)
        self._rtt_flush_timer.setSingleShot(False)
        self._rtt_flush_timer.timeout.connect(self._flush_rtt_buffer)
        self._rtt_flush_timer.start()

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
        chip_list = self._cfg.get_chip_list()
        self.cb_target.addItems(chip_list)
        last_mcu = self._cfg.get("target_mcu")
        if last_mcu:
            self.cb_target.setCurrentText(last_mcu)
        self.cb_target.setMinimumWidth(180)
        # 自动补全：不区分大小写、子串匹配
        completer = QCompleter(chip_list, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.cb_target.setCompleter(completer)
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

        # ---- 设备信息卡片（可展开/收起）----
        self.gb_info = HeaderCardWidget(self)
        self.gb_info.setTitle("设备信息")

        # 在标题栏右侧加展开/收起按钮
        self.btn_info_toggle = TransparentToolButton(FluentIcon.CHEVRON_DOWN_MED, self.gb_info)
        self.gb_info.headerLayout.addStretch(1)
        self.gb_info.headerLayout.addWidget(self.btn_info_toggle)

        # 内容容器
        self._info_container = QWidget(self.gb_info)
        info_grid = QGridLayout(self._info_container)
        info_grid.setHorizontalSpacing(16)
        info_grid.setVerticalSpacing(6)
        self._info_labels: dict[str, StrongBodyLabel] = {}
        rows = [
            ("固件版本", "jlink_firmware"),
            ("硬件版本", "jlink_hardware"),
            ("序列号", "jlink_serial"),
            ("核心名称", "core_name"),
            ("核心 ID", "core_id"),
            ("CPU 类型", "core_cpu"),
            ("目标设备", "target_device"),
            ("接口", "interface"),
            ("速度 (kHz)", "speed_khz"),
        ]
        for i, (text, key) in enumerate(rows):
            r, c = divmod(i, 3)
            info_grid.addWidget(BodyLabel(f"{text}:"), r, c * 2)
            lbl = StrongBodyLabel("-")
            self._info_labels[key] = lbl
            info_grid.addWidget(lbl, r, c * 2 + 1)
        self.gb_info.viewLayout.addWidget(self._info_container)

        # 默认收起：隐藏内容容器、分隔线和 view
        self._info_container.setVisible(False)
        self.gb_info.separator.setVisible(False)
        self.gb_info.view.setVisible(False)

        # 点击按钮切换展开/收起
        self.btn_info_toggle.clicked.connect(self._toggle_info_card)

        root.addWidget(self.gb_info)

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

        # ---- 显示区（qfluentwidgets PlainTextEdit 自动适应主题）----
        self.display = PlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setMaximumBlockCount(self._cfg.get("max_display_lines"))
        self.display.setLineWrapMode(PlainTextEdit.NoWrap)
        root.addWidget(self.display, 1)

        # ---- 搜索栏 ----
        try:
            from qfluentwidgets import SearchLineEdit
            self.le_search = SearchLineEdit(self)
        except (ImportError, AttributeError):
            from PySide6.QtWidgets import QLineEdit
            self.le_search = QLineEdit(self)
        srch = QHBoxLayout()
        self.le_search.setPlaceholderText("搜索日志…")
        self.btn_prev = PushButton("↑", self)
        self.btn_next = PushButton("↓", self)
        self.lbl_match = QLabel("0/0")
        srch.addWidget(self.le_search, 1)
        srch.addWidget(self.btn_prev)
        srch.addWidget(self.btn_next)
        srch.addWidget(self.lbl_match)
        root.addLayout(srch)

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

        # 显式 QueuedConnection：worker 线程 → 主线程槽，避免 PySide6 在
        # 「emit 调用从 native threading.Thread 发起」场景下误判 sender thread 走 DirectConnection。
        self._worker.rtt_data_received.connect(self._on_rtt_data, Qt.QueuedConnection)
        self._worker.connection_state_changed.connect(self._on_state_changed, Qt.QueuedConnection)

        self._cfg.font_changed.connect(self._apply_font)
        self._cfg.max_display_lines_changed.connect(self.display.setMaximumBlockCount)

        # 日志记录
        self.chk_log_rec.toggled.connect(self._on_log_recording_toggled)
        # 保存当前
        self.btn_save.clicked.connect(self._on_save_clicked)
        # 搜索
        self.btn_prev.clicked.connect(lambda: self._do_search(backward=True))
        self.btn_next.clicked.connect(lambda: self._do_search(backward=False))
        self.le_search.returnPressed.connect(lambda: self._do_search(backward=False))
        self.le_search.textChanged.connect(self._update_match_count)

        # 命令结果（错误提示）—— 同样显式 QueuedConnection
        self._worker.command_result.connect(self._on_command_result, Qt.QueuedConnection)
        self._worker.log_message.connect(self._on_log_message, Qt.QueuedConnection)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _toggle_info_card(self) -> None:
        self._set_info_expanded(not self._info_container.isVisible())

    def _set_info_expanded(self, expanded: bool) -> None:
        self._info_container.setVisible(expanded)
        self.gb_info.separator.setVisible(expanded)
        self.gb_info.view.setVisible(expanded)
        self.btn_info_toggle.setIcon(
            FluentIcon.UP if expanded else FluentIcon.CHEVRON_DOWN_MED
        )

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
            # 立即给 UI 反馈：禁用按钮 + 改文字
            self.btn_connect.setEnabled(False)
            self.btn_connect.setText("连接中…")
            self._worker.connect_requested.emit(target, iface, speed, channel)
        else:
            # 乐观立即恢复 UI：worker 内部 try/except 全包，disconnect 不会失败。
            # 不依赖跨线程 connection_state_changed 信号 round-trip，避免信号未到时 UI 卡死。
            # worker 实际跑完后回 _on_state_changed(False, {}) 仍会被调用，幂等无害。
            self._worker.disconnect_requested.emit()
            self._set_disconnected_ui()

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

    def _on_state_changed(self, connected: bool, info: dict) -> None:
        if connected:
            self._set_connected_ui(info)
        else:
            self._set_disconnected_ui()

    def _set_connected_ui(self, info: dict) -> None:
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("断开")
        self.btn_reset.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.chk_power.setEnabled(True)
        for key, lbl in self._info_labels.items():
            lbl.setText(str(info.get(key, "-")))
        # 连接成功后自动展开设备信息卡片
        if not self._info_container.isVisible():
            self._set_info_expanded(True)

    def _set_disconnected_ui(self) -> None:
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("连接")
        self.btn_reset.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.chk_power.setEnabled(False)
        for lbl in self._info_labels.values():
            lbl.setText("-")

    def _on_rtt_data(self, text: str) -> None:
        """只入缓冲，由 _flush_rtt_buffer 定时批量渲染。"""
        self._rtt_buffer.append(text)

    def _flush_rtt_buffer(self) -> None:
        """每 50ms 合并所有积压数据一次性 insertText，极大减少 layout 重算次数。"""
        if not self._rtt_buffer:
            return
        merged = "".join(self._rtt_buffer)
        self._rtt_buffer.clear()

        # 自动滚动判断必须在插入文本前
        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.End)
        for seg, attrs in parse_ansi(merged):
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

    # ------------------------------------------------------------------
    # 日志记录 / 保存当前 / 搜索 / 错误提示
    # ------------------------------------------------------------------
    def _on_log_recording_toggled(self, checked: bool) -> None:
        if checked:
            from core.logger import get_log_dir
            log_dir = self._cfg.get("log_dir") or str(get_log_dir())
            self._worker.start_log_recording_requested.emit(log_dir)
        else:
            self._worker.stop_log_recording_requested.emit()

    def _on_save_clicked(self) -> None:
        from datetime import datetime
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        default_name = f"rtt_snapshot_{datetime.now():%Y%m%d_%H%M%S}.log"
        path, _ = QFileDialog.getSaveFileName(self, "保存当前显示", default_name, "Log files (*.log);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(self.display.toPlainText(), encoding="utf-8")
            InfoBar.success("已保存", path, parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
        except Exception as e:
            InfoBar.error("保存失败", str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)

    def _do_search(self, backward: bool) -> None:
        text = self.le_search.text()
        if not text:
            return
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindBackward
        if not self.display.find(text, flags):
            # 回卷
            cursor = self.display.textCursor()
            cursor.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.display.setTextCursor(cursor)
            self.display.find(text, flags)

    def _update_match_count(self, text: str) -> None:
        if not text:
            self.lbl_match.setText("0/0")
            return
        # 简单计数
        cnt = self.display.toPlainText().count(text)
        self.lbl_match.setText(f"-/{cnt}")

    def _on_command_result(self, cmd: str, ok: bool, payload: dict) -> None:
        if ok:
            return
        err = payload.get("error", "未知错误")
        InfoBar.warning(cmd, err, parent=self,
                        position=InfoBarPosition.TOP, duration=3000)

    def _on_log_message(self, level: str, msg: str) -> None:
        if level == "error":
            InfoBar.error("错误", msg, parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
