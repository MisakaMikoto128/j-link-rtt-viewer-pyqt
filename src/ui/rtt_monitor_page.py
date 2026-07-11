"""RTT 监控页：控制栏 + 选项栏 + 显示区 + 搜索栏 + 发送栏。"""
from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontDatabase,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
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
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    ToolButton,
    ToolTipFilter,
    TransparentToolButton,
    themeColor,
)

from core.ansi_parser import AnsiAttrs, parse_ansi
from core.config_service import ConfigService
from core.crc_utils import CRC_ALGORITHMS, compute_crc
from core.jlink_worker import RESET_MODE_HALT, JLinkWorker

from . import _infobar
from .widgets.search_bar import SearchBar


_FONT_SIZE_MIN = 8
_FONT_SIZE_MAX = 32


def _tip(widget: QWidget, text: str, duration: int = 300) -> None:
    """给控件安装 QFluentWidgets 风格的 tooltip（圆角 + 阴影）。"""
    widget.setToolTip(text)
    widget.installEventFilter(ToolTipFilter(widget, duration))


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

# 预构造 QColor：_fmt 在 RTT 高吞吐场景下每段都调，QColor(hex_string) 每次都要解析
# 字符串 + 申请对象。模块加载时一次性把 16 个调色板色 + 两个默认色都建好，热路径只查 dict。
_ANSI_QCOLORS: dict[str, QColor] = {k: QColor(v) for k, v in _ANSI_COLOR_MAP.items()}
_DEFAULT_FG_QCOLOR = QColor("#dddddd")
_DEFAULT_BG_QCOLOR = QColor("#222222")


def _section_separator(parent: QWidget) -> QFrame:
    """创建一条水平分隔线，用于左侧面板区域划分。"""
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    line.setStyleSheet("QFrame { color: rgba(128,128,128,0.25); max-height: 1px; }")
    return line


