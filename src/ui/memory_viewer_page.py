"""内存查看页（专业版）：

- Hex dump 多行显示，支持 8/16/32 字节/行切换
- 右侧解析面板：u8/u16/u32/i8/i16/i32/float/double，小端/大端切换，
  随光标在 hex 区移动实时刷新
- 地址跳转 + Hex pattern 搜索（在已读取的缓冲区内）
- 自动刷新（1-10 s 可调，已连接时生效）
- 复制 hex / ASCII / C 数组；保存当前缓冲为 .bin
- 显示区改用 qfluentwidgets.PlainTextEdit，跟随 fluent 主题
"""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    LineEdit,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SpinBox,
    StrongBodyLabel,
)

from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker
from core.memory_service import format_as_c_array, format_hex_dump, parse_value

from . import _infobar
from ._scroll_helpers import make_transparent_scroll


_SIZE_PRESETS = [
    ("128 KB", 128 * 1024),
    ("256 KB", 256 * 1024),
    ("512 KB", 512 * 1024),
    ("1 MB", 1024 * 1024),
    ("2 MB", 2 * 1024 * 1024),
    ("自定义", -1),
]

_BYTES_PER_ROW_OPTIONS = [8, 16, 32]
_DTYPES = ["u8", "i8", "u16", "i16", "u32", "i32", "float", "double"]
# format_hex_dump 每行 hex 区起始列：0xXXXXXXXX: + "  " = 11 + 2 = 13
# 测试 test_format_hex_dump_row_layout_contract 保护此契约
_HEX_START_COL = 13
# Diff highlight 阈值——超过这个大小不算 diff（python 字节比较 O(n) + setExtraSelections
# 大量选区会让 UI 主线程冻结 1-3s）。256 KB 在 STM32 系列上够看一片 RAM 或一两段 flash。
_DIFF_MAX_SIZE = 256 * 1024
# 高亮变化字节数上限：超过则只显示前 N 个，避免 setExtraSelections 万级选区拖垮 layout。
_DIFF_MAX_HIGHLIGHTS = 512


def _parse_int(text: str) -> int:
    text = text.strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text)


def _parse_hex_pattern(text: str) -> bytes:
    """支持 'AA BB CC' / 'AABBCC' / '0xAA 0xBB' 等输入，返回 bytes。"""
    cleaned = re.sub(r"(0x|,|\s)+", "", text, flags=re.IGNORECASE)
    if not cleaned or len(cleaned) % 2 != 0:
        raise ValueError("Hex pattern 长度必须为偶数")
    return bytes.fromhex(cleaned)


