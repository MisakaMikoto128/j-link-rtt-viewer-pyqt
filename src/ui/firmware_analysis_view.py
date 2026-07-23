"""固件分析视图：把「符号 / 段 / 占用汇总 / Flash 占用图」四个视图用 SegmentedWidget 切换，
共用同一个已选 axf/elf。作为烧录页底部的附属信息面板，不独立成页。

- 符号 Symbols：SymbolTableView（名称/地址/大小/类型/绑定/段/占段%）
- 段 Sections：内存相关段(SHF_ALLOC) 的地址/大小/RWX/对齐
- 占用汇总 Summary：text/data/bss + Flash/RAM 总量 + 入口/初始 SP/Reset_Handler
- Flash 占用图 FlashMap：固件在选中 MCU Flash 中的位置和占比，颜色跟随主题色
"""
from __future__ import annotations

import contextlib

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHeaderView,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    SegmentedWidget,
    StrongBodyLabel,
    TableWidget,
    themeColor,
)

from core.flash_file_parser import (
    FileParseError,
    read_elf_meta,
    read_memory_summary,
    read_sections,
)
from core.target_discovery import TargetDeviceInfo

from .symbol_table_view import SymbolTableView, _NumericItem

_SEC_COLUMNS = ["Name", "Address", "Size", "Flags", "Align"]

# i18n source strings
_SEC_TITLE_BASE = "段表 Sections"
_SUMMARY_HINT = (
    "内存占用采用 arm-none-eabi-size 的 Berkeley 统计方式："
    "text = 已加载的可执行/只读段（.text/.rodata/.isr_vector），"
    "data = 已初始化可写段（.data），bss = 未初始化段（.bss）；"
    "Flash = text + data，RAM = data + bss。"
    "初始 SP / Reset_Handler 按 Cortex-M 约定，从最低 LOAD 段头 8 字节"
    "读取（向量表第 0、1 个字），非 Cortex-M 架构无意义。"
)
_SEC_HINT = "仅列出占用内存的段（SHF_ALLOC）。"


def _human(n: int) -> str:
    """字节数 → '1234 B (1.2 KiB)' 风格。"""
    if n < 1024:
        return f"{n} B"
    return f"{n} B ({n / 1024:.1f} KiB)"


def _human_short(n: int) -> str:
    """字节数 → '64 KB' / '1 MB' 风格。"""
    if n >= 1024 * 1024 and n % (1024 * 1024) == 0:
        return f"{n // (1024 * 1024)} MB"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024} KB"
    return f"{n} B"


