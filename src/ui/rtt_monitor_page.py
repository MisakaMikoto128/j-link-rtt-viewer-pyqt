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

from . import _infobar


_FONT_SIZE_MIN = 8
_FONT_SIZE_MAX = 32


def _human_bytes(n: int) -> str:
    """1234 → '1.2 KB'；< 1024 不缩。"""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


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

        # 注意：RTT 节流在 worker 侧做（_rtt_drain_timer 50ms 合并 emit），
        # UI 收到的已经是合并好的批量数据，直接 insertText，不再加一层 timer。

        # 搜索匹配数节流：textChanged 每按键全 buffer 扫描太重，200ms 单次延迟
        self._match_count_timer = QTimer(self)
        self._match_count_timer.setSingleShot(True)
        self._match_count_timer.setInterval(200)
        self._match_count_timer.timeout.connect(self._do_update_match_count)

        # 状态栏统计：1s 一次从 worker 同步取吞吐，UI 端算 delta 显示字节/秒
        self._stats_prev_bytes = 0
        self._stats_prev_lines = 0
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start()
        # 初始化编码状态显示
        self._update_encoding_label(cfg.get("rtt_encoding") or "utf-8")
        cfg.rtt_encoding_changed.connect(self._update_encoding_label)

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

        self.btn_connect = PrimaryPushButton(FluentIcon.LINK, "连接", self)
        self.btn_reset = PushButton(FluentIcon.SYNC, "重置目标", self)
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
        # 字号调整按钮（替代 Ctrl+滚轮，避免和滚动冲突）
        self.btn_font_minus = PushButton("A−", self)
        self.btn_font_minus.setFixedWidth(36)
        self.btn_font_minus.setToolTip("字号 −1")
        self.lbl_font_size = BodyLabel(f"{self._cfg.get('font_size')}")
        self.lbl_font_size.setAlignment(Qt.AlignCenter)
        self.lbl_font_size.setFixedWidth(28)
        self.btn_font_plus = PushButton("A+", self)
        self.btn_font_plus.setFixedWidth(36)
        self.btn_font_plus.setToolTip("字号 +1")
        # 插入会话标记：输入框 + 按钮，在 RTT 显示区插入分隔行
        self.le_mark = EditableComboBox(self)
        self.le_mark.setPlaceholderText("会话标记文本…")
        self.le_mark.setMinimumWidth(180)
        # 最近 10 条标记历史（不持久化，会话内可重用）
        self._mark_history: list[str] = []
        self.btn_mark = PushButton("插入标记", self)
        self.btn_mark.setToolTip("在显示区插入一行分隔标记，便于会话分段")
        self.btn_clear = PushButton("清除", self)
        self.btn_save = PushButton("💾 保存当前", self)
        opt.addWidget(self.chk_auto_scroll)
        opt.addWidget(self.chk_pause)
        opt.addWidget(self.chk_power)
        opt.addWidget(self.chk_log_rec)
        opt.addStretch(1)
        opt.addWidget(self.le_mark)
        opt.addWidget(self.btn_mark)
        opt.addWidget(self.btn_font_minus)
        opt.addWidget(self.lbl_font_size)
        opt.addWidget(self.btn_font_plus)
        opt.addWidget(self.btn_clear)
        opt.addWidget(self.btn_save)
        root.addLayout(opt)

        # ---- 显示区（qfluentwidgets PlainTextEdit 自动适应主题）----
        self.display = PlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setMaximumBlockCount(self._cfg.get("max_display_lines"))
        # 固定宽度按窗口宽度换行（超过窗宽自动 wrap，便于阅读长行日志）
        self.display.setLineWrapMode(PlainTextEdit.WidgetWidth)
        # 允许压缩到 80px，避免子控件 sizeHint 累积导致主窗口 mintrack 过大
        # （Windows 最大化时底部被任务栏遮挡 → 搜索栏/发送栏看不见）
        self.display.setMinimumHeight(80)
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
        # EditableComboBox 复用 cfg.send_history（最近 50 条）下拉快速重发
        self.le_send = EditableComboBox(self)
        self.le_send.setPlaceholderText("输入要发送的数据 (Hex 模式下用 16 进制字符)")
        # 加载历史（倒序：最新在最前）
        history = list(self._cfg.get("send_history") or [])
        if history:
            self.le_send.addItems(list(reversed(history)))
            self.le_send.setCurrentText("")  # 不预选任何项
        self.chk_hex = CheckBox("Hex")
        self.chk_hex.setChecked(self._cfg.get("hex_send_mode"))
        self.btn_send = PushButton(FluentIcon.SEND, "发送", self)
        self.btn_send.setEnabled(False)
        send.addWidget(self.le_send, 1)
        send.addWidget(self.chk_hex)
        send.addWidget(self.btn_send)
        root.addLayout(send)

        # ---- 底部状态栏 ----
        status = QHBoxLayout()
        status.setContentsMargins(0, 0, 0, 0)
        self.lbl_status_state = BodyLabel("● 未连接")
        self.lbl_status_state.setStyleSheet("color: #888888;")
        self.lbl_status_state.setMinimumWidth(120)
        self.lbl_status_rate = BodyLabel("")
        self.lbl_status_rate.setMinimumWidth(160)
        self.lbl_status_total = BodyLabel("")
        self.lbl_status_total.setMinimumWidth(200)
        self.lbl_status_encoding = BodyLabel("")
        status.addWidget(self.lbl_status_state)
        status.addWidget(self.lbl_status_rate)
        status.addWidget(self.lbl_status_total)
        status.addStretch(1)
        status.addWidget(self.lbl_status_encoding)
        root.addLayout(status)

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

        # 字号 ± 按钮：直接走 cfg.set → font_changed → _apply_font
        self.btn_font_minus.clicked.connect(lambda: self._adjust_font_size(-1))
        self.btn_font_plus.clicked.connect(lambda: self._adjust_font_size(+1))

        # 插入会话标记（点按钮触发；EditableComboBox 不暴露 lineEdit，
        # Enter 键需通过 ComboBox.activated 信号——但 fluent 实现略有差异，
        # 用户用按钮足够）
        self.btn_mark.clicked.connect(self._on_insert_mark)

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
                _infobar.warn(self, "提示", "请先选择目标芯片")
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
            # 先恢复 UI 再 emit：万一 emit 异常或被堵也不影响按钮已经切回"连接"。
            # worker 内部 _do_disconnect 全部 try/except，不会失败。
            # 跨线程 connection_state_changed 信号回来时走 _on_state_changed → _set_disconnected_ui()，
            # 幂等无害。
            self._set_disconnected_ui()
            self._worker.disconnect_requested.emit()

    def _on_channel_changed(self, ch: int) -> None:
        self._cfg.set("rtt_channel", ch)
        self._worker.set_rtt_channel_requested.emit(ch)

    def _on_send_clicked(self) -> None:
        text = self.le_send.currentText()
        if not text:
            return
        self._worker.send_data_requested.emit(text, self.chk_hex.isChecked())
        # 加入历史（去重 + 末尾追加）
        hist = list(self._cfg.get("send_history"))
        if text in hist:
            hist.remove(text)
        hist.append(text)
        self._cfg.set("send_history", hist)
        # 同步刷新下拉项：最新在最前
        self.le_send.blockSignals(True)
        self.le_send.clear()
        self.le_send.addItems(list(reversed(hist)))
        self.le_send.setCurrentText("")
        self.le_send.blockSignals(False)

    def _on_state_changed(self, connected: bool) -> None:
        if connected:
            # 同步从 worker 取 device_info（lock 保护，不走跨线程 dict signal）
            info = self._worker.get_device_info()
            self._set_connected_ui(info)
        else:
            self._set_disconnected_ui()

    def _set_connected_ui(self, info: dict) -> None:
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("断开")
        self.btn_connect.setIcon(FluentIcon.CLOSE)
        self.btn_reset.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.chk_power.setEnabled(True)
        for key, lbl in self._info_labels.items():
            lbl.setText(str(info.get(key, "-")))
        # 设备信息卡片保持当前折叠/展开状态，不自动展开
        # （用户可以点击右上 ⌄ 按钮手动展开）
        # 状态栏：绿色圆点 + 设备摘要
        target = info.get("target_device", "—")
        iface = info.get("interface", "—")
        speed = info.get("speed_khz", "—")
        self.lbl_status_state.setText(f"● 已连接 {target}")
        self.lbl_status_state.setStyleSheet("color: #2ecc71;")
        # 卡片标题加摘要
        self.gb_info.setTitle(f"设备信息 — {target} / {iface} / {speed} kHz")

    def _set_disconnected_ui(self) -> None:
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("连接")
        self.btn_connect.setIcon(FluentIcon.LINK)
        self.btn_reset.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.chk_power.setEnabled(False)
        for lbl in self._info_labels.values():
            lbl.setText("-")
        # 状态栏复位
        self.lbl_status_state.setText("● 未连接")
        self.lbl_status_state.setStyleSheet("color: #888888;")
        self.lbl_status_rate.setText("")
        self.lbl_status_total.setText("")
        self.gb_info.setTitle("设备信息")
        # 清除上次统计 delta 基线
        self._stats_prev_bytes = 0
        self._stats_prev_lines = 0

    def _update_stats(self) -> None:
        """1s 一次：从 worker 同步取吞吐，UI 端算 delta 显示。"""
        if not hasattr(self, "lbl_status_rate"):
            return
        total_b, total_l, start_ts = self._worker.get_stats()
        if start_ts == 0:
            self.lbl_status_rate.setText("")
            self.lbl_status_total.setText("")
            self._stats_prev_bytes = 0
            self._stats_prev_lines = 0
            return
        delta_b = max(0, total_b - self._stats_prev_bytes)
        delta_l = max(0, total_l - self._stats_prev_lines)
        self._stats_prev_bytes = total_b
        self._stats_prev_lines = total_l
        self.lbl_status_rate.setText(f"{_human_bytes(delta_b)}/s · {delta_l} 行/s")
        # 总字节 + 会话时长
        import time as _t
        secs = int(_t.time() - start_ts) if start_ts > 0 else 0
        hh, mm, ss = secs // 3600, (secs % 3600) // 60, secs % 60
        duration = f"{hh:02d}:{mm:02d}:{ss:02d}"
        self.lbl_status_total.setText(f"总 {_human_bytes(total_b)} · {total_l} 行 · {duration}")

    def _update_encoding_label(self, encoding: str) -> None:
        if hasattr(self, "lbl_status_encoding"):
            self.lbl_status_encoding.setText(f"编码: {encoding}")

    def _on_rtt_data(self, text: str) -> None:
        """worker 已经 50ms 合并好，直接 insertText。"""
        if not text:
            return
        # 自动滚动判断必须在插入文本前
        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.End)
        for seg, attrs in parse_ansi(text):
            cursor.insertText(seg, self._fmt(attrs))

        if at_bottom and self.chk_auto_scroll.isChecked():
            sb.setValue(sb.maximum())

    def _on_insert_mark(self) -> None:
        """在显示区追加一行视觉分隔的会话标记。

        格式：``──── 用户输入文本 ────`` 整行用 bright_yellow + bold。
        不带时间戳（用户明确要求）。空输入时插入纯分隔行 ───────。
        """
        text = self.le_mark.currentText().strip()
        if text:
            if text in self._mark_history:
                self._mark_history.remove(text)
            self._mark_history.append(text)
            self._mark_history = self._mark_history[-10:]
            self.le_mark.clear()
            self.le_mark.addItems(reversed(self._mark_history))

        line = f"──── {text} ────" if text else "─" * 50

        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.End)
        if cursor.columnNumber() != 0:
            cursor.insertText("\n")
        cursor.insertText(line + "\n", self._fmt(AnsiAttrs(fg="bright_yellow", bold=True)))

        if at_bottom:
            sb.setValue(sb.maximum())
        self.le_mark.clearEditText()

    def _fmt(self, attrs: AnsiAttrs) -> QTextCharFormat:
        fmt = QTextCharFormat()
        if attrs.fg:
            fmt.setForeground(QColor(_ANSI_COLOR_MAP.get(attrs.fg, "#dddddd")))
        if attrs.bg:
            fmt.setBackground(QColor(_ANSI_COLOR_MAP.get(attrs.bg, "#222222")))
        if attrs.bold:
            # 用 setFontWeight 而非 setFont(fmt.font())——后者会把字号也设回
            # QTextCharFormat 默认值（通常远小于 widget 字号），导致 bold
            # 段落字号被缩水。setFontWeight 只改 weight，字号继承 widget。
            fmt.setFontWeight(QFont.Bold)
        return fmt

    def _apply_font(self, family: str, size: int) -> None:
        font = QFont(family, size)
        if font.family() != family:
            # 字体回落到等宽字体
            font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            font.setPointSize(size)
        self.display.setFont(font)
        # 同步右上角字号显示
        if hasattr(self, "lbl_font_size"):
            self.lbl_font_size.setText(str(size))

    def _adjust_font_size(self, delta: int) -> None:
        cur = int(self._cfg.get("font_size"))
        new = max(_FONT_SIZE_MIN, min(_FONT_SIZE_MAX, cur + delta))
        if new != cur:
            self._cfg.set("font_size", new)

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
            _infobar.ok(self, "已保存", path)
        except Exception as e:
            _infobar.err(self, "保存失败", str(e))

    def _do_search(self, backward: bool) -> None:
        text = self.le_search.text()
        if not text:
            return
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindBackward
        found = self.display.find(text, flags)
        if not found:
            # 回卷一次
            cursor = self.display.textCursor()
            cursor.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.display.setTextCursor(cursor)
            found = self.display.find(text, flags)
        # 更新 "第 N 个 / 总数" 标签
        self._update_match_position(text)

    def _update_match_count(self, text: str) -> None:
        """textChanged 信号槽：节流到 200ms 再算计数，避免大日志按键卡顿。"""
        if not text:
            self.lbl_match.setText("0/0")
            self._match_count_timer.stop()
            return
        # 重启 timer：连续按键期间一直延后到 200ms 静止后才算
        self._match_count_timer.start()

    def _do_update_match_count(self) -> None:
        self._update_match_position(self.le_search.text())

    def _update_match_position(self, text: str) -> None:
        """显示 "当前第 N 个 / 总数"，并把全部匹配位置叠黄色 ExtraSelection。"""
        if not text:
            self.lbl_match.setText("0/0")
            self.display.setExtraSelections([])
            return
        full = self.display.toPlainText()
        cnt = full.count(text)
        if cnt == 0:
            self.lbl_match.setText("0/0")
            self.display.setExtraSelections([])
            return
        cursor = self.display.textCursor()
        cur_pos = cursor.selectionStart()
        before = full.count(text, 0, max(0, cur_pos))
        if cursor.hasSelection() and full[cursor.selectionStart():cursor.selectionEnd()] == text:
            idx = before + 1
        else:
            idx = before
        self.lbl_match.setText(f"{idx}/{cnt}")
        self._highlight_all_matches(text, full, limit=500)

    def _highlight_all_matches(self, needle: str, full: str, limit: int = 500) -> None:
        """所有匹配位置叠浅黄色背景。超过 limit 截断（500 个足够直观，再多无意义）。"""
        from PySide6.QtWidgets import QTextEdit
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 235, 100, 140))
        selections: list = []
        pos = 0
        nlen = len(needle)
        while len(selections) < limit:
            i = full.find(needle, pos)
            if i < 0:
                break
            c = QTextCursor(self.display.document())
            c.setPosition(i)
            c.setPosition(i + nlen, QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = fmt
            selections.append(sel)
            pos = i + nlen
        self.display.setExtraSelections(selections)

    # 命令内部名 → 用户可读标题
    _CMD_TITLES = {
        "send_data": "发送失败",
        "reset": "重置失败",
        "power_output": "电源切换失败",
        "read_memory": "读取内存失败",
        "log_recording": "日志记录失败",
    }

    def _on_command_result(self, cmd: str, ok: bool, msg: str) -> None:
        if ok:
            return
        title = self._CMD_TITLES.get(cmd, "操作失败")
        _infobar.warn(self, title, msg or "未知错误", duration=3000)

    def _on_log_message(self, level: str, msg: str) -> None:
        """worker → UI 日志投递。level: error/warning/info。"""
        if level == "error":
            _infobar.err(self, "错误", msg)
            # 兜底：连接路径异常时按钮可能卡在"连接中…"，这里强制恢复
            if self.btn_connect.text() == "连接中…":
                self._set_disconnected_ui()
        elif level == "warning":
            _infobar.warn(self, "警告", msg)
        # info 级别只进 logger 文件，不弹 toast——避免噪音