class MemoryViewerPage(QWidget):
    def __init__(self, worker: JLinkWorker, cfg: ConfigService, parent=None):
        super().__init__(parent)
        self.setObjectName("memory-viewer")
        self._worker = worker
        self._cfg = cfg
        self._connected = False
        self._save_path: str = ""

        # 已读取的数据缓冲（用于搜索 / 类型解析 / 复制）
        self._buffer: bytes = b""
        self._buffer_base: int = 0
        self._bytes_per_row: int = int(self._cfg.get("mem_bytes_per_row"))
        # 上次 hover 显示的字节偏移；同一字节内 MouseMove 不重算 tooltip。
        # cursorForPosition 在大 buffer 上做 layout hit-test 不便宜，每秒可调用百次。
        self._last_hover_offset: int = -1

        # 自动刷新 timer
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(False)
        self._auto_refresh_timer.timeout.connect(self._refresh_once)

        self._build_ui()
        self._wire_signals()
        # 应用初始字体（family 固定用 RTT 等宽字体 font_family，size 独立 memory_font_size）
        self._apply_font(self._cfg.get("font_family"), int(self._cfg.get("memory_font_size")))

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # 整页 ScrollArea：窗口压扁时纵向滚动，而不是把 hex 显示区 / 写内存卡压成 1 像素。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll, inner = make_transparent_scroll(self, "mem")
        outer.addWidget(self._scroll)
        root = QVBoxLayout(inner)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ---- 读取卡片 ----
        read_card = CardWidget(self)
        read_outer = QVBoxLayout(read_card)
        read_outer.setSpacing(8)

        r1 = QHBoxLayout()
        self.lbl_read_addr = BodyLabel(self.tr("起始地址"))
        r1.addWidget(self.lbl_read_addr)
        self.le_read_addr = LineEdit(self)
        self.le_read_addr.setText(str(self._cfg.get("mem_read_addr")))
        self.le_read_addr.setMaximumWidth(140)
        r1.addWidget(self.le_read_addr)
        self.lbl_read_size = BodyLabel(self.tr("大小 (字节)"))
        r1.addWidget(self.lbl_read_size)
        self.le_read_size = LineEdit(self)
        self.le_read_size.setText(str(self._cfg.get("mem_read_size")))
        self.le_read_size.setMaximumWidth(100)
        r1.addWidget(self.le_read_size)
        self.lbl_row_width = BodyLabel(self.tr("字节/行"))
        r1.addWidget(self.lbl_row_width)
        self.cb_row_width = ComboBox(self)
        for n in _BYTES_PER_ROW_OPTIONS:
            self.cb_row_width.addItem(str(n))
        self.cb_row_width.setCurrentText(str(self._bytes_per_row))
        r1.addWidget(self.cb_row_width)
        self.btn_read = PrimaryPushButton(self.tr("读取"), self)
        self.btn_clear = PushButton(self.tr("清空"), self)
        r1.addWidget(self.btn_read)
        r1.addWidget(self.btn_clear)
        self.chk_hover = CheckBox(self.tr("悬浮解析"), self)
        self.chk_hover.setChecked(bool(self._cfg.get("mem_hover_parse")))
        self.chk_hover.setToolTip(self.tr("鼠标悬停在 hex 字节上时显示地址和 LE/BE 解析"))
        r1.addWidget(self.chk_hover)
        r1.addStretch(1)
        read_outer.addLayout(r1)

        # 第二行：自动刷新 + 高亮变化 + 跳转 + 搜索
        r2 = QHBoxLayout()
        self.chk_auto_refresh = CheckBox(self.tr("自动刷新"))
        self.sp_refresh_sec = SpinBox(self)
        self.sp_refresh_sec.setRange(1, 60)
        self.sp_refresh_sec.setValue(int(self._cfg.get("mem_refresh_sec")))
        self.sp_refresh_sec.setSuffix(" s")
        self.chk_diff = CheckBox(self.tr("高亮变化"))
        self.chk_diff.setChecked(bool(self._cfg.get("mem_diff_highlight")))
        self.chk_diff.setToolTip(self.tr("重新读取相同地址/大小时，把变化的字节背景标红"))
        r2.addWidget(self.chk_auto_refresh)
        r2.addWidget(self.sp_refresh_sec)
        r2.addWidget(self.chk_diff)
        r2.addSpacing(16)
        self.lbl_goto = BodyLabel(self.tr("跳转到"))
        r2.addWidget(self.lbl_goto)
        self.le_goto = LineEdit(self)
        self.le_goto.setPlaceholderText("0x...")
        _goto = str(self._cfg.get("mem_goto_addr"))
        if _goto:
            self.le_goto.setText(_goto)
        self.le_goto.setMaximumWidth(140)
        self.btn_goto = PushButton("Go", self)
        r2.addWidget(self.le_goto)
        r2.addWidget(self.btn_goto)
        r2.addSpacing(16)
        self.lbl_search = BodyLabel(self.tr("Hex 搜索"))
        r2.addWidget(self.lbl_search)
        self.le_search = LineEdit(self)
        self.le_search.setPlaceholderText("AA BB CC")
        self.le_search.setMaximumWidth(180)
        self.btn_find_next = PushButton(self.tr("查找下一个"), self)
        r2.addWidget(self.le_search)
        r2.addWidget(self.btn_find_next)
        r2.addStretch(1)
        # 字号 ± 按钮（同 RTT 监控页的方案）
        self.btn_font_minus = PushButton("A−", self)
        self.btn_font_minus.setFixedWidth(45)
        self.btn_font_minus.setToolTip(self.tr("Hex 字号 −1"))
        self.lbl_font_size = BodyLabel(str(self._cfg.get("memory_font_size")))
        self.lbl_font_size.setAlignment(Qt.AlignCenter)
        self.lbl_font_size.setFixedWidth(28)
        self.btn_font_plus = PushButton("A+", self)
        self.btn_font_plus.setFixedWidth(45)
        self.btn_font_plus.setToolTip(self.tr("Hex 字号 +1"))
        r2.addWidget(self.btn_font_minus)
        r2.addWidget(self.lbl_font_size)
        r2.addWidget(self.btn_font_plus)
        read_outer.addLayout(r2)

        root.addWidget(read_card)

        # ---- 主体：左 Hex dump + 右 解析面板（QSplitter）----
        splitter = QSplitter(Qt.Horizontal, self)

        # 左：hex dump（字体由 _apply_font 在构造末尾应用）
        self.display = PlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setLineWrapMode(PlainTextEdit.NoWrap)
        # 同 RTT 页：压缩底线 80px，避免 Windows 任务栏遮挡底部固件导出卡片
        self.display.setMinimumHeight(80)
        splitter.addWidget(self.display)

        # 右：数据类型解析面板
        side = CardWidget(self)
        side_lay = QVBoxLayout(side)
        self.lbl_data_types_title = StrongBodyLabel(self.tr("数据类型解析"))
        side_lay.addWidget(self.lbl_data_types_title)

        info_row = QHBoxLayout()
        self.lbl_cursor_addr_label = BodyLabel(self.tr("光标地址"))
        info_row.addWidget(self.lbl_cursor_addr_label)
        self.lbl_cursor_addr = StrongBodyLabel("—")
        self.lbl_cursor_addr.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_row.addWidget(self.lbl_cursor_addr, 1)
        side_lay.addLayout(info_row)

        endian_row = QHBoxLayout()
        self.lbl_endian_label = BodyLabel(self.tr("字节序"))
        endian_row.addWidget(self.lbl_endian_label)
        self.cb_endian = ComboBox(self)
        self.cb_endian.addItems([self.tr("小端 (LE)"), self.tr("大端 (BE)")])
        self.cb_endian.setCurrentIndex(0 if self._cfg.get("mem_endian_little") else 1)
        endian_row.addWidget(self.cb_endian, 1)
        side_lay.addLayout(endian_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self._type_labels: dict[str, StrongBodyLabel] = {}
        for i, dt in enumerate(_DTYPES):
            grid.addWidget(BodyLabel(dt), i, 0)
            lbl = StrongBodyLabel("—")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._type_labels[dt] = lbl
            grid.addWidget(lbl, i, 1)
        side_lay.addLayout(grid)
        side_lay.addStretch(1)

        # 复制按钮
        copy_row = QVBoxLayout()
        self.btn_copy_hex = PushButton(self.tr("复制选中为 Hex 字串"), self)
        self.btn_copy_ascii = PushButton(self.tr("复制选中为 ASCII"), self)
        self.btn_copy_carray = PushButton(self.tr("复制全部为 C 数组"), self)
        self.btn_save_bin = PushButton(self.tr("保存全部为 .bin"), self)
        copy_row.addWidget(self.btn_copy_hex)
        copy_row.addWidget(self.btn_copy_ascii)
        copy_row.addWidget(self.btn_copy_carray)
        copy_row.addWidget(self.btn_save_bin)
        side_lay.addLayout(copy_row)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 280])
        # 给 splitter 一个最小高度——窗口压扁时 QScrollArea 的 viewport 不足，
        # 整页向下滚动出现；而不是把 hex 显示区压成 80px 看不清。
        splitter.setMinimumHeight(320)
        root.addWidget(splitter, 1)

        # ---- 写内存卡片（⚠ 高风险：写错地址可能 brick 目标）----
        write_card = CardWidget(self)
        wr_root = QVBoxLayout(write_card)
        wr_header = QHBoxLayout()
        self.lbl_write_title = StrongBodyLabel(self.tr("写内存 ⚠"))
        wr_header.addWidget(self.lbl_write_title)
        self.warn_lbl = BodyLabel(self.tr("写错地址可能让 MCU 失去响应直到复位。仅在确认安全地址（如 SRAM）时使用。"))
        self.warn_lbl.setStyleSheet("color: #d04040;")
        self.warn_lbl.setWordWrap(True)
        wr_header.addWidget(self.warn_lbl, 1)
        wr_root.addLayout(wr_header)

        wr_row = QHBoxLayout()
        self.lbl_write_addr = BodyLabel(self.tr("地址"))
        wr_row.addWidget(self.lbl_write_addr)
        self.le_write_addr = LineEdit(self)
        self.le_write_addr.setText(str(self._cfg.get("mem_write_addr")))
        self.le_write_addr.setMaximumWidth(140)
        wr_row.addWidget(self.le_write_addr)
        self.lbl_write_data = BodyLabel(self.tr("Hex 数据"))
        wr_row.addWidget(self.lbl_write_data)
        self.le_write_data = LineEdit(self)
        self.le_write_data.setPlaceholderText("AA BB CC DD")
        wr_row.addWidget(self.le_write_data, 1)
        self.btn_write = PushButton(self.tr("写入…"), self)
        self.btn_write.setEnabled(False)
        wr_row.addWidget(self.btn_write)
        wr_root.addLayout(wr_row)
        root.addWidget(write_card)

        # ---- 导出固件卡片 ----
        export_card = CardWidget(self)
        ex_root = QVBoxLayout(export_card)
        self.lbl_export_title = StrongBodyLabel(self.tr("导出固件（按块流式写盘）"))
        ex_root.addWidget(self.lbl_export_title)
        ex_row = QHBoxLayout()
        self.lbl_ex_addr = BodyLabel(self.tr("起始地址"))
        ex_row.addWidget(self.lbl_ex_addr)
        self.le_ex_addr = LineEdit(self)
        self.le_ex_addr.setText(str(self._cfg.get("mem_export_addr")))
        self.le_ex_addr.setMaximumWidth(140)
        ex_row.addWidget(self.le_ex_addr)
        self.lbl_ex_size = BodyLabel(self.tr("大小"))
        ex_row.addWidget(self.lbl_ex_size)
        self.cb_ex_preset = ComboBox(self)
        for label, _ in _SIZE_PRESETS:
            if label == "自定义":
                self.cb_ex_preset.addItem(self.tr("自定义"))
            else:
                self.cb_ex_preset.addItem(label)
        # 把 cb_ex_preset 索引限制到合法范围（防止 user_prefs.json 被手改成越界值）
        _preset_idx = max(0, min(int(self._cfg.get("mem_export_preset_idx")), len(_SIZE_PRESETS) - 1))
        self.cb_ex_preset.setCurrentIndex(_preset_idx)
        ex_row.addWidget(self.cb_ex_preset)
        self.le_ex_custom = LineEdit(self)
        self.le_ex_custom.setPlaceholderText("0x100000")
        self.le_ex_custom.setMaximumWidth(120)
        self.le_ex_custom.setText(str(self._cfg.get("mem_export_custom_size")))
        # 自定义大小只在 preset == 自定义 时启用（_SIZE_PRESETS 末项 size=-1）
        self.le_ex_custom.setEnabled(_SIZE_PRESETS[_preset_idx][1] < 0)
        ex_row.addWidget(self.le_ex_custom)
        self.btn_choose = PushButton(self.tr("选择保存路径"), self)
        ex_row.addWidget(self.btn_choose)
        ex_row.addStretch(1)
        ex_root.addLayout(ex_row)

        self.lbl_path = BodyLabel(self.tr("（未选择保存路径）"), self)
        ex_root.addWidget(self.lbl_path)

        bottom = QHBoxLayout()
        self.btn_export = PrimaryPushButton(self.tr("开始导出"), self)
        self.btn_export.setEnabled(False)
        self.pb_export = ProgressBar(self)
        self.pb_export.setRange(0, 100)
        self.pb_export.setValue(0)
        bottom.addWidget(self.btn_export)
        bottom.addWidget(self.pb_export, 1)
        ex_root.addLayout(bottom)
        root.addWidget(export_card)

        # 控件初始 disabled 状态（启动时未连接）
        self.btn_read.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_write.setEnabled(False)
        self.chk_auto_refresh.setEnabled(False)

        # Hover tooltip：在 hex 字节字符上悬停时显示 LE/BE 解析
        # PlainTextEdit 的鼠标事件在 viewport 上，需要 mouse tracking + filter
        self.display.viewport().setMouseTracking(True)
        self.display.viewport().installEventFilter(self)
        # 用 Fluent 风格气泡替代原生 QToolTip（圆角 + 阴影，与全应用 tooltip 一致）
        from .widgets.fluent_hover_tip import FluentHoverTip
        self._hover_tip = FluentHoverTip(self.display)

    # ------------------------------------------------------------------
    # i18n：语言切换
    # ------------------------------------------------------------------
    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        super().changeEvent(event)

    def _retranslate_ui(self) -> None:
        # ---- 读取卡片 ----
        self.lbl_read_addr.setText(self.tr("起始地址"))
        self.lbl_read_size.setText(self.tr("大小 (字节)"))
        self.lbl_row_width.setText(self.tr("字节/行"))
        self.btn_read.setText(self.tr("读取"))
        self.btn_clear.setText(self.tr("清空"))
        self.chk_hover.setText(self.tr("悬浮解析"))
        self.chk_hover.setToolTip(self.tr("鼠标悬停在 hex 字节上时显示地址和 LE/BE 解析"))
        self.chk_auto_refresh.setText(self.tr("自动刷新"))
        self.chk_diff.setText(self.tr("高亮变化"))
        self.chk_diff.setToolTip(self.tr("重新读取相同地址/大小时，把变化的字节背景标红"))
        self.lbl_goto.setText(self.tr("跳转到"))
        self.lbl_search.setText(self.tr("Hex 搜索"))
        self.btn_find_next.setText(self.tr("查找下一个"))
        self.btn_font_minus.setToolTip(self.tr("Hex 字号 −1"))
        self.btn_font_plus.setToolTip(self.tr("Hex 字号 +1"))

        # ---- 数据类型解析面板 ----
        self.lbl_data_types_title.setText(self.tr("数据类型解析"))
        self.lbl_cursor_addr_label.setText(self.tr("光标地址"))
        self.lbl_endian_label.setText(self.tr("字节序"))
        # ComboBox 字节序：保留当前索引，block signals 避免触发 _refresh_types
        cur_endian = self.cb_endian.currentIndex()
        self.cb_endian.blockSignals(True)
        self.cb_endian.clear()
        self.cb_endian.addItems([self.tr("小端 (LE)"), self.tr("大端 (BE)")])
        self.cb_endian.setCurrentIndex(cur_endian)
        self.cb_endian.blockSignals(False)

        # ---- 复制按钮 ----
        self.btn_copy_hex.setText(self.tr("复制选中为 Hex 字串"))
        self.btn_copy_ascii.setText(self.tr("复制选中为 ASCII"))
        self.btn_copy_carray.setText(self.tr("复制全部为 C 数组"))
        self.btn_save_bin.setText(self.tr("保存全部为 .bin"))

        # ---- 导出固件卡片 ----
        self.lbl_export_title.setText(self.tr("导出固件（按块流式写盘）"))
        self.lbl_ex_addr.setText(self.tr("起始地址"))
        self.lbl_ex_size.setText(self.tr("大小"))
        self.btn_choose.setText(self.tr("选择保存路径"))
        # lbl_path：已选路径时保留路径，否则显示翻译后的占位文本
        if self._save_path:
            self.lbl_path.setText(self._save_path)
        else:
            self.lbl_path.setText(self.tr("（未选择保存路径）"))
        self.btn_export.setText(self.tr("开始导出"))
        # cb_ex_preset：重新填充翻译后的 items（含 "自定义"），保留索引
        cur_preset = self.cb_ex_preset.currentIndex()
        self.cb_ex_preset.blockSignals(True)
        self.cb_ex_preset.clear()
        for label, _ in _SIZE_PRESETS:
            if label == "自定义":
                self.cb_ex_preset.addItem(self.tr("自定义"))
            else:
                self.cb_ex_preset.addItem(label)
        self.cb_ex_preset.setCurrentIndex(cur_preset)
        self.cb_ex_preset.blockSignals(False)

        # ---- 写内存卡片 ----
        self.lbl_write_title.setText(self.tr("写内存 ⚠"))
        self.warn_lbl.setText(self.tr("写错地址可能让 MCU 失去响应直到复位。仅在确认安全地址（如 SRAM）时使用。"))
        self.lbl_write_addr.setText(self.tr("地址"))
        self.lbl_write_data.setText(self.tr("Hex 数据"))
        self.btn_write.setText(self.tr("写入…"))

    # ------------------------------------------------------------------
    # 信号接线
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        self.btn_read.clicked.connect(self._on_read_clicked)
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        self.cb_ex_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.btn_choose.clicked.connect(self._on_choose_path)
        self.btn_export.clicked.connect(self._on_export_clicked)

        self.cb_row_width.currentTextChanged.connect(self._on_row_width_changed)
        self.cb_endian.currentIndexChanged.connect(self._refresh_types)
        self.cb_endian.currentIndexChanged.connect(
            lambda idx: self._cfg.set("mem_endian_little", idx == 0)
        )
        self.chk_auto_refresh.toggled.connect(self._on_auto_refresh_toggled)
        self.chk_diff.toggled.connect(lambda v: self._cfg.set("mem_diff_highlight", v))
        self.chk_hover.toggled.connect(lambda v: self._cfg.set("mem_hover_parse", v))
        self.sp_refresh_sec.valueChanged.connect(self._on_refresh_sec_changed)

        # LineEdit 用 editingFinished：避免每键击都写盘，且按钮点击的 focus 离开会触发
        self.le_read_addr.editingFinished.connect(
            lambda: self._cfg.set("mem_read_addr", self.le_read_addr.text())
        )
        self.le_read_size.editingFinished.connect(
            lambda: self._cfg.set("mem_read_size", self.le_read_size.text())
        )
        self.le_ex_addr.editingFinished.connect(
            lambda: self._cfg.set("mem_export_addr", self.le_ex_addr.text())
        )
        self.le_ex_custom.editingFinished.connect(
            lambda: self._cfg.set("mem_export_custom_size", self.le_ex_custom.text())
        )
        self.le_write_addr.editingFinished.connect(
            lambda: self._cfg.set("mem_write_addr", self.le_write_addr.text())
        )
        self.le_goto.editingFinished.connect(
            lambda: self._cfg.set("mem_goto_addr", self.le_goto.text())
        )
        self.btn_goto.clicked.connect(self._on_goto_clicked)
        self.le_goto.returnPressed.connect(self._on_goto_clicked)
        self.btn_find_next.clicked.connect(self._on_find_next)
        self.le_search.returnPressed.connect(self._on_find_next)
        self.btn_copy_hex.clicked.connect(self._on_copy_hex)
        self.btn_copy_ascii.clicked.connect(self._on_copy_ascii)
        self.btn_copy_carray.clicked.connect(self._on_copy_carray)
        self.btn_save_bin.clicked.connect(self._on_save_bin)
        self.btn_write.clicked.connect(self._on_write_clicked)

        # 字号 ± 按钮：走 cfg → memory_font_size_changed → _apply_font
        self.btn_font_minus.clicked.connect(lambda: self._adjust_font_size(-1))
        self.btn_font_plus.clicked.connect(lambda: self._adjust_font_size(+1))
        # cfg 信号：family 固定跟随 RTT 的 font_family（等宽）——hex dump 必须等宽对齐，
        # 不跟随全局界面字体（切到非等宽 UI 字体会让列错位）；size 走 memory_font_size（独立）。
        self._cfg.font_changed.connect(lambda fam, _sz: self._apply_font(fam or None, None))
        self._cfg.memory_font_size_changed.connect(lambda sz: self._apply_font(None, sz))

        self.display.cursorPositionChanged.connect(self._refresh_types)

        # worker → UI 跨线程连接：一律显式 QueuedConnection
        self._worker.connection_state_changed.connect(
            self._set_enabled_by_connection, Qt.QueuedConnection
        )
        self._worker.memory_read_finished.connect(self._on_memory_read, Qt.QueuedConnection)
        self._worker.firmware_export_progress.connect(self._on_export_progress, Qt.QueuedConnection)
        self._worker.firmware_export_finished.connect(self._on_export_finished, Qt.QueuedConnection)
        self._worker.command_result.connect(self._on_command_result, Qt.QueuedConnection)

    # ------------------------------------------------------------------
    # 状态切换
    # ------------------------------------------------------------------
    def _set_enabled_by_connection(self, connected: bool) -> None:
        self._connected = connected
        self.btn_read.setEnabled(connected)
        self.btn_export.setEnabled(connected and bool(self._save_path))
        self.btn_write.setEnabled(connected)
        self.chk_auto_refresh.setEnabled(connected)
        if not connected:
            # 断开时主动停止自动刷新——但不弹 InfoBar，避免 N 次断开 N 个噪音
            if self.chk_auto_refresh.isChecked():
                self.chk_auto_refresh.setChecked(False)

    # ------------------------------------------------------------------
    # 读取 / 显示
    # ------------------------------------------------------------------
    def _on_clear_clicked(self) -> None:
        self.display.clear()
        self._buffer = b""
        self._buffer_base = 0
        self._last_hover_offset = -1
        self.lbl_cursor_addr.setText("—")
        for lbl in self._type_labels.values():
            lbl.setText("—")

    def _on_row_width_changed(self, text: str) -> None:
        try:
            n = int(text)
        except ValueError:
            return
        if n in _BYTES_PER_ROW_OPTIONS:
            self._bytes_per_row = n
            self._cfg.set("mem_bytes_per_row", n)
            self._rerender()

    def _on_read_clicked(self) -> None:
        if not self._connected:
            _infobar.warn(self, self.tr("未连接 J-Link"), self.tr("请先到 RTT 监控页连接 J-Link"))
            return
        try:
            addr = _parse_int(self.le_read_addr.text())
            size = _parse_int(self.le_read_size.text())
        except ValueError as e:
            _infobar.warn(self, self.tr("地址/大小格式错误"), str(e))
            return
        if size <= 0 or size > 16 * 1024 * 1024:
            _infobar.warn(self, self.tr("大小越界"), self.tr("1B - 16MB"))
            return
        self._worker.read_memory_requested.emit(addr, size)

    def _refresh_once(self) -> None:
        """自动刷新 timer 槽：在已连接时重读当前地址/大小。"""
        if self._connected:
            self._on_read_clicked()

    def _on_memory_read(self, addr: int, raw: bytes) -> None:
        # 抓 diff 用的「上一帧」快照（只在 reassign 前几行内有意义；
        # 不需要 self._prev_* 实例字段——重读后 prev 永远等于当前 buffer，是死状态）
        prev = self._buffer
        prev_base = self._buffer_base
        self._buffer = raw
        self._buffer_base = addr
        self._rerender()
        self._refresh_types()

        # Diff 仅在 地址+长度 都不变 且 在阈值内 时计算
        if (self.chk_diff.isChecked() and prev_base == addr
                and len(prev) == len(raw) and len(raw) <= _DIFF_MAX_SIZE):
            diff_offsets = [i for i in range(len(raw)) if raw[i] != prev[i]]
            if diff_offsets:
                self._highlight_diff(diff_offsets[:_DIFF_MAX_HIGHLIGHTS])
                return
        self.display.setExtraSelections([])

    def _highlight_diff(self, offsets: list[int]) -> None:
        """给变化字节的 HH 字符（2 列）叠红色半透明背景。

        Why findBlockByNumber + setPosition (O(1) per offset)：原实现对每个 offset
        都 cursor.Start → Down*row → Right*col 三步走，单次 O(rows)；512 个 diff
        在 16 KB 多行 buffer 上能阻塞 UI 数百 ms。改用 doc.findBlockByNumber(row)
        + setPosition(block.position()+col) 后是 O(1)，整轮 ~µs 级。
        """
        doc = self.display.document()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 80, 80, 100))
        bpr = self._bytes_per_row
        selections: list[QTextEdit.ExtraSelection] = []
        for offset in offsets:
            row, col_in_row = divmod(offset, bpr)
            block = doc.findBlockByNumber(row)
            if not block.isValid():
                continue
            pos = block.position() + self._byte_start_col(col_in_row)
            cursor = QTextCursor(doc)
            cursor.setPosition(pos)
            cursor.setPosition(pos + 2, QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            selections.append(sel)
        self.display.setExtraSelections(selections)

    # ------------------------------------------------------------------
    # Hover tooltip
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.display.viewport():
            if event.type() == QEvent.MouseMove:
                self._show_hover_tooltip(event)
            elif event.type() == QEvent.Leave:
                self._hover_tip.hide()
        return super().eventFilter(obj, event)

    def _show_hover_tooltip(self, event) -> None:
        if not self._chk_hover_enabled():
            self._hover_tip.hide()
            return
        if not self._buffer:
            return
        cursor = self.display.cursorForPosition(event.pos())
        offset = self._byte_offset_at(cursor.blockNumber(), cursor.positionInBlock())
        if offset < 0 or offset >= len(self._buffer):
            self._last_hover_offset = -1
            self._hover_tip.hide()
            return
        if offset == self._last_hover_offset:
            return  # 仍在同一字节，tooltip 已显示
        self._last_hover_offset = offset
        addr = self._buffer_base + offset
        # 同一 offset 给出 4 字节 LE/BE + 2 字节 LE，方便对照寄存器布局
        le_u32 = parse_value(self._buffer, offset, "u32", True)
        be_u32 = parse_value(self._buffer, offset, "u32", False)
        le_u16 = parse_value(self._buffer, offset, "u16", True)
        text = (f"{self.tr('地址')} 0x{addr:08X}  (+{offset})\n"
                f"u32 LE: {le_u32}\n"
                f"u32 BE: {be_u32}\n"
                f"u16 LE: {le_u16}")
        # duration=0：hover 持续显示直到鼠标移走（Leave 时 hide）
        self._hover_tip.show_at(event.globalPosition().toPoint(), text, duration=0)

    def _chk_hover_enabled(self) -> bool:
        return self.chk_hover.isChecked()

    def _rerender(self) -> None:
        if not self._buffer:
            self.display.setPlainText("")
            return
        text = format_hex_dump(self._buffer, self._buffer_base, self._bytes_per_row)
        self.display.setPlainText(text)

    # ------------------------------------------------------------------
    # 类型解析
    # ------------------------------------------------------------------
    def _refresh_types(self) -> None:
        if not self._buffer:
            self.lbl_cursor_addr.setText("—")
            for lbl in self._type_labels.values():
                lbl.setText("—")
            return
        offset = self._cursor_byte_offset()
        if offset < 0 or offset >= len(self._buffer):
            self.lbl_cursor_addr.setText("—")
            for lbl in self._type_labels.values():
                lbl.setText("—")
            return
        self.lbl_cursor_addr.setText(f"0x{self._buffer_base + offset:08X}  (+{offset})")
        little_endian = self.cb_endian.currentIndex() == 0
        for dt, lbl in self._type_labels.items():
            lbl.setText(parse_value(self._buffer, offset, dt, little_endian))

    @staticmethod
    def _byte_start_col(col_in_row: int) -> int:
        """字节在行内的起始列（hex 区每字节 'HH ' 3 列，每 4 字节末加 1 列分组空格）。
        是 _byte_offset_at 的正向映射，被 _highlight_diff + _select_buffer_range 共用。
        """
        return _HEX_START_COL + col_in_row * 3 + (col_in_row // 4)

    def _byte_offset_at(self, block_num: int, col: int) -> int:
        """根据 (block行号, 列位置) 反推 buffer 字节偏移；-1 表示越界。

        format_hex_dump 每行：``0xXXXXXXXX:  HH HH HH HH  HH HH HH HH ...``
        硬契约：hex 区起始 col 13，每字节 ``HH `` 3 字符，每 4 字节末加 1 个分组空格。
        被 _cursor_byte_offset（点击）+ _hover_byte_offset（悬停）共用。
        契约由 test_format_hex_dump_row_layout_contract 保护。
        """
        if not self._buffer:
            return -1
        bpr = self._bytes_per_row
        line_offset = block_num * bpr
        if line_offset >= len(self._buffer):
            return -1
        if col < _HEX_START_COL:
            return line_offset  # 光标在地址列，定位行首
        c = col - _HEX_START_COL
        consumed = 0
        for j in range(bpr):
            byte_chars = 3 + (1 if j % 4 == 3 else 0)
            if consumed + byte_chars > c:
                return min(line_offset + j, len(self._buffer) - 1)
            consumed += byte_chars
        return min(line_offset + bpr - 1, len(self._buffer) - 1)

    def _cursor_byte_offset(self) -> int:
        if not self._buffer:
            return -1
        cur = self.display.textCursor()
        return self._byte_offset_at(cur.blockNumber(), cur.positionInBlock())

    # ------------------------------------------------------------------
    # 自动刷新
    # ------------------------------------------------------------------
    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        if checked and self._connected:
            self._auto_refresh_timer.start(self.sp_refresh_sec.value() * 1000)
        else:
            self._auto_refresh_timer.stop()

    def _on_refresh_sec_changed(self, sec: int) -> None:
        self._cfg.set("mem_refresh_sec", sec)
        if self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.start(sec * 1000)

    # ------------------------------------------------------------------
    # 跳转 / 搜索
    # ------------------------------------------------------------------
    def _on_goto_clicked(self) -> None:
        if not self._buffer:
            return
        try:
            addr = _parse_int(self.le_goto.text())
        except ValueError as e:
            _infobar.warn(self, self.tr("地址格式错误"), str(e))
            return
        self._cfg.set("mem_goto_addr", self.le_goto.text())
        offset = addr - self._buffer_base
        if offset < 0 or offset >= len(self._buffer):
            _infobar.warn(self, self.tr("地址越界"), self.tr("该地址不在已读取的缓冲区内"))
            return
        self._select_buffer_range(offset, 1)

    def _on_find_next(self) -> None:
        if not self._buffer:
            return
        try:
            needle = _parse_hex_pattern(self.le_search.text())
        except ValueError as e:
            _infobar.warn(self, self.tr("Hex 格式错误"), str(e))
            return
        # 从当前光标位置之后开始找
        start = self._cursor_byte_offset() + 1
        if start < 0 or start >= len(self._buffer):
            start = 0
        idx = self._buffer.find(needle, start)
        if idx < 0 and start > 0:
            idx = self._buffer.find(needle, 0)  # 回卷
        if idx < 0:
            _infobar.warn(self, self.tr("未找到"), f"pattern={needle.hex(' ').upper()}")
            return
        self._select_buffer_range(idx, len(needle))

    def _select_buffer_range(self, byte_offset: int, byte_len: int) -> None:
        """把光标移到 byte_offset 对应的 hex 列位置。"""
        bpr = self._bytes_per_row
        row, col_in_row = divmod(byte_offset, bpr)
        col = self._byte_start_col(col_in_row)
        cursor = self.display.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, row)
        cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, col)
        # 选中 byte_len 字节（粗略：每字节 3 列宽）
        select_cols = byte_len * 3 - 1
        cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, select_cols)
        self.display.setTextCursor(cursor)
        self.display.ensureCursorVisible()

    # ------------------------------------------------------------------
    # 复制 / 保存
    # ------------------------------------------------------------------
    def _on_copy_hex(self) -> None:
        if not self._buffer:
            return
        sel = self._selected_bytes()
        QGuiApplication.clipboard().setText(sel.hex(" ").upper())
        _infobar.ok(self, self.tr("已复制 Hex"), f"{len(sel)} {self.tr('字节')}", duration=1500)

    def _on_copy_ascii(self) -> None:
        if not self._buffer:
            return
        sel = self._selected_bytes()
        text = "".join(chr(b) if 32 <= b <= 126 else "." for b in sel)
        QGuiApplication.clipboard().setText(text)
        _infobar.ok(self, self.tr("已复制 ASCII"), f"{len(sel)} {self.tr('字节')}", duration=1500)

    def _on_copy_carray(self) -> None:
        if not self._buffer:
            return
        text = format_as_c_array(self._buffer, name="data", bytes_per_row=self._bytes_per_row)
        QGuiApplication.clipboard().setText(text)
        _infobar.ok(self, self.tr("已复制 C 数组"), f"{len(self._buffer)} {self.tr('字节')}", duration=1500)

    def _on_save_bin(self) -> None:
        if not self._buffer:
            _infobar.warn(self, self.tr("无数据"), self.tr("请先读取内存"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("保存为 .bin"),
            f"mem_0x{self._buffer_base:08X}_{len(self._buffer)}B.bin",
            "Binary (*.bin);;All (*)",
        )
        if not path:
            return
        try:
            from pathlib import Path
            Path(path).write_bytes(self._buffer)
            _infobar.ok(self, self.tr("已保存"), path)
        except OSError as e:
            _infobar.err(self, self.tr("保存失败"), str(e))

    def _selected_bytes(self) -> bytes:
        """从当前 hex 选区反推 bytes；如无选区则返回全部 buffer。"""
        cursor = self.display.textCursor()
        if not cursor.hasSelection():
            return self._buffer
        # 反推选区起止字节
        start_block = self.display.document().findBlock(cursor.selectionStart()).blockNumber()
        end_block = self.display.document().findBlock(cursor.selectionEnd()).blockNumber()
        bpr = self._bytes_per_row
        start_byte = start_block * bpr
        end_byte = min((end_block + 1) * bpr, len(self._buffer))
        return self._buffer[start_byte:end_byte]

    # ------------------------------------------------------------------
    # 固件导出
    # ------------------------------------------------------------------
    def _on_preset_changed(self, idx: int) -> None:
        _, size = _SIZE_PRESETS[idx]
        self.le_ex_custom.setEnabled(size < 0)
        self._cfg.set("mem_export_preset_idx", idx)

    def _on_choose_path(self) -> None:
        from datetime import datetime
        default = f"firmware_{datetime.now():%Y%m%d_%H%M%S}.bin"
        path, _ = QFileDialog.getSaveFileName(self, self.tr("选择导出路径"), default, "Binary (*.bin);;All (*)")
        if path:
            self._save_path = path
            self.lbl_path.setText(path)
            self.btn_export.setEnabled(self._connected)

    def _on_export_clicked(self) -> None:
        try:
            start = _parse_int(self.le_ex_addr.text())
        except ValueError as e:
            _infobar.warn(self, self.tr("地址格式错误"), str(e))
            return
        idx = self.cb_ex_preset.currentIndex()
        _, preset_size = _SIZE_PRESETS[idx]
        if preset_size < 0:
            try:
                size = _parse_int(self.le_ex_custom.text())
            except ValueError as e:
                _infobar.warn(self, self.tr("大小格式错误"), str(e))
                return
        else:
            size = preset_size

        _infobar.warn(self, self.tr("RTT 接收将暂停"),
                      self.tr("导出 {n} KB 期间无法接收 RTT 数据").format(n=size // 1024))
        self.pb_export.setValue(0)
        self.btn_export.setEnabled(False)
        self._worker.export_firmware_requested.emit(self._save_path, start, size)

    def _on_export_progress(self, current: int, total: int) -> None:
        pct = int(current * 100 / total)
        self.pb_export.setValue(pct)

    def _on_export_finished(self, ok: bool, path: str, err: str) -> None:
        self.btn_export.setEnabled(self._connected)
        if ok:
            _infobar.ok(self, self.tr("导出完成"), path, duration=3000)
        else:
            _infobar.err(self, self.tr("导出失败"), err, duration=4000)

    def _on_command_result(self, cmd: str, ok: bool, msg: str) -> None:
        if cmd == "read_memory" and not ok:
            _infobar.err(self, self.tr("读取失败"), msg or "")
        elif cmd == "write_memory":
            if ok:
                _infobar.ok(self, self.tr("写入成功"), msg or "")
            else:
                _infobar.err(self, self.tr("写入失败"), msg or "")

    def _on_write_clicked(self) -> None:
        """点 "写入…" → 二次确认 → emit write_memory_requested。"""
        if not self._connected:
            _infobar.warn(self, self.tr("未连接"), self.tr("请先连接 J-Link"))
            return
        try:
            addr = _parse_int(self.le_write_addr.text())
        except ValueError as e:
            _infobar.warn(self, self.tr("地址格式错误"), str(e))
            return
        try:
            data = _parse_hex_pattern(self.le_write_data.text())
        except ValueError as e:
            _infobar.warn(self, self.tr("Hex 数据格式错误"), str(e))
            return
        if not data:
            _infobar.warn(self, self.tr("无数据"), self.tr("请输入要写入的 Hex 字节"))
            return
        # 二次确认（高风险）
        preview = data[:16].hex(" ").upper() + (" ..." if len(data) > 16 else "")
        content = self.tr(
            "即将向地址 <b>0x%1</b> 写入 <b>%2</b> 字节：<br/>"
            "<code>%3</code><br/><br/>"
            "<span style='color:#d04040;'>⚠ 写错地址可能让 MCU 失去响应！</span><br/>"
            "请确认地址是可写区域（SRAM/外设寄存器，<b>不要写 Flash 控制器</b>）"
        )
        content = content.replace("%1", f"{addr:08X}").replace("%2", str(len(data))).replace("%3", preview)
        msg_box = MessageBox(self.tr("⚠ 确认写入内存"), content, self)
        msg_box.yesButton.setText(self.tr("确认写入"))
        msg_box.cancelButton.setText(self.tr("取消"))
        if not msg_box.exec():
            return
        self._worker.write_memory_requested.emit(addr, data)

    # ------------------------------------------------------------------
    # 字体 / 字号
    # ------------------------------------------------------------------
    def _apply_font(self, family: str | None, size: int | None) -> None:
        """family/size 任意一者传 None 表示沿用 cfg 当前值。

        family：固定用 RTT 的等宽字体（font_family，默认 Consolas）——hex dump 列对齐
            依赖等宽，不跟随全局界面字体（ui_font_family 可设成非等宽，跟了会错位）。
        size：内存页独立字号（memory_font_size），不随全局界面字号变。
        """
        if family is None:
            family = self._cfg.get("font_family") or "Consolas"
        if size is None or size <= 0:
            size = int(self._cfg.get("memory_font_size") or 12)
        font = QFont(family, size)
        self.display.setFont(font)
        # 标记专属字体：全局界面字号/字体热更新时跳过（hex 区 family+字号都独立自控）。
        self.display.setProperty("_custom_font", True)
        if hasattr(self, "lbl_font_size"):
            self.lbl_font_size.setText(str(size))

    def _adjust_font_size(self, delta: int) -> None:
        cur = int(self._cfg.get("memory_font_size") or 12)
        new = max(8, min(32, cur + delta))
        if new != cur:
            self._cfg.set("memory_font_size", new)
