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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
)

from core.config_service import ConfigService
from core.jlink_worker import JLinkWorker
from core.memory_service import format_as_c_array, format_hex_dump, parse_value


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
        self._bytes_per_row: int = 16

        # 自动刷新 timer
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(False)
        self._auto_refresh_timer.timeout.connect(self._refresh_once)

        self._build_ui()
        self._wire_signals()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- 读取卡片 ----
        read_card = CardWidget(self)
        read_outer = QVBoxLayout(read_card)
        read_outer.setSpacing(8)

        r1 = QHBoxLayout()
        r1.addWidget(BodyLabel("起始地址"))
        self.le_read_addr = LineEdit(self)
        self.le_read_addr.setText("0x08000000")
        self.le_read_addr.setMaximumWidth(140)
        r1.addWidget(self.le_read_addr)
        r1.addWidget(BodyLabel("大小 (字节)"))
        self.le_read_size = LineEdit(self)
        self.le_read_size.setText("0x100")
        self.le_read_size.setMaximumWidth(100)
        r1.addWidget(self.le_read_size)
        r1.addWidget(BodyLabel("字节/行"))
        self.cb_row_width = ComboBox(self)
        for n in _BYTES_PER_ROW_OPTIONS:
            self.cb_row_width.addItem(str(n))
        self.cb_row_width.setCurrentText("16")
        r1.addWidget(self.cb_row_width)
        self.btn_read = PrimaryPushButton("读取", self)
        self.btn_clear = PushButton("清空", self)
        r1.addWidget(self.btn_read)
        r1.addWidget(self.btn_clear)
        r1.addStretch(1)
        read_outer.addLayout(r1)

        # 第二行：自动刷新 + 跳转 + 搜索
        r2 = QHBoxLayout()
        self.chk_auto_refresh = CheckBox("自动刷新")
        self.sp_refresh_sec = SpinBox(self)
        self.sp_refresh_sec.setRange(1, 60)
        self.sp_refresh_sec.setValue(2)
        self.sp_refresh_sec.setSuffix(" s")
        r2.addWidget(self.chk_auto_refresh)
        r2.addWidget(self.sp_refresh_sec)
        r2.addSpacing(16)
        r2.addWidget(BodyLabel("跳转到"))
        self.le_goto = LineEdit(self)
        self.le_goto.setPlaceholderText("0x...")
        self.le_goto.setMaximumWidth(140)
        self.btn_goto = PushButton("Go", self)
        r2.addWidget(self.le_goto)
        r2.addWidget(self.btn_goto)
        r2.addSpacing(16)
        r2.addWidget(BodyLabel("Hex 搜索"))
        self.le_search = LineEdit(self)
        self.le_search.setPlaceholderText("AA BB CC")
        self.le_search.setMaximumWidth(180)
        self.btn_find_next = PushButton("查找下一个", self)
        r2.addWidget(self.le_search)
        r2.addWidget(self.btn_find_next)
        r2.addStretch(1)
        read_outer.addLayout(r2)

        root.addWidget(read_card)

        # ---- 主体：左 Hex dump + 右 解析面板（QSplitter）----
        splitter = QSplitter(Qt.Horizontal, self)

        # 左：hex dump
        self.display = PlainTextEdit(self)
        self.display.setReadOnly(True)
        self.display.setLineWrapMode(PlainTextEdit.NoWrap)
        font = QFont("Consolas", 12)
        self.display.setFont(font)
        splitter.addWidget(self.display)

        # 右：数据类型解析面板
        side = CardWidget(self)
        side_lay = QVBoxLayout(side)
        side_lay.addWidget(StrongBodyLabel("数据类型解析"))

        info_row = QHBoxLayout()
        info_row.addWidget(BodyLabel("光标地址"))
        self.lbl_cursor_addr = StrongBodyLabel("—")
        info_row.addWidget(self.lbl_cursor_addr, 1)
        side_lay.addLayout(info_row)

        endian_row = QHBoxLayout()
        endian_row.addWidget(BodyLabel("字节序"))
        self.cb_endian = ComboBox(self)
        self.cb_endian.addItems(["小端 (LE)", "大端 (BE)"])
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
        self.btn_copy_hex = PushButton("复制选中为 Hex 字串", self)
        self.btn_copy_ascii = PushButton("复制选中为 ASCII", self)
        self.btn_copy_carray = PushButton("复制全部为 C 数组", self)
        self.btn_save_bin = PushButton("保存全部为 .bin", self)
        copy_row.addWidget(self.btn_copy_hex)
        copy_row.addWidget(self.btn_copy_ascii)
        copy_row.addWidget(self.btn_copy_carray)
        copy_row.addWidget(self.btn_save_bin)
        side_lay.addLayout(copy_row)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 280])
        root.addWidget(splitter, 1)

        # ---- 导出固件卡片（保留原功能）----
        export_card = CardWidget(self)
        ex_root = QVBoxLayout(export_card)
        ex_root.addWidget(StrongBodyLabel("导出固件（按块流式写盘）"))
        ex_row = QHBoxLayout()
        ex_row.addWidget(BodyLabel("起始地址"))
        self.le_ex_addr = LineEdit(self)
        self.le_ex_addr.setText("0x08000000")
        self.le_ex_addr.setMaximumWidth(140)
        ex_row.addWidget(self.le_ex_addr)
        ex_row.addWidget(BodyLabel("大小"))
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

        # 控件初始 disabled 状态（启动时未连接）
        self.btn_read.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.chk_auto_refresh.setEnabled(False)

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
        self.chk_auto_refresh.toggled.connect(self._on_auto_refresh_toggled)
        self.sp_refresh_sec.valueChanged.connect(self._on_refresh_sec_changed)
        self.btn_goto.clicked.connect(self._on_goto_clicked)
        self.le_goto.returnPressed.connect(self._on_goto_clicked)
        self.btn_find_next.clicked.connect(self._on_find_next)
        self.le_search.returnPressed.connect(self._on_find_next)
        self.btn_copy_hex.clicked.connect(self._on_copy_hex)
        self.btn_copy_ascii.clicked.connect(self._on_copy_ascii)
        self.btn_copy_carray.clicked.connect(self._on_copy_carray)
        self.btn_save_bin.clicked.connect(self._on_save_bin)

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
            self._rerender()

    def _on_read_clicked(self) -> None:
        if not self._connected:
            InfoBar.warning("未连接 J-Link", "请先到 RTT 监控页连接 J-Link",
                            parent=self, position=InfoBarPosition.TOP, duration=2000)
            return
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

    def _refresh_once(self) -> None:
        """自动刷新 timer 槽：在已连接时重读当前地址/大小。"""
        if self._connected:
            self._on_read_clicked()

    def _on_memory_read(self, addr: int, raw: bytes) -> None:
        self._buffer = raw
        self._buffer_base = addr
        self._rerender()
        self._refresh_types()

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

    def _cursor_byte_offset(self) -> int:
        """根据文本光标在 hex dump 中的位置反推 buffer 字节偏移。

        format_hex_dump 每行：``0xAAAAAAAA:  HH HH HH HH  HH HH HH HH ...`` →
        块头长度 12 字符（``0x12345678:``）+ 2 空格 = 14。每字节 ``HH ``= 3 字符，
        每 4 字节末加一个额外空格。
        """
        if not self._buffer:
            return -1
        cur = self.display.textCursor()
        block_num = cur.blockNumber()
        col = cur.positionInBlock()
        bpr = self._bytes_per_row
        line_offset = block_num * bpr
        if line_offset >= len(self._buffer):
            return -1
        # 14 = 0x + 8 hex + : + 2 spaces
        hex_start = 14
        if col < hex_start:
            return line_offset  # 光标在地址列，定位行首
        # 找到属于第几个字节：每字节 3 字符（"HH "），每 4 字节后多 1 个空格
        c = col - hex_start
        idx = 0
        consumed = 0
        for j in range(bpr):
            byte_chars = 3  # "HH "
            if j % 4 == 3:
                byte_chars += 1  # 块间额外空格
            if consumed + byte_chars > c:
                idx = j
                break
            consumed += byte_chars
            idx = j + 1
        return min(line_offset + idx, len(self._buffer) - 1)

    # ------------------------------------------------------------------
    # 自动刷新
    # ------------------------------------------------------------------
    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        if checked and self._connected:
            self._auto_refresh_timer.start(self.sp_refresh_sec.value() * 1000)
        else:
            self._auto_refresh_timer.stop()

    def _on_refresh_sec_changed(self, sec: int) -> None:
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
            InfoBar.warning("地址格式错误", str(e), parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        offset = addr - self._buffer_base
        if offset < 0 or offset >= len(self._buffer):
            InfoBar.warning("地址越界", "该地址不在已读取的缓冲区内", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        self._select_buffer_range(offset, 1)

    def _on_find_next(self) -> None:
        if not self._buffer:
            return
        try:
            needle = _parse_hex_pattern(self.le_search.text())
        except ValueError as e:
            InfoBar.warning("Hex 格式错误", str(e), parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        # 从当前光标位置之后开始找
        start = self._cursor_byte_offset() + 1
        if start < 0 or start >= len(self._buffer):
            start = 0
        idx = self._buffer.find(needle, start)
        if idx < 0 and start > 0:
            idx = self._buffer.find(needle, 0)  # 回卷
        if idx < 0:
            InfoBar.warning("未找到", f"pattern={needle.hex(' ').upper()}", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        self._select_buffer_range(idx, len(needle))

    def _select_buffer_range(self, byte_offset: int, byte_len: int) -> None:
        """把光标移到 byte_offset 对应的 hex 列位置。"""
        bpr = self._bytes_per_row
        row = byte_offset // bpr
        col_in_row = byte_offset % bpr
        # 反推列：地址列 14 字符 + col_in_row * 3 + (col_in_row // 4) * 1
        col = 14 + col_in_row * 3 + (col_in_row // 4) * 1
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
        InfoBar.success("已复制 Hex", f"{len(sel)} 字节", parent=self,
                        position=InfoBarPosition.TOP, duration=1500)

    def _on_copy_ascii(self) -> None:
        if not self._buffer:
            return
        sel = self._selected_bytes()
        text = "".join(chr(b) if 32 <= b <= 126 else "." for b in sel)
        QGuiApplication.clipboard().setText(text)
        InfoBar.success("已复制 ASCII", f"{len(sel)} 字节", parent=self,
                        position=InfoBarPosition.TOP, duration=1500)

    def _on_copy_carray(self) -> None:
        if not self._buffer:
            return
        text = format_as_c_array(self._buffer, name="data", bytes_per_row=self._bytes_per_row)
        QGuiApplication.clipboard().setText(text)
        InfoBar.success("已复制 C 数组", f"{len(self._buffer)} 字节", parent=self,
                        position=InfoBarPosition.TOP, duration=1500)

    def _on_save_bin(self) -> None:
        if not self._buffer:
            InfoBar.warning("无数据", "请先读取内存", parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存为 .bin",
            f"mem_0x{self._buffer_base:08X}_{len(self._buffer)}B.bin",
            "Binary (*.bin);;All (*)",
        )
        if not path:
            return
        try:
            from pathlib import Path
            Path(path).write_bytes(self._buffer)
            InfoBar.success("已保存", path, parent=self,
                            position=InfoBarPosition.TOP, duration=2000)
        except OSError as e:
            InfoBar.error("保存失败", str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=3000)

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

    def _on_command_result(self, cmd: str, ok: bool, msg: str) -> None:
        if cmd == "read_memory" and not ok:
            InfoBar.error("读取失败", msg or "", parent=self,
                          position=InfoBarPosition.TOP, duration=3000)