class _VResizeHandle(QFrame):
    """display 下方的水平拖动条 —— 极简观感，跟随主题色。

    6px 命中区（够大好抓）+ 1px 中央细灰线（默认几乎不可见）；hover/拖动
    时变 2px 主题色线（用 qfluentwidgets.themeColor()，自动跟用户偏好）。

    为什么不用 QSplitter：splitter 在 QScrollArea 里只能在 viewport 内
    分配 children，display 永远拖不到比 viewport 大 —— 无法触发整页滚。
    """

    heightChanged = Signal(int)  # 拖动结束 emit 最终高度（持久化用）

    _MIN_TARGET_H = 120

    def __init__(self, target: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = target
        self._dragging = False
        self._hover = False
        self._start_y = 0.0
        self._start_h = 0
        self.setFixedHeight(6)
        self.setCursor(Qt.SizeVerCursor)
        # 用 paintEvent 自绘，stylesheet 留空避免 QSS 引擎干扰 paint

    def enterEvent(self, e: QEnterEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e: QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self._dragging or self._hover:
            color = QColor(themeColor())
            color.setAlpha(220 if self._dragging else 150)
            thickness = 2
        else:
            color = QColor(128, 128, 128, 45)
            thickness = 1
        y = (h - thickness) // 2
        p.fillRect(0, y, w, thickness, color)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._start_y = e.globalPosition().y()
            self._start_h = self._target.height()
            self.update()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            dy = int(e.globalPosition().y() - self._start_y)
            new_h = max(self._MIN_TARGET_H, self._start_h + dy)
            self._target.setFixedHeight(new_h)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self.heightChanged.emit(self._target.height())
            self.update()
            e.accept()


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

        # 自动滚动状态：True 表示 sb.setValue 由程序触发（autoscroll 跟新数据），
        # False 表示用户手动滚动。区分两者用来同步 chk_auto_scroll 复选框。
        # 用法：with self._programmatic_scroll_guard(): sb.setValue(...)
        self._programmatic_scroll = False

        # 真状态（worker 端的连接状态镜像）。按钮文字是呈现，不能当状态判断；
        # 由 _on_state_changed 维护。
        self._is_connected = False

        # 复位计数：连接成功时重置为 0，每次 reset 操作 +1
        self._reset_count = 0

        # 自动断帧：上次接收数据的时间戳
        import time as _time_mod
        self._last_rx_time: float = 0.0
        self._time_mod = _time_mod

        # 定时发送：QTimer + pending 标志（未连接时勾选 → 连接后自动启动）
        self._timed_send_pending = False
        self._timed_send_timer = QTimer(self)
        self._timed_send_timer.timeout.connect(self._on_timed_send_fire)

        # 按当前 reset_mode 设置按钮文字 + 订阅 cfg 变化实时刷新
        self._apply_reset_mode_to_button(cfg.get("reset_mode"))
        cfg.reset_mode_changed.connect(self._apply_reset_mode_to_button)

    @contextmanager
    def _programmatic_scroll_guard(self):
        """围栏：with 块内的 sb.setValue 不会触发 _on_display_scrolled 取消勾选。"""
        self._programmatic_scroll = True
        try:
            yield
        finally:
            self._programmatic_scroll = False

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 主布局：左右分栏 ----
        main_split = QHBoxLayout()
        main_split.setContentsMargins(0, 0, 0, 0)
        main_split.setSpacing(0)

        # ==== 左侧配置面板（固定宽度，内部可滚动）====
        self._config_visible = True
        self._config_panel = self._build_left_panel()
        main_split.addWidget(self._config_panel)

        # ==== 右侧数据区（stretch=1，占满剩余空间）====
        self._right_panel = self._build_right_panel()
        main_split.addWidget(self._right_panel, 1)

        # ==== 收起态垂直图标条（默认隐藏，替代配置面板）====
        # 必须在 _build_right_panel 之后，因为引用了 self.display
        self._icon_bar = self._build_icon_bar()
        self._icon_bar.setVisible(False)
        # 插入到 config_panel 和 right_panel 之间
        main_split.insertWidget(1, self._icon_bar)

        outer.addLayout(main_split)

        # 窗口 resize 防抖 timer
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self._on_resize_debounce)

    def _build_icon_bar(self) -> QWidget:
        """收起态垂直图标条：固定 48px 宽，垂直排列常用操作按钮。

        发送按钮不在图标条中——它位于右侧面板的发送区右侧，
        与参考 UI（图3）一致：发送按钮在接收区和发送区之间。
        """
        bar = QWidget(self)
        bar.setObjectName("iconBar")
        bar.setFixedWidth(48)
        bar.setStyleSheet(
            "QWidget#iconBar { background: transparent; "
            "border-right: 1px solid rgba(128,128,128,0.15); }")
        v = QVBoxLayout(bar)
        v.setContentsMargins(6, 12, 6, 12)
        v.setSpacing(8)

        self._btn_quick_pause = TransparentToolButton(FluentIcon.PAUSE, bar)
        self._btn_quick_pause.setFixedSize(36, 36)
        _tip(self._btn_quick_pause, "暂停/恢复接收")
        self._btn_quick_pause.setCheckable(True)
        self._btn_quick_pause.toggled.connect(
            self._worker.set_pause_receive_requested.emit)
        # 双向同步工具栏暂停按钮
        self._btn_quick_pause.toggled.connect(
            lambda c: hasattr(self, 'btn_toolbar_pause') and
            self.btn_toolbar_pause.setChecked(c))
        if hasattr(self, 'btn_toolbar_pause'):
            self.btn_toolbar_pause.toggled.connect(self._btn_quick_pause.setChecked)
        v.addWidget(self._btn_quick_pause, 0, Qt.AlignHCenter)

        self._btn_quick_clear = TransparentToolButton(FluentIcon.DELETE, bar)
        self._btn_quick_clear.setFixedSize(36, 36)
        _tip(self._btn_quick_clear, "清除显示")
        self._btn_quick_clear.clicked.connect(self.display.clear)
        v.addWidget(self._btn_quick_clear, 0, Qt.AlignHCenter)

        self._btn_quick_save = TransparentToolButton(FluentIcon.SAVE, bar)
        self._btn_quick_save.setFixedSize(36, 36)
        _tip(self._btn_quick_save, "保存当前")
        self._btn_quick_save.clicked.connect(self._on_save_clicked)
        v.addWidget(self._btn_quick_save, 0, Qt.AlignHCenter)

        v.addStretch(1)
        return bar
    # ------------------------------------------------------------------
    # 左侧配置面板
    # ------------------------------------------------------------------
    def _build_left_panel(self) -> QWidget:
        """构建左侧配置面板，返回容器 widget。

        布局分为四个区域，用水平分隔线隔开：
          1. 连接设置  — 目标设备 / 接口速度 / 通道 / 连接按钮 / 复位
          2. 设备信息  — 可折叠卡片
          3. 接收设置  — 滚动 / 暂停 / 电源 / 日志 / HEX显示 / 自动断帧
          4. 发送设置  — 定时发送 / CRC脚本 / 字号 / 标记 / 清除保存
        """
        panel = QWidget(self)
        panel.setObjectName("configPanel")
        panel.setFixedWidth(260)
        panel.setStyleSheet(
            "QWidget#configPanel { background: transparent; }"
        )

        scroll = QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        vp = scroll.viewport()
        vp.setObjectName("configVP")
        vp.setStyleSheet("QWidget#configVP { background: transparent; }")

        inner = QWidget()
        inner.setObjectName("configInner")
        inner.setStyleSheet(
            "QWidget#configInner { background: transparent; }")
        scroll.setWidget(inner)

        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(scroll)

        v = QVBoxLayout(inner)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        # ════════════════════════════════════════════════════════════
        # 区域 1：连接设置
        # ════════════════════════════════════════════════════════════
        _lbl_conn = StrongBodyLabel("连接设置")
        _lbl_conn.setStyleSheet("font-size: 13px; margin-top: 2px;")
        v.addWidget(_lbl_conn)

        self.cb_target = EditableComboBox(inner)
        self.cb_target.setPlaceholderText("目标设备")
        chip_list = self._cfg.get_chip_list()
        self.cb_target.addItems(chip_list)
        last_mcu = self._cfg.get("target_mcu")
        if last_mcu:
            self.cb_target.setCurrentText(last_mcu)
        completer = QCompleter(chip_list, self.cb_target)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.cb_target.setCompleter(completer)
        v.addWidget(self.cb_target)

        row_iface = QHBoxLayout()
        row_iface.setSpacing(6)
        row_iface.addWidget(BodyLabel("接口"))
        self.cb_iface = ComboBox(inner)
        self.cb_iface.addItems(["SWD", "JTAG"])
        self.cb_iface.setCurrentText(self._cfg.get("interface"))
        row_iface.addWidget(self.cb_iface)
        row_iface.addSpacing(12)
        row_iface.addWidget(BodyLabel("速度"))
        self.cb_speed = ComboBox(inner)
        for s in self._cfg.get_default_speeds():
            self.cb_speed.addItem(str(s))
        cur_speed = str(self._cfg.get("speed_khz"))
        if self.cb_speed.findText(cur_speed) < 0:
            self.cb_speed.addItem(cur_speed)
        self.cb_speed.setCurrentText(cur_speed)
        row_iface.addWidget(self.cb_speed)
        v.addLayout(row_iface)

        row_ch = QHBoxLayout()
        row_ch.setSpacing(6)
        row_ch.addWidget(BodyLabel("RTT 通道"))
        self.sp_channel = SpinBox(inner)
        self.sp_channel.setRange(0, 15)
        self.sp_channel.setValue(self._cfg.get("rtt_channel"))
        row_ch.addWidget(self.sp_channel)
        row_ch.addStretch(1)
        v.addLayout(row_ch)

        self.btn_connect = PrimaryPushButton(FluentIcon.PLAY, "连接", inner)
        _tip(self.btn_connect, "F2 连接 / F3 断开")
        v.addWidget(self.btn_connect)

        row_reset = QHBoxLayout()
        row_reset.setSpacing(6)
        self.btn_reset = PushButton(FluentIcon.SYNC, "重置目标", inner)
        _tip(self.btn_reset, "F4 重置目标")
        self.btn_reset.setEnabled(False)
        self.btn_reset_halt = PushButton(
            FluentIcon.PAUSE_BOLD, "重置并暂停", inner)
        _tip(self.btn_reset_halt, "复位 MCU 并停在复位状态（halt）")
        self.btn_reset_halt.setEnabled(False)
        row_reset.addWidget(self.btn_reset)
        row_reset.addWidget(self.btn_reset_halt)
        v.addLayout(row_reset)

        # ---- 分隔线 ----
        v.addWidget(_section_separator(inner))

        # ════════════════════════════════════════════════════════════
        # 区域 2：设备信息（可折叠）
        # ════════════════════════════════════════════════════════════
        self.gb_info = HeaderCardWidget(inner)
        self.gb_info.setTitle("设备信息")
        self.btn_info_toggle = TransparentToolButton(
            FluentIcon.CHEVRON_DOWN_MED, self.gb_info)
        self.gb_info.headerLayout.addStretch(1)
        self.gb_info.headerLayout.addWidget(self.btn_info_toggle)

        self._info_container = QWidget(self.gb_info)
        info_grid = QGridLayout(self._info_container)
        info_grid.setHorizontalSpacing(8)
        info_grid.setVerticalSpacing(4)
        self._info_labels: dict[str, StrongBodyLabel] = {}
        info_rows = [
            ("固件版本", "jlink_firmware"),
            ("硬件版本", "jlink_hardware"),
            ("序列号", "jlink_serial"),
            ("核心名称", "core_name"),
            ("核心 ID", "core_id"),
            ("CPU 类型", "core_cpu"),
            ("目标设备", "target_device"),
            ("接口", "interface"),
            ("速度(kHz)", "speed_khz"),
        ]
        for i, (text, key) in enumerate(info_rows):
            info_grid.addWidget(BodyLabel(f"{text}:"), i, 0)
            lbl = StrongBodyLabel("-")
            self._info_labels[key] = lbl
            info_grid.addWidget(lbl, i, 1)
        self.gb_info.viewLayout.addWidget(self._info_container)
        self._info_container.setVisible(False)
        self.gb_info.separator.setVisible(False)
        self.gb_info.view.setVisible(False)
        self.btn_info_toggle.clicked.connect(self._toggle_info_card)
        v.addWidget(self.gb_info)

        # ---- 分隔线 ----
        v.addWidget(_section_separator(inner))

        # ════════════════════════════════════════════════════════════
        # 区域 3：接收设置
        # ════════════════════════════════════════════════════════════
        _lbl_rx = StrongBodyLabel("接收设置")
        _lbl_rx.setStyleSheet("font-size: 13px; margin-top: 2px;")
        v.addWidget(_lbl_rx)

        self.chk_auto_scroll = CheckBox("自动滚动")
        self.chk_auto_scroll.setChecked(self._cfg.get("auto_scroll"))
        v.addWidget(self.chk_auto_scroll)
        self.chk_pause = CheckBox("暂停接收")
        v.addWidget(self.chk_pause)
        self.chk_power = CheckBox("电源输出")
        self.chk_power.setEnabled(False)
        v.addWidget(self.chk_power)
        self.chk_log_rec = CheckBox("实时日志记录")
        v.addWidget(self.chk_log_rec)

        # HEX 显示
        self.chk_hex_display = CheckBox("十六进制显示")
        _tip(self.chk_hex_display, "将接收到的每个字节以大写的 HEX 格式显示")
        v.addWidget(self.chk_hex_display)

        # 自动断帧
        row_frame = QHBoxLayout()
        row_frame.setSpacing(6)
        self.chk_auto_frame = CheckBox("自动断帧")
        _INPUT_W = 80
        self.le_frame_timeout = LineEdit(inner)
        self.le_frame_timeout.setText("20")
        self.le_frame_timeout.setFixedWidth(_INPUT_W)
        self.le_frame_timeout.setClearButtonEnabled(False)
        self.le_frame_timeout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _lbl_ms = QLabel("ms", self.le_frame_timeout)
        _lbl_ms.setStyleSheet(
            "color: rgba(128,128,128,0.6); font-size: 11px; "
            "background: transparent; border: none;")
        self.le_frame_timeout.hBoxLayout.addWidget(_lbl_ms, 0, Qt.AlignVCenter)
        self.le_frame_timeout.setTextMargins(0, 0, 22, 0)
        self.btn_frame_help = PushButton("?", inner)
        self.btn_frame_help.setFixedSize(33, 33)
        self.btn_frame_help.clicked.connect(self._on_frame_help_clicked)
        self._frame_help_content = (
            "接收超时设置（1~200 毫秒），默认 20ms。\n\n"
            "在接收连续数据流时，如果相邻两批数据的接收时间间隔\n"
            "超过设定值，则判定为一帧数据结束，自动插入换行。\n\n"
            "自动断帧：启用后，每个数据帧显示后自动添加换行符，\n"
            "便于区分不同帧。")
        _frame_group = QWidget(inner)
        _frame_group.setStyleSheet("background: transparent;")
        _fg_lay = QHBoxLayout(_frame_group)
        _fg_lay.setContentsMargins(0, 0, 0, 0)
        _fg_lay.setSpacing(2)
        _fg_lay.addWidget(self.le_frame_timeout)
        _fg_lay.addWidget(self.btn_frame_help)
        row_frame.addWidget(self.chk_auto_frame)
        row_frame.addStretch(1)
        row_frame.addWidget(_frame_group)
        v.addLayout(row_frame)
        self.chk_auto_frame.toggled.connect(self._on_auto_frame_toggled)

        # ---- 分隔线 ----
        v.addWidget(_section_separator(inner))

        # ════════════════════════════════════════════════════════════
        # 区域 4：发送设置 + 标记
        # ════════════════════════════════════════════════════════════
        _lbl_tx = StrongBodyLabel("发送设置")
        _lbl_tx.setStyleSheet("font-size: 13px; margin-top: 2px;")
        v.addWidget(_lbl_tx)

        # 定时发送
        row_timed = QHBoxLayout()
        row_timed.setSpacing(6)
        self.chk_timed_send = CheckBox("定时发送")
        self.le_timed_interval = LineEdit(inner)
        self.le_timed_interval.setText("1.0")
        self.le_timed_interval.setFixedWidth(_INPUT_W)
        self.le_timed_interval.setClearButtonEnabled(False)
        self.le_timed_interval.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.btn_timed_unit = ToolButton(inner)
        self.btn_timed_unit.setText("秒")
        self.btn_timed_unit.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_timed_unit.setFixedSize(33, 33)
        _timed_group = QWidget(inner)
        _timed_group.setStyleSheet("background: transparent;")
        _tg_lay = QHBoxLayout(_timed_group)
        _tg_lay.setContentsMargins(0, 0, 0, 0)
        _tg_lay.setSpacing(2)
        _tg_lay.addWidget(self.le_timed_interval)
        _tg_lay.addWidget(self.btn_timed_unit)
        row_timed.addWidget(self.chk_timed_send)
        row_timed.addStretch(1)
        row_timed.addWidget(_timed_group)
        v.addLayout(row_timed)

        # CRC 脚本
        row_crc = QHBoxLayout()
        row_crc.setSpacing(6)
        self.chk_crc_script = CheckBox("脚本")
        self.cb_crc_algo = ComboBox(inner)
        for display_name, _ in CRC_ALGORITHMS:
            self.cb_crc_algo.addItem(display_name)
        self.cb_crc_algo.setCurrentIndex(1)  # 默认 CRC-16/MODBUS
        _tip(self.cb_crc_algo, "发送时追加 CRC 后缀（算法选）")
        row_crc.addWidget(self.chk_crc_script)
        row_crc.addWidget(self.cb_crc_algo)
        row_crc.addStretch(1)
        v.addLayout(row_crc)

        # 字号控制
        row_font = QHBoxLayout()
        row_font.setSpacing(6)
        row_font.addWidget(BodyLabel("字号"))
        self.btn_font_minus = TransparentToolButton(
            FluentIcon.ZOOM_OUT, inner)
        self.btn_font_minus.setFixedSize(28, 28)
        _tip(self.btn_font_minus, "字号 −1")
        self.lbl_font_size = BodyLabel(f"{self._cfg.get('font_size')}")
        self.lbl_font_size.setAlignment(Qt.AlignCenter)
        self.lbl_font_size.setFixedWidth(28)
        self.btn_font_plus = TransparentToolButton(
            FluentIcon.ZOOM_IN, inner)
        self.btn_font_plus.setFixedSize(28, 28)
        _tip(self.btn_font_plus, "字号 +1")
        row_font.addWidget(self.btn_font_minus)
        row_font.addWidget(self.lbl_font_size)
        row_font.addWidget(self.btn_font_plus)
        row_font.addStretch(1)
        v.addLayout(row_font)

        # ---- 分隔线 ----
        v.addWidget(_section_separator(inner))

        # 标记 / 保存 / 清空
        _lbl_mark = StrongBodyLabel("标记与保存")
        _lbl_mark.setStyleSheet("font-size: 13px; margin-top: 2px;")
        v.addWidget(_lbl_mark)

        self.le_mark = EditableComboBox(inner)
        self.le_mark.setPlaceholderText("会话标记文本…")
        self._mark_history: list[str] = []
        v.addWidget(self.le_mark)

        row_mark = QHBoxLayout()
        row_mark.setSpacing(8)
        self.btn_mark = PushButton("插入标记", inner)
        _tip(self.btn_mark, "在显示区插入分隔标记")
        self.btn_clear = PushButton("清除", inner)
        self.btn_save = PushButton("💾 保存", inner)
        row_mark.addWidget(self.btn_mark)
        row_mark.addWidget(self.btn_clear)
        row_mark.addWidget(self.btn_save)
        v.addLayout(row_mark)

        v.addStretch(1)
        return panel

    # ------------------------------------------------------------------
    # 右侧数据区
    # ------------------------------------------------------------------
    def _build_right_panel(self) -> QWidget:
        """构建右侧数据收发区，返回容器 widget。

        布局（从上到下）：
          1. 显示区 (display, PlainTextEdit, stretch=1)
          2. 工具栏行 — HEX↑ / HEX↓ / 暂停 / 清空 / 发送
          3. 发送输入区 (PlainTextEdit ~6行) + 右侧大发送按钮
             · 脚本启用时顶部红色提示条
          4. 底部状态栏
        """
        panel = QWidget(self)
        panel.setObjectName("rightPanel")
        panel.setStyleSheet(
            "QWidget#rightPanel { background: transparent; }")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # ---- 显示区 ----
        self.display = PlainTextEdit(panel)
        self.display.setReadOnly(True)
        self.display.setMaximumBlockCount(
            self._cfg.get("max_display_lines"))
        self.display.setLineWrapMode(PlainTextEdit.WidgetWidth)
        self.display.setMinimumHeight(120)

        # ---- 搜索栏（浮动在 right_panel 右上角，不随文本滚动）----
        self.search_bar = SearchBar(panel)
        self.search_bar.setVisible(False)
        self._cfg.theme_color_changed.connect(
            lambda _c: self.search_bar._apply_style())

        # ════════════════════════════════════════════════════════════
        # 收窄模式工具栏行（位于显示区和发送区之间，仅在左侧面板隐藏时显示）
        # ════════════════════════════════════════════════════════════
        self._toolbar = QWidget(panel)
        self._toolbar.setObjectName("narrowToolbar")
        toolbar_row = QHBoxLayout(self._toolbar)
        toolbar_row.setContentsMargins(0, 0, 0, 0)
        toolbar_row.setSpacing(6)

        # HEX 模式切换（接收方向）
        self.btn_hex_rx_up = ToolButton(self._toolbar)
        self.btn_hex_rx_up.setText("HEX ↑")
        self.btn_hex_rx_up.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_hex_rx_up.setFixedSize(56, 30)
        _tip(self.btn_hex_rx_up, "接收 HEX 显示切换")
        self.btn_hex_rx_up.setCheckable(True)
        # 同步 chk_hex_display ↔ btn_hex_rx_up（纯 UI 状态，不持久化）
        self.btn_hex_rx_up.toggled.connect(self.chk_hex_display.setChecked)
        self.chk_hex_display.toggled.connect(self.btn_hex_rx_up.setChecked)

        # HEX 发送模式
        self.chk_hex = CheckBox("Hex", self._toolbar)
        self.chk_hex.setChecked(self._cfg.get("hex_send_mode"))
        self.chk_hex.setFixedWidth(50)

        # 暂停/恢复
        self.btn_toolbar_pause = ToolButton(FluentIcon.PAUSE, self._toolbar)
        self.btn_toolbar_pause.setFixedSize(36, 30)
        _tip(self.btn_toolbar_pause, "暂停/恢复接收")
        self.btn_toolbar_pause.setCheckable(True)
        self.btn_toolbar_pause.toggled.connect(
            self._worker.set_pause_receive_requested.emit)
        self.btn_toolbar_pause.toggled.connect(self.chk_pause.setChecked)
        self.chk_pause.toggled.connect(self.btn_toolbar_pause.setChecked)

        # 清空显示
        self.btn_toolbar_clear = ToolButton(FluentIcon.DELETE, self._toolbar)
        self.btn_toolbar_clear.setFixedSize(36, 30)
        _tip(self.btn_toolbar_clear, "清除显示")
        self.btn_toolbar_clear.clicked.connect(self.display.clear)

        toolbar_row.addWidget(self.btn_hex_rx_up)
        toolbar_row.addWidget(self.chk_hex)
        toolbar_row.addWidget(self.btn_toolbar_pause)
        toolbar_row.addWidget(self.btn_toolbar_clear)
        toolbar_row.addStretch(1)
        self._toolbar.setVisible(False)  # 默认隐藏，仅收窄模式显示

        # ════════════════════════════════════════════════════════════
        # 发送区：多行文本框 + 发送按钮
        # 脚本启用时 → 输入框红色边框 + 朝内渐变（不加独立标签）
        # ════════════════════════════════════════════════════════════
        self.te_send = PlainTextEdit(panel)
        self.te_send.setPlaceholderText(
            "输入要发送的数据…\n"
            "(Hex 模式下用 16 进制字符)")
        self.te_send.setMinimumHeight(100)   # 约 6 行
        self.te_send.setMaximumHeight(160)
        font = self.te_send.font()
        font.setPointSize(12)
        self.te_send.setFont(font)

        # 发送按钮（文字+图标，正常尺寸，竖向居中于发送框）
        send_btn_col = QVBoxLayout()
        send_btn_col.setContentsMargins(0, 0, 0, 0)
        send_btn_col.setSpacing(0)
        self.btn_send = PrimaryPushButton(FluentIcon.SEND, "发送", panel)
        self.btn_send.setFixedHeight(36)
        self.btn_send.setMinimumWidth(72)
        self.btn_send.setEnabled(False)
        _tip(self.btn_send, "发送 (Enter)")
        send_btn_col.addStretch(1)
        send_btn_col.addWidget(self.btn_send, 0, Qt.AlignVCenter)
        send_btn_col.addStretch(1)

        # 发送区水平布局：文本框 stretch=1 + 按钮
        send_area = QHBoxLayout()
        send_area.setSpacing(6)
        send_area.addWidget(self.te_send, 1)
        send_area.addLayout(send_btn_col)

        # ---- 底部状态栏 ----
        self.lbl_status_state = BodyLabel("● 未连接")
        self.lbl_status_state.setStyleSheet("color: #888888;")
        self.lbl_status_state.setMinimumWidth(100)
        self.lbl_status_rate = BodyLabel("")
        self.lbl_status_rate.setMinimumWidth(120)
        self.lbl_status_total = BodyLabel("")
        self.lbl_status_total.setMinimumWidth(160)
        self.lbl_status_encoding = BodyLabel("")
        self.lbl_reset_count = BodyLabel("")

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        status_row.addWidget(self.lbl_status_state)
        status_row.addWidget(self.lbl_status_rate)
        status_row.addWidget(self.lbl_status_total)
        status_row.addStretch(1)
        status_row.addWidget(self.lbl_reset_count)
        status_row.addWidget(self.lbl_status_encoding)

        v.addWidget(self.display, 1)
        v.addWidget(self._toolbar)
        v.addLayout(send_area)
        v.addLayout(status_row)
        return panel

    # ------------------------------------------------------------------
    # 窗口 resize → 自动收起/展开左侧面板
    # ------------------------------------------------------------------
    _COLLAPSE_WIDTH = 900

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, '_resize_timer'):
            self._resize_timer.start()

    def _on_resize_debounce(self) -> None:
        w = self.width()
        if w < self._COLLAPSE_WIDTH and self._config_visible:
            self._set_config_panel_visible(False)
        elif w >= self._COLLAPSE_WIDTH and not self._config_visible:
            self._set_config_panel_visible(True)

    def _set_config_panel_visible(self, visible: bool) -> None:
        self._config_visible = visible
        self._config_panel.setVisible(visible)
        self._icon_bar.setVisible(not visible)
        # 收窄模式：显示接收/发送区之间的工具栏行
        self._toolbar.setVisible(not visible)

    # ------------------------------------------------------------------
    # 信号接线
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_reset.clicked.connect(self._on_reset_clicked)
        self.btn_reset_halt.clicked.connect(self._on_reset_halt_clicked)
        self.btn_clear.clicked.connect(self.display.clear)
        self.chk_pause.toggled.connect(self._worker.set_pause_receive_requested.emit)
        self.chk_power.toggled.connect(self._worker.set_power_output_requested.emit)
        self.sp_channel.valueChanged.connect(self._on_channel_changed)
        self.chk_auto_scroll.toggled.connect(self._on_auto_scroll_toggled)
        # 用户手动滚动 → 取消 chk_auto_scroll 勾选；用 _programmatic_scroll 标志
        # 区分程序性 setValue 和用户拖动
        self.display.verticalScrollBar().valueChanged.connect(self._on_display_scrolled)
        self.chk_hex.toggled.connect(self._on_hex_send_toggled)
        self.btn_send.clicked.connect(self._on_send_clicked)
        self.chk_timed_send.toggled.connect(self._on_timed_send_toggled)

        # 发送文本框：Enter = 发送；Shift+Enter = 换行
        _send_enter_shortcut = QShortcut(
            QKeySequence(Qt.Key_Return | Qt.Key_Enter), self.te_send)
        _send_enter_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        _send_enter_shortcut.activated.connect(self._on_send_clicked)

        # CRC 脚本：勾选时显示红色提示条
        self.chk_crc_script.toggled.connect(self._on_crc_script_toggled)

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
        # 搜索栏
        self.search_bar.search_requested.connect(self._do_search)
        self.search_bar.options_changed.connect(self._on_search_options_changed)
        self.search_bar.replace_requested.connect(self._do_replace)
        self.search_bar.closed.connect(self._on_search_bar_closed)

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

    # ---- 快捷键路由（F2/F3/F4，由 MainWindow 的 QShortcut 调用）----
    # 用 _is_connected 真状态当 gate；按钮文字是呈现，不能当 state enum
    def on_shortcut_connect(self) -> None:
        if self.btn_connect.isEnabled() and not self._is_connected:
            self.btn_connect.click()

    def on_shortcut_disconnect(self) -> None:
        if self.btn_connect.isEnabled() and self._is_connected:
            self.btn_connect.click()

    def on_shortcut_reset(self) -> None:
        if self.btn_reset.isEnabled():
            self.btn_reset.click()

    def _on_connect_clicked(self) -> None:
        if not self._is_connected:
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

    def _apply_reset_mode_to_button(self, mode: str) -> None:
        """按 cfg.reset_mode 刷新 btn_reset 文字 + tooltip。"""
        if mode == "auto_reconnect":
            self.btn_reset.setText("重置(重连)")
            _tip(self.btn_reset, "F4 重置目标 — 当前模式：断开+重连（更可靠）")
        else:
            self.btn_reset.setText("重置目标")
            _tip(self.btn_reset, "F4 重置目标 — 当前模式：仅重置 MCU")

    def _on_reset_clicked(self) -> None:
        # auto_reconnect=reset+disconnect+sleep+reconnect）
        self._reset_count += 1
        self.lbl_reset_count.setText(f"复位: {self._reset_count}")
        self._worker.reset_requested.emit(self._cfg.get("reset_mode"))

    def _on_reset_halt_clicked(self) -> None:
        """重置并暂停：固定 halt 意图，与配置的 reset_mode 无关。"""
        self._reset_count += 1
        self.lbl_reset_count.setText(f"复位: {self._reset_count}")
        self._worker.reset_requested.emit(RESET_MODE_HALT)

    def _on_channel_changed(self, ch: int) -> None:
        self._cfg.set("rtt_channel", ch)
        self._worker.set_rtt_channel_requested.emit(ch)

    def _on_send_clicked(self) -> None:
        text = self.te_send.toPlainText().strip()
        if not text:
            return
        is_hex = self.chk_hex.isChecked()

        # CRC 脚本：在原始 payload 后追加 CRC 字节
        if self.chk_crc_script.isChecked():
            try:
                algo_idx = self.cb_crc_algo.currentIndex()
                _, algo_key = CRC_ALGORITHMS[algo_idx]
                # 先把用户输入转成原始 bytes
                if is_hex:
                    cleaned = text.replace(" ", "").replace("\n", "").replace("\r", "")
                    if len(cleaned) % 2 != 0:
                        cleaned += "0"
                    payload = bytes.fromhex(cleaned)
                else:
                    payload = text.encode("utf-8")
                crc_bytes = compute_crc(algo_key, payload)
                full_payload = payload + crc_bytes
                # 追加 CRC 后以 HEX 方式发送
                text = " ".join(f"{b:02X}" for b in full_payload)
                is_hex = True
            except Exception as exc:
                _infobar.warn(self, "CRC 错误", str(exc))
                return

        self._worker.send_data_requested.emit(text, is_hex)
        # 加入历史（去重 + 末尾追加）—— 存用户原始输入，不存 CRC 追加后的
        orig_text = self.te_send.toPlainText().strip()
        hist = list(self._cfg.get("send_history") or [])
        if orig_text in hist:
            hist.remove(orig_text)
        hist.append(orig_text)
        self._cfg.set("send_history", hist)

    def _on_crc_script_toggled(self, checked: bool) -> None:
        """CRC 脚本 checkbox 切换：给发送框加红色边框 + 朝内渐变背景。

        不加独立标签——渐变效果直接画在输入框边框上，更干净。
        保存原始 styleSheet，取消时恢复，避免清掉 qfluentwidgets 主题样式。
        """
        if checked:
            self._te_send_orig_ss = self.te_send.styleSheet()
            self.te_send.setStyleSheet(
                self._te_send_orig_ss
                + "\nQPlainTextEdit {"
                "  border: 2px solid #cc3300;"
                "  border-radius: 5px;"
                "  background: qlineargradient("
                "    x1:0, y1:0, x2:0, y2:1,"
                "    stop:0 rgba(204,51,0,0.10),"
                "    stop:0.15 rgba(204,51,0,0.04),"
                "    stop:0.4 rgba(204,51,0,0));"
                "}")
        else:
            orig = getattr(self, "_te_send_orig_ss", None)
            if orig is not None:
                self.te_send.setStyleSheet(orig)
                self._te_send_orig_ss = None

    def _on_hex_send_toggled(self, checked: bool) -> None:
        """HEX 发送模式切换：双向转换发送框内容。

        checked=True  → 文本 → HEX："hello" → "68 65 6C 6C 6F"
        checked=False → HEX → 文本："68 65 6C 6C 6F" → "hello"
        转换失败（非法 HEX）则保留原文。
        """
        self._cfg.set("hex_send_mode", checked)
        cur = self.te_send.toPlainText()
        if not cur:
            return
        if checked:
            # 文本 → HEX
            try:
                raw = cur.encode("utf-8")
                hex_str = " ".join(f"{b:02X}" for b in raw)
                self.te_send.setPlainText(hex_str)
            except Exception:
                pass
        else:
            # HEX → 文本
            try:
                cleaned = cur.replace(" ", "").replace("\n", "").replace("\r", "")
                if len(cleaned) % 2 != 0:
                    cleaned += "0"
                raw = bytes.fromhex(cleaned)
                self.te_send.setPlainText(raw.decode("utf-8", errors="replace"))
            except ValueError:
                pass  # 非法 HEX，保留原文

    def _on_frame_help_clicked(self) -> None:
        """? 按钮点击：弹出 PopupTeachingTip，点击外部自动关闭。"""
        from qfluentwidgets import (
            PopupTeachingTip,
            TeachingTipTailPosition,
            TeachingTipView,
        )
        view = TeachingTipView(
            title="自动断帧",
            content=self._frame_help_content,
            isClosable=True,
            tailPosition=TeachingTipTailPosition.TOP,
        )
        self._frame_tip = PopupTeachingTip.make(
            view,
            target=self.btn_frame_help,
            duration=-1,
            tailPosition=TeachingTipTailPosition.TOP,
            parent=self,
        )
        view.closed.connect(self._frame_tip.close)

    # ---- 自动断帧 ----
    def _on_auto_frame_toggled(self, checked: bool) -> None:
        """自动断帧 checkbox 切换：选中 = 功能启用，参数锁定（禁用编辑）。"""
        self.le_frame_timeout.setEnabled(not checked)
        self.btn_frame_help.setEnabled(not checked)

    def _get_frame_timeout_ms(self) -> int:
        """从 LineEdit 解析自动断帧超时值，夹到 [1, 200]。"""
        try:
            return max(1, min(200, int(self.le_frame_timeout.text())))
        except (ValueError, AttributeError):
            return 20

    # ---- 定时发送 ----
    def _on_timed_send_toggled(self, checked: bool) -> None:
        """定时发送 checkbox 切换：选中 = 功能启用，参数锁定（禁用编辑）。"""
        self.le_timed_interval.setEnabled(not checked)
        self.btn_timed_unit.setEnabled(not checked)
        if checked:
            if not self._is_connected:
                _infobar.warn(self, "提示", "未连接目标，定时发送将在连接后自动启动")
                self._timed_send_pending = True
                return
            self._start_timed_send_timer()
        else:
            self._timed_send_timer.stop()
            self._timed_send_pending = False

    def _get_timed_interval_sec(self) -> float:
        """从 LineEdit 解析定时发送间隔，夹到 [0.001, 999]。"""
        try:
            v = float(self.le_timed_interval.text())
            return max(0.001, min(999.0, v))
        except (ValueError, AttributeError):
            return 1.0

    def _start_timed_send_timer(self) -> None:
        """按当前 interval 启动/重启定时器。"""
        self._timed_send_timer.stop()
        interval_ms = max(1, int(self._get_timed_interval_sec() * 1000))
        self._timed_send_timer.setInterval(interval_ms)
        self._timed_send_timer.start()
        self._timed_send_pending = False

    def _on_timed_send_fire(self) -> None:
        """定时器回调：自动触发发送。"""
        if not self._is_connected:
            self._timed_send_timer.stop()
            self._timed_send_pending = True
            return
        # 如果用户改了间隔，实时生效
        interval_ms = max(1, int(self._get_timed_interval_sec() * 1000))
        if self._timed_send_timer.interval() != interval_ms:
            self._timed_send_timer.setInterval(interval_ms)
        self._on_send_clicked()

    def _on_state_changed(self, connected: bool) -> None:
        from datetime import datetime
        if connected:
            # 同步从 worker 取 device_info（lock 保护，不走跨线程 dict signal）
            info = self._worker.get_device_info()
            self._set_connected_ui(info)
            if self._cfg.get("auto_mark_on_connect"):
                target = info.get("target_device", "—")
                ts = datetime.now().strftime("%H:%M:%S")
                self._insert_mark_text(f"已连接 {target} @ {ts}")
        else:
            self._set_disconnected_ui()
            if self._cfg.get("auto_mark_on_disconnect"):
                ts = datetime.now().strftime("%H:%M:%S")
                self._insert_mark_text(f"已断开 @ {ts}")

    def _set_connected_ui(self, info: dict) -> None:
        self._is_connected = True
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("断开")
        self.btn_connect.setIcon(FluentIcon.PAUSE)
        self.btn_reset.setEnabled(True)
        self.btn_reset_halt.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.chk_power.setEnabled(True)
        for key, lbl in self._info_labels.items():
            lbl.setText(str(info.get(key, "-")))
        # 复位计数：新连接重置为 0
        self._reset_count = 0
        self.lbl_reset_count.setText("")
        # 状态栏：绿色圆点 + 设备摘要
        target = info.get("target_device", "—")
        iface = info.get("interface", "—")
        speed = info.get("speed_khz", "—")
        self.lbl_status_state.setText(f"● 已连接 {target}")
        self.lbl_status_state.setStyleSheet("color: #2ecc71;")
        # 定时发送：连接后自动恢复（如果 checkbox 仍勾选且 pending）
        if self._timed_send_pending and self.chk_timed_send.isChecked():
            self._start_timed_send_timer()

        # 卡片标题加摘要
        self.gb_info.setTitle(f"设备信息 — {target} / {iface} / {speed} kHz")

    def _set_disconnected_ui(self) -> None:
        self._is_connected = False
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("连接")
        self.btn_connect.setIcon(FluentIcon.PLAY)
        self.btn_reset.setEnabled(False)
        self.btn_reset_halt.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.chk_power.setEnabled(False)
        for lbl in self._info_labels.values():
            lbl.setText("-")
        # 状态栏复位
        self.lbl_status_state.setText("● 未连接")
        self.lbl_status_state.setStyleSheet("color: #888888;")
        self.lbl_status_rate.setText("")
        self.lbl_status_total.setText("")
        self.lbl_reset_count.setText("")
        self.gb_info.setTitle("设备信息")
        # 清除上次统计 delta 基线
        self._stats_prev_bytes = 0
        self._stats_prev_lines = 0
        # 断开时停止定时发送
        self._timed_send_timer.stop()

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
        """worker 已经 50ms 合并好，直接 insertText。

        支持：
        - HEX 显示：每字节大写 HEX + 空格
        - 自动断帧：两批数据间隔 > 阈值时自动插入换行
        """
        if not text:
            return

        # 自动断帧：两批数据间隔超过阈值时插入换行
        now = self._time_mod.time()
        if (self.chk_auto_frame.isChecked()
                and self._last_rx_time > 0
                and (now - self._last_rx_time) * 1000 > self._get_frame_timeout_ms()):
            # 插入换行分隔不同帧
            sb_pre = self.display.verticalScrollBar()
            at_b = sb_pre.value() >= sb_pre.maximum() - 4
            tc = self.display.textCursor()
            tc.movePosition(QTextCursor.End)
            if tc.columnNumber() != 0:
                tc.insertText("\n")
            if at_b and self.chk_auto_scroll.isChecked():
                with self._programmatic_scroll_guard():
                    sb_pre.setValue(sb_pre.maximum())
        self._last_rx_time = now

        # 自动滚动判断必须在插入文本前
        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.End)

        if self.chk_hex_display.isChecked():
            # HEX 显示：将文本编码为字节，每字节大写 HEX + 空格
            try:
                raw = text.encode(self._cfg.get("rtt_encoding") or "utf-8",
                                  errors="replace")
            except LookupError:
                raw = text.encode("utf-8", errors="replace")
            hex_str = " ".join(f"{b:02X}" for b in raw)
            cursor.insertText(hex_str + " ")
        else:
            for seg, attrs in parse_ansi(text):
                cursor.insertText(seg, self._fmt(attrs))

        if at_bottom and self.chk_auto_scroll.isChecked():
            with self._programmatic_scroll_guard():
                sb.setValue(sb.maximum())

    def _on_auto_scroll_toggled(self, checked: bool) -> None:
        """checkbox 勾选/取消：持久化 + 勾选时立即跳到底并恢复跟踪。"""
        self._cfg.set("auto_scroll", checked)
        if checked:
            sb = self.display.verticalScrollBar()
            with self._programmatic_scroll_guard():
                sb.setValue(sb.maximum())

    def _on_display_scrolled(self, _value: int) -> None:
        """display 滚动条 valueChanged：双向同步 chk_auto_scroll。
        - 已勾选 + 用户上滚离开底部 → 取消勾选（停止自动滚动）
        - 未勾选 + 用户滚回底部 → 重新勾选（恢复自动滚动）
        程序性 sb.setValue() 不触发（_programmatic_scroll_guard 过滤）。
        """
        if self._programmatic_scroll:
            return
        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        is_checked = self.chk_auto_scroll.isChecked()
        if is_checked and not at_bottom:
            self._set_auto_scroll_silent(False)
        elif not is_checked and at_bottom:
            self._set_auto_scroll_silent(True)

    def _set_auto_scroll_silent(self, checked: bool) -> None:
        """改 checkbox + 落 cfg，但不触发 _on_auto_scroll_toggled 回调
        （避免它再发起一次程序性 setValue 形成回环）。"""
        self.chk_auto_scroll.blockSignals(True)
        self.chk_auto_scroll.setChecked(checked)
        self.chk_auto_scroll.blockSignals(False)
        self._cfg.set("auto_scroll", checked)

    def _insert_mark_text(self, text: str) -> None:
        """在显示区追加一行视觉分隔的标记。颜色由 cfg.mark_color 决定。

        text="" → 插入纯分隔线 ──────。
        被用户点 "插入标记" + 连接/断开自动标记共用。
        """
        line = f"──── {text} ────" if text else "─" * 50
        sb = self.display.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.End)
        if cursor.columnNumber() != 0:
            cursor.insertText("\n")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._cfg.get("mark_color") or "#ffff55"))
        fmt.setFontWeight(QFont.Bold)
        cursor.insertText(line + "\n", fmt)

        if at_bottom:
            with self._programmatic_scroll_guard():
                sb.setValue(sb.maximum())

    def _on_insert_mark(self) -> None:
        text = self.le_mark.currentText().strip()
        if text:
            if text in self._mark_history:
                self._mark_history.remove(text)
            self._mark_history.append(text)
            self._mark_history = self._mark_history[-10:]
            self.le_mark.clear()
            self.le_mark.addItems(reversed(self._mark_history))
        self._insert_mark_text(text)
        # qfluentwidgets EditableComboBox 没有 clearEditText()——用 setCurrentText 替代
        self.le_mark.setCurrentText("")

    def _fmt(self, attrs: AnsiAttrs) -> QTextCharFormat:
        # 注意：QColor 必须从预构造表查（_ANSI_QCOLORS），不要 QColor(hex_string)。
        # RTT 高吞吐时本函数每段都调，每次构造 QColor 是不必要的 syscall + alloc。
        fmt = QTextCharFormat()
        if attrs.fg:
            fmt.setForeground(_ANSI_QCOLORS.get(attrs.fg, _DEFAULT_FG_QCOLOR))
        if attrs.bg:
            fmt.setBackground(_ANSI_QCOLORS.get(attrs.bg, _DEFAULT_BG_QCOLOR))
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

    # ---- 搜索栏快捷键入口（由 MainWindow QShortcut 调用）----
    def on_shortcut_find(self) -> None:
        """Ctrl+F：切换搜索栏显示/隐藏。已打开时仅聚焦。

        如果显示区有选中文本，自动填入搜索框（VSCode 行为）。
        """
        sel = self.display.textCursor().selectedText().strip()
        if self.search_bar.isVisible():
            if sel:
                self.search_bar.le_search.setText(sel)
            self.search_bar.le_search.setFocus()
            self.search_bar.le_search.selectAll()
        else:
            self.search_bar.show_search(initial_text=sel)

    def on_shortcut_replace(self) -> None:
        """Ctrl+H：切换搜索栏 + 展开替换行。已展开时关闭。

        如果显示区有选中文本，自动填入搜索框。
        """
        sel = self.display.textCursor().selectedText().strip()
        if self.search_bar.isVisible() and self.search_bar.is_replace_visible():
            self.search_bar.close_bar()
        else:
            self.search_bar.show_replace(initial_text=sel)

    def _on_search_bar_closed(self) -> None:
        """搜索栏关闭时把焦点还给 display，清除高亮。"""
        self.display.setFocus()
        self.display.setExtraSelections([])

    def _on_search_options_changed(self) -> None:
        """搜索选项（大小写/全词/正则）变化时重新计数。"""
        self._match_count_timer.start()

    def _build_regex(self, pattern: str, whole_word: bool, regex: bool, case_sensitive: bool):
        """构建编译好的正则表达式。返回 re.Pattern 或 None（模式无效时）。"""
        import re
        if regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                expr = f"\\b(?:{pattern})\\b" if whole_word else pattern
                return re.compile(expr, flags)
            except re.error:
                return None
        # 非正则：把 pattern 当字面量
        flags = 0 if case_sensitive else re.IGNORECASE
        expr = re.escape(pattern)
        if whole_word:
            expr = f"\\b{expr}\\b"
        return re.compile(expr, flags)

    def _do_search(self, text: str, backward: bool,
                   case_sensitive: bool, whole_word: bool, regex: bool) -> None:
        pat = self._build_regex(text, whole_word, regex, case_sensitive)
        if pat is None:
            self.search_bar.set_match_label("无效正则")
            return
        full = self.display.toPlainText()
        matches = list(pat.finditer(full))
        if not matches:
            self.search_bar.set_match_label(f"0/0")
            self.display.setExtraSelections([])
            return
        # 找当前光标后/前的下一个匹配
        cursor = self.display.textCursor()
        cur_pos = cursor.selectionEnd() if not backward else cursor.selectionStart()
        if backward:
            target = None
            for m in reversed(matches):
                if m.start() < cur_pos:
                    target = m
                    break
            if target is None:
                target = matches[-1]  # 回卷到最后一个
        else:
            target = None
            for m in matches:
                if m.start() >= cur_pos:
                    target = m
                    break
            if target is None:
                target = matches[0]  # 回卷到第一个
        # 移动光标到匹配位置
        tc = self.display.textCursor()
        tc.setPosition(target.start())
        tc.setPosition(target.end(), QTextCursor.KeepAnchor)
        self.display.setTextCursor(tc)
        self.display.ensureCursorVisible()
        self._update_match_position(text)

    def _do_replace(self, text: str, replacement: str, replace_all: bool,
                    case_sensitive: bool, whole_word: bool, regex: bool) -> None:
        pat = self._build_regex(text, whole_word, regex, case_sensitive)
        if pat is None:
            self.search_bar.set_match_label("无效正则")
            return
        if replace_all:
            # 从后往前逐段替换，保留周围文本的 QTextCharFormat（不用 setPlainText，
            # 后者会清除所有格式导致 ANSI 染色丢失）
            full = self.display.toPlainText()
            matches = list(pat.finditer(full))
            if not matches:
                self._update_match_position(text)
                return
            doc = self.display.document()
            for m in reversed(matches):
                tc = QTextCursor(doc)
                tc.setPosition(m.start())
                tc.setPosition(m.end(), QTextCursor.KeepAnchor)
                tc.insertText(replacement)  # 保留周围文本的格式
            self._update_match_position(text)
        else:
            # 替换当前选中：如果当前选中文本匹配 pattern，替换它
            cursor = self.display.textCursor()
            if cursor.hasSelection():
                sel = cursor.selectedText()
                if pat.fullmatch(sel):
                    cursor.insertText(replacement)
            # 找下一个
            self._do_search(text, False, case_sensitive, whole_word, regex)

    def _update_match_count(self, text: str) -> None:
        """textChanged/optionsChanged 信号槽：节流到 200ms 再算计数。"""
        if not text:
            self.search_bar.set_match_label("")
            self._match_count_timer.stop()
            self.display.setExtraSelections([])
            return
        self._match_count_timer.start()

    def _do_update_match_count(self) -> None:
        self._update_match_position(self.search_bar.search_text())

    def _update_match_position(self, text: str) -> None:
        """显示 "第 N 项，共 M 项"，并把全部匹配位置叠黄色 ExtraSelection。"""
        if not text:
            self.search_bar.set_match_label("")
            self.display.setExtraSelections([])
            return
        pat = self._build_regex(
            text, self.search_bar.whole_word(),
            self.search_bar.regex_enabled(),
            self.search_bar.case_sensitive())
        if pat is None:
            self.search_bar.set_match_label("无效正则")
            self.display.setExtraSelections([])
            return
        full = self.display.toPlainText()
        matches = list(pat.finditer(full))
        cnt = len(matches)
        if cnt == 0:
            self.search_bar.set_match_label(f"0/0")
            self.display.setExtraSelections([])
            return
        # 当前光标在哪个匹配中
        cursor = self.display.textCursor()
        cur_pos = cursor.selectionStart()
        idx = 0
        for i, m in enumerate(matches):
            if m.start() <= cur_pos <= m.end():
                idx = i + 1
                break
        else:
            # 光标不在任何匹配中，找最近的
            for i, m in enumerate(matches):
                if m.start() >= cur_pos:
                    idx = i + 1
                    break
            else:
                idx = cnt
        self.search_bar.set_match_label(f"{idx}/{cnt}")
        self._highlight_matches(matches, limit=500)

    def _highlight_matches(self, matches: list, limit: int = 500) -> None:
        """匹配位置叠浅黄色背景。超过 limit 截断。"""
        from PySide6.QtWidgets import QTextEdit
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 235, 100, 140))
        selections: list = []
        for m in matches[:limit]:
            c = QTextCursor(self.display.document())
            c.setPosition(m.start())
            c.setPosition(m.end(), QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = fmt
            selections.append(sel)
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
        """worker → UI 日志投递。level: error/warning/info。
        连接路径异常 / 卡在"连接中…"不需要这里兜底——worker 失败路径会
        走 _do_disconnect 并 emit connection_state_changed(False)，UI 自动回正。
        """
        if level == "error":
            _infobar.err(self, "错误", msg)
        elif level == "warning":
            _infobar.warn(self, "警告", msg)
        # info 级别只进 logger 文件，不弹 toast——避免噪音