class _FlashMapWidget(QWidget):
    """水平条形图：显示固件在 MCU Flash 中的位置与占比。

    - 背景 = 未占用 Flash
    - 主题色 = 固件占用区
    - 红色 = 超出 Flash 范围的溢出区
    - 无设备/无固件时显示占位文字
    """

    _OVERFLOW_COLOR = QColor("#e74c3c")
    _TEXT_MARGIN = 4
    _BAR_HEIGHT = 24
    _RADIUS = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device: TargetDeviceInfo | None = None
        self._fw_start: int | None = None
        self._fw_end: int | None = None
        self.setMinimumHeight(70)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

    def set_device_info(self, info: TargetDeviceInfo | None) -> None:
        self._device = info
        self.update()

    def set_firmware_range(self, start: int | None, end: int | None) -> None:
        self._fw_start = start
        self._fw_end = end
        self.update()

    def clear(self) -> None:
        self._fw_start = None
        self._fw_end = None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        bar_y = 8
        bar_h = self._BAR_HEIGHT
        margin = 6
        bar_w = max(0, w - margin * 2)

        # 背景条（未占用 Flash）
        bg = self.palette().window().color()
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(margin, bar_y, bar_w, bar_h, self._RADIUS, self._RADIUS)

        text_y = bar_y + bar_h + 8

        if self._device is None or not self._device.flash_size:
            painter.setPen(self.palette().text().color())
            painter.drawText(
                margin, text_y, bar_w, 20,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.tr("未选择目标设备"),
            )
            painter.end()
            return

        flash_addr = self._device.flash_addr or 0
        flash_size = self._device.flash_size
        flash_end = flash_addr + flash_size

        if self._fw_start is None or self._fw_end is None:
            # 无固件：不显示占用区，只显示 Flash 容量占位
            label = (
                f"Flash: 0x{flash_addr:08X} - 0x{flash_end - 1:08X} "
                f"({_human_short(flash_size)}) · {self.tr('无固件占用')}"
            )
            painter.setPen(self.palette().text().color())
            painter.drawText(
                margin, text_y, bar_w, 20,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            painter.end()
            return

        fw_start = self._fw_start
        fw_end = self._fw_end
        fw_size = max(0, fw_end - fw_start)

        # 像素映射：Flash 起始地址对应 0 像素
        def _addr_to_x(addr: int) -> float:
            if flash_size <= 0:
                return margin
            ratio = (addr - flash_addr) / flash_size
            return margin + ratio * bar_w

        x_start = _addr_to_x(fw_start)
        x_end = _addr_to_x(fw_end)

        # 溢出检测
        overflow = fw_end > flash_end

        # 正常占用区（裁剪到 Flash 范围内）
        occ_x = max(margin, x_start)
        occ_w = min(x_end, margin + bar_w) - occ_x
        if occ_w > 0:
            color = themeColor()
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(occ_x, bar_y, occ_w, bar_h, self._RADIUS, self._RADIUS)

        # 溢出区
        if overflow:
            over_x = margin + bar_w
            over_w = x_end - (margin + bar_w)
            if over_w > 0:
                painter.setBrush(self._OVERFLOW_COLOR)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(over_x, bar_y, over_w, bar_h, self._RADIUS, self._RADIUS)

        # 文字
        pct = min(100.0, fw_size / flash_size * 100) if flash_size else 0.0
        label = (
            f"Flash: 0x{flash_addr:08X} - 0x{flash_end - 1:08X} "
            f"({_human_short(flash_size)}) | "
            f"{self.tr('固件')}: 0x{fw_start:08X} - 0x{fw_end - 1:08X} "
            f"({_human_short(fw_size)} / {pct:.1f}%)"
        )
        if overflow:
            label += self.tr(" ⚠ 超出 Flash 范围")

        painter.setPen(self.palette().text().color())
        painter.drawText(
            margin, text_y, bar_w, 20,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        painter.end()


class _SectionsView(QWidget):
    """内存相关段（SHF_ALLOC）表。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._sec_count: int = -1  # -1 = 未加载
        self.lbl_title = StrongBodyLabel(self.tr(_SEC_TITLE_BASE))
        layout.addWidget(self.lbl_title)

        self.table = TableWidget()
        self.table.setColumnCount(len(_SEC_COLUMNS))
        self.table.setHorizontalHeaderLabels(_SEC_COLUMNS)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        self.lbl_hint = CaptionLabel(self.tr(_SEC_HINT))
        self.lbl_hint.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.lbl_hint)

    def load(self, path: str) -> None:
        try:
            sections = read_sections(path)
        except FileParseError:
            self.clear()
            return
        self._sec_count = len(sections)
        self.table.setRowCount(self._sec_count)
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        for r, s in enumerate(sections):
            name_item = QTableWidgetItem(s.name)
            addr_item = _NumericItem(s.addr, f"0x{s.addr:08X}")
            size_item = _NumericItem(s.size, str(s.size))
            flags_item = QTableWidgetItem(s.flags)
            align_item = _NumericItem(s.align, str(s.align))
            align_item.setData(Qt.ItemDataRole.UserRole, s.align)
            for c, item in enumerate(
                    (name_item, addr_item, size_item, flags_item, align_item)):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.setUpdatesEnabled(True)
        self.lbl_title.setText(self.tr(_SEC_TITLE_BASE) + f"（{self._sec_count}）")

    def clear(self) -> None:
        self._sec_count = -1
        self.table.setRowCount(0)
        self.lbl_title.setText(self.tr(_SEC_TITLE_BASE))


class _SummaryView(QWidget):
    """内存占用汇总 + ELF 元信息 + Flash 占用图。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        self._lbl_flash_map_title = StrongBodyLabel(self.tr("Flash 占用图"))
        layout.addWidget(self._lbl_flash_map_title)
        self.flash_map = _FlashMapWidget()
        layout.addWidget(self.flash_map)

        self._lbl_mem_title = StrongBodyLabel(self.tr("内存占用 Memory usage"))
        layout.addWidget(self._lbl_mem_title)
        form = QFormLayout()
        form.setSpacing(8)
        self.lbl_flash = BodyLabel("-")
        self.lbl_ram = BodyLabel("-")
        self.lbl_text = BodyLabel("-")
        self.lbl_data = BodyLabel("-")
        self.lbl_bss = BodyLabel("-")
        self._row_flash = StrongBodyLabel(self.tr("Flash（text+data）"))
        self._row_ram = StrongBodyLabel(self.tr("RAM（data+bss）"))
        self._row_text = BodyLabel(self.tr("text（代码 + 只读）"))
        self._row_data = BodyLabel(self.tr("data（已初始化）"))
        self._row_bss = BodyLabel(self.tr("bss（未初始化）"))
        form.addRow(self._row_flash, self.lbl_flash)
        form.addRow(self._row_ram, self.lbl_ram)
        form.addRow(self._row_text, self.lbl_text)
        form.addRow(self._row_data, self.lbl_data)
        form.addRow(self._row_bss, self.lbl_bss)
        layout.addLayout(form)

        self._lbl_entry_title = StrongBodyLabel(self.tr("入口与向量 Entry & vectors"))
        layout.addWidget(self._lbl_entry_title)
        form2 = QFormLayout()
        form2.setSpacing(8)
        self.lbl_entry = BodyLabel("-")
        self.lbl_sp = BodyLabel("-")
        self.lbl_reset = BodyLabel("-")
        self._row_entry = BodyLabel("Entry point")
        self._row_sp = BodyLabel(self.tr("初始 SP（向量表[0]）"))
        self._row_reset = BodyLabel(self.tr("Reset_Handler（向量表[1]）"))
        form2.addRow(self._row_entry, self.lbl_entry)
        form2.addRow(self._row_sp, self.lbl_sp)
        form2.addRow(self._row_reset, self.lbl_reset)
        layout.addLayout(form2)

        self.lbl_hint = CaptionLabel(self.tr(_SUMMARY_HINT))
        self.lbl_hint.setStyleSheet("color: #6b7280;")
        self.lbl_hint.setWordWrap(True)
        layout.addWidget(self.lbl_hint)
        layout.addStretch(1)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        super().changeEvent(event)

    def _retranslate_ui(self) -> None:
        self._lbl_flash_map_title.setText(self.tr("Flash 占用图"))
        self._lbl_mem_title.setText(self.tr("内存占用 Memory usage"))
        self._row_flash.setText(self.tr("Flash（text+data）"))
        self._row_ram.setText(self.tr("RAM（data+bss）"))
        self._row_text.setText(self.tr("text（代码 + 只读）"))
        self._row_data.setText(self.tr("data（已初始化）"))
        self._row_bss.setText(self.tr("bss（未初始化）"))
        self._lbl_entry_title.setText(self.tr("入口与向量 Entry & vectors"))
        self._row_sp.setText(self.tr("初始 SP（向量表[0]）"))
        self._row_reset.setText(self.tr("Reset_Handler（向量表[1]）"))
        self.lbl_hint.setText(self.tr(_SUMMARY_HINT))

    def set_device_info(self, info: TargetDeviceInfo | None) -> None:
        self.flash_map.set_device_info(info)

    def set_firmware_range(self, start: int | None, end: int | None) -> None:
        self.flash_map.set_firmware_range(start, end)

    def load(self, path: str) -> None:
        try:
            s = read_memory_summary(path)
            m = read_elf_meta(path)
        except FileParseError:
            self.clear()
            return
        self.lbl_flash.setText(_human(s.flash))
        self.lbl_ram.setText(_human(s.ram))
        self.lbl_text.setText(_human(s.text))
        self.lbl_data.setText(_human(s.data))
        self.lbl_bss.setText(_human(s.bss))
        self.lbl_entry.setText(f"0x{m.entry:08X}")
        self.lbl_sp.setText(
            f"0x{m.initial_sp:08X}" if m.initial_sp is not None else "—")
        self.lbl_reset.setText(
            f"0x{m.reset_handler:08X}" if m.reset_handler is not None else "—")

    def clear(self) -> None:
        for lbl in (self.lbl_flash, self.lbl_ram, self.lbl_text, self.lbl_data,
                    self.lbl_bss, self.lbl_entry, self.lbl_sp, self.lbl_reset):
            lbl.setText("-")
        self.flash_map.clear()


class FirmwareAnalysisView(QWidget):
    """SegmentedWidget 切换的「符号 / 段 / 占用汇总 / Flash 占用图」复合视图。"""

    # pivot 文本 key → source string
    _PIVOT_ITEMS = [
        ("symbols", "符号 Symbols"),
        ("sections", "段 Sections"),
        ("summary", "占用汇总 Summary"),
        ("flashmap", "Flash 占用图"),
    ]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.pivot = SegmentedWidget()
        self.stack = QStackedWidget()
        layout.addWidget(self.pivot)
        layout.addWidget(self.stack, 1)

        self.symbols = SymbolTableView()
        self.sections = _SectionsView()
        self.summary = _SummaryView()
        self.flashmap = self.summary.flash_map
        for w, key, text in [
            (self.symbols, "symbols", self.tr("符号 Symbols")),
            (self.sections, "sections", self.tr("段 Sections")),
            (self.summary, "summary", self.tr("占用汇总 Summary")),
            (self.flashmap, "flashmap", self.tr("Flash 占用图")),
        ]:
            self._add(w, key, text)

        self.stack.currentChanged.connect(self._sync_pivot)
        self.pivot.setCurrentItem("symbols")
        self.stack.setCurrentWidget(self.symbols)

    def _add(self, w: QWidget, key: str, text: str) -> None:
        w.setObjectName(key)
        self.stack.addWidget(w)
        self.pivot.addItem(
            routeKey=key, text=text,
            onClick=lambda: self.stack.setCurrentWidget(w))

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        super().changeEvent(event)

    def _retranslate_ui(self) -> None:
        for key, src in self._PIVOT_ITEMS:
            item = self.pivot.widget(key)
            if item is not None:
                with contextlib.suppress(Exception):
                    item.setText(self.tr(src))

    def _sync_pivot(self, idx: int) -> None:
        self.pivot.setCurrentItem(self.stack.widget(idx).objectName())

    def set_device_info(self, info: TargetDeviceInfo | None) -> None:
        self.summary.set_device_info(info)

    def set_firmware_range(self, start: int | None, end: int | None) -> None:
        self.summary.set_firmware_range(start, end)

    # ---- 公开 API：三视图共用同一路径 ----
    def load(self, path: str) -> None:
        self.symbols.load(path)
        self.sections.load(path)
        self.summary.load(path)

    def clear(self) -> None:
        self.symbols.clear()
        self.sections.clear()
        self.summary.clear()


class FlashOccupancyBar(QWidget):
    """烧录页固件文件卡片内的紧凑 Flash 占用条（一行高）。

    与 _FlashMapWidget 同一数据模型（device info + 固件地址范围），但更紧凑：
    无下方文字行，占比文字直接画在条上。任何固件格式（bin/hex/axf）加载后
    都显示；占用区颜色跟随主题色；无固件 / 无设备时只有背景条、无占用区。
    """

    _OVERFLOW_COLOR = QColor("#e74c3c")
    _BAR_H = 16
    _RADIUS = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device: TargetDeviceInfo | None = None
        self._fw_start: int | None = None
        self._fw_end: int | None = None
        self.setMinimumHeight(self._BAR_H + 8)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

    def set_device_info(self, info: TargetDeviceInfo | None) -> None:
        self._device = info
        self.update()

    def set_firmware_range(self, start: int | None, end: int | None) -> None:
        self._fw_start = start
        self._fw_end = end
        self.update()

    def clear(self) -> None:
        self._fw_start = None
        self._fw_end = None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 2
        bar_y = 4
        bar_h = self._BAR_H
        bar_w = max(0, self.width() - margin * 2)

        # 背景条（未占用 Flash）
        painter.setBrush(self.palette().window().color())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(margin, bar_y, bar_w, bar_h, self._RADIUS, self._RADIUS)

        # 无设备 -> 空条 + 提示
        if self._device is None or not self._device.flash_size:
            painter.setPen(self.palette().text().color())
            painter.drawText(
                margin, bar_y, bar_w, bar_h,
                Qt.AlignmentFlag.AlignCenter,
                self.tr("未选择目标设备"),
            )
            painter.end()
            return

        flash_addr = self._device.flash_addr or 0
        flash_size = self._device.flash_size
        flash_end = flash_addr + flash_size

        # 无固件 -> 空条 + 「无固件占用」
        if self._fw_start is None or self._fw_end is None:
            painter.setPen(self.palette().text().color())
            painter.drawText(
                margin, bar_y, bar_w, bar_h,
                Qt.AlignmentFlag.AlignCenter,
                f"{self.tr('无固件占用')} · {_human_short(flash_size)}",
            )
            painter.end()
            return

        fw_start = self._fw_start
        fw_end = self._fw_end
        fw_size = max(0, fw_end - fw_start)

        def _addr_to_x(addr: int) -> float:
            if flash_size <= 0:
                return margin
            return margin + (addr - flash_addr) / flash_size * bar_w

        x_start = _addr_to_x(fw_start)
        x_end = _addr_to_x(fw_end)
        overflow = fw_end > flash_end

        # 占用区（主题色，裁剪到 Flash 范围内）
        occ_x = max(margin, x_start)
        occ_w = min(x_end, margin + bar_w) - occ_x
        if occ_w > 0:
            painter.setBrush(themeColor())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(occ_x, bar_y, occ_w, bar_h, self._RADIUS, self._RADIUS)

        # 溢出区（红色，超出 Flash 末尾）
        if overflow:
            over_x = margin + bar_w
            over_w = x_end - over_x
            if over_w > 0:
                painter.setBrush(self._OVERFLOW_COLOR)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(over_x, bar_y, over_w, bar_h, self._RADIUS, self._RADIUS)

        # 占比文字
        pct = min(100.0, fw_size / flash_size * 100) if flash_size else 0.0
        label = f"{_human_short(fw_size)} / {_human_short(flash_size)} ({pct:.1f}%)"
        if overflow:
            label += self.tr(" ⚠ 超出")
        painter.setPen(self.palette().text().color())
        painter.drawText(
            margin, bar_y, bar_w, bar_h,
            Qt.AlignmentFlag.AlignCenter,
            label,
        )
        painter.end()
